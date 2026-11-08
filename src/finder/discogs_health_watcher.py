"""Default-off scheduled health incidents with durable, rate-gated notifications.

The health report is read-only. This component owns only its additive tables and
atomic reservations in the existing aggregate notification budget. No fallback
can send if the main database or that budget is unavailable.
"""

import argparse
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from finder.discogs_health_messages import REASONS, health_message
from finder.discogs_notification_lock import notification_lock, recover_abandoned
from finder.discogs_notification_outbox import (
    BASE_BACKOFF,
    LEASE_SECONDS,
    MAX_ATTEMPTS,
    MAX_BACKOFF,
)
from finder.discogs_ntfy import NtfySender, PrivateNtfyConfig
from finder.discogs_price_alerts import (
    DAILY_SEND_CAP,
    FRESH_SECONDS,
    MAX_POLL_SECONDS,
    MIN_POLL_SECONDS,
)
from finder.discogs_price_health import health_report

CHECK_SECONDS = 300
DEFAULT_EXPECTED_POLL_SECONDS = 300
MAX_CHECK_GAP = 900
HEALTH_SEND_SECONDS = 900
HEALTH_DAILY_CAP = 4
EVENT_TTL = 86400
_SCHEMA = """
CREATE TABLE IF NOT EXISTS discogs_health_control (
    id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
    last_check REAL, bad INTEGER NOT NULL DEFAULT 0, good INTEGER NOT NULL DEFAULT 0,
    incident_id INTEGER, pending_delivery_problem INTEGER NOT NULL DEFAULT 0,
    send_day INTEGER NOT NULL DEFAULT 0, sends INTEGER NOT NULL DEFAULT 0,
    next_send REAL NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO discogs_health_control(id,schema_version) VALUES(1,1);
CREATE TABLE IF NOT EXISTS discogs_health_seen (event_hash TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS discogs_health_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'open', reasons TEXT NOT NULL,
    test_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS discogs_health_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER NOT NULL,
    kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL NOT NULL, token TEXT, lease_until REAL,
    destination_ref TEXT, UNIQUE(incident_id,kind));
"""


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("A finite current time is required.")
    return value


def current_reasons(report):
    """Lifetime failed/uncertain/expired totals are deliberately not current alarms."""
    if report.get("status") == "unavailable":
        return None  # No safe persistent reservation can be established.
    reasons = []
    if not report["controls"]["enabled"]:
        reasons.append("monitor_disabled")
    if not report["rules_enabled"]:
        reasons.append("no_enabled_rules")
    if any(rule["enabled"] and rule["issues"] for rule in report["rules"]):
        reasons.append("polling_problem")
    if any(
        report["configuration"][key]["status"] != "valid" for key in ("discogs_app", "destination")
    ):
        reasons.append("configuration_problem")
    queue = report["queue"]
    age = queue["oldest_waiting_age_seconds"]
    if (
        report["controls"].get("notification_manual_attention", False)
        or queue["destination_mismatch"]
        or age is not None
        and age >= CHECK_SECONDS
        or queue["waiting_total"]
        and report["controls"]["daily_cap_reached"]
    ):
        reasons.append("notification_backlog")
    return reasons


def _terminal_hashes(db, now):
    """Private stable identities, not public diagnostics or provider payloads."""
    rows = db.execute(
        "SELECT event_key FROM discogs_price_outbox "
        "WHERE state IN ('failed','uncertain','expired') "
        "OR (state='leased' AND lease_until<=?) "
        "OR (state IN ('pending','retry_wait') AND observed_at<=?)",
        (now, now - FRESH_SECONDS),
    )
    result = set()
    for row in rows:
        if not isinstance(row[0], str) or not 1 <= len(row[0]) <= 256:
            raise ValueError("Invalid terminal delivery evidence.")
        result.add(hashlib.sha256(row[0].encode()).hexdigest())
    return result


def _reasons(raw):
    value = json.loads(raw)
    if not isinstance(value, list) or any(item not in REASONS for item in value):
        raise ValueError("Invalid health evidence.")
    return sorted(set(value))


