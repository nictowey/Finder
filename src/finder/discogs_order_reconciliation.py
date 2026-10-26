"""Durable Discogs order evidence, with no checkout or payment executor.

Discogs creates an order before payment checkout. This ledger therefore fences a
possible submit before any eventual browser action and never interprets a missing
lookup as permission to submit again. It does not authorize a purchase, navigate,
read a browser, click a control, or send a payment.
"""

import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path
from uuid import UUID

APPLICATION_ID = 1397637171
SCHEMA_VERSION = 1
ORDER_ID = re.compile(r"^[0-9]{1,20}-[0-9]{1,20}$")
LISTING_ID = re.compile(r"^[1-9][0-9]{0,15}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
SELLER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
CURRENCIES = frozenset({"USD", "EUR", "GBP", "CAD", "AUD"})
PAYMENT_STATES = {
    "Invoice Sent": "unpaid",
    "Payment Pending": "pending",
    "Payment Received": "paid",
    "In Progress": "paid",
    "Shipped": "paid",
}


class OrderEvidenceStopped(RuntimeError):
    """A fixed local stop, not a provider response or permission to retry."""


def _uuid(value):
    try:
        parsed = UUID(value) if type(value) is str else None
    except (ValueError, AttributeError):
        parsed = None
    if parsed is None or parsed.int == 0 or str(parsed) != value:
        raise ValueError("A canonical opaque reference is required.")


def _money(value):
    if type(value) is not str or not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})\.[0-9]{2}", value):
        raise ValueError("An exact nonnegative money amount is required.")
    try:
        Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid money amount.") from None


@dataclass(frozen=True, slots=True)
class ExpectedOrder:
    """A proposed one-copy order identity, not evidence of owner authorization."""

    intent_id: str
    approval_ref: str
    release_id: int
    listing_id: str
    seller: str
    quoted_total: str
    currency: str
    quote_fingerprint: str
    rules_revision: int
    destination_ref: str
    payment_ref: str
    quote_expires_at: float

    def __post_init__(self):
        for value in (self.intent_id, self.approval_ref, self.destination_ref, self.payment_ref):
            _uuid(value)
        if (
            type(self.release_id) is not int
            or not 0 < self.release_id <= 9007199254740991
            or type(self.listing_id) is not str
            or not LISTING_ID.fullmatch(self.listing_id)
            or type(self.seller) is not str
            or not SELLER.fullmatch(self.seller)
            or self.currency not in CURRENCIES
            or type(self.quote_fingerprint) is not str
            or not FINGERPRINT.fullmatch(self.quote_fingerprint)
            or type(self.rules_revision) is not int
            or self.rules_revision < 0
            or type(self.quote_expires_at) not in (int, float)
            or not isfinite(self.quote_expires_at)
        ):
            raise ValueError("Invalid proposed order identity.")
        _money(self.quoted_total)


@dataclass(frozen=True, slots=True)
class OrderPageEvidence:
    """Normalized future read-only order detail; no reader is supplied here."""

    order_id: str
    listing_id: str
    seller: str
    total: str
    currency: str
    displayed_status: str
    observed_at: float
    source: str

    def __post_init__(self):
        if (
            type(self.order_id) is not str
            or not ORDER_ID.fullmatch(self.order_id)
            or type(self.listing_id) is not str
            or not LISTING_ID.fullmatch(self.listing_id)
            or type(self.seller) is not str
            or not SELLER.fullmatch(self.seller)
            or self.currency not in CURRENCIES
            or type(self.displayed_status) is not str
            or len(self.displayed_status) > 80
            or type(self.observed_at) not in (int, float)
            or not isfinite(self.observed_at)
            or self.source not in {"normal_order_page", "official_order_api"}
        ):
            raise ValueError("Invalid read-only order-page evidence.")
        _money(self.total)


