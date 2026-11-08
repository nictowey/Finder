"""Opt-in aggregate item-price leads. No listing discovery or purchase authority."""

import hashlib
import json
import math
import time
import uuid
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from finder.discogs_aggregate_signals import (
    SCHEMA as CHANGE_SCHEMA,
)
from finder.discogs_aggregate_signals import (
    change_details,
    change_message,
    change_supported,
    observe_changes,
    pause_changes,
)
from finder.discogs_listing import ListingReadError, ReadBudgetBusy, ReleaseStatsReader
from finder.discogs_notification_outbox import (
    BASE_BACKOFF,
    LEASE_SECONDS,
    MAX_ATTEMPTS,
    MAX_BACKOFF,
    NotificationBinding,
)
from finder.discogs_price_messages import availability_message as availability_message
from finder.discogs_price_messages import lead_message as lead_message
from finder.discogs_read_guard import DatabaseReadBudget, guard_pending, initialize_guard
from finder.discogs_rules import CURRENCIES, RulesStore, StrictInput, amount, release_id

POLL_SECONDS = 600
MIN_POLL_SECONDS = 120
MAX_POLL_SECONDS = 86400
FRESH_SECONDS = 1200
DAILY_SEND_CAP = 200  # Below the reviewed anonymous free allowance; no paid fallback.


class PriceLeadRule(StrictInput):
    """Separate opt-in prefilter, never a replacement for grade/all-in buying rules."""

    release_id: int
    public_title: str = Field(min_length=1, max_length=120)
    currency: str
    item_price_ceiling: str | None = None
    any_price: bool = False
    enabled: bool = False
    alert_initial_low: bool = True
    alert_price_drops: bool = False
    alert_count_increases: bool = False

    _release = field_validator("release_id", mode="before")(release_id)

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

    @field_validator("item_price_ceiling", mode="before")
    @classmethod
    def positive(cls, value):
        if value is None:
            return None
        value = amount(value)
        if Decimal(value) <= 0:
            raise ValueError("Use a positive item-price prefilter.")
        return value

    @model_validator(mode="after")
    def price_mode(self):
        if self.any_price == (self.item_price_ceiling is not None):
            raise ValueError("Choose either any_price:true or a positive item-price ceiling.")
        return self


def _rule_json(rule):
    # A fully disabled rule stays readable by the preceding release.
    return rule.model_dump_json(
        exclude={
            field
            for field in ("alert_price_drops", "alert_count_increases")
            if not getattr(rule, field)
        }
    )


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
    if rule.any_price:
        # Availability depends only on a valid positive aggregate count. The empty
        # price marker fits existing NOT NULL outboxes without inventing a ceiling
        # or monetary value; availability messages never display a price.
        if rule.alert_price_drops:
            try:
                return True, amount(price), count
            except ValueError:
                pass  # Unknown price never erases a valid availability/count observation.
        return True, "", count
    try:
        price = amount(price)
    except ValueError:
        return None
    return Decimal(price) <= Decimal(rule.item_price_ceiling), price, count


def rule_message(rule, price, details=None):
    if details is not None:
        previous_price, previous_count, count = details
        return change_message(
            rule.public_title,
            rule.release_id,
            rule.currency,
            price,
            previous_price,
            count,
            previous_count,
            item_price_ceiling=rule.item_price_ceiling,
            any_price=rule.any_price,
        )
    if rule.any_price:
        return availability_message(
            rule.public_title, rule.release_id, currency=rule.currency, price=price
        )
    return lead_message(
        rule.public_title,
        rule.release_id,
        rule.currency,
        price,
        item_price_ceiling=rule.item_price_ceiling,
    )


