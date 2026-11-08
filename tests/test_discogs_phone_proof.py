"""All diagnostic tests are offline; no real secrets or provider requests."""

import json
import math
import socket
import sqlite3

import httpx
import pytest

from finder.discogs_health_watcher import HealthWatcher
from finder.discogs_notification_lock import notification_lock, recover_abandoned
from finder.discogs_ntfy import NtfySender, PrivateNtfyConfig, deliver_one
from finder.discogs_phone_proof import TABLE, run_proof
from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_rules import RulesStore


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Sockets and DNS forbidden")

    for name in ("socket", "create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, fail)


class Rig:
    def __init__(self, tmp_path):
        self.path = tmp_path / "source.db"
        RulesStore(self.path, create=True).close()
        self.now = 1800000000.0
        self.clock = lambda: self.now
        self.alerts = AggregatePriceAlerts(self.path, clock=self.clock)
        self.alerts.save_rule(
            PriceLeadRule(
                release_id=123,
                public_title="Offline record",
                currency="USD",
                item_price_ceiling="10",
                enabled=True,
            ),
            expected_revision=0,
        )
        self.alerts.set_enabled(True, expected_revision=0)
        self.alerts.observe(
            123,
            1,
            {"release_id": 123, "currency": "USD", "num_for_sale": 2, "lowest_item_price": "8.00"},
            observed_at=self.now,
        )
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE discogs_price_outbox SET state='accepted'")
        self.health = HealthWatcher(self.path, clock=self.clock)
        self.health.initialize()
        self.config = tmp_path / "private-fixture.json"
        self.config.write_text(
            json.dumps(
                {
                    "origin": "https://vps-a0c5b746.vps.ovh.us",
                    "topic": "offline_fixture_destination_only",
                    "token": "tk_" + "X" * 20,
                }
            )
        )
        self.config.chmod(0o600)
        self.evidence = tmp_path / "evidence"
        self.evidence.mkdir(mode=0o700)
        self.calls = []
        self.status = 200
        self.headers = {}
        self.handler = lambda request: httpx.Response(self.status, headers=self.headers)
        self.transport = httpx.MockTransport(self.request)

    def request(self, request):
        payload = json.loads(request.content)
        assert payload["title"].startswith("TEST ONLY: Offline record")
        assert "USD 8.00" in payload["message"]
        assert "USD 10.00" in payload["message"]
        assert request.method == "POST"
        self.calls.append(payload)
        return self.handler(request)

    def sql(self, sql, values=()):
        with sqlite3.connect(self.path) as db:
            return db.execute(sql, values).fetchall()

    def run(self, **kwargs):
        return run_proof(
            self.path,
            self.config,
            self.evidence,
            "fixed-offline-proof",
            release_id=123,
            authorized=True,
            clock=self.clock,
            transport=self.transport,
            **kwargs,
        )

    def fence(self):
        return self.sql("SELECT next_send FROM discogs_price_control")[0][0]

    def content(self):
        with sqlite3.connect(self.path) as db:
            return [
                line
                for line in db.iterdump()
                if TABLE not in line and "discogs_price_control" not in line
            ]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    monkeypatch.setattr("finder.discogs_ntfy.time.time", rig.clock)
    return rig


def test_success_exactly_once_immutable_source(rig):
    before = rig.content()
    result = rig.run()
    assert result == {
        "state": "accepted",
        "provider_accepted": True,
        "delivery_fenced": False,
        "phone_receipt_verified": False,
    }
    assert len(rig.calls) == 1
    assert rig.fence() == rig.now + 90
    assert rig.content() == before
    assert rig.sql("SELECT sends FROM discogs_price_control") == [(1,)]
    with pytest.raises(ValueError, match="already reserved"):
        rig.run()
    assert len(rig.calls) == 1


@pytest.mark.parametrize(
    "stage,sends,fenced",
    [
        ("before_reservation", 0, False),
        ("before_reservation_commit", 0, False),
        ("after_reservation_commit", 0, True),
        ("before_http", 0, True),
        ("after_response", 1, True),
        ("before_finalize_commit", 1, True),
        ("after_finalize_commit", 1, False),
    ],
)
def test_crash_boundaries_no_retry(rig, stage, sends, fenced):
    def crash(point):
        if point == stage:
            raise SystemExit("simulated process death")

    with pytest.raises(SystemExit):
        rig.run(_checkpoint=crash)
    assert len(rig.calls) == sends
    assert (rig.fence() == math.inf) is fenced
    with pytest.raises((ValueError, FileExistsError)):
        rig.run()
    assert len(rig.calls) == sends


@pytest.mark.parametrize(
    "status,header,fenced",
    [
        (200, "", False),
        (429, "1200", False),
        (503, "", True),
        (400, "", True),
        (200, "not-a-deadline", True),
        (429, "999999999999", True),
        (200, "7200", False),
    ],
)
def test_provider_outcomes_and_deadlines(rig, status, header, fenced):
    rig.status, rig.headers = status, {"Retry-After": header}
    result = rig.run()
    assert result["delivery_fenced"] is fenced
    assert (rig.fence() == math.inf) is fenced
    if not fenced:
        assert rig.fence() >= rig.now + max(900 if status == 429 else 90, int(header or 0))
    assert len(rig.calls) == 1
    with pytest.raises(ValueError):
        rig.run()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE discogs_price_control SET next_send=1800000090",
        "UPDATE discogs_health_control SET next_send=1800000090",
        "UPDATE discogs_price_control SET sends=200,send_day=20833",
        "UPDATE discogs_price_control SET send_day=999999",
        "UPDATE discogs_price_control SET sends=-1",
        "UPDATE discogs_price_control SET enabled=0",
        "UPDATE discogs_health_control SET schema_version=2",
        "UPDATE discogs_price_samples SET status='error'",
        "UPDATE discogs_price_samples SET last_success=1799990000",
        "DELETE FROM discogs_price_samples",
        "UPDATE discogs_price_samples SET price='12.00'",
        "UPDATE discogs_price_outbox SET state='leased'",
        "UPDATE discogs_price_outbox SET state='uncertain',lease_until=1800000090",
    ],
)
def test_preconditions_block_without_source_effects(rig, sql):
    rig.sql(sql)
    before = rig.content()
    with pytest.raises(ValueError):
        rig.run()
    assert not rig.calls
    assert rig.content() == before


