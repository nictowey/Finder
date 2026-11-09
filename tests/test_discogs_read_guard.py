"""Offline write-ahead guard, restart, crash and storage-failure regressions."""

import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing

import httpx
import pytest

from finder import discogs_read_guard as module
from finder.discogs_listing import ListingReadError, ReleaseStatsReader
from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_price_health import health_report
from finder.discogs_read_guard import (
    DatabaseReadBudget,
    ReadSafetyHalt,
    guard_row,
    initialize_guard,
)
from finder.discogs_rules import RulesStore


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "rules.db"
    RulesStore(path, create=True).close()
    now = [1800000000.0]
    alerts = AggregatePriceAlerts(path, clock=lambda: now[0], poll_seconds=120)
    alerts.save_rule(
        PriceLeadRule(
            release_id=123, public_title="Fixture", currency="USD", any_price=True, enabled=True
        ),
        expected_revision=0,
    )
    alerts.set_enabled(True, expected_revision=0)
    return path, now, alerts


def reader(now, handle):
    return ReleaseStatsReader(
        transport=httpx.MockTransport(handle), clock=lambda: now[0], wall_clock=lambda: now[0]
    )


def stored(path):
    with closing(sqlite3.connect(path)) as db:
        return guard_row(db)


@pytest.mark.parametrize("status", [200, 429, 503, 403])
def test_guard_committed_before_http_and_finalized_at_every_header(workspace, status):
    path, now, alerts = workspace

    def handle(request):
        token, since, _ = stored(path)
        assert token and since == now[0]
        return httpx.Response(status, json={"num_for_sale": 0, "lowest_price": None})

    assert alerts.poll_once(reader(now, handle))["requests"] == 1
    token, since, deadline = stored(path)
    assert token is since is None
    assert deadline >= now[0] + (60 if status in (429, 503) else 3)


def test_unknown_preheader_failure_survives_restart_and_never_expires(workspace):
    path, now, alerts = workspace

    def fail(request):
        raise httpx.ConnectError("private transport detail must not appear")

    with pytest.raises(ReadSafetyHalt, match="response_unknown"):
        alerts.poll_once(reader(now, fail))
    before = stored(path)
    reopened = AggregatePriceAlerts(path, clock=lambda: now[0], poll_seconds=120)
    assert stored(path) == before
    assert reopened.poll_once(reader(now, lambda _: pytest.fail("no network")))["requests"] == 0
    now[0] += 1000000
    with pytest.raises(ReadSafetyHalt, match="recovery_required"):
        reopened.poll_once(reader(now, lambda _: pytest.fail("no network")))
    assert stored(path) == before
    report = health_report(path, now=now[0], poll_seconds=120)
    assert report["controls"]["api_guard"]["state"] == "pending"
    assert report["controls"]["request_manual_attention"]
    assert before[0] not in json.dumps(report)


@pytest.mark.parametrize("release_lock", [True, False])
def test_header_hold_with_transient_or_permanent_database_lock(
    workspace, monkeypatch, release_lock
):
    path, now, alerts = workspace
    monkeypatch.setattr(module, "PERSIST_SECONDS", 0.25)
    lock = sqlite3.connect(path, check_same_thread=False)
    timer = None

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            assert stored(path)[0] is None
            assert stored(path)[2] >= now[0] + 180
            yield b'{"num_for_sale":0,"lowest_price":null}'

    def handle(request):
        nonlocal timer
        lock.execute("BEGIN IMMEDIATE")
        if release_lock:
            timer = threading.Timer(0.08, lock.rollback)
            timer.start()
        return httpx.Response(200, headers={"Retry-After": "180"}, stream=Body())

    client = reader(now, handle)
    try:
        if release_lock:
            assert alerts.poll_once(client)["requests"] == 1
        else:
            with pytest.raises(ReadSafetyHalt, match="hold_unpersisted"):
                alerts.poll_once(client)
            assert stored(path)[0] is not None
            assert client.budget.next_read >= now[0] + 180
    finally:
        if timer:
            timer.join()
        lock.rollback()
        lock.close()
    if not release_lock:
        now[0] += 31
        with pytest.raises(ReadSafetyHalt):
            AggregatePriceAlerts(path, clock=lambda: now[0], poll_seconds=120).poll_once(client)


def test_stale_token_cannot_clear_another_request_or_shorten_hold(workspace):
    path, now, _ = workspace
    with closing(sqlite3.connect(path)) as db:
        budget = DatabaseReadBudget(db, clock=lambda: now[0])
        budget.reserve()
        with db:
            db.execute("UPDATE discogs_api_read_guard SET pending_token=?", ("f" * 32,))
            db.execute("UPDATE discogs_monitor_control SET next_request=?", (now[0] + 3600,))
        with pytest.raises(ReadSafetyHalt, match="token_changed"):
            budget.response(180)
        assert guard_row(db)[0] == "f" * 32
        assert (
            db.execute("SELECT next_request FROM discogs_monitor_control").fetchone()[0]
            == now[0] + 3600
        )


