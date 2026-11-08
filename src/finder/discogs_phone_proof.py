"""Explicit one-shot diagnostic. Never called by routine publishers.

A crash intentionally leaves the shared infinity fence. There is no resume,
retry, or fence-clearing command. See docs/discogs-phone-proof.md.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from finder.discogs_notification_lock import notification_lock
from finder.discogs_ntfy import NtfySender, PrivateNtfyConfig
from finder.discogs_price_alerts import (
    DAILY_SEND_CAP,
    FRESH_SECONDS,
    AggregatePriceAlerts,
    PriceLeadRule,
    _sample,
)
from finder.discogs_price_messages import validate_price_message
from finder.discogs_rules import RulesStore, amount

TABLE = "discogs_phone_proof_attempts"
SCHEMA = f"""CREATE TABLE {TABLE} (
proof_id TEXT PRIMARY KEY, version INTEGER NOT NULL, kind TEXT NOT NULL,
state TEXT NOT NULL, token TEXT NOT NULL, payload_hash TEXT NOT NULL,
destination_ref TEXT NOT NULL, reserved_at REAL NOT NULL,
send_day INTEGER NOT NULL, sends INTEGER NOT NULL, previous_next_send REAL NOT NULL,
finished_at REAL, outcome TEXT, provider_deadline REAL,
manual_attention INTEGER, retry_after REAL, applied_release_deadline REAL, hold_reason TEXT)"""


def _now(clock):
    value = clock()
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid diagnostic clock.")
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _pin(path):
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Use the canonical absolute source database path.")
    info = path.stat()
    if not path.is_file() or info.st_nlink != 1:
        raise ValueError("Source database identity unavailable.")
    return info.st_dev, info.st_ino


@contextmanager
def _database(path, identity):
    if _pin(path) != identity:
        raise ValueError("Source database identity changed; no retry.")
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=2)
    try:
        db.execute("PRAGMA trusted_schema=OFF")
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        if mode not in {"delete", "truncate", "persist", "wal"}:
            raise ValueError("Durable SQLite journal mode required; do not repair here.")
        db.execute("PRAGMA synchronous=FULL")
        if db.execute("PRAGMA synchronous").fetchone()[0] != 2:
            raise ValueError("SQLite FULL synchronization unavailable.")
        if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
            raise ValueError("Existing aggregate database required.")
        if db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]:
            raise ValueError("Unsupported source triggers.")
        if _pin(path) != identity:
            raise ValueError("Source database identity changed; no retry.")
        yield db
    finally:
        db.close()


def _journal(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()
    if row is not None and row[0] != SCHEMA:
        raise ValueError("Unsupported diagnostic journal schema.")
    return row is not None


def _preflight(db, now):
    control = db.execute(
        "SELECT enabled,revision,send_day,sends,next_send FROM discogs_price_control WHERE id=1"
    ).fetchone()
    if not control or control[0] != 1:
        raise ValueError("Aggregate delivery must already be enabled.")
    _, revision, day, sends, deadline = control
    if any(type(v) is not int or v < 0 for v in (revision, day, sends)) or day > int(now // 86400):
        raise ValueError("Invalid shared diagnostic budget.")
    if (
        type(deadline) not in (float, int)
        or not math.isfinite(deadline)
        or deadline < 0
        or deadline > now
    ):
        raise ValueError("Shared delivery hold or cooldown blocks proof.")
    if day == int(now // 86400) and sends >= DAILY_SEND_CAP:
        raise ValueError("Shared daily limit blocks proof.")
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    health = {
        "discogs_health_control",
        "discogs_health_outbox",
        "discogs_health_incidents",
        "discogs_health_seen",
    }
    if tables & health:
        if not health <= tables:
            raise ValueError("Incomplete health schema.")
        from finder.discogs_health_watcher import HEALTH_DAILY_CAP, HealthWatcher

        old_factory = db.row_factory
        db.row_factory = sqlite3.Row
        try:
            h = HealthWatcher._control(db)
        finally:
            db.row_factory = old_factory
        if (
            h["send_day"] > int(now // 86400)
            or h["next_send"] > now
            or (h["send_day"] == int(now // 86400) and h["sends"] >= HEALTH_DAILY_CAP)
        ):
            raise ValueError("Health delivery hold or budget blocks proof.")
    for table in ("discogs_price_outbox", "discogs_health_outbox"):
        if (
            table in tables
            and db.execute(
                f"SELECT 1 FROM {table} WHERE state IN ('pending','retry_wait','leased') OR "
                "(state='uncertain' AND lease_until IS NOT NULL) LIMIT 1"
            ).fetchone()
        ):
            raise ValueError("Unfinished source attempt blocks proof; do not recover here.")
    return control


def _test_ceiling(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,7}\.[0-9]{2}", value):
        raise ValueError(
            "Test cap requires a positive decimal string with exactly two cents digits."
        )
    parsed = amount(value)
    if parsed == "0.00":
        raise ValueError("Test cap must be positive.")
    return parsed


def _fixture_rule(rule, test_item_ceiling):
    updates = {"public_title": ("TEST ONLY: " + rule.public_title)[:120], "alert_initial_low": True}
    if test_item_ceiling is not None:
        prefix = f"TEST ONLY: TEMPORARY TEST CAP {rule.currency} {test_item_ceiling}; "
        updates.update(
            public_title=(prefix + rule.public_title)[:120],
            item_price_ceiling=test_item_ceiling,
            any_price=False,
        )
    return PriceLeadRule.model_validate({**rule.model_dump(), **updates})


def _select(db, now, release_id, test_item_ceiling):
    rows = db.execute(
        "SELECT r.data,s.status,s.last_success,s.price,s.count FROM discogs_price_rules r "
        "JOIN discogs_price_samples s USING(release_id) WHERE r.release_id=?",
        (release_id,),
    ).fetchall()
    for row in rows:
        rule = PriceLeadRule.model_validate_json(row[0])
        stamp = row[2]
        if (
            not rule.enabled
            or row[1] != "ok"
            or type(stamp) not in (int, float)
            or not math.isfinite(stamp)
            or not 0 <= now - stamp < FRESH_SECONDS
        ):
            continue
        value = {
            "release_id": rule.release_id,
            "currency": rule.currency,
            "lowest_item_price": row[3] or None,
            "num_for_sale": row[4],
        }
        matched = _sample(_fixture_rule(rule, test_item_ceiling), value)
        if matched and matched[0] and matched[2] > 0:
            return rule, value, stamp, _hash(row)
    raise ValueError("No fresh eligible stored observation; no provider read permitted.")


def _still_current(db, rule, stamp, fingerprint, now):
    row = db.execute(
        "SELECT r.data,s.status,s.last_success,s.price,s.count FROM discogs_price_rules r "
        "JOIN discogs_price_samples s USING(release_id) WHERE r.release_id=?",
        (rule.release_id,),
    ).fetchone()
    if row is None or _hash(row) != fingerprint or not 0 <= now - stamp < FRESH_SECONDS:
        raise ValueError("Stored observation changed or expired; proof remains fenced if reserved.")


def run_proof(
    database,
    private_config,
    evidence_root,
    proof_id,
    *,
    release_id,
    authorized=False,
    test_item_ceiling=None,
    clock=time.time,
    transport=None,
    _checkpoint=lambda stage: None,
):
    """Default-off. Any exception after reservation requires separate reconciliation."""
    if authorized is not True:
        return {"state": "disabled", "sent": False}
    if not isinstance(proof_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", proof_id):
        raise ValueError("An explicit stable non-secret proof ID is required.")
    test_item_ceiling = _test_ceiling(test_item_ceiling)
    path = Path(database)
    identity = _pin(path)
    sender = NtfySender(
        private_config=PrivateNtfyConfig(private_config), enabled=True, transport=transport
    )
    with notification_lock(path) as acquired:
        if not acquired:
            raise ValueError("Source publisher busy; no proof attempted.")
        with _database(path, identity) as db:
            if (
                _journal(db)
                and db.execute(f"SELECT 1 FROM {TABLE} WHERE proof_id=?", (proof_id,)).fetchone()
            ):
                raise ValueError("Proof ID already reserved; never retry or replace its ID.")
            now = _now(clock)
            _preflight(db, now)
            rule, value, stamp, fingerprint = _select(db, now, release_id, test_item_ceiling)
        root = Path(evidence_root)
        if not root.is_absolute() or root.resolve() != root or not root.is_dir():
            raise ValueError("Existing canonical evidence directory required.")
        evidence = root / proof_id
        evidence.mkdir(mode=0o700)  # Existing attempt evidence is never reused.
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        fixture = evidence / "fixture.db"
        RulesStore(fixture, create=True).close()
        alerts = AggregatePriceAlerts(fixture, clock=clock)
        test_rule = _fixture_rule(rule, test_item_ceiling)
        revision = alerts.save_rule(test_rule, expected_revision=0)
        alerts.set_enabled(True, expected_revision=0)
        alerts.observe(rule.release_id, revision, value, observed_at=stamp)
        reservation = alerts.claim(sender.binding)
        if reservation is None or not alerts.delivery_current(reservation):
            raise ValueError("Candidate fixture is not eligible.")
        payload = reservation["payload"]
        validate_price_message(payload)
        if not payload["title"].startswith("TEST ONLY"):
            raise ValueError("Diagnostic label missing.")
        token, payload_hash = uuid.uuid4().hex, _hash(payload)
        _checkpoint("before_reservation")
        with _database(path, identity) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                now = _now(clock)
                control = _preflight(db, now)
                _still_current(db, rule, stamp, fingerprint, now)
                if not _journal(db):
                    db.execute(SCHEMA)
                day = int(now // 86400)
                sends = control[3] + 1 if control[2] == day else 1
                db.execute(
                    f"INSERT INTO {TABLE} VALUES(?,1,'actionable_phone_proof','attempting',"
                    "?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
                    (
                        proof_id,
                        token,
                        payload_hash,
                        sender.binding.destination_ref,
                        now,
                        day,
                        sends,
                        control[4],
                    ),
                )
                db.execute(
                    "UPDATE discogs_price_control SET send_day=?,sends=?,next_send=? WHERE id=1",
                    (day, sends, math.inf),
                )
                _checkpoint("before_reservation_commit")
                if _pin(path) != identity:
                    raise ValueError("Source database identity changed; no retry.")
                db.commit()
                _checkpoint("after_reservation_commit")
            except BaseException:
                db.rollback()
                raise
            expected = (control[0], control[1], day, sends, math.inf)
            journal_expected = (
                proof_id,
                1,
                "actionable_phone_proof",
                "attempting",
                token,
                payload_hash,
                sender.binding.destination_ref,
                now,
                day,
                sends,
                control[4],
            )
            _verify_owned(db, proof_id, expected, journal_expected)
        _checkpoint("before_http")
        with _database(path, identity) as db:
            _verify_owned(db, proof_id, expected, journal_expected)
            before_http = _now(clock)
            if before_http < now:
                raise ValueError("Clock reversal; retained diagnostic fence.")
            _still_current(db, rule, stamp, fingerprint, before_http)
        if _hash(payload) != payload_hash or not alerts.delivery_current(reservation):
            raise ValueError("Candidate changed; retained diagnostic fence.")
        if _pin(path) != identity:
            raise ValueError("Source database identity changed; no retry.")
        result = sender.send(payload)  # Exactly one invocation; never retried.
        _checkpoint("after_response")
        fixture_result = alerts.finish(
            reservation,
            result.outcome,
            retry_after=result.retry_after,
            manual_attention=result.manual_attention,
            provider_deadline=result.provider_deadline,
        )
        completed = _now(clock)
        fixture_expected = "retry_wait" if result.outcome == "rejected" else result.outcome
        fixture_ok = fixture_result["state"] == fixture_expected
        definitive = (
            fixture_ok
            and result.outcome in {"accepted", "rejected"}
            and not result.manual_attention
            and completed >= before_http
        )
        hold_reason = (
            None
            if definitive
            else "fixture_not_finalized"
            if not fixture_ok
            else "clock_reversal"
            if completed < before_http
            else "provider_manual_attention"
            if result.manual_attention
            else "provider_not_definitive"
        )
        deadline = max(
            control[4],
            completed + (900 if result.outcome == "rejected" else 90),
            result.provider_deadline or 0,
            completed + result.retry_after,
        )
        with _database(path, identity) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                _verify_owned(db, proof_id, expected, journal_expected)
                db.execute(
                    f"UPDATE {TABLE} SET state=?,finished_at=?,outcome=?,provider_deadline=?, "
                    "manual_attention=?,retry_after=?,applied_release_deadline=?,hold_reason=? "
                    "WHERE proof_id=? AND token=? AND state='attempting'",
                    (
                        result.outcome if definitive else "uncertain",
                        completed,
                        result.outcome,
                        result.provider_deadline,
                        int(result.manual_attention),
                        result.retry_after,
                        deadline if definitive else None,
                        hold_reason,
                        proof_id,
                        token,
                    ),
                )
                if definitive:
                    db.execute(
                        "UPDATE discogs_price_control SET next_send=? WHERE id=1", (deadline,)
                    )
                _checkpoint("before_finalize_commit")
                if _pin(path) != identity:
                    raise ValueError("Source database identity changed; no retry.")
                db.commit()
                _checkpoint("after_finalize_commit")
            except BaseException:
                db.rollback()
                raise
        return {
            "state": result.outcome,
            "provider_accepted": result.outcome == "accepted",
            "delivery_fenced": not definitive,
            "phone_receipt_verified": False,
        }


def _verify_owned(db, proof_id, expected, journal_expected):
    if not _journal(db):
        raise ValueError("Diagnostic ownership lost; retain fence.")
    row = db.execute(
        f"SELECT proof_id,version,kind,state,token,payload_hash,destination_ref,reserved_at,"
        f"send_day,sends,previous_next_send FROM {TABLE} WHERE proof_id=?",
        (proof_id,),
    ).fetchone()
    control = db.execute(
        "SELECT enabled,revision,send_day,sends,next_send FROM discogs_price_control WHERE id=1"
    ).fetchone()
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("discogs_price_outbox", "discogs_health_outbox"):
        if (
            table in tables
            and db.execute(
                f"SELECT 1 FROM {table} WHERE state='leased' OR "
                "(state='uncertain' AND lease_until IS NOT NULL) LIMIT 1"
            ).fetchone()
        ):
            raise ValueError("Source attempt ownership changed; retain fence.")
    if row != journal_expected or control != expected:
        raise ValueError("Diagnostic ownership changed; no retry or resumed finalization.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("database", "private-config", "evidence-root", "proof-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--release-id", required=True, type=int)
    parser.add_argument("--test-item-ceiling", help="Explicit fixture-only cap, e.g. 20.00")
    parser.add_argument("--authorize-one-test-send", action="store_true")
    args = parser.parse_args()
    try:
        result = run_proof(
            args.database,
            args.private_config,
            args.evidence_root,
            args.proof_id,
            authorized=args.authorize_one_test_send,
            release_id=args.release_id,
            test_item_ceiling=args.test_item_ceiling,
        )
        print(json.dumps(result))
    except Exception:
        # No exception strings: SQLite/configuration errors can contain private paths.
        print(
            json.dumps(
                {
                    "state": "stopped",
                    "may_be_fenced": True,
                    "instruction": (
                        "Do not rerun, change proof ID, clear holds, or retry. "
                        "Inspect retained evidence through separately reviewed reconciliation."
                    ),
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