def test_midnight_counts_once_preserves_deadline(rig):
    rig.now = (int(rig.now // 86400) + 1) * 86400 - 1
    rig.sql("UPDATE discogs_price_samples SET last_success=?", (rig.now,))
    day = int(rig.now // 86400)
    rig.sql("UPDATE discogs_price_control SET send_day=?,sends=3", (day,))

    def response(request):
        rig.now += 2
        return httpx.Response(200, headers={"Retry-After": "3600"})

    rig.handler = response
    rig.run()
    assert rig.sql("SELECT send_day,sends FROM discogs_price_control") == [(day, 4)]
    assert rig.fence() >= rig.now + 3600


def test_clock_reversal_keeps_fence(rig):
    def response(request):
        rig.now -= 1
        return httpx.Response(200)

    rig.handler = response
    assert rig.run()["delivery_fenced"]
    assert rig.fence() == math.inf


def test_ownership_change_never_clears_fence(rig):
    def changed(stage):
        if stage == "after_response":
            rig.sql("UPDATE discogs_price_control SET sends=sends+1")

    with pytest.raises(ValueError, match="ownership changed"):
        rig.run(_checkpoint=changed)
    assert rig.fence() == math.inf
    assert len(rig.calls) == 1


def test_old_publishers_and_recovery_respect_fence(rig):
    def crash(stage):
        if stage == "after_reservation_commit":
            raise SystemExit()

    with pytest.raises(SystemExit):
        rig.run(_checkpoint=crash)
    sender = NtfySender(
        private_config=PrivateNtfyConfig(rig.config), enabled=True, transport=rig.transport
    )
    with notification_lock(rig.path) as acquired:
        assert acquired
        assert recover_abandoned(rig.path, rig.now) == 0
    rig.sql("UPDATE discogs_price_outbox SET state='pending'")
    assert deliver_one(rig.alerts, sender)["state"] == "idle"
    rig.health.queue_test("offline-only", "degraded")
    assert rig.health.deliver(sender, test_key="offline-only")["state"] == "idle"
    assert rig.fence() == math.inf
    assert not rig.calls


def test_default_off_reads_nothing():
    assert run_proof("missing", "missing", "missing", "fixed", release_id=123) == {
        "state": "disabled",
        "sent": False,
    }


@pytest.mark.parametrize("stage", ["before_reservation_commit", "before_finalize_commit"])
def test_persistence_error_fails_closed(rig, stage):
    def fail(point):
        if point == stage:
            raise sqlite3.OperationalError("simulated commit persistence failure")

    with pytest.raises(sqlite3.OperationalError):
        rig.run(_checkpoint=fail)
    assert len(rig.calls) == (1 if stage == "before_finalize_commit" else 0)
    assert (rig.fence() == math.inf) is (stage == "before_finalize_commit")


def test_fixture_finish_persistence_failure_keeps_source_fenced(rig, monkeypatch):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("fixture persistence failed")

    monkeypatch.setattr(AggregatePriceAlerts, "finish", fail)
    with pytest.raises(sqlite3.OperationalError):
        rig.run()
    assert rig.fence() == math.inf
    assert len(rig.calls) == 1


@pytest.mark.parametrize("state", ["leased", "uncertain"])
def test_intervening_unfinished_source_attempt_keeps_fence(rig, state):
    def changed(stage):
        if stage == "after_response":
            rig.sql("UPDATE discogs_price_outbox SET state=?,lease_until=?", (state, rig.now + 90))

    with pytest.raises(ValueError, match="ownership changed"):
        rig.run(_checkpoint=changed)
    assert rig.fence() == math.inf


def test_changed_sample_before_http_never_sends(rig):
    def changed(stage):
        if stage == "before_http":
            rig.sql("UPDATE discogs_price_samples SET count=count+1")

    with pytest.raises(ValueError, match="observation changed"):
        rig.run(_checkpoint=changed)
    assert rig.fence() == math.inf
    assert not rig.calls


def test_unknown_journal_schema_blocks(rig):
    rig.sql(f"CREATE TABLE {TABLE}(proof_id TEXT)")
    with pytest.raises(ValueError, match="journal schema"):
        rig.run()
    assert not rig.calls


def test_canonical_path_alias_rejected(rig, tmp_path):
    alias = tmp_path / "alias.db"
    alias.symlink_to(rig.path)
    with pytest.raises(ValueError, match="canonical"):
        run_proof(alias, rig.config, rig.evidence, "fixed", release_id=123, authorized=True)
    assert not rig.calls


def test_source_inode_replacement_stops_before_http(rig):
    def changed(stage):
        if stage == "before_http":
            old = rig.path.with_suffix(".retained.db")
            rig.path.rename(old)
            rig.path.write_bytes(old.read_bytes())

    with pytest.raises(ValueError, match="identity changed"):
        rig.run(_checkpoint=changed)
    assert not rig.calls
    assert rig.fence() == math.inf


def test_continuous_lock_excludes_regular_dispatch(rig):
    sender = NtfySender(
        private_config=PrivateNtfyConfig(rig.config), enabled=True, transport=rig.transport
    )

    def check(stage):
        if stage == "before_http":
            assert deliver_one(rig.alerts, sender)["state"] == "busy"
            assert rig.health.deliver(sender)["state"] == "busy"

    assert not rig.run(_checkpoint=check)["delivery_fenced"]
    assert len(rig.calls) == 1


def test_pending_source_event_does_not_break_fence_ownership(rig):
    def changed(stage):
        if stage == "after_response":
            rig.sql("UPDATE discogs_price_outbox SET state='pending',observed_at=observed_at+1")

    assert not rig.run(_checkpoint=changed)["delivery_fenced"]


def test_any_price_unknown_minimum_is_not_invented(rig):
    rule = PriceLeadRule(
        release_id=123, public_title="Offline record", currency="USD", any_price=True, enabled=True
    )
    rig.sql("UPDATE discogs_price_rules SET data=?", (rule.model_dump_json(),))
    rig.sql("UPDATE discogs_price_samples SET price='' ")
    received = []

    def handler(request):
        payload = json.loads(request.content)
        assert payload["title"].startswith("TEST ONLY")
        assert "any price" in payload["message"]
        assert "8.00" not in payload["message"] and "minimum item price" not in payload["message"]
        received.append(payload)
        return httpx.Response(200)

    rig.transport = httpx.MockTransport(handler)
    assert rig.run()["provider_accepted"]
    assert len(received) == 1


@pytest.mark.parametrize("stage", ["before_http", "after_response"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("kind", "wrong"),
        ("payload_hash", "wrong"),
        ("destination_ref", "wrong"),
        ("reserved_at", 1),
        ("send_day", 1),
        ("sends", 99),
        ("previous_next_send", 1),
    ],
)
def test_full_journal_ownership_mutations_keep_fence(rig, stage, field, value):
    def changed(point):
        if point == stage:
            rig.sql(f"UPDATE {TABLE} SET {field}=?", (value,))

    with pytest.raises(ValueError, match="ownership changed"):
        rig.run(_checkpoint=changed)
    assert rig.fence() == math.inf
    assert len(rig.calls) == (1 if stage == "after_response" else 0)


@pytest.mark.parametrize("state", ["pending", "retry_wait"])
@pytest.mark.parametrize("table", ["discogs_price_outbox", "discogs_health_outbox"])
def test_initial_pending_source_work_blocks(rig, state, table):
    if table == "discogs_health_outbox":
        rig.health.queue_test("offline-queued", "degraded")
    rig.sql(f"UPDATE {table} SET state=?", (state,))
    with pytest.raises(ValueError, match="Unfinished source"):
        rig.run()
    assert not rig.calls


@pytest.mark.parametrize("mode", ["OFF", "MEMORY"])
def test_unsafe_sqlite_mode_rejected(rig, monkeypatch, mode):
    original = sqlite3.connect

    def connection(*args, **kwargs):
        db = original(*args, **kwargs)
        db.execute(f"PRAGMA journal_mode={mode}")
        return db

    monkeypatch.setattr("finder.discogs_phone_proof.sqlite3.connect", connection)
    with pytest.raises(ValueError, match="Durable SQLite"):
        rig.run()
    assert not rig.calls


def test_expired_fixture_result_leaves_fence(rig):
    def response(request):
        rig.now += 1000
        return httpx.Response(200)

    rig.handler = response
    result = rig.run()
    assert result["provider_accepted"] and result["delivery_fenced"]
    assert rig.fence() == math.inf
    assert rig.sql(f"SELECT hold_reason FROM {TABLE}") == [("fixture_not_finalized",)]


def test_journal_records_manual_hold_and_applied_deadline(rig):
    rig.headers = {"Retry-After": "invalid"}
    rig.run()
    assert rig.sql(
        f"SELECT manual_attention,retry_after,applied_release_deadline,hold_reason FROM {TABLE}"
    ) == [(1, 0.0, None, "provider_manual_attention")]


def set_outside_source_cap(rig, *, title="Offline record", currency="USD"):
    rule = PriceLeadRule(
        release_id=123,
        public_title=title,
        currency=currency,
        item_price_ceiling="6.00",
        enabled=True,
    )
    rig.sql("UPDATE discogs_price_rules SET data=?", (rule.model_dump_json(),))
    rig.sql("UPDATE discogs_price_samples SET low=0")


@pytest.mark.parametrize("cap", ["8.00", "9.00"])
def test_explicit_fixture_cap_actual_price_equality_and_above(rig, cap):
    set_outside_source_cap(rig)
    before = rig.content()
    received = []

    def handler(request):
        payload = json.loads(request.content)
        assert payload["title"].startswith(f"TEST ONLY: TEMPORARY TEST CAP USD {cap};")
        assert payload["message"].startswith(f"TEST ONLY: TEMPORARY TEST CAP USD {cap};")
        assert "reported minimum item price USD 8.00" in payload["message"]
        assert f"Watch: item price at or below USD {cap}." in payload["message"]
        received.append(payload)
        return httpx.Response(200)

    rig.transport = httpx.MockTransport(handler)
    assert rig.run(test_item_ceiling=cap)["provider_accepted"]
    assert len(received) == 1
    assert rig.content() == before
    with sqlite3.connect(rig.evidence / "fixed-offline-proof" / "fixture.db") as db:
        assert db.execute(
            "SELECT price,count,last_success FROM discogs_price_samples"
        ).fetchone() == ("8.00", 2, rig.now)
        fixture_rule = json.loads(db.execute("SELECT data FROM discogs_price_rules").fetchone()[0])
        assert fixture_rule["item_price_ceiling"] == cap
        assert fixture_rule["currency"] == "USD"


@pytest.mark.parametrize("cap", [None, "7.99"])
def test_original_cap_failure_omitted_or_too_low_override_never_reserves(rig, cap):
    set_outside_source_cap(rig)
    before = rig.content()
    with pytest.raises(ValueError, match="No fresh eligible"):
        rig.run(test_item_ceiling=cap)
    assert not rig.calls
    assert rig.content() == before
    assert rig.sql("SELECT name FROM sqlite_master WHERE name=?", (TABLE,)) == []
    assert list(rig.evidence.iterdir()) == []


@pytest.mark.parametrize(
    "cap",
    [
        "0.00",
        "00.00",
        "-1.00",
        "1",
        "1.0",
        "1.001",
        "1e2",
        "USD 8.00",
        "8,00",
        " 8.00",
        "8.00 ",
        "NaN",
        "Infinity",
        "１００.00",
        "10000000.00",
        8,
        8.0,
        True,
    ],
)
def test_invalid_fixture_cap_has_no_source_writes(rig, cap):
    with sqlite3.connect(rig.path) as db:
        before = list(db.iterdump())
    with pytest.raises(ValueError, match="Test cap"):
        rig.run(test_item_ceiling=cap)
    with sqlite3.connect(rig.path) as db:
        assert list(db.iterdump()) == before
    assert not rig.calls
    assert list(rig.evidence.iterdir()) == []


def test_long_unicode_title_cannot_hide_temporary_cap(rig):
    set_outside_source_cap(rig, title="盤" * 120, currency="EUR")
    received = []

    def handler(request):
        payload = json.loads(request.content)
        prefix = "TEST ONLY: TEMPORARY TEST CAP EUR 9.00;"
        assert payload["title"].startswith(prefix)
        assert payload["message"].startswith(prefix)
        assert "reported minimum item price EUR 8.00" in payload["message"]
        assert "Watch: item price at or below EUR 9.00." in payload["message"]
        assert len(payload["title"].encode()) <= 180
        received.append(payload)
        return httpx.Response(200)

    rig.transport = httpx.MockTransport(handler)
    assert rig.run(test_item_ceiling="9.00")["provider_accepted"]
    assert len(received) == 1


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE discogs_price_samples SET count=0",
        "UPDATE discogs_price_samples SET count=NULL",
        "UPDATE discogs_price_samples SET price=NULL",
        "UPDATE discogs_price_samples SET price='unknown'",
        "UPDATE discogs_price_samples SET status='error'",
        "UPDATE discogs_price_samples SET last_success=1799990000",
    ],
)
def test_override_never_bypasses_observation_integrity(rig, sql):
    rig.sql(sql)
    with pytest.raises(ValueError, match="No fresh eligible"):
        rig.run(test_item_ceiling="9.00")
    assert not rig.calls
    assert rig.sql("SELECT name FROM sqlite_master WHERE name=?", (TABLE,)) == []


def test_override_original_source_fingerprint_still_checked(rig):
    set_outside_source_cap(rig)

    def changed(stage):
        if stage == "before_reservation":
            rig.sql("UPDATE discogs_price_rules SET data=replace(data,'6.00','5.00')")

    with pytest.raises(ValueError, match="observation changed"):
        rig.run(test_item_ceiling="9.00", _checkpoint=changed)
    assert not rig.calls
    assert rig.sql("SELECT name FROM sqlite_master WHERE name=?", (TABLE,)) == []
