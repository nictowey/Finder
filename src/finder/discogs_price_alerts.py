"""Opt-in aggregate item-price leads. No listing discovery or purchase authority."""

import hashlib
import math
import time
import uuid
from decimal import Decimal

from pydantic import Field, field_validator

from finder.discogs_listing import ListingReadError
from finder.discogs_notification_outbox import (
    BASE_BACKOFF,
    LEASE_SECONDS,
    MAX_ATTEMPTS,
    MAX_BACKOFF,
    NotificationBinding,
)
from finder.discogs_rules import CURRENCIES, RulesStore, StrictInput, amount, release_id

POLL_SECONDS = 600
FRESH_SECONDS = 1200
DAILY_SEND_CAP = 200  # Below the reviewed anonymous free allowance; no paid fallback.


class PriceLeadRule(StrictInput):
    """Separate opt-in prefilter, never a replacement for grade/all-in buying rules."""

    release_id: int
    public_title: str = Field(min_length=1, max_length=120)
    currency: str
    item_price_ceiling: str
    enabled: bool = False
    alert_initial_low: bool = True

    _release = field_validator("release_id", mode="before")(release_id)
    _amount = field_validator("item_price_ceiling", mode="before")(amount)

    @field_validator("public_title")
    @classmethod
    def title(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Use a public single-line record title.")
        return value.strip()

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value):
        if value not in CURRENCIES:
            raise ValueError("Unsupported currency; no conversion.")
        return value

    @field_validator("item_price_ceiling")
    @classmethod
    def positive(cls, value):
        if Decimal(value) <= 0:
            raise ValueError("Use a positive item-price prefilter.")
        return value


def _sample(rule, value):
    if (
        not isinstance(value, dict)
        or type(value.get("release_id")) is not int
        or value.get("release_id") != rule.release_id
    ):
        return None
    count, price = value.get("num_for_sale"), value.get("lowest_item_price")
    if type(count) is not int or not 0 <= count <= 10000000:
        return None
    if value.get("currency") != rule.currency:
        return None
    if count == 0:
        return (False, None, 0) if price is None else None
    try:
        price = amount(price)
    except ValueError:
        return None
    return Decimal(price) <= Decimal(rule.item_price_ceiling), price, count


def lead_message(title, release, currency, price):
    """Only public record facts; no topic, seller, destination or buying verdict."""
    return {
        "title": "Discogs low item-price lead",
        "message": (
            f"{title}\nRelease {release}: reported minimum item price {currency} {price}. "
            "Condition and delivered total are unverified. "
            "This is a manual-review lead, not a qualified purchase.\n"
            f"https://www.discogs.com/sell/release/{release}\nData provided by Discogs."
        ),
        "click": f"https://www.discogs.com/sell/release/{release}",
    }