class DiscogsOrderLedger:
    """Owner-only one-copy fence and post-attempt read-only reconciliation."""

    def __init__(self, path: Path, *, create=False):
        self.path = Path(path).absolute()
        if self.path.is_symlink():
            raise ValueError("Use a dedicated regular ledger file.")
        if create:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        elif not self.path.is_file():
            raise ValueError("Ledger file has not been initialized.")
        info = self.path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
        ):
            raise ValueError("Ledger must be an owner-only regular file.")
        with self._db() as db:
            if create:
                db.executescript(f"""
                    BEGIN IMMEDIATE;
                    PRAGMA application_id={APPLICATION_ID};
                    PRAGMA user_version={SCHEMA_VERSION};
                    CREATE TABLE intents (
                        release_id INTEGER PRIMARY KEY,
                        intent_id TEXT NOT NULL UNIQUE,
                        approval_ref TEXT NOT NULL,
                        listing_id TEXT NOT NULL,
                        seller TEXT NOT NULL,
                        quoted_total TEXT NOT NULL,
                        currency TEXT NOT NULL,
                        quote_fingerprint TEXT NOT NULL,
                        rules_revision INTEGER NOT NULL,
                        destination_ref TEXT NOT NULL,
                        payment_ref TEXT NOT NULL,
                        quote_expires_at REAL NOT NULL,
                        state TEXT NOT NULL CHECK(state IN
                            ('prepared','submission_unknown','order_seen','identity_conflict')),
                        order_id TEXT UNIQUE,
                        payment_state TEXT NOT NULL CHECK(payment_state IN
                            ('unknown','unpaid','pending','paid')),
                        review_required INTEGER NOT NULL CHECK(review_required IN (0,1)),
                        last_status TEXT,
                        last_observed_at REAL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );
                    CREATE TRIGGER no_intent_delete BEFORE DELETE ON intents
                      BEGIN SELECT RAISE(ABORT,'order intents cannot be deleted'); END;
                    COMMIT;
                """)
            elif (
                db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            ):
                raise ValueError("Not a supported Discogs order ledger.")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(f"{self.path.as_uri()}?mode=rw", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _public(row):
        return {
            "intent_id": row["intent_id"],
            "release_id": row["release_id"],
            "state": row["state"],
            "order_id": row["order_id"],
            "payment_state": row["payment_state"],
            "review_required": bool(row["review_required"]),
            "may_submit_again": False,
            "purchase_allowed": False,
        }

    def prepare(self, expected: ExpectedOrder, *, now):
        """Keep a one-copy slot; a quoted intent alone cannot submit anything."""
        if (
            not isinstance(expected, ExpectedOrder)
            or type(now) not in (int, float)
            or not isfinite(now)
        ):
            raise ValueError("Expected order and timestamp required.")
        if not now < expected.quote_expires_at <= now + 300:
            raise OrderEvidenceStopped("quote_expired_or_too_old")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM intents WHERE release_id=?", (expected.release_id,)
            ).fetchone()
            if row:
                if all(
                    row[field] == getattr(expected, field)
                    for field in expected.__dataclass_fields__
                ):
                    return self._public(row)
                raise OrderEvidenceStopped("one_copy_slot_already_used")
            db.execute(
                "INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'prepared',NULL,'unknown',0,"
                "NULL,NULL,?,?)",
                (
                    expected.release_id,
                    expected.intent_id,
                    expected.approval_ref,
                    expected.listing_id,
                    expected.seller,
                    expected.quoted_total,
                    expected.currency,
                    expected.quote_fingerprint,
                    expected.rules_revision,
                    expected.destination_ref,
                    expected.payment_ref,
                    expected.quote_expires_at,
                    now,
                    now,
                ),
            )
            row = db.execute(
                "SELECT * FROM intents WHERE intent_id=?", (expected.intent_id,)
            ).fetchone()
            return self._public(row)

    def fence_possible_submit(self, intent_id, *, now):
        """Mark uncertainty BEFORE an eventual committing click; never issue that click."""
        _uuid(intent_id)
        if type(now) not in (int, float) or not isfinite(now):
            raise ValueError("Timestamp required.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE intents SET state='submission_unknown',updated_at=? "
                "WHERE intent_id=? AND state='prepared' AND quote_expires_at>?",
                (now, intent_id, now),
            ).rowcount
            if changed != 1:
                raise OrderEvidenceStopped("intent_missing_stale_or_already_fenced")
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            return self._public(row)

    def record_read_only_lookup(self, intent_id, evidence: OrderPageEvidence | None, *, now):
        """Record what an order page shows; absence and mismatch never release the slot."""
        _uuid(intent_id)
        if evidence is not None and not isinstance(evidence, OrderPageEvidence):
            raise ValueError("Only normalized read-only order evidence is accepted.")
        if type(now) not in (int, float) or not isfinite(now):
            raise ValueError("Timestamp required.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            if row is None or row["state"] == "prepared":
                raise OrderEvidenceStopped("no_fenced_submission_to_reconcile")
            if evidence is None:
                return self._public(row)
            if evidence.observed_at > now or evidence.observed_at < row["created_at"]:
                raise OrderEvidenceStopped("order_evidence_time_unverified")
            if row["order_id"] and row["order_id"] != evidence.order_id:
                db.execute(
                    "UPDATE intents SET state='identity_conflict',review_required=1,updated_at=? "
                    "WHERE intent_id=?",
                    (now, intent_id),
                )
                return self._public(
                    db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
                )
            matches = (
                row["listing_id"] == evidence.listing_id
                and row["seller"].casefold() == evidence.seller.casefold()
                and row["quoted_total"] == evidence.total
                and row["currency"] == evidence.currency
            )
            payment = PAYMENT_STATES.get(evidence.displayed_status, "unknown")
            if (
                row["last_observed_at"] is not None
                and evidence.observed_at < row["last_observed_at"]
            ):
                raise OrderEvidenceStopped("stale_order_evidence")
            rank = {"unknown": 0, "unpaid": 1, "pending": 2, "paid": 3}
            downgrade = rank[payment] < rank[row["payment_state"]]
            review = not matches or payment == "unknown" or downgrade or row["review_required"]
            new_payment = row["payment_state"] if downgrade or not matches else payment
            state = (
                "order_seen"
                if matches and row["state"] != "identity_conflict"
                else "identity_conflict"
            )
            db.execute(
                "UPDATE intents SET state=?,order_id=?,payment_state=?,review_required=?,"
                "last_status=?,last_observed_at=?,updated_at=? WHERE intent_id=?",
                (
                    state,
                    evidence.order_id,
                    new_payment,
                    int(review),
                    evidence.displayed_status,
                    evidence.observed_at,
                    now,
                    intent_id,
                ),
            )
            return self._public(
                db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            )

    def status(self, intent_id):
        _uuid(intent_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            return self._public(row) if row else None
