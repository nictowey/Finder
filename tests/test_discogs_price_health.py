"""Disposable end-to-end worker/health rehearsals. All HTTP is intercepted."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from finder import discogs_price_health as health
from finder.discogs_app_auth import AppCredentials, AuthenticatedReleaseStatsReader
from finder.discogs_ntfy import NtfySender, PrivateNtfyConfig, deliver_one
from finder.discogs_price_alerts import (
    DAILY_SEND_CAP,
    FRESH_SECONDS,
    AggregatePriceAlerts,
    PriceLeadRule,
)
from finder.discogs_price_worker import tick
from finder.discogs_rules import RulesStore


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "rules.db"
    RulesStore(path, create=True).close()
    now = [1800000000.0]
    alerts = AggregatePriceAlerts(path, clock=lambda: now[0], poll_seconds=300)
    app = tmp_path / "app.json"
    app.write_text(json.dumps({"consumer_key": "fixture_key", "consumer_secret": "fixture_secret"}))
    app.chmod(0o600)
    ntfy = tmp_path / "ntfy.json"
    ntfy.write_text(
        json.dumps(
            {
                "origin": "https://vps-a0c5b746.vps.ovh.us",
                "topic": "offline_fixture_destination_not_live",
                "token": "tk_OfflineFixtureTokenNotLive12345",
            }
        )
    )
    ntfy.chmod(0o600)
    return path, now, alerts, {"discogs_app_file": app, "ntfy_config_file": ntfy}


def save_rule(alerts, release=1001, *, enabled=True):
    return alerts.save_rule(
        PriceLeadRule(
            release_id=release,
            public_title="Offline fixture only",
            currency="USD",
            any_price=True,
            enabled=enabled,
        ),
        expected_revision=0,
    )


def activate(alerts):
    alerts.set_enabled(True, expected_revision=0)


def observe(workspace, release=1001, *, count=1):
    path, now, alerts, _ = workspace
    execute(
        path,
        "UPDATE discogs_price_samples SET next_check=? WHERE release_id=?",
        (now[0] + 300, release),
    )
    return alerts.observe(
        release,
        1,
        {
            "release_id": release,
            "currency": "USD",
            "num_for_sale": count,
            "lowest_item_price": "10.00" if count else None,
        },
        observed_at=now[0],
    )


def report(workspace, **kwargs):
    path, now, _, config = workspace
    return health.health_report(path, now=now[0], poll_seconds=300, **config, **kwargs)


def database_fingerprint(path):
    """Data files and schema/data content; reading is never a migration/reconciliation."""
    raw = {
        suffix: hashlib.sha256(Path(str(path) + suffix).read_bytes()).hexdigest()
        for suffix in ("", "-wal", "-journal")
        if Path(str(path) + suffix).exists()
    }
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        content = "\n".join(db.iterdump())
    return raw, content


def execute(path, sql, args=()):
    with sqlite3.connect(path) as db:
        db.execute(sql, args)


def test_e2e_19_rules_actual_worker_poll_acceptance_and_health_are_read_only(workspace):
    path, now, alerts, config = workspace
    for release in range(1001, 1020):
        save_rule(alerts, release)
    activate(alerts)
    requests = []

    def stats(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path.startswith("/marketplace/stats/")
        assert "fixture_secret" in request.headers["Authorization"]
        return httpx.Response(
            200,
            json={
                "num_for_sale": 1,
                "lowest_price": {"currency": "USD", "value": 10},
            },
        )

    publishes = []

    def accept(request):
        publishes.append(request)
        assert request.method == "POST"
        assert request.url.path == "/"
        return httpx.Response(200)

    reader = AuthenticatedReleaseStatsReader(
        AppCredentials(config["discogs_app_file"]),
        transport=httpx.MockTransport(stats),
        clock=lambda: now[0],
    )
    sender = NtfySender(
        private_config=PrivateNtfyConfig(config["ntfy_config_file"]),
        enabled=True,
        transport=httpx.MockTransport(accept),
    )
    for _ in range(19):
        result = tick(alerts, reader=reader, sender=sender)
        assert result["poll"]["status"] == "available"
        assert result["delivery"]["delivered"] is False
        now[0] += 3
    assert len(requests) == 19
    assert len(publishes) == 1
    before = database_fingerprint(path)
    result = report(workspace)
    assert result["status"] == "healthy"
    assert result["rules_total"] == result["rules_enabled"] == 19
    assert {rule["freshness"] for rule in result["rules"]} == {"fresh"}
    assert result["queue"]["stored_states"]["pending"] == 18
    assert result["queue"]["stored_states"]["accepted"] == 1
    assert result["delivery"] == {
        "provider_accepted_total": 1,
        "phone_delivery": "unverified",
        "provider_acceptance_is_delivery": False,
    }
    assert database_fingerprint(path) == before
    assert len(requests) == 19 and len(publishes) == 1
    output = json.dumps(result) + health.format_text(result)
    for private in (
        "fixture_key",
        "fixture_secret",
        "offline_fixture_destination_not_live",
        "tk_OfflineFixtureTokenNotLive12345",
        str(path),
        "Offline fixture only",
    ):
        assert private not in output


def test_e2e_error_unknown_and_recovery_do_not_claim_zero_stock(workspace):
    _, now, alerts, config = workspace
    save_rule(alerts)
    activate(alerts)
    response = [httpx.Response(200, json={"num_for_sale": 0, "lowest_price": None})]
    reader = AuthenticatedReleaseStatsReader(
        AppCredentials(config["discogs_app_file"]),
        transport=httpx.MockTransport(lambda request: response[0]),
        clock=lambda: now[0],
    )
    assert tick(alerts, reader=reader)["poll"]["status"] == "unavailable"
    assert report(workspace)["rules"][0]["sample_status"] == "ok"
    response[0] = httpx.Response(503)
    now[0] += 300
    assert tick(alerts, reader=reader)["poll"]["status"] == "unavailable"
    failed = report(workspace)
    assert failed["status"] == "attention"
    assert failed["rules"][0]["sample_status"] == "error"
    assert failed["rules"][0]["consecutive_failed_polls"] == 1
    assert failed["rules"][0]["last_success_age_seconds"] == 300
    assert failed["rules"][0]["freshness"] == "fresh"
    now[0] += 300
    # Transport-valid but unusable item-price samples are tested through the actual bridge.
    assert (
        alerts.observe(
            1001,
            1,
            {
                "release_id": 1001,
                "currency": "EUR",
                "num_for_sale": 0,
                "lowest_item_price": None,
            },
            observed_at=now[0],
        )
        == "unknown"
    )
    assert report(workspace)["rules"][0]["sample_status"] == "unknown"
    response[0] = httpx.Response(200, json={"num_for_sale": 0, "lowest_price": None})
    now[0] += 300
    tick(alerts, reader=reader)
    recovered = report(workspace)
    assert recovered["status"] == "healthy"
    assert recovered["rules"][0]["consecutive_failed_polls"] == 0
    assert recovered["rules"][0]["last_success_age_seconds"] == 0
    assert recovered["queue"]["waiting_total"] == 0


def test_stale_boundary_projects_expiry_without_reconciling(workspace):
    path, now, alerts, _ = workspace
    save_rule(alerts)
    activate(alerts)
    observe(workspace)
    now[0] += FRESH_SECONDS - 1
    assert report(workspace)["rules"][0]["freshness"] == "fresh"
    now[0] += 1
    before = database_fingerprint(path)
    result = report(workspace)
    assert result["status"] == "attention"
    assert result["rules"][0]["freshness"] == "stale"
    assert result["queue"]["stored_states"]["pending"] == 1
    assert result["queue"]["effective_states"]["expired"] == 1
    assert database_fingerprint(path) == before


def test_retry_then_acceptance_and_uncertain_expired_lease(workspace):
    path, now, alerts, config = workspace
    save_rule(alerts)
    activate(alerts)
    observe(workspace)
    response = [httpx.Response(429, headers={"Retry-After": "900"})]
    sender = NtfySender(
        private_config=PrivateNtfyConfig(config["ntfy_config_file"]),
        enabled=True,
        transport=httpx.MockTransport(lambda request: response[0]),
    )
    assert deliver_one(alerts, sender)["state"] == "retry_wait"
    result = report(workspace)
    assert result["queue"]["effective_states"]["retry_wait"] == 1
    assert result["queue"]["next_retry_at"] == now[0] + 900
    assert result["queue"]["blocked_reasons"]["retry_or_send_delay"] == 1
    now[0] += 900
    response[0] = httpx.Response(200)
    assert deliver_one(alerts, sender)["state"] == "accepted"
    now[0] += 1
    observe(workspace, count=0)
    now[0] += 90
    observe(workspace)
    reservation = alerts.claim(sender.binding)
    assert reservation is not None
    assert report(workspace)["queue"]["effective_states"]["leased"] == 1
    now[0] += 1000
    before = database_fingerprint(path)
    result = report(workspace)
    assert result["queue"]["stored_states"]["leased"] == 1
    assert result["queue"]["effective_states"]["uncertain"] == 1
    assert result["queue"]["expired_leases_projected_uncertain"] == 1
    assert result["delivery"]["phone_delivery"] == "unverified"
    assert database_fingerprint(path) == before


def test_empty_disabled_and_never_polled_are_not_healthy(workspace):
    _, _, alerts, _ = workspace
    assert report(workspace)["status"] == "empty"
    save_rule(alerts, enabled=False)
    result = report(workspace)
    assert result["status"] == "disabled"
    activate(alerts)
    assert report(workspace)["status"] == "disabled"
    save_rule(alerts, 1002)
    result = report(workspace)
    assert result["status"] == "attention"
    assert result["rules"][1]["freshness"] == "never_succeeded"
    assert result["rules_enabled"] == 1


def test_backlog_controls_and_destination_mismatch_are_visible_without_ids(workspace):
    path, now, alerts, _ = workspace
    save_rule(alerts)
    activate(alerts)
    observe(workspace)
    now[0] += 300
    execute(
        path,
        "UPDATE discogs_price_control SET sends=?,send_day=?,next_send=?",
        (DAILY_SEND_CAP, int(now[0] // 86400), now[0] + 40),
    )
    execute(path, "UPDATE discogs_monitor_control SET next_request=?", (now[0] + 80,))
    execute(
        path,
        "UPDATE discogs_price_outbox SET destination_ref=?,provider_id='ntfy'",
        ("PRIVATE_DESTINATION_REFERENCE_NEVER_PRINT",),
    )
    execute(path, "CREATE TABLE cloud_control (id INTEGER PRIMARY KEY, killed INTEGER)")
    execute(path, "INSERT INTO cloud_control VALUES(1,1)")
    before = database_fingerprint(path)
    result = report(workspace)
    assert result["controls"]["cloud_kill_switch"] is True
    assert result["controls"]["cloud_kill_switch_controls_aggregate_alerts"] is False
    assert result["controls"]["daily_cap_reached"] is True
    assert result["controls"]["send_cooldown_seconds"] == 40
    assert result["controls"]["request_cooldown_seconds"] == 80
    assert result["queue"]["waiting_total"] == result["queue"]["due_waiting"] == 1
    assert result["queue"]["eligible_now"] == 0
    assert result["queue"]["destination_mismatch"] == 1
    assert "queue_backlog" in result["issues"]
    assert "PRIVATE_DESTINATION_REFERENCE_NEVER_PRINT" not in json.dumps(result)
    assert database_fingerprint(path) == before
    now[0] = (int(now[0] // 86400) + 1) * 86400
    assert report(workspace)["controls"]["send_attempts_today"] == 0


@pytest.mark.parametrize("status", [401, 403])
def test_e2e_access_restriction_stops_control_health_does_not_restart(workspace, status):
    path, _, alerts, config = workspace
    save_rule(alerts)
    activate(alerts)
    reader = AuthenticatedReleaseStatsReader(
        AppCredentials(config["discogs_app_file"]),
        transport=httpx.MockTransport(lambda request: httpx.Response(status)),
    )
    tick(alerts, reader=reader)
    before = database_fingerprint(path)
    result = report(workspace)
    assert result["status"] == "disabled"
    assert result["controls"]["enabled"] is False
    assert result["rules"][0]["consecutive_failed_polls"] == 1
    assert database_fingerprint(path) == before


def test_current_destination_config_validation_is_local_not_network(workspace):
    path, now, alerts, config = workspace
    save_rule(alerts)
    activate(alerts)
    observe(workspace, count=0)
    result = health.health_report(path, now=now[0], poll_seconds=300)
    assert result["status"] == "attention"
    assert result["configuration"]["destination"]["status"] == "not_checked"
    assert result["configuration"]["running_worker_configuration_verified"] is False
    assert result["configuration"]["network_checked"] is False
    config["ntfy_config_file"].chmod(0o644)
    assert report(workspace)["configuration"]["destination"]["status"] == "unavailable"
    config["discogs_app_file"].write_text('{"secret":"DO_NOT_PRINT_CONFIG"}')
    result = report(workspace)
    assert result["configuration"]["discogs_app"]["status"] == "unavailable"
    assert "DO_NOT_PRINT_CONFIG" not in json.dumps(result)


def test_public_topic_config_and_symlink_rejection(workspace, tmp_path):
    path, now, alerts, config = workspace
    save_rule(alerts)
    activate(alerts)
    observe(workspace, count=0)
    topic = tmp_path / "topic"
    topic.write_text("offline_fixture_destination_not_live\n")
    topic.chmod(0o600)
    result = health.health_report(
        path, now=now[0], topic_file=topic, discogs_app_file=config["discogs_app_file"]
    )
    assert result["configuration"]["destination"] == {"status": "valid", "kind": "public_ntfy"}
    link = tmp_path / "topic-link"
    link.symlink_to(topic)
    result = health.health_report(path, now=now[0], topic_file=link)
    assert result["configuration"]["destination"]["status"] == "unavailable"
    db_link = tmp_path / "db-link"
    db_link.symlink_to(path)
    assert health.health_report(db_link)["status"] == "unavailable"


@pytest.mark.parametrize("kind", ["missing", "foreign", "old", "corrupt"])
def test_unavailable_databases_are_never_created_migrated_or_printed(tmp_path, kind):
    path = tmp_path / "DO_NOT_PRINT_DATABASE_PATH"
    if kind == "foreign":
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE owner_data (secret TEXT)")
    elif kind == "old":
        RulesStore(path, create=True).close()
    elif kind == "corrupt":
        path.write_bytes(b"DO_NOT_PRINT_CORRUPT_CONTENT")
    before = path.read_bytes() if path.exists() else None
    result = health.health_report(path)
    assert result["status"] == "unavailable"
    assert "DO_NOT_PRINT" not in json.dumps(result)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE discogs_price_samples SET status='DO_NOT_PRINT_SECRET'",
        "UPDATE discogs_price_samples SET last_success='DO_NOT_PRINT_SECRET'",
        "UPDATE discogs_price_samples SET failures=-1",
        "UPDATE discogs_price_samples SET claim='secret',claim_until=NULL",
        "UPDATE discogs_price_rules SET data='DO_NOT_PRINT_SECRET'",
        "UPDATE discogs_price_outbox SET state='DO_NOT_PRINT_SECRET'",
        "UPDATE discogs_price_outbox SET state='leased',lease_until=NULL",
        "UPDATE discogs_price_control SET enabled=2",
        "DELETE FROM discogs_price_control",
    ],
)
def test_bad_persisted_data_fails_closed_and_redacts(workspace, sql):
    path, _, alerts, _ = workspace
    save_rule(alerts)
    activate(alerts)
    observe(workspace)
    execute(path, sql)
    before = database_fingerprint(path)
    result = report(workspace)
    assert result["status"] == "unavailable"
    assert "DO_NOT_PRINT" not in json.dumps(result)
    assert database_fingerprint(path) == before


def test_future_timestamps_and_expired_poll_claim_never_report_healthy(workspace):
    path, now, alerts, _ = workspace
    save_rule(alerts)
    activate(alerts)
    observe(workspace)
    execute(
        path,
        "UPDATE discogs_price_samples SET last_success=?,last_observed=?,"
        "claim='secret',claim_until=?",
        (now[0] + 60, now[0] + 60, now[0]),
    )
    result = report(workspace)
    assert result["status"] == "attention"
    assert result["rules"][0]["freshness"] == "future_timestamp"
    assert result["rules"][0]["poll_claim"] == "expired"
    assert result["queue"]["eligible_now"] == 0
    assert "secret" not in json.dumps(result)


def test_sqlite_connection_is_readonly_and_wal_commits_are_visible(workspace, monkeypatch):
    path, _, alerts, _ = workspace
    save_rule(alerts)
    activate(alerts)
    observe(workspace, count=0)
    original = sqlite3.connect
    writer = original(path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE discogs_price_samples SET failures=3,status='error'")
        writer.commit()
        before = database_fingerprint(path)
        connections = []

        def verify_read_only(*args, **kwargs):
            assert args[0].endswith("?mode=ro")
            assert kwargs["uri"] is True
            db = original(*args, **kwargs)
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                db.execute("UPDATE discogs_price_control SET enabled=0")
            db.rollback()
            connections.append(True)
            return db

        monkeypatch.setattr(health.sqlite3, "connect", verify_read_only)
        result = report(workspace)
        assert result["rules"][0]["consecutive_failed_polls"] == 3
        assert connections == [True]
        monkeypatch.setattr(health.sqlite3, "connect", original)
        assert database_fingerprint(path) == before
    finally:
        writer.close()


def test_cli_json_text_exit_codes_and_no_reconciliation(workspace, monkeypatch, capsys):
    path, now, alerts, config = workspace
    monkeypatch.setattr(health.time, "time", lambda: now[0])
    args = [
        "--database",
        str(path),
        "--poll-seconds",
        "300",
        "--discogs-app-file",
        str(config["discogs_app_file"]),
        "--ntfy-config-file",
        str(config["ntfy_config_file"]),
    ]
    assert health.main(args + ["--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "empty"
    save_rule(alerts)
    activate(alerts)
    observe(workspace, count=0)
    before = database_fingerprint(path)
    assert health.main(args) == 0
    text = capsys.readouterr().out
    assert "Aggregate monitor: healthy" in text
    assert "not proof of phone" in text
    assert health.main(args + ["--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "healthy"
    assert health.main(args + ["--poll-seconds", "299", "--format", "json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "unavailable"
    assert database_fingerprint(path) == before


def test_module_entrypoint_missing_database_is_safe_and_machine_readable(tmp_path):
    path = tmp_path / "absent.db"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "finder.discogs_price_health",
            "--database",
            str(path),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert process.returncode == 2
    assert json.loads(process.stdout)["status"] == "unavailable"
    assert process.stderr == ""
    assert not path.exists()
    assert str(path) not in process.stdout


def test_unexpected_health_exception_text_never_leaks(workspace, monkeypatch):
    def fail(*args):
        raise health.HealthUnavailable("DO_NOT_PRINT_UNEXPECTED_SECRET")

    monkeypatch.setattr(health, "_snapshot", fail)
    result = report(workspace)
    assert result["status"] == "unavailable"
    assert result["issues"] == ["health_evidence_unavailable"]
    assert "DO_NOT_PRINT" not in json.dumps(result)