class AggregatePriceAlerts:
    """Atomic episode/outbox bridge using the existing tagged rules workspace.

    Own default-off control; never releases buying/browser stops or edits targets.
    Topic values are never accepted here. Claims bind an opaque destination once.
    """

    def __init__(self, database, *, clock=time.time, poll_seconds=POLL_SECONDS):
        if (
            type(poll_seconds) is not int
            or not MIN_POLL_SECONDS <= poll_seconds <= MAX_POLL_SECONDS
        ):
            raise ValueError("Poll interval must be an integer from 120 to 86400 seconds.")
        self.poll_seconds = poll_seconds
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
                store.db.executescript(CHANGE_SCHEMA)
                initialize_guard(store.db)
        finally:
            store.close()

    def save_rule(self, rule, *, expected_revision):
        rule = PriceLeadRule.model_validate(
            rule.model_dump() if isinstance(rule, PriceLeadRule) else rule
        )
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
                    (rule.release_id, revision, _rule_json(rule)),
                )
                # Old claimed delivery might already have reached the provider.
                store.db.execute(
                    "UPDATE discogs_price_outbox SET state=CASE WHEN state='leased' "
                    "THEN 'uncertain' ELSE 'revoked' END WHERE release_id=? "
                    "AND state IN ('leased','pending','retry_wait')",
                    (rule.release_id,),
                )
                store.db.execute(
                    "DELETE FROM discogs_price_extrema WHERE release_id=?", (rule.release_id,)
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

    def configure_change_alerts(
        self, release, *, alert_price_drops, alert_count_increases, expected_revision
    ):
        """Opt in without resetting entry state or generating an initial-alert flood.

        This narrow revision-checked update preserves prices, thresholds, sample
        history and polling/backoff. A fresh observation silently establishes the
        new comparison baseline. Unsent old-revision events are revoked; claimed
        sends remain uncertain. Reapplying identical settings is a no-op.
        """
        release = release_id(release)
        if (
            type(alert_price_drops) is not bool
            or type(alert_count_increases) is not bool
            or type(expected_revision) is not int
        ):
            raise ValueError("Use explicit change-alert flags and a current rule revision.")
        store = RulesStore(self.database)
        try:
            with store.db:
                db = store.db
                db.execute("BEGIN IMMEDIATE")
                saved = db.execute(
                    "SELECT revision,data FROM discogs_price_rules WHERE release_id=?", (release,)
                ).fetchone()
                if not saved or saved[0] != expected_revision:
                    raise RuntimeError("Alert rule changed; reload before saving.")
                rule = PriceLeadRule.model_validate_json(saved[1])
                if (rule.alert_price_drops, rule.alert_count_increases) == (
                    alert_price_drops,
                    alert_count_increases,
                ):
                    return expected_revision
                rule = rule.model_copy(
                    update={
                        "alert_price_drops": alert_price_drops,
                        "alert_count_increases": alert_count_increases,
                    }
                )
                revision = expected_revision + 1
                db.execute(
                    "UPDATE discogs_price_rules SET revision=?,data=? WHERE release_id=?",
                    (revision, _rule_json(rule), release),
                )
                db.execute(
                    "UPDATE discogs_price_outbox SET state=CASE WHEN state='leased' "
                    "THEN 'uncertain' ELSE 'revoked' END WHERE release_id=? "
                    "AND state IN ('pending','retry_wait','leased')",
                    (release,),
                )
                db.execute(
                    "UPDATE discogs_price_samples SET claim=NULL WHERE release_id=?", (release,)
                )
                db.execute(
                    "UPDATE discogs_price_extrema SET continuous=0 WHERE release_id=?", (release,)
                )
                return revision
        finally:
            store.close()

    def prepare_legacy_rollback(self, *, expected_control_revision):
        """Prepare stopped alerts for the pre-change binary without replaying history.

        Stop the worker and alert control first. Do not restore an old database
        snapshot: accepted and uncertain sends must survive a code rollback.
        """
        if type(expected_control_revision) is not int:
            raise ValueError("Use a current stopped alert-control revision.")
        store = RulesStore(self.database)
        try:
            with store.db:
                db = store.db
                db.execute("BEGIN IMMEDIATE")
                if self._control(db) != (0, expected_control_revision):
                    raise RuntimeError(
                        "Stop alert control and reload its revision before rollback."
                    )
                changed = 0
                for release, revision, raw in db.execute(
                    "SELECT release_id,revision,data FROM discogs_price_rules"
                ).fetchall():
                    PriceLeadRule.model_validate_json(raw)
                    data = json.loads(raw)
                    if not {"alert_price_drops", "alert_count_increases"}.intersection(data):
                        continue
                    data.pop("alert_price_drops", None)
                    data.pop("alert_count_increases", None)
                    db.execute(
                        "UPDATE discogs_price_rules SET revision=?,data=? WHERE release_id=?",
                        (revision + 1, json.dumps(data, separators=(",", ":")), release),
                    )
                    db.execute(
                        "UPDATE discogs_price_outbox SET state=CASE WHEN state='leased' "
                        "THEN 'uncertain' ELSE 'revoked' END WHERE release_id=? "
                        "AND state IN ('pending','retry_wait','leased')",
                        (release,),
                    )
                    changed += 1
                # Defense in depth: no remaining change event may be interpreted
                # as an ordinary entry message by the earlier binary.
                db.execute(
                    "UPDATE discogs_price_outbox SET state=CASE WHEN state='leased' "
                    "THEN 'uncertain' ELSE 'revoked' END WHERE event_key IN "
                    "(SELECT event_key FROM discogs_price_changes) "
                    "AND state IN ('pending','retry_wait','leased')"
                )
                db.execute("UPDATE discogs_price_extrema SET continuous=0")
                return {"rules_updated": changed, "enabled": False, "history_preserved": True}
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
                    pause_changes(store.db)
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
                    "SELECT episode,low,seen,last_observed,claim,status,last_success,price "
                    "FROM discogs_price_samples "
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
                    db.execute("DELETE FROM discogs_price_extrema WHERE release_id=?", (release,))
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
                    episode = observe_changes(
                        db,
                        rule,
                        revision,
                        episode,
                        was_low=bool(sample[1]),
                        continuous=(
                            sample[5] == "ok"
                            and sample[6] is not None
                            and 0 <= observed_at - sample[6] < FRESH_SECONDS
                        ),
                        previous_price=sample[7],
                        price=price,
                        count=count,
                        observed_at=observed_at,
                        now=now,
                    )
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
                if rule.any_price:
                    return "available" if low else "unavailable"
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
                if guard_pending(db, now):
                    return {"status": "cooldown", "requests": 0}
                if (
                    db.execute(
                        "SELECT next_request FROM discogs_monitor_control WHERE id=1"
                    ).fetchone()[0]
                    > now
                ):
                    return {"status": "cooldown", "requests": 0}
                rows = db.execute(
                    "SELECT r.release_id,r.revision,r.data,s.failures,s.next_check "
                    "FROM discogs_price_rules r "
                    "JOIN discogs_price_samples s USING(release_id) WHERE s.next_check<=? "
                    "AND (s.claim IS NULL OR s.claim_until<=?) ORDER BY s.next_check,r.release_id",
                    (now, now),
                ).fetchall()
                chosen = next(
                    (r for r in rows if PriceLeadRule.model_validate_json(r[2]).enabled), None
                )
                if chosen is None:
                    return {"status": "idle", "requests": 0}
                release, revision, data, failures, previous_due = chosen
                rule, token = PriceLeadRule.model_validate_json(data), uuid.uuid4().hex
                db.execute(
                    "UPDATE discogs_price_samples SET claim=?,claim_until=?,next_check=? "
                    "WHERE release_id=?",
                    (token, now + 30, now + self.poll_seconds, release),
                )
                if not isinstance(reader, ReleaseStatsReader):
                    db.execute("UPDATE discogs_monitor_control SET next_request=?", (now + 3,))
            try:
                value = reader.read(
                    release,
                    rule.currency,
                    **(
                        {"budget": DatabaseReadBudget(db, clock=self.clock)}
                        if isinstance(reader, ReleaseStatsReader)
                        else {}
                    ),
                )
            except ReadBudgetBusy:
                with db:
                    db.execute(
                        "UPDATE discogs_price_samples SET claim=NULL,claim_until=NULL,next_check=? "
                        "WHERE release_id=? AND claim=?",
                        (previous_due, release, token),
                    )
                return {"status": "cooldown", "requests": 0}
            except ListingReadError as error:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    delay = max(
                        self.poll_seconds,
                        error.retry_after,
                        min(MAX_BACKOFF, 60 * 2 ** min(failures, 10)),
                    )
                    db.execute(
                        "UPDATE discogs_price_samples SET status='error',failures=failures+1,"
                        "claim=NULL,next_check=? WHERE release_id=? AND claim=?",
                        (self.clock() + delay, release, token),
                    )
                    if error.access_restricted:
                        pause_changes(db)
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
                            (self.clock() + error.retry_after,),
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
                    "o.attempts,r.revision,r.data,s.episode,s.low,s.last_success,s.status,"
                    "s.price,s.count "
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
                    details = change_details(db, row[0])
                    if (
                        not rule.enabled
                        or row[2] != row[7]
                        or row[3] != row[9]
                        or not row[10]
                        or row[12] != "ok"
                        or row[4] != row[13]
                        or not 0 <= now - row[11] < FRESH_SECONDS
                        or not change_supported(rule, row[13], row[14], details)
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
                        "payload": rule_message(rule, row[4], details),
                    }
                return None
        finally:
            store.close()

    def finish(
        self, reservation, outcome, *, retry_after=0, manual_attention=False, provider_deadline=None
    ):
        """Unknown delivery is never retried. Known rejection alone can back off."""
        if outcome not in {"accepted", "rejected", "failed", "uncertain"}:
            raise ValueError("Use a fixed delivery outcome.")
        if (
            type(manual_attention) is not bool
            or type(retry_after) not in (int, float)
            or not math.isfinite(retry_after)
            or retry_after < 0
        ):
            raise ValueError("Use a valid provider cooldown.")
        if provider_deadline is not None and (
            type(provider_deadline) not in (int, float)
            or not math.isfinite(provider_deadline)
            or provider_deadline < 0
        ):
            raise ValueError("Use a valid provider deadline.")
        store = RulesStore(self.database)
        try:
            with store.db:
                db, now = store.db, self.clock()
                db.execute("BEGIN IMMEDIATE")
                self._reconcile(db, now)
                row = db.execute(
                    "SELECT state,token,attempts,lease_until FROM discogs_price_outbox "
                    "WHERE event_key=? "
                    "AND provider_id=? AND destination_ref=?",
                    (
                        reservation["event_key"],
                        reservation["provider_id"],
                        reservation["destination_ref"],
                    ),
                ).fetchone()
                if not row or row[1] != reservation["token"]:
                    raise RuntimeError("Delivery reservation changed; do not retry.")
                # An invalidated/expired lease may still receive its provider
                # result. Its matching token and retained lease identify the
                # unfinished attempt; consumed/terminal results stay idempotent.
                if row[0] != "leased" and not (row[0] == "uncertain" and row[3] is not None):
                    return {"state": row[0], "delivered": False}
                # A delayed dispatch/result must not shorten the shared provider pacing.
                db.execute(
                    "UPDATE discogs_price_control SET next_send=max(next_send,?) WHERE id=1",
                    (now + 90,),
                )
                if provider_deadline is not None:
                    db.execute(
                        "UPDATE discogs_price_control SET next_send=max(next_send,?) WHERE id=1",
                        (provider_deadline,),
                    )
                if manual_attention or retry_after:
                    db.execute(
                        "UPDATE discogs_price_control SET next_send=max(next_send,?) WHERE id=1",
                        (math.inf if manual_attention else now + retry_after,),
                    )
                next_attempt = now
                state = "failed" if manual_attention and outcome == "rejected" else outcome
                if outcome == "rejected" and not manual_attention:
                    state = "failed" if row[2] >= MAX_ATTEMPTS else "retry_wait"
                    next_attempt = now + max(
                        min(MAX_BACKOFF, BASE_BACKOFF * 2 ** (row[2] - 1)),
                        retry_after,
                    )
                if outcome == "rejected":
                    # Provider rejection is shared by this destination/worker. A
                    # newly invalidated/replaced event must not bypass its cooldown.
                    db.execute(
                        "UPDATE discogs_price_control SET next_send=max(next_send,?)",
                        (next_attempt,),
                    )
                if row[0] == "uncertain":
                    # Preserve quarantine even after known rejection. Consume
                    # this response once so duplicate finish calls cannot extend
                    # the shared cooldown using an old completed reservation.
                    db.execute(
                        "UPDATE discogs_price_outbox SET lease_until=NULL WHERE event_key=?",
                        (reservation["event_key"],),
                    )
                    return {"state": "uncertain", "delivered": False}
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
                "r.revision,r.data,s.episode,s.low,s.price,s.last_success,s.status,s.count "
                "FROM discogs_price_outbox o JOIN discogs_price_rules r USING(release_id) "
                "JOIN discogs_price_samples s USING(release_id) WHERE o.event_key=? "
                "AND o.provider_id=? AND o.destination_ref=?",
                (
                    reservation["event_key"],
                    reservation["provider_id"],
                    reservation["destination_ref"],
                ),
            ).fetchone()
            details = change_details(db, reservation["event_key"])
            return bool(
                row
                and self._control(db)[0]
                and row[0] == "leased"
                and row[1] == reservation["token"]
                and now < row[2]
                and db.execute("SELECT next_send FROM discogs_price_control WHERE id=1").fetchone()[
                    0
                ]
                <= row[2] - LEASE_SECONDS + 90
                and row[3] == row[6]
                and row[4] == row[8]
                and row[9]
                and row[5] == row[10]
                and row[12] == "ok"
                and 0 <= now - row[11] < FRESH_SECONDS
                and PriceLeadRule.model_validate_json(row[7]).enabled
                and change_supported(
                    PriceLeadRule.model_validate_json(row[7]), row[10], row[13], details
                )
                and reservation.get("payload")
                == rule_message(PriceLeadRule.model_validate_json(row[7]), row[5], details)
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
