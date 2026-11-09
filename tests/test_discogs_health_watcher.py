"""Offline watcher/worker/private transport rehearsals with disposable state."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import httpx
import pytest

from finder import discogs_health_watcher as module
from finder.discogs_app_auth import AppCredentials, AuthenticatedReleaseStatsReader
from finder.discogs_health_messages import health_message
from finder.discogs_health_watcher import HealthWatcher
from finder.discogs_ntfy import NtfySender, PrivateNtfyConfig, deliver_one
from finder.discogs_price_alerts import DAILY_SEND_CAP, AggregatePriceAlerts, PriceLeadRule
from finder.discogs_price_health import health_report
from finder.discogs_price_worker import tick
from finder.discogs_rules import RulesStore


class Rig:
    def __init__(self, tmp_path):
        self.database = tmp_path / "aggregate.db"
        RulesStore(self.database, create=True).close()
        self.now = 1800000000.0
        self.alerts = AggregatePriceAlerts(self.database, clock=lambda: self.now, poll_seconds=300)
        self.alerts.save_rule(
            PriceLeadRule(
                release_id=123,
                public_title="Synthetic public record",
                currency="USD",
                any_price=True,
                enabled=True,
            ),
            expected_revision=0,
        )
        self.alerts.set_enabled(True, expected_revision=0)
        self.app = tmp_path / "app.json"
        self.app.write_text(
            json.dumps({"consumer_key": "fixture_key", "consumer_secret": "fixture_secret"})
        )
        self.app.chmod(0o600)
        self.ntfy = tmp_path / "ntfy.json"
        self.ntfy.write_text(
            json.dumps(
                {
                    "origin": "https://vps-a0c5b746.vps.ovh.us",
                    "topic": "offline_fixture_destination_not_live",
                    "token": "tk_OfflineFixtureTokenNotLive12345",
                }
            )
        )
        self.ntfy.chmod(0o600)
        self.stats_status = 200
        self.count = 0
        self.send_status = 200
        self.headers = {}
        self.requests = []
        self.reader = AuthenticatedReleaseStatsReader(
            AppCredentials(self.app),
            clock=lambda: self.now,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    self.stats_status,
                    json={
                        "num_for_sale": self.count,
                        "lowest_price": {"currency": "USD", "value": 10} if self.count else None,
                    },
                )
            ),
        )
        self.sender = NtfySender(
            private_config=PrivateNtfyConfig(self.ntfy),
            enabled=True,
            transport=httpx.MockTransport(self.handle),
        )
        self.watcher = HealthWatcher(self.database, clock=lambda: self.now)
        tick(self.alerts, reader=self.reader)
        self.watcher.initialize()

    def handle(self, request):
        self.requests.append(request)
        assert request.url.path == "/"
        return httpx.Response(self.send_status, headers=self.headers)

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.database)) as db, db:
            return db.execute(query, args).fetchall()

    def report(self):
        return health_report(
            self.database,
            now=self.now,
            poll_seconds=300,
            discogs_app_file=self.app,
            ntfy_config_file=self.ntfy,
        )

    def enable(self):
        return self.watcher.set_enabled(True, expected_revision=self.watcher.status()["revision"])

    def step(self, *, error=False, deliver=True, advance=300):
        self.now += advance
        self.stats_status = 503 if error else 200
        tick(self.alerts, reader=self.reader)
        observation = self.watcher.observe(self.report())
        delivery = (
            self.watcher.deliver(self.sender) if deliver and observation["send_allowed"] else None
        )
        return observation, delivery

    def states(self):
        return self.sql("SELECT kind,state,attempts FROM discogs_health_outbox ORDER BY id")

    def history(self, key="old_event", state="uncertain"):
        self.sql(
            "INSERT INTO discogs_price_outbox(event_key,release_id,rule_revision,episode,"
            "price,observed_at,state,attempts,next_attempt) VALUES(?,123,1,?,'',?,?,1,?)",
            (
                key,
                len(self.sql("SELECT event_key FROM discogs_price_outbox")) + 10,
                self.now,
                state,
                self.now,
            ),
        )


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_default_off_no_network_no_aggregate_mutations_and_idempotent_init(rig):
    before = rig.sql("SELECT * FROM discogs_price_control")
    assert rig.watcher.status()["status"] == "disabled"
    rig.step(error=True)
    rig.step(error=True)
    assert not rig.requests and not rig.states()
    assert rig.watcher.initialize()["revision"] == 0
    assert rig.sql("SELECT * FROM discogs_price_control") == before


def test_end_to_end_debounce_one_failure_and_one_recovery_no_healthy_pushes(rig):
    rig.enable()
    assert rig.step(error=True)[1]["state"] == "idle"
    assert rig.step(error=True)[1] == {"state": "accepted", "delivered": False}
    assert len(rig.requests) == 1
    for _ in range(3):
        rig.step(error=True)
    assert len(rig.requests) == 1
    rig.step()
    assert rig.step()[1]["state"] == "accepted"
    for _ in range(10):
        rig.step()
    assert rig.states() == [("degraded", "accepted", 1), ("recovered", "accepted", 1)]
    assert len(rig.requests) == 2
    payloads = [json.loads(request.content) for request in rig.requests]
    assert payloads[0]["title"] == "Sniper needs attention"
    assert payloads[1]["title"] == "Sniper recovered"
    assert "not been resent" in payloads[1]["message"]
    assert rig.watcher.status()["phone_delivery"] == "unverified"
    assert rig.sql("SELECT enabled FROM discogs_price_control") == [(1,)]


def test_transient_failure_and_duplicate_runs_cannot_advance_debounce(rig):
    rig.enable()
    rig.step(error=True, deliver=False)
    assert rig.watcher.observe(rig.report())["status"] == "check_not_due"
    rig.step()
    rig.step()
    assert not rig.states() and not rig.requests


def test_missing_checks_and_backward_clock_do_not_infer_consecutive_checks(rig):
    rig.enable()
    rig.step(error=True, deliver=False)
    rig.step(error=True, deliver=False, advance=901)
    assert not rig.states()
    rig.now -= 600
    assert rig.watcher.observe(rig.report())["status"] == "clock_reversed"
    assert rig.watcher.deliver(rig.sender)["state"] == "idle"


def test_historical_queue_rows_are_baselined_then_new_events_report_once_and_recover(rig):
    rig.history()
    rig.enable()
    for _ in range(3):
        rig.step()
    assert rig.report()["status"] == "attention"  # Historical CLI warnings remain accurate.
    assert not rig.states()
    rig.history("new_event", "failed")
    rig.step()
    assert rig.step()[1]["state"] == "accepted"
    assert "new notification attempt" in json.loads(rig.requests[0].content)["message"]
    for _ in range(7):
        rig.step()
    assert len(rig.requests) == 2
    assert rig.states()[-1][0:2] == ("recovered", "accepted")
    assert len(rig.sql("SELECT * FROM discogs_health_seen")) == 2


def test_cause_changes_aggregate_into_same_incident_without_reminders(rig):
    rig.enable()
    rig.step(error=True)
    rig.step(error=True)
    rig.history("new_failure", "failed")
    rig.step(error=True)
    assert len(rig.requests) == 1
    reasons = json.loads(rig.sql("SELECT reasons FROM discogs_health_incidents")[0][0])
    assert set(reasons) == {"polling_problem", "new_delivery_problem"}


def test_recovery_cancels_unsent_degradation_and_never_creates_orphan_recovery(rig):
    rig.enable()
    rig.step(error=True, deliver=False)
    rig.step(error=True, deliver=False)
    rig.step(deliver=False)
    rig.step()
    assert rig.states() == [("degraded", "cancelled", 0)]
    assert not rig.requests and not rig.watcher.status()["incident_open"]


@pytest.mark.parametrize("response", [500, 400, 302])
def test_uncertain_or_failed_degradation_never_generates_recovery_or_retry(rig, response):
    rig.enable()
    rig.send_status = response
    rig.step(error=True)
    rig.step(error=True)
    for _ in range(8):
        rig.step()
    assert len(rig.requests) == 1
    assert len(rig.states()) == 1
    assert rig.states()[0][1] == ("uncertain" if response == 500 else "failed")
    assert not rig.watcher.status()["incident_open"]


def test_expired_lease_uncertain_across_restart_and_no_recovery(rig):
    rig.enable()
    rig.step(error=True, deliver=False)
    rig.step(error=True, deliver=False)
    assert rig.watcher.claim(rig.sender)
    rig.now += 121
    rig.watcher = HealthWatcher(rig.database, clock=lambda: rig.now)
    for _ in range(5):
        rig.step()
    assert rig.states() == [("degraded", "uncertain", 1)]
    assert not rig.requests


def test_shared_429_from_health_blocks_aggregate_and_retries_only_after_cooldown(rig):
    rig.enable()
    rig.send_status = 429
    rig.headers = {"Retry-After": "1800"}
    rig.step(error=True)
    assert rig.step(error=True)[1]["state"] == "retry_wait"
    until = rig.now + 1800
    assert rig.sql("SELECT next_send FROM discogs_price_control")[0][0] == until
    rig.count = 1
    rig.step(advance=300)
    assert deliver_one(rig.alerts, rig.sender)["state"] == "idle"
    assert len(rig.requests) == 1
    rig.send_status = 200
    while rig.now < until:
        rig.step(deliver=False)
    tick(rig.alerts, reader=rig.reader)
    assert deliver_one(rig.alerts, rig.sender)["state"] == "accepted"


def test_shared_aggregate_429_blocks_health_test_without_independent_retry(rig):
    rig.count = 1
    rig.now += 300
    tick(rig.alerts, reader=rig.reader)
    rig.send_status = 429
    rig.headers = {"Retry-After": "3600"}
    assert deliver_one(rig.alerts, rig.sender)["state"] == "retry_wait"
    rig.watcher.queue_test("canary", "degraded")
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "idle"
    rig.now += 3599
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "idle"
    assert len(rig.requests) == 1


def test_daily_caps_health_spacing_and_utc_reset_are_shared(rig):
    for i in range(4):
        rig.watcher.queue_test(f"canary{i}", "degraded")
        assert rig.watcher.deliver(rig.sender, test_key=f"canary{i}")["state"] == "accepted"
        rig.now += 900
    rig.watcher.queue_test("capped", "degraded")
    assert rig.watcher.deliver(rig.sender, test_key="capped")["state"] == "idle"
    assert rig.sql("SELECT sends FROM discogs_price_control") == [(4,)]
    rig.now = (int(rig.now // 86400) + 1) * 86400
    assert rig.watcher.deliver(rig.sender, test_key="capped")["state"] == "accepted"
    rig.now += 900
    rig.sql("UPDATE discogs_price_control SET sends=?", (DAILY_SEND_CAP,))
    rig.watcher.queue_test("aggregate_cap", "degraded")
    assert rig.watcher.deliver(rig.sender, test_key="aggregate_cap")["state"] == "idle"


def test_canary_is_explicit_labeled_idempotent_isolated_and_real_spacing_applies(rig):
    rig.watcher.queue_test("canary", "degraded")
    assert rig.watcher.deliver(rig.sender)["state"] == "idle"
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "accepted"
    rig.watcher.queue_test("canary", "degraded")
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "idle"
    rig.watcher.queue_test("canary", "recovered")
    rig.now += 899
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "idle"
    rig.now += 1
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "accepted"
    assert rig.watcher.status()["status"] == "disabled"
    assert rig.watcher.status()["last_check_at"] is None
    for request in rig.requests:
        assert "TEST:" in json.loads(request.content)["title"]
        assert "test" in json.loads(request.content)["message"]
    assert len(rig.requests) == 2


def test_test_recovery_rejected_before_degradation_acceptance(rig):
    with pytest.raises(ValueError):
        rig.watcher.queue_test("missing", "recovered")
    rig.watcher.queue_test("uncertain", "degraded")
    rig.send_status = 500
    rig.watcher.deliver(rig.sender, test_key="uncertain")
    with pytest.raises(ValueError):
        rig.watcher.queue_test("uncertain", "recovered")


def test_two_processes_cannot_double_reserve_or_send(rig):
    rig.watcher.queue_test("canary", "degraded")

    def claim():
        return HealthWatcher(rig.database, clock=lambda: rig.now).claim(
            rig.sender, test_key="canary"
        )

    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert sum(value is not None for value in claims) == 1
    assert rig.sql("SELECT sends FROM discogs_price_control") == [(1,)]


def test_disable_rollback_preserves_aggregate_and_does_not_replay_history(rig):
    rig.enable()
    rig.step(error=True, deliver=False)
    rig.step(error=True, deliver=False)
    reservation = rig.watcher.claim(rig.sender)
    before = rig.sql("SELECT * FROM discogs_price_control")
    assert rig.watcher.set_enabled(False, expected_revision=1)["status"] == "disabled"
    assert rig.sql("SELECT * FROM discogs_price_control") == before
    assert not rig.watcher.delivery_current(reservation)
    result = rig.watcher.finish(reservation, "rejected", retry_after=1800)
    assert result["state"] == "uncertain"
    cooldown = rig.sql("SELECT next_send FROM discogs_price_control")[0][0]
    rig.watcher.finish(reservation, "rejected", retry_after=3600)
    assert rig.sql("SELECT next_send FROM discogs_price_control")[0][0] == cooldown
    rig.history("while_muted")
    rig.enable()
    for _ in range(5):
        rig.step()
    assert not rig.requests
    assert rig.states() == [("degraded", "uncertain", 1)]
    assert rig.sql("SELECT enabled FROM discogs_price_control") == [(1,)]


def test_changed_destination_never_reroutes_bound_retry(rig):
    rig.watcher.queue_test("canary", "degraded")
    rig.send_status = 429
    rig.watcher.deliver(rig.sender, test_key="canary")
    value = json.loads(rig.ntfy.read_text())
    value["token"] = "tk_DifferentOfflineFixtureToken12345"
    rig.ntfy.write_text(json.dumps(value))
    changed = NtfySender(
        private_config=PrivateNtfyConfig(rig.ntfy),
        enabled=True,
        transport=httpx.MockTransport(rig.handle),
    )
    rig.now += 900
    assert rig.watcher.deliver(changed, test_key="canary")["state"] == "idle"
    assert len(rig.requests) == 1


def test_recovery_flapping_blocks_dispatch_until_two_clear_checks(rig):
    rig.enable()
    rig.step(error=True)
    rig.step(error=True)
    rig.step(deliver=False)
    rig.step(deliver=False)
    assert rig.states()[-1][0:2] == ("recovered", "pending")
    rig.step(error=True)
    assert len(rig.requests) == 1
    rig.step()
    rig.step()
    assert len(rig.requests) == 2


def test_expired_recovery_does_not_leave_uncloseable_incident(rig):
    rig.enable()
    rig.step(error=True)
    rig.step(error=True)
    rig.step(deliver=False)
    rig.step(deliver=False)
    rig.step(advance=86401, deliver=False)
    assert not rig.watcher.status()["incident_open"]
    assert rig.states()[-1][1] == "expired"


def test_report_status_and_inspect_are_readonly_and_redacted(rig, capsys):
    with closing(sqlite3.connect(rig.database)) as db:
        before = list(db.iterdump())
        result = module.main(
            [
                "inspect",
                "--database",
                str(rig.database),
                "--discogs-app-file",
                str(rig.app),
                "--ntfy-config-file",
                str(rig.ntfy),
            ]
        )
        assert result == 0
        output = capsys.readouterr().out
        assert list(db.iterdump()) == before
    for secret in (
        str(rig.database),
        "fixture_secret",
        "OfflineFixtureToken",
        "destination_not_live",
    ):
        assert secret not in output


@pytest.mark.parametrize("kind", ["missing", "corrupt", "locked"])
def test_unavailable_database_never_falls_back_to_sending(rig, kind, capsys):
    lock = None
    if kind == "missing":
        path = rig.database.with_name("missing.db")
    elif kind == "corrupt":
        path = rig.database.with_name("corrupt.db")
        path.write_text("DO_NOT_PRINT_SECRET")
    else:
        path = rig.database
        lock = sqlite3.connect(path)
        lock.execute("BEGIN EXCLUSIVE")
    try:
        assert (
            module.main(
                [
                    "tick",
                    "--database",
                    str(path),
                    "--discogs-app-file",
                    str(rig.app),
                    "--ntfy-config-file",
                    str(rig.ntfy),
                    "--deliver",
                ]
            )
            == 2
        )
        output = capsys.readouterr().out
        assert "DO_NOT_PRINT" not in output
        assert not rig.requests
    finally:
        if lock is not None:
            lock.close()


def test_sender_health_validation_does_not_weaken_original_payload_guard(rig):
    with pytest.raises(ValueError):
        rig.sender.send({"title": "x", "message": "secret", "click": "https://example.com"})
    with pytest.raises(ValueError):
        rig.sender.send_health("degraded", ["DO_NOT_PRINT_SECRET"])
    public = NtfySender("offline_fixture_destination_not_live", enabled=True)
    with pytest.raises(ValueError):
        public.send_health("degraded", ["polling_problem"])
    assert not rig.requests
    assert "test" not in health_message("degraded", ["polling_problem"])["title"]


def test_health_only_spacing_does_not_delay_aggregate_for_fifteen_minutes(rig):
    rig.watcher.queue_test("canary", "degraded")
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "accepted"
    rig.now += 91
    rig.alerts.observe(
        123,
        1,
        {"release_id": 123, "currency": "USD", "num_for_sale": 1, "lowest_item_price": "10.00"},
        observed_at=rig.now,
    )
    assert deliver_one(rig.alerts, rig.sender)["state"] == "accepted"
    assert len(rig.requests) == 2


def test_health_retry_obeys_backoff_durable_attempts_and_current_incident(rig):
    rig.enable()
    rig.send_status = 429
    rig.step(error=True)
    rig.step(error=True)
    first_attempt = rig.now
    rig.watcher = HealthWatcher(rig.database, clock=lambda: rig.now)
    rig.step(error=True)
    rig.step(error=True)
    assert len(rig.requests) == 1
    rig.step(error=True)
    assert rig.now == first_attempt + 900
    assert len(rig.requests) == 2
    assert rig.states() == [("degraded", "retry_wait", 2)]
    assert rig.sql("SELECT next_send FROM discogs_price_control")[0][0] == rig.now + 1800


def test_two_observers_cannot_duplicate_incidents(rig):
    rig.enable()
    rig.step(error=True, deliver=False)
    rig.now += 300
    tick(rig.alerts, reader=rig.reader)
    report = rig.report()
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda _: HealthWatcher(rig.database, clock=lambda: rig.now).observe(report),
                range(2),
            )
        )
    assert {value["status"] for value in results} == {"degraded", "check_not_due"}
    assert len(rig.sql("SELECT id FROM discogs_health_incidents")) == 1
    assert rig.states() == [("degraded", "pending", 0)]


def test_expired_recovery_lease_never_retries_after_restart(rig):
    rig.enable()
    rig.step(error=True)
    rig.step(error=True)
    rig.step(deliver=False)
    rig.step(deliver=False)
    rig.step(deliver=False)
    reservation = rig.watcher.claim(rig.sender)
    assert reservation["kind"] == "recovered"
    rig.step(deliver=False)
    assert rig.states()[-1][1] == "uncertain"
    assert not rig.watcher.status()["incident_open"]
    for _ in range(5):
        rig.step()
    assert len(rig.requests) == 1


@pytest.mark.parametrize(
    "failure,expected",
    [
        (httpx.ConnectError, "retry_wait"),
        (httpx.ReadTimeout, "uncertain"),
        (httpx.WriteError, "uncertain"),
    ],
)
def test_health_transport_outcomes_preserve_existing_no_retry_for_ambiguity(rig, failure, expected):
    def fail(request):
        raise failure("DO_NOT_PRINT_PRIVATE_ERROR", request=request)

    rig.sender._transport = httpx.MockTransport(fail)
    rig.watcher.queue_test("canary", "degraded")
    result = rig.watcher.deliver(rig.sender, test_key="canary")
    assert result == {"state": expected, "delivered": False}
    assert "DO_NOT_PRINT" not in json.dumps(rig.watcher.status())


def test_invalid_configuration_no_send_and_cli_redacts_failure(rig, capsys):
    rig.ntfy.write_text('{"token":"DO_NOT_PRINT_PRIVATE_ERROR"}')
    assert (
        module.main(
            [
                "test",
                "--database",
                str(rig.database),
                "--test-id",
                "canary",
                "--phase",
                "degraded",
                "--ntfy-config-file",
                str(rig.ntfy),
                "--deliver",
            ]
        )
        == 2
    )
    assert "DO_NOT_PRINT" not in capsys.readouterr().out
    assert not rig.requests
    assert rig.sql("SELECT sends FROM discogs_price_control") == [(0,)]


def test_disabled_health_watcher_does_not_stop_aggregate_polling_or_delivery(rig):
    rig.enable()
    rig.watcher.set_enabled(False, expected_revision=1)
    rig.count = 1
    rig.now += 300
    result = tick(rig.alerts, reader=rig.reader, sender=rig.sender)
    assert result["poll"]["status"] == "available"
    assert result["delivery"] == {"state": "accepted", "delivered": False}
    assert not rig.states()


def test_late_aggregate_429_invalidates_reserved_health_before_post(rig):
    rig.now += 300
    rig.count = 1
    tick(rig.alerts, reader=rig.reader)
    aggregate = rig.alerts.claim(rig.sender.binding)
    rig.now += 91
    rig.watcher.queue_test("canary", "degraded")
    health = rig.watcher.claim(rig.sender, test_key="canary")
    # Direct reservation unit test; production pipelines additionally share the lock.
    rig.alerts.finish(aggregate, "rejected", retry_after=1800)
    assert not rig.watcher.delivery_current(health)
    assert not rig.requests
    assert rig.sql("SELECT next_send FROM discogs_price_control")[0][0] == rig.now + 1800


def test_late_health_429_invalidates_reserved_aggregate_before_post(rig):
    rig.watcher.queue_test("canary", "degraded")
    health = rig.watcher.claim(rig.sender, test_key="canary")
    rig.now += 91
    rig.alerts.observe(
        123,
        1,
        {"release_id": 123, "currency": "USD", "num_for_sale": 1, "lowest_item_price": "10.00"},
        observed_at=rig.now,
    )
    aggregate = rig.alerts.claim(rig.sender.binding)
    rig.watcher.finish(health, "rejected", retry_after=1800)
    assert not rig.alerts.delivery_current(aggregate)
    assert not rig.requests
    assert rig.sql("SELECT next_send FROM discogs_price_control")[0][0] == rig.now + 1800


def test_delayed_health_post_preserves_actual_health_and_shared_spacing(rig):
    rig.watcher.queue_test("canary", "degraded")
    reservation = rig.watcher.claim(rig.sender, test_key="canary")
    claimed_at = rig.now
    rig.now += 119
    assert rig.watcher.delivery_current(reservation)
    result = rig.sender.send_health("degraded", ["polling_problem"], test=True)
    rig.watcher.finish(reservation, result.outcome)
    completed_at = rig.now
    rig.alerts.observe(
        123,
        1,
        {"release_id": 123, "currency": "USD", "num_for_sale": 1, "lowest_item_price": "10.00"},
        observed_at=rig.now,
    )
    assert deliver_one(rig.alerts, rig.sender)["state"] == "idle"
    rig.watcher.queue_test("canary", "recovered")
    rig.now = claimed_at + 900
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "idle"
    rig.now = completed_at + 900
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "accepted"
    assert len(rig.requests) == 2


def test_delayed_aggregate_post_preserves_actual_shared_spacing(rig):
    rig.now += 300
    rig.count = 1
    tick(rig.alerts, reader=rig.reader)
    reservation = rig.alerts.claim(rig.sender.binding)
    rig.now += 119
    assert rig.alerts.delivery_current(reservation)
    result = rig.sender.send(reservation["payload"])
    rig.alerts.finish(reservation, result.outcome)
    rig.watcher.queue_test("canary", "degraded")
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "idle"
    rig.now += 90
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "accepted"


def test_shared_lock_blocks_aggregate_during_paused_health_dispatch(rig, monkeypatch):
    rig.watcher.queue_test("canary", "degraded")
    original = rig.sender.send_health
    competing = []

    def paused(*args, **kwargs):
        rig.now += 91
        rig.alerts.observe(
            123,
            1,
            {"release_id": 123, "currency": "USD", "num_for_sale": 1, "lowest_item_price": "10.00"},
            observed_at=rig.now,
        )
        before = rig.sql("SELECT sends FROM discogs_price_control")
        competing.append(deliver_one(rig.alerts, rig.sender))
        assert rig.sql("SELECT sends FROM discogs_price_control") == before
        return original(*args, **kwargs)

    monkeypatch.setattr(rig.sender, "send_health", paused)
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "accepted"
    assert competing == [{"state": "busy", "delivered": False}]
    assert len(rig.requests) == 1


def test_shared_lock_blocks_health_during_paused_aggregate_dispatch(rig, monkeypatch):
    rig.now += 300
    rig.count = 1
    tick(rig.alerts, reader=rig.reader)
    rig.watcher.queue_test("canary", "degraded")
    original = rig.sender.send
    competing = []

    def paused(payload):
        rig.now += 91
        before = rig.sql("SELECT sends FROM discogs_price_control")
        competing.append(rig.watcher.deliver(rig.sender, test_key="canary"))
        assert rig.sql("SELECT sends FROM discogs_price_control") == before
        return original(payload)

    monkeypatch.setattr(rig.sender, "send", paused)
    assert deliver_one(rig.alerts, rig.sender)["state"] == "accepted"
    assert competing == [{"state": "busy", "delivered": False}]
    assert len(rig.requests) == 1


@pytest.mark.parametrize("source", ["aggregate", "health"])
def test_abandoned_claim_quarantined_once_with_recovery_time_hold(rig, source):
    if source == "aggregate":
        rig.now += 300
        rig.count = 1
        tick(rig.alerts, reader=rig.reader)
        assert rig.alerts.claim(rig.sender.binding)
    else:
        rig.watcher.queue_test("abandoned", "degraded")
        assert rig.watcher.claim(rig.sender, test_key="abandoned")
    rig.now += 1000
    rig.watcher.queue_test("next", "degraded")
    assert rig.watcher.deliver(rig.sender, test_key="next")["state"] == "uncertain_recovery"
    until = rig.now + 900
    assert rig.sql("SELECT next_send FROM discogs_price_control")[0][0] == until
    assert rig.watcher.deliver(rig.sender, test_key="next")["state"] == "idle"
    assert rig.sql("SELECT next_send FROM discogs_price_control")[0][0] == until
    assert not rig.requests
    rig.now = until
    assert rig.watcher.deliver(rig.sender, test_key="next")["state"] == "accepted"
    assert len(rig.requests) == 1


def test_unknown_recovery_never_shortens_known_provider_deadline(rig):
    rig.watcher.queue_test("abandoned", "degraded")
    assert rig.watcher.claim(rig.sender, test_key="abandoned")
    known = rig.now + 172800
    rig.sql("UPDATE discogs_price_control SET next_send=?", (known,))
    rig.now += 500
    assert rig.watcher.deliver(rig.sender, test_key="abandoned")["state"] == "uncertain_recovery"
    assert rig.sql("SELECT next_send FROM discogs_price_control") == [(known,)]
    assert rig.watcher.deliver(rig.sender, test_key="abandoned")["state"] == "idle"


def test_lock_files_are_private_and_symlink_lock_is_refused(rig, tmp_path):
    lock = rig.database.with_name(rig.database.name + ".ntfy-send.lock")
    foreign = tmp_path / "unchanged"
    foreign.write_text("DO_NOT_READ")
    foreign.chmod(0o600)
    lock.symlink_to(foreign)
    rig.watcher.queue_test("canary", "degraded")
    with pytest.raises(OSError):
        rig.watcher.deliver(rig.sender, test_key="canary")
    assert foreign.read_text() == "DO_NOT_READ"
    assert not rig.requests


@pytest.mark.parametrize("status", [200, 429, 503])
@pytest.mark.parametrize("header", ["172800", "Sun, 17 Jan 2027 08:00:00 GMT"])
def test_explicit_long_provider_cooldown_survives_all_outcomes(rig, monkeypatch, status, header):
    from email.utils import parsedate_to_datetime

    from finder import discogs_ntfy

    monkeypatch.setattr(discogs_ntfy.time, "time", lambda: rig.now)
    # Use a future HTTP date relative to this deterministic fixture.
    if not header.isdigit():
        from email.utils import formatdate

        header = formatdate(rig.now + 172800, usegmt=True)
    expected = (
        rig.now + int(header) if header.isdigit() else parsedate_to_datetime(header).timestamp()
    )
    rig.send_status = status
    rig.headers = {"Retry-After": header}
    rig.watcher.queue_test("canary", "degraded")
    result = rig.watcher.deliver(rig.sender, test_key="canary")
    assert result["state"] == {200: "accepted", 429: "retry_wait", 503: "uncertain"}[status]
    assert rig.sql("SELECT next_send FROM discogs_price_control")[0][0] >= expected
    assert rig.sql("SELECT next_send FROM discogs_price_control")[0][0] > rig.now + 86400
    rig.now += 900
    rig.watcher.queue_test("next", "degraded")
    assert rig.watcher.deliver(rig.sender, test_key="next")["state"] == "idle"
    assert len(rig.requests) == 1


@pytest.mark.parametrize("header", ["999999999999999999999", "not-a-valid-provider-date"])
def test_unsupported_provider_deadline_blocks_manually_without_nonfinite_json(rig, header):
    rig.headers = {"Retry-After": header}
    rig.send_status = 429
    rig.watcher.queue_test("canary", "degraded")
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "failed"
    report = rig.report()
    assert report["controls"]["notification_manual_attention"] is True
    assert report["controls"]["send_cooldown_seconds"] is None
    assert "provider_manual_attention" in report["issues"]
    json.dumps(report, allow_nan=False)
    rig.now += 90000
    rig.watcher.queue_test("next", "degraded")
    assert rig.watcher.deliver(rig.sender, test_key="next")["state"] == "idle"
    assert len(rig.requests) == 1


def test_uncertain_aggregate_response_preserves_http_date_cooldown(rig, monkeypatch):
    from email.utils import formatdate

    from finder import discogs_ntfy

    monkeypatch.setattr(discogs_ntfy.time, "time", lambda: rig.now)
    rig.now += 300
    rig.count = 1
    tick(rig.alerts, reader=rig.reader)
    rig.send_status = 503
    rig.headers = {"Retry-After": formatdate(rig.now + 172800, usegmt=True)}
    assert deliver_one(rig.alerts, rig.sender)["state"] == "uncertain"
    assert rig.sql("SELECT next_send FROM discogs_price_control")[0][0] >= rig.now + 172800
    assert len(rig.requests) == 1


def test_absolute_provider_deadline_survives_clock_change_before_finish(rig):
    rig.watcher.queue_test("canary", "degraded")
    reservation = rig.watcher.claim(rig.sender, test_key="canary")
    deadline = rig.now + 172800
    rig.now -= 60
    rig.watcher.finish(reservation, "rejected", retry_after=172800, provider_deadline=deadline)
    assert rig.sql("SELECT next_send FROM discogs_price_control")[0][0] >= deadline


def test_aggregate_manual_hold_is_persisted_in_legacy_honored_gate(rig):
    import math

    rig.now += 300
    rig.count = 1
    tick(rig.alerts, reader=rig.reader)
    rig.headers = {"Retry-After": "999999999999999999999999"}
    rig.send_status = 503
    assert deliver_one(rig.alerts, rig.sender)["state"] == "uncertain"
    assert rig.sql("SELECT next_send FROM discogs_price_control") == [(math.inf,)]
    assert rig.alerts.claim(rig.sender.binding) is None
    rig.watcher.queue_test("canary", "degraded")
    assert rig.watcher.deliver(rig.sender, test_key="canary")["state"] == "idle"
    json.dumps(rig.report(), allow_nan=False)


def test_explicit_cutover_recovery_is_non_sending_and_repeatable(rig):
    rig.watcher.queue_test("canary", "degraded")
    assert rig.watcher.claim(rig.sender, test_key="canary")
    assert rig.watcher.status()["unfinished_health_attempts"] == 1
    rig.now += 30
    result = rig.watcher.recover()
    assert result == {"status": "recovered_unknown", "recovered_attempts": 1, "sent": False}
    assert rig.watcher.status()["unfinished_health_attempts"] == 0
    assert rig.watcher.status()["unfinished_aggregate_attempts"] == 0
    assert rig.watcher.status()["status"] == "disabled"
    cooldown = rig.sql("SELECT next_send FROM discogs_price_control")
    assert rig.watcher.recover() == {"status": "clear", "recovered_attempts": 0, "sent": False}
    assert rig.sql("SELECT next_send FROM discogs_price_control") == cooldown
    assert not rig.requests


def test_service_template_explicitly_selects_candidate_source_tree(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    template = (root / "deployment/sniper-health.service.example").read_text()
    assert "WorkingDirectory=@@EXISTING_CODE_DIRECTORY@@\n" in template
    assert "Environment=PYTHONPATH=@@EXISTING_CODE_DIRECTORY@@/src\n" in template
    assert "ExecStart=@@EXISTING_PYTHON@@ -m finder.discogs_health_watcher tick " in template
    assert "--deliver" in template
    source_path = next(
        line.removeprefix("Environment=PYTHONPATH=")
        for line in template.splitlines()
        if line.startswith("Environment=PYTHONPATH=")
    ).replace("@@EXISTING_CODE_DIRECTORY@@", str(root))
    result = subprocess.run(
        [sys.executable, "-c", "import finder.discogs_health_watcher as m; print(m.__file__)"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": source_path},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert (
        Path(result.stdout.strip()).resolve()
        == (root / "src/finder/discogs_health_watcher.py").resolve()
    )


@pytest.mark.parametrize("action", ["inspect", "tick"])
@pytest.mark.parametrize(
    "options,expected",
    [([], 300), (["--poll-seconds", "120"], 120), (["--poll-seconds", "86400"], 86400)],
)
def test_cli_expected_interval_is_independent_of_health_schedule(
    rig, monkeypatch, capsys, action, options, expected
):
    calls = []

    def report(database, **kwargs):
        calls.append(kwargs["poll_seconds"])
        return health_report(database, now=rig.now, **kwargs)

    monkeypatch.setattr(module, "health_report", report)
    monkeypatch.setattr(module, "HealthWatcher", lambda database: rig.watcher)
    assert (
        module.main(
            [
                action,
                "--database",
                str(rig.database),
                "--discogs-app-file",
                str(rig.app),
                "--ntfy-config-file",
                str(rig.ntfy),
                *options,
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert calls == [expected]
    if action == "inspect":
        assert result["health"]["expected_poll_seconds"] == expected
        assert result["health"]["sample_fresh_seconds"] == 1200
    assert module.CHECK_SECONDS == 300
    assert module.FRESH_SECONDS == 1200
    assert not rig.requests


@pytest.mark.parametrize("interval", ["119", "86401", "nan", "120.5", "0"])
def test_watcher_invalid_poll_interval_fails_before_database_access(tmp_path, interval):
    path = tmp_path / "absent.db"
    with pytest.raises(SystemExit) as error:
        module.main(["init", "--database", str(path), "--poll-seconds", interval])
    assert error.value.code == 2
    assert not path.exists()


def test_fast_report_preserves_three_hundred_second_incident_debounce(rig):
    rig.enable()
    rig.sql("UPDATE discogs_price_samples SET next_check=?", (rig.now - 150,))

    def report():
        return health_report(
            rig.database,
            now=rig.now,
            poll_seconds=120,
            discogs_app_file=rig.app,
            ntfy_config_file=rig.ntfy,
        )

    assert "poll_overdue" in report()["rules"][0]["issues"]
    assert "poll_overdue" not in rig.report()["rules"][0]["issues"]
    rig.watcher.observe(report())
    assert rig.watcher.status()["bad_checks"] == 1
    rig.now += 120
    rig.watcher.observe(report())
    assert rig.watcher.status()["bad_checks"] == 1
    assert not rig.watcher.status()["incident_open"]
    rig.now += 180
    rig.watcher.observe(report())
    assert rig.watcher.status()["bad_checks"] == 2
    assert rig.watcher.status()["incident_open"]
    assert report()["sample_fresh_seconds"] == 1200


def test_service_expected_poll_interval_is_not_health_timer():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    service = (root / "deployment/sniper-health.service.example").read_text()
    timer = (root / "deployment/sniper-health.timer.example").read_text()
    assert "tick --poll-seconds 300 --database" in service
    assert "OnUnitInactiveSec=5min" in timer


def test_pending_api_guard_is_polling_problem_even_with_fresh_samples(rig):
    rig.sql(
        "UPDATE discogs_api_read_guard SET pending_token=?,pending_since=?",
        ("a" * 32, rig.now - 31),
    )
    report = rig.report()
    assert all(rule["freshness"] == "fresh" for rule in report["rules"])
    assert module.current_reasons(report) == ["polling_problem"]
    assert not rig.requests