class FailingCommit(sqlite3.Connection):
    fail = False
    committed_failure = False

    def __exit__(self, kind, error, trace):
        if self.fail and kind is None:
            if self.committed_failure:
                super().__exit__(kind, error, trace)
            else:
                self.rollback()
            self.fail = False
            raise sqlite3.OperationalError("simulated disk failure")
        return super().__exit__(kind, error, trace)


@pytest.mark.parametrize("after_headers", [False, True])
@pytest.mark.parametrize("committed", [False, True])
def test_failed_or_uncertain_commit_never_loses_safety(workspace, after_headers, committed):
    path, now, _ = workspace
    with closing(sqlite3.connect(path, factory=FailingCommit)) as db:
        budget = DatabaseReadBudget(db, clock=lambda: now[0])
        db.committed_failure = committed
        if after_headers:
            budget.reserve()
        db.fail = True
        with pytest.raises(ReadSafetyHalt):
            budget.response(180) if after_headers else budget.reserve()
        token, _, known = guard_row(db)
        if after_headers and committed:
            assert token is None and known >= now[0] + 180
        elif after_headers or committed:
            assert token is not None
        else:
            assert token is None  # Nothing committed; reserve raised before any HTTP.
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE discogs_api_read_guard",
        "DROP TABLE discogs_api_guard_metadata",
        "DELETE FROM discogs_api_read_guard",
        "UPDATE discogs_api_read_guard SET schema_version=99",
        "UPDATE discogs_api_read_guard SET pending_token='malformed'",
        "UPDATE discogs_api_read_guard SET known_deadline=-1",
    ],
)
def test_corrupt_or_missing_guard_never_repaired_by_restart(workspace, sql):
    path, now, _ = workspace
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(sql)
    with pytest.raises(ReadSafetyHalt):
        AggregatePriceAlerts(path, clock=lambda: now[0], poll_seconds=120)
    report = health_report(path, now=now[0], poll_seconds=120)
    assert report["controls"]["api_guard"]["state"] == "unavailable"
    assert report["controls"]["request_manual_attention"]


def test_known_expiry_allows_next_request_but_unknown_never_does(workspace):
    path, now, _ = workspace
    with closing(sqlite3.connect(path)) as db:
        budget = DatabaseReadBudget(db, clock=lambda: now[0])
        budget.reserve()
        budget.response(180)
        now[0] += 179
        with pytest.raises(ListingReadError):
            budget.reserve()
        now[0] += 1
        budget.reserve()
        assert guard_row(db)[0] is not None
        assert guard_row(db)[2] == now[0]
        now[0] += 1000000
        with pytest.raises(ReadSafetyHalt):
            DatabaseReadBudget(db, clock=lambda: now[0]).reserve()


def test_guard_migration_is_additive_default_off_and_preserves_existing_budget(tmp_path):
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA application_id=1178882643")
        db.execute(
            "CREATE TABLE discogs_monitor_control(id INTEGER,revision INTEGER,next_request REAL)"
        )
        db.execute("INSERT INTO discogs_monitor_control VALUES(1,7,1800001000)")
        db.commit()
        initialize_guard(db)
        assert guard_row(db) == (None, None, 0)
        assert db.execute("SELECT * FROM discogs_monitor_control").fetchone() == (1, 7, 1800001000)
        initialize_guard(db)
        assert guard_row(db) == (None, None, 0)


def test_busy_timeout_is_restored_and_unsafe_durability_rejects_network(workspace):
    path, now, _ = workspace
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA busy_timeout=4321")
        budget = DatabaseReadBudget(db, clock=lambda: now[0])
        budget.reserve()
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 4321
        budget.response(180)
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 4321
        db.execute("PRAGMA synchronous=OFF")
        with pytest.raises(ReadSafetyHalt, match="durability"):
            DatabaseReadBudget(db)


