"""Scheduler orchestration exercised with synthetic data and mocked transports only."""

import json
import sqlite3

import httpx
import pytest
from test_discogs_inventory import listing, page, workspace  # noqa: F401
from test_discogs_inventory_notifications import mutate
from test_discogs_inventory_notifications import rig as notification_rig

from finder.discogs_inventory import STATE_TABLE, InventoryObserver
from finder.discogs_inventory_notifications import TABLE
from finder.discogs_inventory_step import main, run_step
from finder.discogs_read_guard import ReadSafetyHalt

rig = notification_rig


def step(rig, response, **kw):
    return run_step(
        rig[0],
        rig[2],
        rig[3],
        enabled=True,
        clock=rig[1],
        sleep=rig[1].sleep,
        transport=httpx.MockTransport(response),
        **kw,
    )


def no_http(request):
    pytest.fail("Unexpected source request")


def state(rig):
    with sqlite3.connect(rig[0]) as db:
        return json.loads(db.execute(f"SELECT data FROM {STATE_TABLE}").fetchone()[0])


def test_default_off_never_opens_database_or_credentials(rig, tmp_path, capsys):
    assert run_step("missing.db", rig[2])["state"] == "disabled"
    cfg = tmp_path / "config.json"
    cfg.write_text(rig[2].model_dump_json())
    assert main(["--config", str(cfg), "--private-notify-config", "missing.json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "disabled"
    assert not rig[4]


def test_end_to_end_silent_baseline_pending_drain_delivery_and_120_due(rig):
    path, clock, cfg, _, calls = rig
    cfg = cfg.model_copy(update={"poll_seconds": 120})
    rig = path, clock, cfg, rig[3], calls
    with sqlite3.connect(path) as db:
        db.execute(f"UPDATE {STATE_TABLE} SET config_hash=?", (cfg.fingerprint,))
        before = db.execute("SELECT * FROM discogs_price_rules").fetchall()
    mutate(rig, lambda s: s.update(baseline=False, seed_verified_at=None, seen=[], results=[]))
    requests = []
    fail_detail = True
    rows = [listing()]

    def respond(request):
        requests.append(request)
        if request.url.path.startswith("/users/"):
            return httpx.Response(200, json=page(rows))
        ident = int(request.url.path.rsplit("/", 1)[1])
        if request.url.query and fail_detail:
            return httpx.Response(503)
        return httpx.Response(200, json=listing(ident))

    first = step(rig, respond)
    assert first["source"] == "baseline" and first["delivery"] == "idle"
    assert not calls
    assert step(rig, no_http)["source"] == "not_due"
    clock.now = state(rig)["next_poll"]
    rows.append(listing(101))
    second = step(rig, respond)
    assert second["source"] == "unavailable" and second["delivery"] == "not_run"
    assert state(rig)["pending"][0]["listing_id"] == 101
    assert not calls
    clock.now = state(rig)["next_poll"]
    fail_detail = False
    requests.clear()
    third = step(rig, respond)
    assert third["operation"] == "drain_pending" and third["source"] == "pending_details"
    assert third["delivery"] == "accepted" and third["delivered"] is False
    assert len(requests) == 1 and requests[0].url.path == "/marketplace/listings/101"
    assert len(calls) == 1 and not state(rig)["pending"]
    assert "Both records included" not in json.dumps(third)
    assert "fixture-seller" not in json.dumps(third)
    clock.now = state(rig)["next_poll"]
    assert step(rig, respond)["delivery"] == "idle"
    assert len(calls) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM discogs_price_rules").fetchall() == before
        assert len(before) == 19
        assert db.execute(f"SELECT state FROM {TABLE}").fetchone()[0] == "accepted"


@pytest.mark.parametrize("field", ["denied", "in_progress"])
def test_source_stop_never_dispatches(rig, field):
    mutate(rig, lambda s: s.update({field: True}))
    with pytest.raises(ReadSafetyHalt):
        step(rig, no_http)
    assert not rig[4]


def test_source_exception_blocks_existing_fresh_candidate(rig, monkeypatch):
    def fail(self, **kw):
        raise RuntimeError("private provider payload must never be logged")

    monkeypatch.setattr(InventoryObserver, "poll", fail)
    with pytest.raises(RuntimeError):
        step(rig, no_http)
    assert not rig[4]


def test_shared_cooldown_blocks_source_and_delivery(rig):
    with sqlite3.connect(rig[0]) as db:
        db.execute("UPDATE discogs_monitor_control SET next_request=?", (rig[1]() + 1200,))
    result = step(rig, no_http)
    assert result["source"] == "cooldown" and result["delivery"] == "not_run"
    assert not rig[4]


@pytest.mark.parametrize("code,expected", [(403, "access_denied"), (429, "cooldown")])
def test_source_denial_or_rate_limit_blocks_delivery(rig, code, expected):
    result = step(rig, lambda r: httpx.Response(code, headers={"Retry-After": "300"}))
    assert result["source"] == expected and result["delivery"] == "not_run"
    assert not rig[4]


def test_stale_candidate_never_delivered(rig):
    rig[1].now += 120
    result = step(rig, lambda r: httpx.Response(200, json=page([])))
    assert result["source"] == "unavailable" and result["delivery"] == "not_run"
    assert not rig[4]


def test_uncertain_notification_never_replayed(rig):
    sends = []

    def timeout(request):
        sends.append(request)
        raise httpx.ReadTimeout("synthetic uncertainty")

    rig[3]._transport = httpx.MockTransport(timeout)
    result = step(rig, lambda r: httpx.Response(200, json=page([])))
    assert result["delivery"] == "uncertain"
    rig[1].now = state(rig)["next_poll"]
    assert step(rig, lambda r: httpx.Response(200, json=page([])))["delivery"] == "idle"
    assert len(sends) == 1
    with sqlite3.connect(rig[0]) as db:
        assert db.execute(f"SELECT state FROM {TABLE}").fetchone()[0] == "uncertain"


def test_config_fingerprint_mismatch_never_calls_network(rig):
    with pytest.raises(ReadSafetyHalt):
        run_step(
            rig[0],
            rig[2].model_copy(update={"poll_seconds": 120}),
            rig[3],
            enabled=True,
            transport=httpx.MockTransport(no_http),
            clock=rig[1],
        )
    assert not rig[4]


def test_cli_sanitizes_exception_and_has_no_snapshot_test_or_init(
    rig, tmp_path, monkeypatch, capsys
):
    import finder.discogs_inventory_step as module

    cfg = tmp_path / "config.json"
    cfg.write_text(rig[2].model_dump_json())
    monkeypatch.setattr(module, "PrivateNtfyConfig", lambda _: None)
    monkeypatch.setattr(module, "NtfySender", lambda **kw: rig[3])

    def fail(*args, **kwargs):
        raise RuntimeError("private seller payload secret")

    monkeypatch.setattr(module, "run_step", fail)
    assert (
        main(
            [
                "--config",
                str(cfg),
                "--run-step",
                "--database",
                str(rig[0]),
                "--private-notify-config",
                "unused",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "private" not in output and "secret" not in output
    for flag in ("--snapshot", "--test-key", "--init-state", "--reset"):
        with pytest.raises(SystemExit):
            main(["--config", str(cfg), flag])


def test_missing_outbox_stops_before_source_work(rig):
    before = state(rig)
    with sqlite3.connect(rig[0]) as db:
        db.execute(f"DROP TABLE {TABLE}")
    with pytest.raises(sqlite3.Error):
        step(rig, no_http)
    assert state(rig) == before and not rig[4]


@pytest.mark.parametrize("prune", [False, True])
def test_stale_unattempted_result_is_requalified_with_same_identity(rig, prune):
    original = state(rig)["results"][0]
    rig[1].now += 21601 if prune else 120
    if prune:
        InventoryObserver(rig[0], rig[2], clock=rig[1]).results()
        assert state(rig)["results"][0]["result"] is None
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=listing())

    result = step(rig, respond)
    assert result["operation"] == "drain_pending" and result["delivery"] == "accepted"
    assert len(requests) == 1 and requests[0].url.path == "/marketplace/listings/100"
    refreshed = state(rig)["results"][0]
    for key in ("event_key", "origin", "discovered_at"):
        assert refreshed[key] == original[key]
    assert refreshed["checked_at"] > original["checked_at"]


def test_two_qualifying_ids_are_delivered_in_separate_due_steps(rig):
    mutate(rig, lambda s: s.update(seen=[100], results=[]))
    reads = []

    def respond(request):
        reads.append(request)
        if request.url.path.startswith("/users/"):
            return httpx.Response(200, json=page([listing(), listing(101), listing(102)]))
        return httpx.Response(200, json=listing(int(request.url.path.rsplit("/", 1)[1])))

    assert step(rig, respond)["delivery"] == "accepted"
    assert [v["listing_id"] for v in state(rig)["pending"]] == [102]
    rig[1].now = state(rig)["next_poll"]
    reads.clear()
    result = step(rig, respond)
    assert result["operation"] == "drain_pending" and result["delivery"] == "accepted"
    assert len(reads) == 1 and len(rig[4]) == 2 and not state(rig)["pending"]


def test_pacing_race_leaves_unattempted_result_for_later_guarded_refresh(rig):
    mutate(rig, lambda s: s.update(seen=[100], results=[]))

    def race(request):
        if request.url.path.startswith("/users/"):
            return httpx.Response(200, json=page([listing(), listing(101)]))
        with sqlite3.connect(rig[0]) as db:
            db.execute("UPDATE discogs_price_control SET next_send=?", (rig[1]() + 1000,))
        return httpx.Response(200, json=listing(101))

    assert step(rig, race)["delivery"] == "idle"
    original = state(rig)["results"][0]
    rig[1].now = state(rig)["next_poll"]
    before = state(rig)
    assert step(rig, no_http)["delivery"] == "notification_deferred"
    assert state(rig) == before and not rig[4]
    rig[1].now += 1001
    assert step(rig, lambda r: httpx.Response(200, json=listing(101)))["delivery"] == "accepted"
    refreshed = state(rig)["results"][0]
    assert refreshed["event_key"] == original["event_key"]
    assert refreshed["discovered_at"] == original["discovered_at"]
    assert len(rig[4]) == 1


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "failed", "uncertain", "leased"])
def test_any_attempted_event_is_never_requeued(rig, outcome):
    from finder.discogs_inventory_notifications import InventoryNotifications

    bridge = InventoryNotifications(rig[0], rig[2], enabled=True, clock=rig[1])
    assert bridge.claim(rig[3])
    with sqlite3.connect(rig[0]) as db:
        db.execute(f"UPDATE {TABLE} SET state=?,lease_until=NULL", (outcome,))
    rig[1].now += 601
    result = step(rig, lambda r: httpx.Response(200, json=page([])))
    assert result["operation"] == "poll" and result["delivery"] == "idle"
    assert not state(rig)["pending"] and not rig[4]


def test_snapshot_stale_result_is_never_requeued(rig):
    mutate(rig, lambda s: s["results"][0].update(origin="snapshot"))
    rig[1].now += 601
    assert step(rig, lambda r: httpx.Response(200, json=page([])))["operation"] == "poll"
    assert not state(rig)["pending"] and not rig[4]


@pytest.mark.parametrize("limit", [0, 3, True, 1.5])
def test_observer_rejects_unbounded_detail_override(rig, limit):
    with pytest.raises(ValueError):
        InventoryObserver(
            rig[0], rig[2], clock=rig[1], transport=httpx.MockTransport(no_http)
        ).poll(detail_limit=limit)


@pytest.mark.parametrize("hold", ["pacing", "daily", "lock"])
def test_notification_admission_preserves_source_before_any_read(rig, hold):
    from contextlib import nullcontext

    from finder.discogs_notification_lock import notification_lock
    from finder.discogs_price_alerts import DAILY_SEND_CAP

    before = state(rig)
    with sqlite3.connect(rig[0]) as db:
        if hold == "pacing":
            db.execute("UPDATE discogs_price_control SET next_send=?", (rig[1]() + 300,))
        if hold == "daily":
            db.execute(
                "UPDATE discogs_price_control SET send_day=?,sends=?",
                (int(rig[1]() // 86400), DAILY_SEND_CAP),
            )
    with notification_lock(rig[0]) if hold == "lock" else nullcontext():
        result = step(rig, no_http)
    assert result["state"] == "deferred"
    assert result["delivery"] == ("busy" if hold == "lock" else "notification_deferred")
    assert state(rig) == before and not rig[4]


@pytest.mark.parametrize("kind", ["criteria_blocked", "expired_unknown", "expired_blocked"])
def test_nonqualifying_or_unknown_expired_records_are_not_refreshed(rig, kind):
    def change(source):
        row = source["results"][0]
        row["state"] = "criteria_blocked" if kind == "criteria_blocked" else "expired"
        if kind == "expired_blocked":
            row["expired_from"] = "criteria_blocked"

    mutate(rig, change)
    rig[1].now += 21601
    result = step(rig, lambda r: httpx.Response(200, json=page([])))
    assert result["operation"] == "poll" and result["delivery"] == "idle"
    assert not state(rig)["pending"] and not rig[4]
