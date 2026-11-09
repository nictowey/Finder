"""Bounded retained aggregate alert reads. No writer or reconciliation dependencies."""

import math
import sqlite3
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

from finder.discogs_rules import release_id

ACTIVITY_REQUEST_LIMIT = 1024
ACTIVITY_RESPONSE_LIMIT = 16384
STATES = frozenset(
    ("accepted", "pending", "retry_wait", "leased", "uncertain", "failed", "expired", "revoked")
)
KINDS = frozenset(("availability", "price_drop", "count_increase", "price_drop_and_count_increase"))
_COLUMNS = (
    "SELECT o.rule_revision,o.episode,o.observed_at,o.state,"
    "c.event_key IS NOT NULL,c.previous_price,c.previous_count,c.count,o.price "
    "FROM discogs_price_outbox o LEFT JOIN discogs_price_changes c ON c.event_key=o.event_key "
    "WHERE o.release_id=? "
)
ACTIVITY_QUERY = _COLUMNS + "ORDER BY o.rule_revision DESC,o.episode DESC LIMIT 21"
ACTIVITY_NEXT_QUERY = (
    _COLUMNS
    + "AND (o.rule_revision,o.episode)<(?,?) ORDER BY o.rule_revision DESC,o.episode DESC LIMIT 21"
)


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value < 2**63:
        raise ValueError("Invalid activity")
    return value


def _cursor(value):
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"revision", "episode"}:
        raise ValueError("Invalid activity")
    return {"revision": _integer(value["revision"], 1), "episode": _integer(value["episode"])}


def activity_fields(fields):
    if type(fields) is not dict or set(fields) != {"release_id", "cursor"}:
        raise ValueError("Invalid activity")
    ident = fields["release_id"]
    if type(ident) is not int:
        raise ValueError("Invalid activity")
    return {"release_id": release_id(ident), "cursor": _cursor(fields["cursor"])}


def project_activity(data, ident=None, cursor=None):
    """Exact projection checked independently on both ends of the private socket."""
    if type(data) is not dict or set(data) != {
        "release_id",
        "current_revision",
        "rows",
        "next_cursor",
        "has_more",
    }:
        raise ValueError("Invalid activity")
    activity_fields({"release_id": data["release_id"], "cursor": cursor})
    if ident is not None and data["release_id"] != ident:
        raise ValueError("Invalid activity")
    revision = _integer(data["current_revision"], 1)
    rows = data["rows"]
    if type(rows) is not list or len(rows) > 20 or type(data["has_more"]) is not bool:
        raise ValueError("Invalid activity")
    previous = (cursor["revision"], cursor["episode"]) if cursor else None
    for row in rows:
        if type(row) is not dict or set(row) != {
            "revision",
            "episode",
            "observed_at",
            "state",
            "kind",
            "settings_scope",
        }:
            raise ValueError("Invalid activity")
        seq = (_integer(row["revision"], 1), _integer(row["episode"]))
        if seq[0] > revision or previous is not None and seq >= previous:
            raise ValueError("Invalid activity")
        previous = seq
        timestamp = row["observed_at"]
        if (
            type(timestamp) not in (float, int)
            or timestamp < 0
            or timestamp > 1.7976931348623157e308
            or not math.isfinite(timestamp)
        ):
            raise ValueError("Invalid activity")
        if (
            type(row["state"]) is not str
            or row["state"] not in STATES
            or type(row["kind"]) is not str
            or row["kind"] not in KINDS
        ):
            raise ValueError("Invalid activity")
        if row["settings_scope"] != ("current" if seq[0] == revision else "earlier"):
            raise ValueError("Invalid activity")
    next_cursor = _cursor(data["next_cursor"])
    expected = (
        {"revision": rows[-1]["revision"], "episode": rows[-1]["episode"]}
        if data["has_more"] and rows
        else None
    )
    if next_cursor != expected or data["has_more"] and len(rows) != 20:
        raise ValueError("Invalid activity")
    return data


def _indexed(db, table, columns):
    for index in db.execute(f"PRAGMA index_list({table})"):
        # Only SQLite metadata, never caller-selected SQL identifiers.
        name = index[1].replace('"', '""')
        if (
            index[2]
            and not index[4]
            and tuple(row[2] for row in db.execute(f'PRAGMA index_info("{name}")')) == columns
        ):
            return
    raise ValueError("Activity schema unavailable")


def _price(value):
    if type(value) is not str or len(value) > 64:
        raise ValueError("Invalid activity")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid activity") from None
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid activity")
    return result


def read_activity(database, ident, cursor=None):
    fields = activity_fields({"release_id": ident, "cursor": cursor})
    cursor = fields["cursor"]
    with closing(
        sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)
    ) as db:
        budget = 0

        def bounded():
            nonlocal budget
            budget += 1
            return budget > 100

        db.set_progress_handler(bounded, 1000)
        db.execute("BEGIN")
        if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
            raise ValueError("Activity schema unavailable")
        _indexed(db, "discogs_price_outbox", ("release_id", "rule_revision", "episode"))
        _indexed(db, "discogs_price_changes", ("event_key",))
        current = db.execute(
            "SELECT revision FROM discogs_price_rules WHERE release_id=?", (ident,)
        ).fetchone()
        if current is None:
            raise ValueError("Activity unavailable")
        params = (ident,) if cursor is None else (ident, cursor["revision"], cursor["episode"])
        records = db.execute(
            ACTIVITY_QUERY if cursor is None else ACTIVITY_NEXT_QUERY, params
        ).fetchall()
        rows = []
        for (
            revision,
            episode,
            observed_at,
            state,
            changed,
            old_price,
            old_count,
            count,
            price,
        ) in records[:20]:
            kind = "availability"
            if changed:
                if old_price is None and old_count is None:
                    raise ValueError("Invalid activity")
                if type(count) is not int or not 0 <= count <= 10000000:
                    raise ValueError("Invalid activity")
                if old_price is not None and _price(price) >= _price(old_price):
                    raise ValueError("Invalid activity")
                if old_count is not None and (
                    type(old_count) is not int or not 0 <= old_count < count
                ):
                    raise ValueError("Invalid activity")
                kind = (
                    "price_drop_and_count_increase"
                    if old_price is not None and old_count is not None
                    else "price_drop"
                    if old_price is not None
                    else "count_increase"
                )
            rows.append(
                {
                    "revision": revision,
                    "episode": episode,
                    "observed_at": observed_at,
                    "state": state,
                    "kind": kind,
                    "settings_scope": "current" if revision == current[0] else "earlier",
                }
            )
        more = len(records) > 20
        return project_activity(
            {
                "release_id": ident,
                "current_revision": current[0],
                "rows": rows,
                "has_more": more,
                "next_cursor": {"revision": rows[-1]["revision"], "episode": rows[-1]["episode"]}
                if more
                else None,
            },
            ident,
            cursor,
        )