@pytest.mark.parametrize("at_headers", [False, True])
def test_real_process_crash_preserves_committed_pending(workspace, at_headers):
    path, now, _ = workspace
    code = """
import os, signal, sqlite3, sys
import httpx
from finder.discogs_read_guard import DatabaseReadBudget
from finder.discogs_listing import ReleaseStatsReader
path, at_headers = sys.argv[1:]
db = sqlite3.connect(path)
class CrashBudget(DatabaseReadBudget):
    def response(self, seconds):
        os.kill(os.getpid(), signal.SIGKILL)
budget = CrashBudget(db, clock=lambda: 1800000000)
if at_headers == 'True':
    transport = httpx.MockTransport(lambda _: httpx.Response(200,
        headers={'Retry-After':'180'}, json={}))
    reader = ReleaseStatsReader(transport=transport, clock=lambda:1800000000,
        wall_clock=lambda:1800000000)
    reader.read(123, 'USD', budget=budget)
else:
    budget.reserve()
    os._exit(0)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(path), str(at_headers)], capture_output=True, timeout=4
    )
    assert result.returncode == (-9 if at_headers else 0)
    assert stored(path)[0] is not None
    now[0] += 31
    with pytest.raises(ReadSafetyHalt):
        AggregatePriceAlerts(path, clock=lambda: now[0], poll_seconds=120).poll_once(
            reader(now, lambda _: pytest.fail("no HTTP"))
        )


def test_worker_has_explicit_safety_halt_exit(workspace, monkeypatch, capsys):
    from finder import discogs_price_worker as worker

    path, now, alerts = workspace
    with closing(sqlite3.connect(path)) as db:
        DatabaseReadBudget(db, clock=lambda: now[0]).reserve()
    now[0] += 31
    monkeypatch.setattr(worker, "AggregatePriceAlerts", lambda *args, **kwargs: alerts)
    assert worker.main(["--database", str(path), "--poll", "--poll-seconds", "120"]) == 78
    assert json.loads(capsys.readouterr().out)["manual_recovery_required"] is True


def test_normal_contention_never_counts_http_or_creates_rule_failures(workspace):
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    path, now, alerts = workspace
    alerts.save_rule(
        PriceLeadRule(
            release_id=124,
            public_title="Other fixture",
            currency="USD",
            any_price=True,
            enabled=True,
        ),
        expected_revision=0,
    )
    barrier = threading.Barrier(2)
    entered, finish = threading.Event(), threading.Event()
    calls = []

    class DelayedReader(ReleaseStatsReader):
        def read(self, *args, **kwargs):
            barrier.wait(timeout=5)
            return super().read(*args, **kwargs)

    def handle(request):
        calls.append(request.url.path)
        entered.set()
        assert finish.wait(8)
        return httpx.Response(200, json={"num_for_sale": 0, "lowest_price": None})

    def poll():
        return alerts.poll_once(
            DelayedReader(
                transport=httpx.MockTransport(handle),
                clock=lambda: now[0],
                wall_clock=lambda: now[0],
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(poll), pool.submit(poll)]
        try:
            entered_in_time = entered.wait(5)
            if not entered_in_time:
                for future in futures:
                    if future.done():
                        future.result()  # Surface worker failures before a timing assertion.
            assert entered_in_time
            done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
            assert len(done) == 1
            assert next(iter(done)).result() == {"status": "cooldown", "requests": 0}
        finally:
            finish.set()
        results = [future.result() for future in futures]
    assert sum(result["requests"] for result in results) == len(calls) == 1
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute(
            "SELECT failures,status,next_check FROM discogs_price_samples "
            "WHERE last_success IS NULL"
        ).fetchall()
        assert rows == [(0, "waiting", 0)]


def test_local_cooldown_never_commits_pending_guard_or_adds_a_wait(workspace):
    from finder.discogs_listing import PublicReadBudget, ReadBudgetBusy

    path, now, _ = workspace
    waits = []
    local = PublicReadBudget(clock=lambda: now[0], sleep=waits.append)
    local.defer(60)
    with closing(sqlite3.connect(path)) as db:
        budget = DatabaseReadBudget(db, clock=lambda: now[0], sleep=waits.append)
        with pytest.raises(ReadBudgetBusy):
            budget.reserve_for(local, wait=True)
        assert guard_row(db)[0] is None
        assert db.execute("SELECT next_request FROM discogs_monitor_control").fetchone()[0] == 0
    assert waits == []


@pytest.mark.parametrize("kind", ["claim_callback", "defer_callback", "subclass"])
def test_custom_local_budget_is_rejected_before_transaction_or_callback(workspace, kind):
    from finder.discogs_listing import PublicReadBudget

    path, now, _ = workspace

    def forbidden(*args, **kwargs):
        pytest.fail("custom local callback must never be invoked")

    class CustomBudget(PublicReadBudget):
        reserve = forbidden

    local = (
        CustomBudget()
        if kind == "subclass"
        else PublicReadBudget(
            claim=forbidden if kind == "claim_callback" else None,
            defer=forbidden if kind == "defer_callback" else None,
        )
    )
    with closing(sqlite3.connect(path)) as db:
        budget = DatabaseReadBudget(db, clock=lambda: now[0])
        with pytest.raises(ReadSafetyHalt, match="local_budget_unsupported"):
            budget.reserve_for(local, wait=True)
        assert not db.in_transaction
        assert guard_row(db)[0] is None
        assert db.execute("SELECT next_request FROM discogs_monitor_control").fetchone()[0] == 0
