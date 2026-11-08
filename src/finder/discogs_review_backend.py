"""Private DB-owned backend for known-listing reads; no web authentication."""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

import httpx

from finder.discogs_completeness import MAX_COMMENTS
from finder.discogs_listing import GRADE_NAMES, ListingReadError, listing_id, normalize
from finder.discogs_listing_preview import PreviewReader, preview_price
from finder.discogs_read_guard import DatabaseReadBudget, ReadSafetyHalt, guard_row


class DeadlineStream(httpx.SyncByteStream):
    def __init__(self, stream, deadline, clock):
        self.stream, self.deadline, self.clock = stream, deadline, clock

    def __iter__(self):
        for chunk in self.stream:
            if self.clock() > self.deadline:
                raise httpx.ReadTimeout("bounded_public_read")
            yield chunk

    def close(self):
        self.stream.close()


class DeadlineTransport(httpx.BaseTransport):
    """Bound slow-drip bodies:12s elapsed plus at most one8s transport operation."""

    def __init__(self, inner=None, *, clock=time.monotonic):
        self.inner = inner or httpx.HTTPTransport(retries=0, trust_env=False)
        self.clock = clock

    def handle_request(self, request):
        deadline = self.clock() + 12
        response = self.inner.handle_request(request)
        response.stream = DeadlineStream(response.stream, deadline, self.clock)
        return response

    def close(self):
        self.inner.close()


class PublicPreviewReader(PreviewReader):
    # Ignore proxy and netrc environment credentials. Never use DiscogsAppAuth.
    _trust_env = False


def _guard_authorizer(action, table, column, database, source):
    """Restrict request connection writes, not a substitute for OS separation."""
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_TRANSACTION):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and column == "max":
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (
        table == "busy_timeout"
        or (table in {"application_id", "synchronous", "journal_mode"} and column is None)
    ):
        return sqlite3.SQLITE_OK
    allowed = {
        "discogs_api_read_guard": {"pending_token", "pending_since", "known_deadline"},
        "discogs_monitor_control": {"next_request"},
        "discogs_phone_review_state": {"access_denied"},
    }
    if action == sqlite3.SQLITE_UPDATE and column in allowed.get(table, set()):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class ReviewReadBudget(DatabaseReadBudget):
    """Retain the durable guard until the response is classified.

    Classify/persist an access refusal before releasing the shared request slot.
    """

    response_delay = None

    def response(self, seconds):
        self.response_delay = seconds

    def finish(self):
        if self.response_delay is not None:
            super().response(self.response_delay)


def initialize_review_state(db):
    """Explicit operator provisioning only, never called by a web request.

    Additive page-local stop state in the real shared DB. Never clears a latch.
    """
    if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
        raise ReadSafetyHalt("review_wrong_workspace")
    guard_row(db)
    with db:
        db.execute("BEGIN IMMEDIATE")
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='discogs_phone_review_state'"
        ).fetchone()
        if not exists:
            db.execute(
                "CREATE TABLE discogs_phone_review_state ("
                "id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL, "
                "access_denied INTEGER NOT NULL CHECK(access_denied IN (0,1)))"
            )
            db.execute("INSERT INTO discogs_phone_review_state VALUES(1,1,0)")
        _review_state(db)


def _review_state(db):
    rows = db.execute(
        "SELECT id,schema_version,access_denied FROM discogs_phone_review_state"
    ).fetchall()
    if len(rows) != 1 or tuple(rows[0]) not in {(1, 1, 0), (1, 1, 1)}:
        raise ReadSafetyHalt("review_state_invalid")
    return rows[0][2]


def _enabled_control(db):
    rows = db.execute("SELECT enabled,revision FROM discogs_price_control WHERE id=1").fetchall()
    if len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not int or rows[0][1] < 0:
        raise ReadSafetyHalt("review_stopped")
    return tuple(rows[0])


class DirectReviewBackend:
    """Only broker/operator fixtures may hold this backend in production."""

    def __init__(self, database, *, transport=None, clock=time.time, sleep=time.sleep):
        self.database = Path(database).resolve()
        self.transport, self.clock, self.sleep = transport, clock, sleep

    def status(self):
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
                raise ReadSafetyHalt("review_wrong_workspace")
            denied = bool(_review_state(db))
            _enabled_control(db)
            guard_row(db)
            return "denied" if denied else "ready"

    def read(self, ident):
        ident = listing_id(ident)
        # mode=rw never creates a replacement DB; no schema initialization or guard repair.
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=rw", uri=True)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.set_authorizer(_guard_authorizer)
            guard_row(db)
            control = _enabled_control(db)

            def check():
                if _review_state(db):
                    raise ReadSafetyHalt("review_access_denied")
                if _enabled_control(db) != control:
                    raise ReadSafetyHalt("review_control_changed")

            budget = ReviewReadBudget(db, clock=self.clock, sleep=self.sleep, check=check)
            reader = PublicPreviewReader(
                budget=budget, transport=DeadlineTransport(self.transport), wall_clock=self.clock
            )
            try:
                payload = reader._request(
                    # One <=3s wait may precede the atomic slot recheck, never HTTP replay.
                    f"/marketplace/listings/{ident}",
                    params={"curr_abbr": "USD"},
                    wait=True,
                )
            except ListingReadError as error:
                if error.access_restricted:
                    # Keep shared reservation until the page-only stop is durable.
                    # If this write fails, leave pending intact: persistence is uncertain.
                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute(
                            "UPDATE discogs_phone_review_state SET access_denied=1 WHERE id=1"
                        )
                budget.finish()
                raise
            budget.finish()
            check()
        return project_payload(payload, ident)


def project_payload(payload, ident):
    """Minimal API-shaped record; retain unsupported comments so sanitizing cannot pass."""
    normalized = normalize(payload, ident)

    def price(key, original=False):
        value = preview_price(payload.get(key), original=original)
        if value is None:
            return None
        return {
            "value": value["raw_value"],
            "curr_abbr" if original else "currency": value["currency"],
        }

    comments = payload.get("comments")
    return {
        "id": ident,
        "release": {"id": normalized["release_id"]},
        "condition": payload.get("condition")
        if isinstance(payload.get("condition"), str) and payload["condition"] in GRADE_NAMES
        else None,
        "sleeve_condition": payload.get("sleeve_condition")
        if isinstance(payload.get("sleeve_condition"), str)
        and payload["sleeve_condition"] in GRADE_NAMES
        else None,
        "status": "For Sale" if normalized["available"] else None,
        "price": price("price"),
        "original_price": price("original_price", True),
        "shipping_price": price("shipping_price"),
        "shipping_is_blocked": normalized["shipping_blocked"],
        # MAX_COMMENTS+1 preserves the existing truncation/unsupported signal.
        "comments": comments[: MAX_COMMENTS + 1] if isinstance(comments, str) else None,
    }
