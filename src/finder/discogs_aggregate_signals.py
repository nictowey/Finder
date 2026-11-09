"""Opt-in aggregate changes, never listing discovery or buying qualification.

The caller owns the SQLite transaction and existing alert-control/revision gates.
Extrema survive errors and restarts. An event sequence is not a listing identity.
"""

import hashlib
from decimal import Decimal

from finder.discogs_price_messages import change_message as change_message

SCHEMA = """
    CREATE TABLE IF NOT EXISTS discogs_price_extrema (
        release_id INTEGER PRIMARY KEY, price_floor TEXT, count_high INTEGER NOT NULL,
        continuous INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS discogs_price_changes (
        event_key TEXT PRIMARY KEY, previous_price TEXT, previous_count INTEGER,
        count INTEGER NOT NULL);
"""


def pause_changes(db):
    """No queued change survives a paused comparison interval for later dispatch."""
    db.execute("UPDATE discogs_price_extrema SET continuous=0")
    db.execute(
        "UPDATE discogs_price_outbox SET state='revoked' WHERE state IN ('pending','retry_wait') "
        "AND event_key IN (SELECT event_key FROM discogs_price_changes)"
    )


def change_details(db, key):
    return db.execute(
        "SELECT previous_price,previous_count,count FROM discogs_price_changes WHERE event_key=?",
        (key,),
    ).fetchone()


def change_supported(rule, price, count, details):
    """Recheck the live observation and opt-ins, including count-only notifications."""
    if details is None:
        return True  # Historical entry/availability notification.
    old_price, old_count, event_count = details
    return bool(
        (old_price is not None or old_count is not None)
        and (
            old_price is None
            or rule.alert_price_drops
            and price not in (None, "")
            and Decimal(price) < Decimal(old_price)
        )
        and (
            old_count is None
            or rule.alert_count_increases
            and type(count) is int
            and event_count == count
            and count > old_count
        )
    )


def observe_changes(
    db,
    rule,
    revision,
    episode,
    *,
    was_low,
    continuous,
    previous_price,
    price,
    count,
    observed_at,
    now,
):
    """Merge extrema and coalesce changes without resetting attempts or backoff.

    ``episode`` is the durable outbox sequence. It advances on eligibility entry
    as before, and additionally when a distinct opted-in change needs an event.
    Pending entry alerts absorb changes; leased events are immutable and absorb
    concurrent changes conservatively. Failed/uncertain events are never retried.
    """
    if not (rule.alert_price_drops or rule.alert_count_increases):
        return episode
    old = db.execute(
        "SELECT price_floor,count_high,continuous FROM discogs_price_extrema WHERE release_id=?",
        (rule.release_id,),
    ).fetchone()
    valid_price = price if price not in (None, "") else None
    if not was_low or old is None:
        db.execute(
            "INSERT OR REPLACE INTO discogs_price_extrema VALUES(?,?,?,1)",
            (rule.release_id, valid_price, count),
        )
        return episode

    floor, high, connected = old
    continuous = continuous and connected
    price_continuous = continuous and previous_price not in (None, "")
    lower = valid_price is not None and (floor is None or Decimal(valid_price) < Decimal(floor))
    higher = count > high
    old_price = floor if lower and price_continuous and rule.alert_price_drops else None
    old_count = high if higher and continuous and rule.alert_count_increases else None
    # Recovery silently incorporates its extrema without erasing earlier marks.
    db.execute(
        "UPDATE discogs_price_extrema SET price_floor=?,count_high=?,continuous=1 "
        "WHERE release_id=?",
        (valid_price if lower else floor, max(high, count), rule.release_id),
    )

    active = db.execute(
        "SELECT event_key,state FROM discogs_price_outbox WHERE release_id=? "
        "AND rule_revision=? AND episode=? AND state IN ('pending','retry_wait','leased')",
        (rule.release_id, revision, episode),
    ).fetchone()
    if active:
        key, state = active
        details = change_details(db, key)
        if details is None or state == "leased":
            return episode  # No duplicate alongside an entry or ambiguous in-flight send.
        pending_price, pending_count, _ = details
        if (
            not price_continuous
            or valid_price is None
            or (pending_price is not None and Decimal(valid_price) >= Decimal(pending_price))
        ):
            pending_price = None
        if not continuous or pending_count is not None and count <= pending_count:
            pending_count = None
        pending_price = pending_price if pending_price is not None else old_price
        pending_count = pending_count if pending_count is not None else old_count
        if pending_price is None and pending_count is None:
            db.execute("UPDATE discogs_price_outbox SET state='revoked' WHERE event_key=?", (key,))
        else:
            db.execute(
                "UPDATE discogs_price_changes SET previous_price=?,previous_count=?,count=? "
                "WHERE event_key=?",
                (pending_price, pending_count, count, key),
            )
        return episode

    if old_price is None and old_count is None:
        return episode
    episode += 1
    key = hashlib.sha256(
        f"aggregate-change-v1:{rule.release_id}:{revision}:{episode}".encode()
    ).hexdigest()
    db.execute(
        "INSERT INTO discogs_price_outbox(event_key,release_id,rule_revision,episode,price,"
        "observed_at,next_attempt) VALUES(?,?,?,?,?,?,?)",
        (key, rule.release_id, revision, episode, price, observed_at, now),
    )
    db.execute(
        "INSERT INTO discogs_price_changes VALUES(?,?,?,?)", (key, old_price, old_count, count)
    )
    return episode