class AggregatePriceAlerts:
    """Atomic episode/outbox bridge using the existing tagged rules workspace.

    Own default-off control; never releases buying/browser stops or edits targets.
    Topic values are never accepted here. Claims bind an opaque destination once.
    """

    def __init__(self, database, *, clock=time.time):
        self.database, self.clock = database, clock
        store = RulesStore(database)
        try:
            with store.db:
                store.db.executescript("""
                    CREATE TABLE IF NOT EXISTS discogs_price_control (
                        id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
                        revision INTEGER NOT NULL, send_day INTEGER NOT NULL,
                        sends INTEGER NOT NULL, next_send REAL NOT NULL);
                    INSERT OR IGNORE INTO discogs_price_control VALUES(1,0,0,0,0,0);
                    CREATE TABLE IF NOT EXISTS discogs_price_rules (
                        release_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL,
                        data TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS discogs_price_samples (
                        release_id INTEGER PRIMARY KEY, episode INTEGER NOT NULL DEFAULT 0,
                        low INTEGER NOT NULL DEFAULT 0, seen INTEGER NOT NULL DEFAULT 0,
                        last_observed REAL, last_success REAL, price TEXT, count INTEGER,
                        status TEXT NOT NULL DEFAULT 'waiting', next_check REAL NOT NULL DEFAULT 0,
                        failures INTEGER NOT NULL DEFAULT 0, claim TEXT, claim_until REAL);
                    CREATE TABLE IF NOT EXISTS discogs_price_outbox (
                        event_key TEXT PRIMARY KEY, release_id INTEGER NOT NULL,
                        rule_revision INTEGER NOT NULL, episode INTEGER NOT NULL,
                        price TEXT NOT NULL, observed_at REAL NOT NULL,
                        state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                        next_attempt REAL NOT NULL, provider_id TEXT, destination_ref TEXT,
                        token TEXT, lease_until REAL, last_error TEXT,
                        UNIQUE(release_id,rule_revision,episode));
                    CREATE TABLE IF NOT EXISTS discogs_monitor_control (
                        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                        next_request REAL NOT NULL);
                    INSERT OR IGNORE INTO discogs_monitor_control VALUES(1,0,0);
                """)
        finally:
            store.close()

    def save_rule(self, rule, *, expected_revision):
        rule = PriceLeadRule.model_validate(rule)
        if type(expected_revision) is not int:
            raise ValueError("Use a current rule revision.")
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                old = store.db.execute(
                    "SELECT revision FROM discogs_price_rules WHERE release_id=?",
                    (rule.release_id,),
                ).fetchone()
                if (old[0] if old else 0) != expected_revision:
                    raise RuntimeError("Alert rule changed; reload before saving.")
                revision = expected_revision + 1
                store.db.execute(
                    "INSERT INTO discogs_price_rules VALUES(?,?,?) ON CONFLICT(release_id) "
                    "DO UPDATE SET revision=excluded.revision,data=excluded.data",
                    (rule.release_id, revision, rule.model_dump_json()),
                )
                # Old claimed delivery might already have reached the provider.
                store.db.execute(
                    "UPDATE discogs_price_outbox SET state=CASE WHEN state='leased' "
                    "THEN 'uncertain' ELSE 'revoked' END WHERE release_id=? "
                    "AND state IN ('leased','pending','retry_wait')",
                    (rule.release_id,),
                )
                store.db.execute(
                    "DELETE FROM discogs_price_samples WHERE release_id=?", (rule.release_id,)
                )
                store.db.execute(
                    "INSERT INTO discogs_price_samples(release_id) VALUES(?)", (rule.release_id,)
                )
                return revision
        finally:
            store.close()

    def set_enabled(self, enabled, *, expected_revision):
        if type(enabled) is not bool or type(expected_revision) is not int:
            raise ValueError("Use an explicit alert control and revision.")
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                changed = store.db.execute(
                    "UPDATE discogs_price_control SET enabled=?,revision=revision+1 "
                    "WHERE id=1 AND revision=?",
                    (enabled, expected_revision),
                ).rowcount
                if not changed:
                    raise RuntimeError("Alert control changed.")
                if not enabled:
                    store.db.execute(
                        "UPDATE discogs_price_outbox SET state='uncertain' WHERE state='leased'"
                    )
                return expected_revision + 1
        finally:
            store.close()

    @staticmethod
    def _control(db):
        return db.execute(
            "SELECT enabled,revision FROM discogs_price_control WHERE id=1"
        ).fetchone()

    @staticmethod
    def _reconcile(db, now):
        db.execute(
            "UPDATE discogs_price_outbox SET state='uncertain',last_error='lease_expired' "
            "WHERE state='leased' AND lease_until<=?",
            (now,),
        )
        db.execute(
            "UPDATE discogs_price_outbox SET state='expired' "
            "WHERE state IN ('pending','retry_wait') AND observed_at<=?",
            (now - FRESH_SECONDS,),
        )

    def observe(self, release, revision, value, *, observed_at, claim=None, control=None):
        """Bridge a successful current API read; unknown data never rearms an episode."""
        now = self.clock()
        if (
            not isinstance(observed_at, (int, float))
            or isinstance(observed_at, bool)
            or not math.isfinite(observed_at)
        ):
            raise ValueError("A genuine observation time is required.")
        if not 0 <= now - observed_at < FRESH_SECONDS:
            return "stale"
        store = RulesStore(self.database)
        try:
            with store.db:
                db = store.db
                db.execute("BEGIN IMMEDIATE")
                self._reconcile(db, now)
                row = db.execute(
                    "SELECT revision,data FROM discogs_price_rules WHERE release_id=?", (release,)
                ).fetchone()
                sample = db.execute(
                    "SELECT episode,low,seen,last_observed,claim FROM discogs_price_samples "
                    "WHERE release_id=?",
                    (release,),
                ).fetchone()
                current = self._control(db)
                if (
                    not row
                    or row[0] != revision
                    or not sample
                    or not current[0]
                    or control is not None
                    and current != control
                ):
                    return "discarded"
                rule = PriceLeadRule.model_validate_json(row[1])
                if not rule.enabled or claim is not None and sample[4] != claim:
                    return "discarded"
                if sample[3] is not None and observed_at <= sample[3]:
                    return "stale"
                normalized = _sample(rule, value)
                if normalized is None:
                    db.execute(
                        "UPDATE discogs_price_samples SET status='unknown',"
                        "last_observed=?,claim=NULL "
                        "WHERE release_id=?",
                        (observed_at, release),
                    )
                    return "unknown"
                low, price, count = normalized
                episode = sample[0] + int(low and not sample[1])
                if not low:
                    db.execute(
                        "UPDATE discogs_price_outbox SET state=CASE WHEN state='leased' "
                        "THEN 'uncertain' ELSE 'revoked' END WHERE release_id=? "
                        "AND state IN ('pending','retry_wait','leased')",
                        (release,),
                    )
                elif not sample[1] and (sample[2] or rule.alert_initial_low):
                    key = hashlib.sha256(
                        f"aggregate-price-v1:{release}:{revision}:{episode}".encode()
                    ).hexdigest()
                    db.execute(
                        "INSERT OR IGNORE INTO discogs_price_outbox(event_key,release_id,"
                        "rule_revision,"
                        "episode,price,observed_at,next_attempt) VALUES(?,?,?,?,?,?,?)",
                        (key, release, revision, episode, price, observed_at, now),
                    )
                if low:
                    # Queued messages describe the latest minimum, not an old lower price.
                    db.execute(
                        "UPDATE discogs_price_outbox SET price=?,observed_at=? WHERE release_id=? "
                        "AND rule_revision=? AND episode=? AND state IN ('pending','retry_wait')",
                        (price, observed_at, release, revision, episode),
                    )
                db.execute(
                    "UPDATE discogs_price_samples SET episode=?,low=?,seen=1,last_observed=?,"
                    "last_success=?,price=?,count=?,status='ok',failures=0,claim=NULL "
                    "WHERE release_id=?",
                    (episode, low, observed_at, observed_at, price, count, release),
                )
                return "low" if low else "outside"
        finally:
            store.close()

    def poll_once(self, reader=None):
        """One official API read, no listing/browser path and no same-tick retry."""
        if reader is None:
            return {"status": "disabled", "requests": 0}
        store = RulesStore(self.database)
        try:
            db, now = store.db, self.clock()
            with db:
                db.execute("BEGIN IMMEDIATE")
                control = self._control(db)
                if not control[0]:
                    return {"status": "stopped", "requests": 0}
                if (
                    db.execute(
                        "SELECT next_request FROM discogs_monitor_control WHERE id=1"
                    ).fetchone()[0]
                    > now
                ):
                    return {"status": "cooldown", "requests": 0}
                rows = db.execute(
                    "SELECT r.release_id,r.revision,r.data,s.failures FROM discogs_price_rules r "
                    "JOIN discogs_price_samples s USING(release_id) WHERE s.next_check<=? "
                    "AND (s.claim IS NULL OR s.claim_until<=?) ORDER BY s.next_check,r.release_id",
                    (now, now),
                ).fetchall()
                chosen = next(
                    (r for r in rows if PriceLeadRule.model_validate_json(r[2]).enabled), None
                )
                if chosen is None:
                    return {"status": "idle", "requests": 0}
                release, revision, data, failures = chosen
                rule, token = PriceLeadRule.model_validate_json(data), uuid.uuid4().hex
                db.execute(
                    "UPDATE discogs_price_samples SET claim=?,claim_until=?,next_check=? "
                    "WHERE release_id=?",
                    (token, now + 30, now + POLL_SECONDS, release),
                )
                db.execute("UPDATE discogs_monitor_control SET next_request=?", (now + 3,))
            try:
                value = reader.read(release, rule.currency)
            except ListingReadError as error:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    delay = max(
                        POLL_SECONDS,
                        min(MAX_BACKOFF, error.retry_after),
                        min(MAX_BACKOFF, 60 * 2 ** min(failures, 10)),
                    )
                    db.execute(
                        "UPDATE discogs_price_samples SET status='error',failures=failures+1,"
                        "claim=NULL,next_check=? WHERE release_id=? AND claim=?",
                        (self.clock() + delay, release, token),
                    )
                    if error.access_restricted:
                        db.execute(
                            "UPDATE discogs_price_control SET enabled=0,revision=revision+1 "
                            "WHERE enabled=1"
                        )
                        db.execute(
                            "UPDATE discogs_price_outbox SET state='uncertain' WHERE state='leased'"
                        )
                    if error.rate_limited:
                        db.execute(
                            "UPDATE discogs_monitor_control SET next_request=max(next_request,?)",
                            (self.clock() + min(MAX_BACKOFF, error.retry_after),),
                        )
                return {"status": "unavailable", "requests": 1}
            return {
                "status": self.observe(
                    release, revision, value, observed_at=self.clock(), claim=token, control=control
                ),
                "requests": 1,
            }
        finally:
            store.close()

    def claim(self, binding):
        if not isinstance(binding, NotificationBinding) or binding.provider_id != "ntfy":
            raise ValueError("Use the verified opaque ntfy binding.")
        store = RulesStore(self.database)
        try:
            with store.db:
                db, now = store.db, self.clock()
                db.execute("BEGIN IMMEDIATE")
                self._reconcile(db, now)
                if not self._control(db)[0]:
                    return None
                day, used, next_send = db.execute(
                    "SELECT send_day,sends,next_send FROM discogs_price_control WHERE id=1"
                ).fetchone()
                if next_send > now or day == int(now // 86400) and used >= DAILY_SEND_CAP:
                    return None
                rows = db.execute(
                    "SELECT o.event_key,o.release_id,o.rule_revision,o.episode,o.price,"
                    "o.observed_at,"
                    "o.attempts,r.revision,r.data,s.episode,s.low,s.last_success,s.status,s.price "
                    "FROM discogs_price_outbox o JOIN discogs_price_rules r USING(release_id) "
                    "JOIN discogs_price_samples s USING(release_id) "
                    "WHERE o.state IN ('pending','retry_wait') AND o.next_attempt<=? "
                    "AND o.attempts<? AND (o.destination_ref IS NULL OR "
                    "(o.destination_ref=? AND o.provider_id=?)) "
                    "ORDER BY o.next_attempt,o.event_key",
                    (now, MAX_ATTEMPTS, binding.destination_ref, binding.provider_id),
                ).fetchall()
                for row in rows:
                    rule = PriceLeadRule.model_validate_json(row[8])
                    if (
                        not rule.enabled
                        or row[2] != row[7]
                        or row[3] != row[9]
                        or not row[10]
                        or row[12] != "ok"
                        or row[4] != row[13]
                        or not 0 <= now - row[11] < FRESH_SECONDS
                    ):
                        continue
                    token = uuid.uuid4().hex
                    db.execute(
                        "UPDATE discogs_price_outbox SET state='leased',"
                        "attempts=attempts+1,token=?,"
                        "lease_until=?,provider_id=?,destination_ref=? WHERE event_key=?",
                        (
                            token,
                            now + LEASE_SECONDS,
                            binding.provider_id,
                            binding.destination_ref,
                            row[0],
                        ),
                    )
                    db.execute(
                        "UPDATE discogs_price_control SET send_day=?,sends=?,next_send=?",
                        (int(now // 86400), used + 1 if day == int(now // 86400) else 1, now + 90),
                    )
                    return {
                        "event_key": row[0],
                        "token": token,
                        "provider_id": binding.provider_id,
                        "destination_ref": binding.destination_ref,
                        "payload": lead_message(rule.public_title, row[1], rule.currency, row[4]),
                    }
                return None
        finally:
            store.close()

    def finish(self, reservation, outcome, *, retry_after=0):
        """Unknown delivery is never retried. Known rejection alone can back off."""
        if outcome not in {"accepted", "rejected", "failed", "uncertain"}:
            raise ValueError("Use a fixed delivery outcome.")
        store = RulesStore(self.database)
        try:
            with store.db:
                db, now = store.db, self.clock()
                db.execute("BEGIN IMMEDIATE")
                self._reconcile(db, now)
                row = db.execute(
                    "SELECT state,token,attempts FROM discogs_price_outbox WHERE event_key=? "
                    "AND provider_id=? AND destination_ref=?",
                    (
                        reservation["event_key"],
                        reservation["provider_id"],
                        reservation["destination_ref"],
                    ),
                ).fetchone()
                if not row or row[1] != reservation["token"]:
                    raise RuntimeError("Delivery reservation changed; do not retry.")
                if row[0] != "leased":
                    return {"state": row[0], "delivered": False}
                next_attempt = now
                state = outcome
                if outcome == "rejected":
                    state = "failed" if row[2] >= MAX_ATTEMPTS else "retry_wait"
                    next_attempt = now + max(
                        min(MAX_BACKOFF, BASE_BACKOFF * 2 ** (row[2] - 1)),
                        min(MAX_BACKOFF, max(0, retry_after)),
                    )
                db.execute(
                    "UPDATE discogs_price_outbox SET state=?,next_attempt=?,lease_until=NULL,"
                    "last_error=? WHERE event_key=?",
                    (
                        state,
                        next_attempt,
                        None if state == "accepted" else outcome,
                        reservation["event_key"],
                    ),
                )
                return {"state": state, "delivered": False}
        finally:
            store.close()

    def delivery_current(self, reservation):
        """Revalidate queued rules/price/control immediately before a provider call."""
        store = RulesStore(self.database)
        try:
            now, db = self.clock(), store.db
            self._reconcile(db, now)
            row = db.execute(
                "SELECT o.state,o.token,o.lease_until,o.rule_revision,o.episode,o.price,"
                "r.revision,r.data,s.episode,s.low,s.price,s.last_success,s.status "
                "FROM discogs_price_outbox o JOIN discogs_price_rules r USING(release_id) "
                "JOIN discogs_price_samples s USING(release_id) WHERE o.event_key=? "
                "AND o.provider_id=? AND o.destination_ref=?",
                (
                    reservation["event_key"],
                    reservation["provider_id"],
                    reservation["destination_ref"],
                ),
            ).fetchone()
            return bool(
                row
                and self._control(db)[0]
                and row[0] == "leased"
                and row[1] == reservation["token"]
                and now < row[2]
                and row[3] == row[6]
                and row[4] == row[8]
                and row[9]
                and row[5] == row[10]
                and row[12] == "ok"
                and 0 <= now - row[11] < FRESH_SECONDS
                and PriceLeadRule.model_validate_json(row[7]).enabled
                and reservation.get("payload")
                == lead_message(
                    PriceLeadRule.model_validate_json(row[7]).public_title,
                    PriceLeadRule.model_validate_json(row[7]).release_id,
                    PriceLeadRule.model_validate_json(row[7]).currency,
                    row[5],
                )
            )
        finally:
            store.close()

    def status(self):
        store = RulesStore(self.database)
        try:
            with store.db:
                self._reconcile(store.db, self.clock())
                control = self._control(store.db)
                return {
                    "enabled": bool(control[0]),
                    "revision": control[1],
                    "states": dict(
                        store.db.execute(
                            "SELECT state,count(*) FROM discogs_price_outbox GROUP BY state"
                        )
                    ),
                    "purchase_allowed": False,
                    "provider_acceptance_is_delivery": False,
                }
        finally:
            store.close()