class HealthWatcher:
    def __init__(self, database, *, clock=time.time):
        self.database = Path(database)
        self.clock = clock

    @contextmanager
    def connection(self, *, write=False):
        path = self.database.absolute()
        if path.is_symlink() or path.name.startswith(".env") or not path.is_file():
            raise ValueError("Existing aggregate database required.")
        db = sqlite3.connect(
            path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=2
        )
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA trusted_schema=OFF")
            if not write:
                db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
                raise ValueError("Existing aggregate database required.")
            # No RulesStore/AggregatePriceAlerts construction or implicit migration.
            aggregate = db.execute(
                "SELECT enabled FROM discogs_price_control WHERE id=1"
            ).fetchone()
            if aggregate is None or aggregate[0] not in (0, 1):
                raise ValueError("Existing aggregate controls required.")
            yield db
        finally:
            db.close()

    @staticmethod
    def _control(db):
        row = db.execute("SELECT * FROM discogs_health_control WHERE id=1").fetchone()
        if row is None or row["schema_version"] != 1 or row["enabled"] not in (0, 1):
            raise ValueError("Initialized health watcher required.")
        for name in ("revision", "bad", "good", "send_day", "sends"):
            if type(row[name]) is not int or row[name] < 0:
                raise ValueError("Invalid health control.")
        for name in ("last_check", "next_send"):
            if row[name] is not None:
                _time(row[name])
        if row["pending_delivery_problem"] not in (0, 1):
            raise ValueError("Invalid health control.")
        return row

    def initialize(self):
        with self.connection(write=True) as db:
            db.executescript(_SCHEMA)
            self._control(db)
        return self.status()

    def set_enabled(self, enabled, *, expected_revision):
        if type(enabled) is not bool or type(expected_revision) is not int:
            raise ValueError("Explicit watcher control and revision required.")
        now = _time(self.clock())
        with self.connection(write=True) as db, db:
            db.execute("BEGIN IMMEDIATE")
            control = self._control(db)
            if control["revision"] != expected_revision:
                raise ValueError("Health watcher control changed.")
            if bool(control["enabled"]) == enabled:
                return {"status": "unchanged", "revision": expected_revision}
            if not enabled:
                db.execute(
                    "UPDATE discogs_health_outbox SET state=CASE WHEN state='leased' "
                    "THEN 'uncertain' ELSE 'cancelled' END "
                    "WHERE state IN ('pending','retry_wait','leased') AND incident_id IN "
                    "(SELECT id FROM discogs_health_incidents WHERE test_key IS NULL)"
                )
                db.execute(
                    "UPDATE discogs_health_incidents SET state='muted' "
                    "WHERE state='open' AND test_key IS NULL"
                )
            else:
                # All terminal work that predates explicit activation is history.
                db.executemany(
                    "INSERT OR IGNORE INTO discogs_health_seen VALUES(?)",
                    [(key,) for key in _terminal_hashes(db, now)],
                )
            db.execute(
                "UPDATE discogs_health_control SET enabled=?,revision=revision+1,last_check=NULL,"
                "bad=0,good=0,incident_id=NULL,pending_delivery_problem=0 WHERE id=1",
                (enabled,),
            )
        return self.status()

    @staticmethod
    def _reconcile(db, now):
        db.execute(
            "UPDATE discogs_health_outbox SET state='uncertain' "
            "WHERE state='leased' AND lease_until<=?",
            (now,),
        )
        db.execute(
            "UPDATE discogs_health_outbox SET state='expired' "
            "WHERE state IN ('pending','retry_wait') AND created_at<=?",
            (now - EVENT_TTL,),
        )

    @staticmethod
    def _queue(db, incident, kind, now):
        db.execute(
            "INSERT OR IGNORE INTO discogs_health_outbox"
            "(incident_id,kind,created_at,next_attempt) VALUES(?,?,?,?)",
            (incident, kind, now, now),
        )

    def observe(self, report):
        reasons = current_reasons(report)
        if reasons is None:
            return {"status": "evidence_unavailable", "send_allowed": False}
        now = _time(self.clock())
        if not 0 <= now - report["snapshot_at"] < CHECK_SECONDS:
            return {"status": "stale_diagnostic", "send_allowed": False}
        with self.connection(write=True) as db, db:
            db.execute("BEGIN IMMEDIATE")
            control = self._control(db)
            if not control["enabled"]:
                return {"status": "disabled", "send_allowed": False}
            last = control["last_check"]
            if last is not None and now < last:
                return {"status": "clock_reversed", "send_allowed": False}
            if last is not None and now - last < CHECK_SECONDS:
                return {"status": "check_not_due", "send_allowed": True}
            self._reconcile(db, now)
            seen = {row[0] for row in db.execute("SELECT event_hash FROM discogs_health_seen")}
            observed = _terminal_hashes(db, now)
            new = observed - seen
            db.executemany(
                "INSERT OR IGNORE INTO discogs_health_seen VALUES(?)", [(key,) for key in new]
            )
            pending = bool(new) or bool(control["pending_delivery_problem"])
            if pending:
                reasons.append("new_delivery_problem")
            timely = last is not None and now - last <= MAX_CHECK_GAP
            bad = (control["bad"] if timely else 0) + 1 if reasons else 0
            good = (control["good"] if timely else 0) + 1 if not reasons else 0
            bad, good = min(bad, 2), min(good, 2)
            incident = control["incident_id"]
            if incident is not None:
                recovery = db.execute(
                    "SELECT state FROM discogs_health_outbox "
                    "WHERE incident_id=? AND kind='recovered'",
                    (incident,),
                ).fetchone()
                if recovery is not None and recovery[0] in {
                    "accepted",
                    "failed",
                    "uncertain",
                    "expired",
                    "cancelled",
                }:
                    db.execute(
                        "UPDATE discogs_health_incidents SET state='closed' WHERE id=?", (incident,)
                    )
                    incident = None
                    bad = 1 if reasons else 0
                    good = 1 if not reasons else 0
            if incident is None and bad >= 2:
                incident = db.execute(
                    "INSERT INTO discogs_health_incidents(created_at,reasons) VALUES(?,?)",
                    (now, json.dumps(sorted(set(reasons)))),
                ).lastrowid
                self._queue(db, incident, "degraded", now)
                pending = False  # This terminal observation is now durably reported once.
            elif incident is not None:
                row = db.execute(
                    "SELECT reasons FROM discogs_health_incidents WHERE id=?", (incident,)
                ).fetchone()
                combined = sorted(set(_reasons(row[0]) + reasons))
                db.execute(
                    "UPDATE discogs_health_incidents SET reasons=? WHERE id=?",
                    (json.dumps(combined), incident),
                )
                # New terminal events join this incident, never trigger reminders.
                pending = False
                if good >= 2:
                    alert = db.execute(
                        "SELECT state FROM discogs_health_outbox "
                        "WHERE incident_id=? AND kind='degraded'",
                        (incident,),
                    ).fetchone()
                    if alert[0] == "accepted":
                        self._queue(db, incident, "recovered", now)
                    else:
                        db.execute(
                            "UPDATE discogs_health_outbox SET state=CASE "
                            "WHEN state='leased' THEN 'uncertain' ELSE 'cancelled' END "
                            "WHERE incident_id=? AND state IN ('pending','retry_wait','leased')",
                            (incident,),
                        )
                        db.execute(
                            "UPDATE discogs_health_incidents SET state='closed' WHERE id=?",
                            (incident,),
                        )
                        incident = None
            db.execute(
                "UPDATE discogs_health_control SET last_check=?,bad=?,good=?,"
                "incident_id=?,pending_delivery_problem=? WHERE id=1",
                (now, bad, good, incident, pending),
            )
        return {
            "status": "degraded" if reasons else "clear",
            "send_allowed": True,
            "current_reasons": sorted(set(reasons)),
        }

    def queue_test(self, test_key, kind):
        if not isinstance(test_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", test_key):
            raise ValueError("Use a short non-secret test label.")
        health_message(kind, ["polling_problem"] if kind == "degraded" else [], test=True)
        now = _time(self.clock())
        with self.connection(write=True) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._control(db)
            row = db.execute(
                "SELECT id FROM discogs_health_incidents WHERE test_key=?", (test_key,)
            ).fetchone()
            if row is None:
                if kind != "degraded":
                    raise ValueError("A recovery test requires an accepted degradation test.")
                incident = db.execute(
                    "INSERT INTO discogs_health_incidents(created_at,reasons,test_key) "
                    "VALUES(?,?,?)",
                    (now, '["polling_problem"]', test_key),
                ).lastrowid
            else:
                incident = row[0]
            if kind == "recovered":
                accepted = db.execute(
                    "SELECT state FROM discogs_health_outbox "
                    "WHERE incident_id=? AND kind='degraded'",
                    (incident,),
                ).fetchone()
                if accepted is None or accepted[0] != "accepted":
                    raise ValueError("A recovery test requires an accepted degradation test.")
            self._queue(db, incident, kind, now)
        return incident

    @staticmethod
    def _eligible(db, row, control, now, test_key):
        if row["test_key"] is not None:
            return test_key is not None and row["test_key"] == test_key
        return (
            test_key is None
            and control["enabled"]
            and control["incident_id"] == row["incident_id"]
            and control["last_check"] is not None
            and 0 <= now - control["last_check"] < 2 * CHECK_SECONDS
            and (
                row["kind"] == "degraded"
                and control["bad"] >= 2
                or row["kind"] == "recovered"
                and control["good"] >= 2
            )
        )

    def claim(self, sender, *, test_key=None):
        if not isinstance(sender, NtfySender) or not sender.enabled or sender._token is None:
            return None
        now = _time(self.clock())
        with self.connection(write=True) as db, db:
            db.execute("BEGIN IMMEDIATE")
            control = self._control(db)
            self._reconcile(db, now)
            if control["last_check"] is not None and now < control["last_check"]:
                return None
            shared = db.execute(
                "SELECT send_day,sends,next_send FROM discogs_price_control WHERE id=1"
            ).fetchone()
            day = int(now // 86400)
            if any(
                type(shared[key]) is not int or shared[key] < 0 for key in ("send_day", "sends")
            ):
                raise ValueError("Invalid shared notification budget.")
            if shared["next_send"] == math.inf:
                return None  # Durable manual-attention hold, not a retry deadline.
            _time(shared["next_send"])
            if shared["send_day"] > day or control["send_day"] > day:
                raise ValueError("Clock precedes the persisted notification budget.")
            if (
                shared["next_send"] > now
                or control["next_send"] > now
                or shared["send_day"] == day
                and shared["sends"] >= DAILY_SEND_CAP
                or control["send_day"] == day
                and control["sends"] >= HEALTH_DAILY_CAP
            ):
                return None
            rows = db.execute(
                "SELECT o.*,i.reasons,i.test_key FROM discogs_health_outbox o "
                "JOIN discogs_health_incidents i ON i.id=o.incident_id "
                "WHERE o.state IN ('pending','retry_wait') AND o.next_attempt<=? "
                "AND o.attempts<? AND (o.destination_ref IS NULL OR o.destination_ref=?) "
                "ORDER BY o.id",
                (now, MAX_ATTEMPTS, sender.binding.destination_ref),
            ).fetchall()
            for row in rows:
                if not self._eligible(db, row, control, now, test_key):
                    continue
                reasons = _reasons(row["reasons"]) if row["kind"] == "degraded" else []
                health_message(row["kind"], reasons, test=row["test_key"] is not None)
                token = uuid.uuid4().hex
                db.execute(
                    "UPDATE discogs_health_outbox SET state='leased',attempts=attempts+1,"
                    "token=?,lease_until=?,destination_ref=? WHERE id=?",
                    (token, now + LEASE_SECONDS, sender.binding.destination_ref, row["id"]),
                )
                db.execute(
                    "UPDATE discogs_price_control SET send_day=?,sends=?,next_send=? WHERE id=1",
                    (day, shared["sends"] + 1 if shared["send_day"] == day else 1, now + 90),
                )
                db.execute(
                    "UPDATE discogs_health_control SET send_day=?,sends=?,next_send=? WHERE id=1",
                    (
                        day,
                        control["sends"] + 1 if control["send_day"] == day else 1,
                        now + HEALTH_SEND_SECONDS,
                    ),
                )
                return {
                    "id": row["id"],
                    "token": token,
                    "destination_ref": sender.binding.destination_ref,
                    "kind": row["kind"],
                    "reasons": reasons,
                    "test": row["test_key"] is not None,
                    "test_key": test_key,
                }
        return None

    def delivery_current(self, reservation):
        now = _time(self.clock())
        with self.connection() as db:
            control = self._control(db)
            row = db.execute(
                "SELECT o.*,i.reasons,i.test_key FROM discogs_health_outbox o "
                "JOIN discogs_health_incidents i ON i.id=o.incident_id WHERE o.id=?",
                (reservation["id"],),
            ).fetchone()
            return bool(
                row
                and row["state"] == "leased"
                and row["token"] == reservation["token"]
                and row["destination_ref"] == reservation["destination_ref"]
                and row["lease_until"] > now
                and db.execute("SELECT next_send FROM discogs_price_control WHERE id=1").fetchone()[
                    0
                ]
                <= row["lease_until"] - LEASE_SECONDS + 90
                and self._eligible(db, row, control, now, reservation["test_key"])
            )

    def finish(
        self, reservation, outcome, *, retry_after=0, manual_attention=False, provider_deadline=None
    ):
        if outcome not in ("accepted", "rejected", "failed", "uncertain"):
            raise ValueError("Use a fixed delivery outcome.")
        retry_after = _time(retry_after)
        if provider_deadline is not None:
            _time(provider_deadline)
        if type(manual_attention) is not bool:
            raise ValueError("Use an explicit provider status.")
        now = _time(self.clock())
        with self.connection(write=True) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._control(db)
            self._reconcile(db, now)
            row = db.execute(
                "SELECT * FROM discogs_health_outbox WHERE id=? AND token=? AND destination_ref=?",
                (reservation["id"], reservation["token"], reservation["destination_ref"]),
            ).fetchone()
            if row is None:
                raise ValueError("Health reservation changed; do not retry.")
            if row["state"] != "leased" and not (
                row["state"] == "uncertain" and row["lease_until"] is not None
            ):
                return {"state": row["state"], "delivered": False}
            # Reserve-to-dispatch pauses must not shorten actual provider-call pacing.
            # This never shortens a late provider cooldown and consumes each result once.
            db.execute(
                "UPDATE discogs_price_control SET next_send=max(next_send,?) WHERE id=1",
                (now + 90,),
            )
            db.execute(
                "UPDATE discogs_health_control SET next_send=max(next_send,?) WHERE id=1",
                (now + HEALTH_SEND_SECONDS,),
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
            state, next_attempt = (
                ("failed" if manual_attention and outcome == "rejected" else outcome),
                now,
            )
            if outcome == "rejected" and not manual_attention:
                state = "failed" if row["attempts"] >= MAX_ATTEMPTS else "retry_wait"
                next_attempt = now + max(
                    min(MAX_BACKOFF, BASE_BACKOFF * 2 ** (row["attempts"] - 1)),
                    retry_after,
                )
                db.execute(
                    "UPDATE discogs_price_control SET next_send=max(next_send,?) WHERE id=1",
                    (next_attempt,),
                )
            if row["state"] == "uncertain":
                state = "uncertain"
            db.execute(
                "UPDATE discogs_health_outbox SET state=?,next_attempt=?,lease_until=NULL "
                "WHERE id=?",
                (state, next_attempt, row["id"]),
            )
            if row["kind"] == "recovered" and state in ("accepted", "failed", "uncertain"):
                db.execute(
                    "UPDATE discogs_health_incidents SET state='closed' WHERE id=?",
                    (row["incident_id"],),
                )
                db.execute(
                    "UPDATE discogs_health_control SET incident_id=NULL,bad=0,good=0 "
                    "WHERE incident_id=?",
                    (row["incident_id"],),
                )
        return {"state": state, "delivered": False}

    def deliver(self, sender, *, test_key=None):
        if not isinstance(sender, NtfySender) or not sender.enabled or sender._token is None:
            return {"state": "disabled", "delivered": False}
        with notification_lock(self.database) as acquired:
            if not acquired:
                return {"state": "busy", "delivered": False}
            if recover_abandoned(self.database, self.clock()):
                return {"state": "uncertain_recovery", "delivered": False}
            reservation = self.claim(sender, test_key=test_key)
            if reservation is None:
                return {"state": "idle", "delivered": False}
            if not self.delivery_current(reservation):
                return self.finish(reservation, "failed")
            result = sender.send_health(
                reservation["kind"], reservation["reasons"], test=reservation["test"]
            )
            return self.finish(
                reservation,
                result.outcome,
                retry_after=result.retry_after,
                manual_attention=result.manual_attention,
                provider_deadline=result.provider_deadline,
            )

    def recover(self):
        """Explicit non-sending recovery for a drained, coordinated cutover."""
        with notification_lock(self.database) as acquired:
            if not acquired:
                return {"status": "busy", "recovered_attempts": 0, "sent": False}
            count = recover_abandoned(self.database, _time(self.clock()))
            return {
                "status": "recovered_unknown" if count else "clear",
                "recovered_attempts": count,
                "sent": False,
            }

    def status(self):
        now = _time(self.clock())
        with self.connection() as db:
            control = self._control(db)
            states = dict(
                db.execute("SELECT state,count(*) FROM discogs_health_outbox GROUP BY state")
            )
            if any(
                state
                not in {
                    "pending",
                    "retry_wait",
                    "leased",
                    "accepted",
                    "failed",
                    "uncertain",
                    "expired",
                    "cancelled",
                }
                for state in states
            ):
                raise ValueError("Invalid health outbox.")
            return {
                "status": "enabled" if control["enabled"] else "disabled",
                "revision": control["revision"],
                "last_check_at": control["last_check"],
                "next_send_at": control["next_send"],
                "send_cooldown_seconds": max(0, control["next_send"] - now),
                "send_attempts_today": control["sends"]
                if control["send_day"] == int(now // 86400)
                else 0,
                "daily_send_cap": HEALTH_DAILY_CAP,
                "bad_checks": control["bad"],
                "good_checks": control["good"],
                "incident_open": control["incident_id"] is not None,
                "unfinished_health_attempts": db.execute(
                    "SELECT count(*) FROM discogs_health_outbox "
                    "WHERE state IN ('leased','uncertain') AND lease_until IS NOT NULL"
                ).fetchone()[0],
                "unfinished_aggregate_attempts": db.execute(
                    "SELECT count(*) FROM discogs_price_outbox "
                    "WHERE state IN ('leased','uncertain') AND lease_until IS NOT NULL"
                ).fetchone()[0],
                "outbox_states": states,
                "phone_delivery": "unverified",
                "provider_acceptance_is_delivery": False,
                "purchase_allowed": False,
            }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit private aggregate health watcher")
    parser.add_argument(
        "action", choices=("inspect", "init", "enable", "disable", "tick", "test", "recover")
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=DEFAULT_EXPECTED_POLL_SECONDS,
        help=(
            "Expected worker per-rule interval (120–86400; default 300); "
            "does not change the health check timer or debounce"
        ),
    )
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--discogs-app-file", type=Path)
    parser.add_argument("--ntfy-config-file", type=Path)
    parser.add_argument("--deliver", action="store_true")
    parser.add_argument("--test-id")
    parser.add_argument("--phase", choices=("degraded", "recovered"))
    args = parser.parse_args(argv)
    if not MIN_POLL_SECONDS <= args.poll_seconds <= MAX_POLL_SECONDS:
        parser.error("--poll-seconds must be between 120 and 86400")
    if args.deliver and (args.action not in ("tick", "test") or not args.ntfy_config_file):
        parser.error("--deliver requires tick/test and --ntfy-config-file")
    if args.action in ("enable", "disable") and args.expected_revision is None:
        parser.error("enable/disable requires --expected-revision")
    if args.action in ("tick", "inspect") and (
        not args.discogs_app_file or not args.ntfy_config_file
    ):
        parser.error("tick/inspect requires both existing private configuration files")
    if args.action == "test" and (not args.test_id or not args.phase):
        parser.error("test requires --test-id and --phase")
    if args.action != "test" and (args.test_id is not None or args.phase is not None):
        parser.error("test options require test action")
    try:
        watcher = HealthWatcher(args.database)
        if args.action == "init":
            result = watcher.initialize()
        elif args.action == "recover":
            result = watcher.recover()
        elif args.action in ("enable", "disable"):
            result = watcher.set_enabled(
                args.action == "enable", expected_revision=args.expected_revision
            )
        elif args.action == "test":
            watcher.queue_test(args.test_id, args.phase)
            result = {
                "status": "test_queued",
                "delivery": {"state": "disabled", "delivered": False},
            }
            if args.deliver:
                result["delivery"] = watcher.deliver(
                    NtfySender(
                        private_config=PrivateNtfyConfig(args.ntfy_config_file), enabled=True
                    ),
                    test_key=args.test_id,
                )
        else:
            report = health_report(
                args.database,
                poll_seconds=args.poll_seconds,
                discogs_app_file=args.discogs_app_file,
                ntfy_config_file=args.ntfy_config_file,
            )
            if args.action == "inspect":
                result = {
                    "health": report,
                    "watcher": watcher.status(),
                    "current_reasons": current_reasons(report),
                }
            else:
                result = {
                    "observation": watcher.observe(report),
                    "delivery": {"state": "disabled", "delivered": False},
                }
                if args.deliver and result["observation"]["send_allowed"]:
                    result["delivery"] = watcher.deliver(
                        NtfySender(
                            private_config=PrivateNtfyConfig(args.ntfy_config_file), enabled=True
                        )
                    )
        result.update(
            phone_delivery="unverified",
            provider_acceptance_is_delivery=False,
            purchase_allowed=False,
        )
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 2 if result.get("observation", {}).get("status") == "evidence_unavailable" else 0
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError, OverflowError, RecursionError):
        print('{"status":"health_watcher_unavailable","delivered":false,"purchase_allowed":false}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
