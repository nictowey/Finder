"""Offline-only helper checks: synthetic DB, simulated clock, intercepted HTTP."""

import json
import sqlite3
import stat
from contextlib import closing, contextmanager
from types import SimpleNamespace

import httpx
import pytest

from finder import discogs_read_guard
from finder.discogs_app_auth import AppCredentials
from finder.discogs_listing import ReadBudgetBusy
from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_read_guard import DatabaseReadBudget, ReadSafetyHalt, guard_row
from finder.discogs_rules import RulesStore
from scripts import sniper_polling_canary as helper


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    now = [1800000000.0]
    monkeypatch.setattr(
        helper,
        "time",
        SimpleNamespace(
            time=lambda: now[0],
            monotonic=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        ),
    )
    source = tmp_path / "source.db"
    RulesStore(source, create=True).close()
    alerts = AggregatePriceAlerts(source, clock=lambda: now[0], poll_seconds=300)
    for release in range(1, 21):
        alerts.save_rule(
            PriceLeadRule(
                release_id=release if release < 20 else helper.DISABLED_RELEASE,
                public_title=f"Fixture {release}",
                currency="USD",
                any_price=True,
                enabled=release < 20,
            ),
            expected_revision=0,
        )
    with closing(sqlite3.connect(source)) as db, db:
        db.execute(
            "UPDATE discogs_price_rules SET revision=2 WHERE release_id=?",
            (helper.DISABLED_RELEASE,),
        )
    alerts.set_enabled(True, expected_revision=0)
    app = tmp_path / "app.json"
    app.write_text(json.dumps({"consumer_key": "FixtureKey", "consumer_secret": "FixtureSecret"}))
    app.chmod(0o600)
    return source, app, now


def state(path):
    # Compare ALL source tables/columns except the four explicitly allowed fields.
    with closing(sqlite3.connect(path)) as db:
        result = {}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            columns = [
                column[1]
                for column in db.execute(f'PRAGMA table_info("{name}")')
                if (name, column[1]) not in helper.SOURCE_WRITES
            ]
            selection = ",".join(f'"{column}"' for column in columns)
            result[name] = db.execute(f'SELECT {selection} FROM "{name}"').fetchall()
        return result


def guard(path):
    with closing(sqlite3.connect(path, timeout=0)) as db:
        return guard_row(db)


def report():
    return {
        "requests": 0,
        "poll_attempts": 0,
        "responses": [],
        "poll_statuses": {},
        "cycles": [],
        "live_api_gate_updates": 0,
    }


def test_private_copy_only_seeds_scheduling_and_cleans_all_sidecars(fixture):
    source, _, _ = fixture
    before = state(source)
    with helper.private_copy(source) as path:
        directory = path.parent
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        helper.scope_and_seed(path)
        for suffix in ("-wal", "-shm", ".ntfy-send.lock"):
            path.with_name(path.name + suffix).touch()
    assert not directory.exists()
    assert state(source) == before


def test_shared_gate_write_allowlist_and_user_change_fence(fixture):
    source, _, now = fixture
    with closing(sqlite3.connect(source)) as db:
        expected = helper.intent_fingerprint(db)
    result = report()
    gate = helper.LiveReadGate(source, result, expected)
    before = state(source)
    with gate.request_budget() as budget:
        budget.reserve()
        assert guard(source)[0] == budget.token
        with pytest.raises(ReadBudgetBusy):
            budget.reserve()
        budget.response(180)
    assert gate.deadline() == now[0] + 180
    assert state(source) == before
    for query in (
        "UPDATE discogs_price_control SET next_send=0",
        "UPDATE discogs_price_control SET enabled=0,revision=99",
        "UPDATE discogs_monitor_control SET enabled=0",
        "UPDATE discogs_price_samples SET next_check=0",
        "DELETE FROM discogs_price_rules",
        "UPDATE discogs_api_read_guard SET schema_version=2",
        "UPDATE discogs_api_guard_metadata SET schema_version=2",
        "DELETE FROM discogs_api_read_guard",
        "INSERT INTO discogs_api_guard_metadata VALUES(2,1)",
        "CREATE TABLE unrelated(value)",
        "PRAGMA synchronous=OFF",
        "PRAGMA journal_mode=MEMORY",
        "PRAGMA application_id=0",
    ):
        with pytest.raises(sqlite3.DatabaseError):
            with gate.connection() as db:
                db.execute(query)
    now[0] += 181
    with gate.request_budget() as budget:
        budget.reserve()
        with closing(sqlite3.connect(source)) as db, db:
            db.execute("UPDATE discogs_price_control SET revision=revision+1")
        budget.response(300)  # Finalize a known hold even after intent changes.
    with pytest.raises(helper.CanaryStop, match="live_intent_changed"):
        with gate.request_budget() as budget:
            budget.reserve()
    assert gate.deadline() == now[0] + 300


def test_three_real_algorithm_cycles_offline_and_disabled_row_never_requested(fixture, monkeypatch):
    source, app, now = fixture
    before = state(source)
    calls = []

    def handle(request):
        release = int(request.url.path.rsplit("/", 1)[1])
        assert release < 20
        assert request.url.host == "api.discogs.com"
        assert request.url.path.startswith("/marketplace/stats/")
        assert guard(source)[0] is not None
        assert guard(path)[0] is not None
        calls.append(release)
        now[0] += 0.17
        return httpx.Response(
            200,
            headers={"X-Discogs-Ratelimit": "60", "X-Discogs-Ratelimit-Remaining": "59"},
            json={"num_for_sale": 1, "lowest_price": {"value": 5, "currency": "USD"}},
        )

    monkeypatch.setattr(helper.httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(handle))
    result = report()
    with helper.private_copy(source) as path:
        helper.run_copy(path, source, AppCredentials(app), result)
    assert result["status"] == "three_cycles_observed"
    assert calls == list(range(1, 20)) * 3
    assert result["requests"] == 57
    assert guard(source)[0] is None
    assert result["live_api_gate_updates"] == 114
    assert len(result["cycles"]) == 3
    assert all(len(rule["success_at"]) == 3 for rule in result["rules"])
    assert all(
        all(interval >= 120 for interval in rule["interval_seconds"]) for rule in result["rules"]
    )
    assert result["elapsed_seconds"] < 480
    assert state(source) == before
    encoded = json.dumps(result, allow_nan=False)
    assert "FixtureSecret" not in encoded and "Authorization" not in encoded


def test_provider_hold_reaches_live_and_copy_before_body(fixture, monkeypatch):
    source, app, now = fixture
    result = report()
    with helper.private_copy(source) as path:

        class Body(httpx.SyncByteStream):
            def __iter__(self):
                for database in (source, path):
                    assert guard(database)[0] is None
                    with closing(sqlite3.connect(database)) as db:
                        assert (
                            db.execute(
                                "SELECT next_request FROM discogs_monitor_control"
                            ).fetchone()[0]
                            >= now[0] + 180
                        )
                yield b'{"num_for_sale":1,"lowest_price":{"value":5,"currency":"USD"}}'

        monkeypatch.setattr(
            helper.httpx,
            "HTTPTransport",
            lambda **kwargs: httpx.MockTransport(
                lambda _: httpx.Response(200, headers={"Retry-After": "180"}, stream=Body())
            ),
        )
        with pytest.raises(helper.CanaryStop):
            helper.run_copy(path, source, AppCredentials(app), result)
    assert result["requests"] <= 3


def test_existing_long_hold_is_not_cleared_or_requested(fixture, monkeypatch):
    source, app, now = fixture
    with closing(sqlite3.connect(source)) as db, db:
        db.execute("UPDATE discogs_monitor_control SET next_request=?", (now[0] + 86400,))
    monkeypatch.setattr(
        helper.httpx,
        "HTTPTransport",
        lambda **kwargs: httpx.MockTransport(lambda _: pytest.fail("network must not run")),
    )
    result = report()
    with helper.private_copy(source) as path:
        with pytest.raises(helper.CanaryStop, match="provider_hold_exceeds_canary_window"):
            helper.run_copy(path, source, AppCredentials(app), result)
    assert result["requests"] == 0
    assert result["live_api_gate_updates"] == 0


def test_explicit_flag_required_before_opening_any_input(tmp_path):
    with pytest.raises(SystemExit):
        helper.main(
            [
                "--database",
                str(tmp_path / "absent"),
                "--discogs-app-file",
                str(tmp_path / "absent-app"),
            ]
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "query",
    [
        "UPDATE discogs_price_control SET enabled=0,revision=revision+1",
        "DELETE FROM discogs_price_rules WHERE release_id=19",
        "UPDATE discogs_price_rules SET revision=revision+1 WHERE release_id=1",
        "UPDATE discogs_price_rules SET data=replace(data,'Fixture 1','Changed 1') "
        "WHERE release_id=1",
    ],
)
def test_each_real_request_aborts_after_user_changes(fixture, monkeypatch, query):
    source, app, _ = fixture
    result = report()

    def handle(request):
        with closing(sqlite3.connect(source)) as db, db:
            db.execute(query)
        return httpx.Response(200, json={"num_for_sale": 0, "lowest_price": None})

    monkeypatch.setattr(helper.httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(handle))
    with helper.private_copy(source) as path:
        with pytest.raises(helper.CanaryStop, match="live_intent_changed"):
            helper.run_copy(path, source, AppCredentials(app), result)
    assert result["requests"] == 1


def test_bounded_context_raises_and_restores_signals(monkeypatch):
    import signal

    before = signal.getsignal(signal.SIGALRM)
    alarms = []
    monkeypatch.setattr(helper.signal, "setitimer", lambda *args: alarms.append(args))
    with pytest.raises(helper.CanaryStop, match="deadline_reached"):
        with helper.bounded_run():
            signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
    assert signal.getsignal(signal.SIGALRM) == before
    assert alarms == [(signal.ITIMER_REAL, 480), (signal.ITIMER_REAL, 0)]


def test_main_blocker_is_redacted_and_copy_is_removed(fixture, monkeypatch, capsys):
    source, app, now = fixture
    with closing(sqlite3.connect(source)) as db, db:
        db.execute("UPDATE discogs_monitor_control SET next_request=?", (now[0] + 86400,))
    monkeypatch.setattr(
        helper.httpx,
        "HTTPTransport",
        lambda **kwargs: httpx.MockTransport(lambda _: pytest.fail("network must not run")),
    )
    created = []
    real_temp = helper.tempfile.TemporaryDirectory

    def temporary(**kwargs):
        directory = real_temp(**kwargs)
        created.append(directory.name)
        return directory

    monkeypatch.setattr(helper.tempfile, "TemporaryDirectory", temporary)
    assert (
        helper.main(
            ["--database", str(source), "--discogs-app-file", str(app), "--run-live-read-canary"]
        )
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    assert result["blocker"] == "provider_hold_exceeds_canary_window"
    assert result["notifications_attempted"] == 0
    assert result["requests"] == 0
    assert result["source_write_allowlist"] == sorted(
        ".".join(item) for item in helper.SOURCE_WRITES
    )
    from pathlib import Path

    assert created and all(not Path(path).exists() for path in created)


@pytest.mark.parametrize("column,value", [("release_id", 11398561), ("revision", 3)])
def test_wrong_disabled_identity_or_revision_blocks_before_network(
    fixture, monkeypatch, column, value
):
    source, app, _ = fixture
    with closing(sqlite3.connect(source)) as db, db:
        db.execute(
            f"UPDATE discogs_price_rules SET {column}=? WHERE release_id=?",
            (value, helper.DISABLED_RELEASE),
        )
    monkeypatch.setattr(
        helper.httpx, "HTTPTransport", lambda **kwargs: pytest.fail("must not construct network")
    )
    with helper.private_copy(source) as path:
        with pytest.raises(helper.CanaryStop, match="rule_scope_changed"):
            helper.run_copy(path, source, AppCredentials(app), report())


def test_transport_cleanup_even_when_reader_constructor_fails(fixture, monkeypatch):
    source, app, _ = fixture
    closed = []
    monkeypatch.setattr(helper.RecordingTransport, "finish", lambda self: closed.append(True))

    def fail(*args, **kwargs):
        raise ValueError("fixture-only constructor failure")

    monkeypatch.setattr(helper, "CoordinatedStatsReader", fail)
    with helper.private_copy(source) as path:
        with pytest.raises(ValueError):
            helper.run_copy(path, source, AppCredentials(app), report())
    assert closed == [True]


def run_subprocess_probe(code, args):
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", code, *map(str, args)],
        text=True,
        capture_output=True,
        timeout=4,
        check=True,
    )
    return json.loads(result.stdout)


def test_real_sqlite_exclusive_lock_stops_and_parent_cleans(fixture, tmp_path):
    source, app, _ = fixture
    scratch = tmp_path / "supervised"
    scratch.mkdir()
    result = run_subprocess_probe(
        """
import json, sqlite3, sys, tempfile, time
from pathlib import Path
from scripts import sniper_polling_canary as h
source, app, scratch = map(Path, sys.argv[1:])
tempfile.tempdir = str(scratch)
h.MAX_SECONDS = .1
with sqlite3.connect(source) as lock:
    lock.execute("BEGIN EXCLUSIVE")
    begin = time.monotonic()
    result = h.supervised_run(source, app)
    elapsed = time.monotonic() - begin
print(json.dumps({"status": result["status"], "elapsed": elapsed,
                  "clean": not list(scratch.iterdir())}))
""",
        [source, app, scratch],
    )
    assert result["status"] == "blocked"
    assert result["elapsed"] < 2
    assert result["clean"]


def test_real_busy_backup_progress_honors_deadline(fixture, tmp_path):
    source, _, _ = fixture
    result = run_subprocess_probe(
        """
import json, sqlite3, sys, time
from scripts import sniper_polling_canary as h
source, destination = sys.argv[1:]
begin = time.monotonic()
with (sqlite3.connect(source) as lock,
      sqlite3.connect(source, timeout=0) as reader,
      sqlite3.connect(destination, timeout=0) as writer):
    lock.execute("BEGIN EXCLUSIVE")
    try:
        h.backup_snapshot(reader, writer, time.monotonic() + .1)
        result = "unexpected_success"
    except h.CanaryStop as error:
        result = str(error)
print(json.dumps({"result": result, "elapsed": time.monotonic() - begin}))
""",
        [source, tmp_path / "backup.db"],
    )
    assert result["result"] == "deadline_reached"
    assert result["elapsed"] < 2


def test_supervisor_kills_uncooperative_child_and_removes_sidecars(tmp_path):
    result = run_subprocess_probe(
        """
import json, os, signal, sys, tempfile, time
from pathlib import Path
from scripts import sniper_polling_canary as h
scratch = Path(sys.argv[1])
tempfile.tempdir = str(scratch)
h.MAX_SECONDS = .1
def stuck(source, app, directory, output):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGALRM, signal.SIG_IGN)
    for name in ("canary.db", "canary.db-wal", "canary.db-shm", "canary.db.ntfy-send.lock"):
        fd = os.open(Path(directory) / name, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    while True:
        time.sleep(60)
h.execute_child = stuck
begin = time.monotonic()
result = h.supervised_run(Path("unused"), Path("unused"))
print(json.dumps({"blocker": result.get("blocker"),
                  "elapsed": time.monotonic() - begin, "clean": not list(scratch.iterdir())}))
""",
        [tmp_path],
    )
    assert result["blocker"] == "deadline_reached"
    assert result["elapsed"] < 2.5
    assert result["clean"]


def test_missing_source_guard_never_implicitly_migrated(fixture):
    source, _, _ = fixture
    with closing(sqlite3.connect(source)) as db, db:
        db.execute("DROP TABLE discogs_api_read_guard")
        db.execute("DROP TABLE discogs_api_guard_metadata")
    before = state(source)
    with pytest.raises(ReadSafetyHalt, match="api_guard_unavailable"):
        with helper.private_copy(source):
            pytest.fail("missing source schema must not be copied or repaired")
    assert state(source) == before


def test_private_copy_rejects_nonprivate_parent_directory(fixture, tmp_path):
    source, _, _ = fixture
    directory = tmp_path / "not-private"
    directory.mkdir(mode=0o755)
    with pytest.raises(helper.CanaryStop, match="private_directory_permissions_invalid"):
        with helper.private_copy(source, directory=directory):
            pytest.fail("must not copy into accessible directory")
    assert not list(directory.iterdir())


@contextmanager
def prepared_reader(fixture, handle, *, checkpoint=None):
    source, app, now = fixture
    with helper.private_copy(source) as path, closing(sqlite3.connect(path, timeout=0)) as db:
        result = helper.new_report()
        gate = helper.LiveReadGate(source, result, helper.intent_fingerprint(db), checkpoint)
        reader = helper.CoordinatedStatsReader(
            AppCredentials(app),
            gate,
            transport=httpx.MockTransport(handle),
            clock=lambda: now[0],
            wall_clock=lambda: now[0],
        )
        copied = DatabaseReadBudget(db, clock=lambda: now[0])
        yield SimpleNamespace(
            source=source, path=path, db=db, gate=gate, reader=reader, copied=copied, report=result
        )


def test_both_guards_commit_before_http_and_finalize_normal_headers(fixture):
    source, _, now = fixture

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            assert guard(source)[0] is None
            assert guard(context.path)[0] is None
            yield b'{"num_for_sale":0,"lowest_price":null}'

    def handle(request):
        for path in (source, context.path):
            token, since, _ = guard(path)  # A new connection sees the committed token.
            assert token is not None and since == now[0]
        assert not context.db.in_transaction
        return httpx.Response(200, stream=Body())

    with prepared_reader(fixture, handle) as context:
        context.reader.read(1, "USD", budget=context.copied)
        assert context.report["source_guard"]["state"] == "ready"
        assert context.report["copied_guard"]["state"] == "ready"
        assert context.report["live_api_gate_updates"] == 2


@pytest.mark.parametrize("wait", [False, True])
def test_local_cooldown_does_not_allocate_either_guard(fixture, wait):
    source, _, now = fixture
    with prepared_reader(fixture, lambda _: pytest.fail("no HTTP")) as context:
        context.reader.budget.defer(60)
        with pytest.raises(ReadBudgetBusy):
            context.reader._request(
                "/marketplace/stats/1",
                params={"curr_abbr": "USD"},
                budget=context.copied,
                wait=wait,
            )
        assert guard(source)[0] is None
        assert guard(context.path)[0] is None
        assert now[0] == 1800000000


def test_source_busy_after_copy_reservation_stops_without_clearing(fixture):
    source, _, _ = fixture
    with prepared_reader(fixture, lambda _: pytest.fail("no HTTP")) as context:
        with closing(sqlite3.connect(source, timeout=0)) as lock:
            lock.execute("BEGIN IMMEDIATE")
            with pytest.raises(ReadSafetyHalt, match="source_api_reservation_incomplete"):
                context.reader.read(1, "USD", budget=context.copied)
            lock.rollback()
        assert guard(source)[0] is None
        assert guard(context.path)[0] is not None
        assert context.report["copied_guard"]["state"] == "pending"


@pytest.mark.parametrize("target", ["source", "copy"])
def test_reservation_sql_failure_never_sends_http_or_clears_committed_token(fixture, target):
    source, _, _ = fixture
    with prepared_reader(fixture, lambda _: pytest.fail("no HTTP")) as context:
        database = source if target == "source" else context.path
        with closing(sqlite3.connect(database)) as db, db:
            db.execute(
                "CREATE TRIGGER block_reservation BEFORE UPDATE ON discogs_api_read_guard "
                "WHEN NEW.pending_token IS NOT NULL BEGIN SELECT RAISE(ABORT,'fixture'); END"
            )
        with pytest.raises(ReadSafetyHalt, match="api_guard_reservation_failed"):
            context.reader.read(1, "USD", budget=context.copied)
        assert guard(source)[0] is None
        assert (guard(context.path)[0] is not None) == (target == "source")


def test_source_fingerprint_check_holds_reservation_transaction(fixture):
    source, _, _ = fixture
    with closing(sqlite3.connect(source)) as db:
        expected = helper.intent_fingerprint(db)
    gate = helper.LiveReadGate(source, report(), expected)
    checks = []
    original = gate.check_intent

    def check(db):
        assert db.in_transaction
        with closing(sqlite3.connect(source, timeout=0)) as other:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("UPDATE discogs_price_control SET revision=revision+1")
        original(db)
        checks.append(True)

    gate.check_intent = check
    with gate.request_budget() as budget:
        budget.reserve()
        budget.response(3)
    assert checks == [True]


@pytest.mark.parametrize("target", ["source", "copy"])
def test_permanent_header_sql_failure_still_finalizes_other_guard(fixture, target):
    source, _, now = fixture
    checkpoints = []

    def handle(request):
        return httpx.Response(200, headers={"Retry-After": "180"}, json={})

    with prepared_reader(
        fixture,
        handle,
        checkpoint=lambda: checkpoints.append(json.loads(json.dumps(context.report))),
    ) as context:
        database = source if target == "source" else context.path
        with closing(sqlite3.connect(database)) as db, db:
            db.execute(
                "CREATE TRIGGER block_finalization BEFORE UPDATE ON discogs_api_read_guard "
                "WHEN NEW.pending_token IS NULL BEGIN SELECT RAISE(ABORT,'fixture'); END"
            )
        with pytest.raises(ReadSafetyHalt, match="canary_api_guard_finalization_failed"):
            context.reader.read(1, "USD", budget=context.copied)
        other = context.path if target == "source" else source
        assert guard(database)[0] is not None
        assert guard(other)[0] is None
        assert guard(other)[2] >= now[0] + 180
        assert context.reader.budget.next_read >= now[0] + 180
        assert any(item.get("provider_hold", {}).get("state") == "pending" for item in checkpoints)
        assert context.report["header_finalization_failures"] == [
            "source" if target == "source" else "copied"
        ]
        assert context.report["provider_hold"]["state"] == (
            "pending" if target == "source" else "persisted"
        )


def test_real_busy_header_persistence_is_bounded_and_leaves_source_pending(fixture, monkeypatch):
    import time

    source, _, now = fixture
    monkeypatch.setattr(discogs_read_guard, "PERSIST_SECONDS", 0.08)
    lock = sqlite3.connect(source, timeout=0)

    def handle(request):
        lock.execute("BEGIN IMMEDIATE")
        return httpx.Response(200, headers={"Retry-After": "180"}, json={})

    try:
        with prepared_reader(fixture, handle) as context:
            start = time.monotonic()
            with pytest.raises(ReadSafetyHalt, match="canary_api_guard_finalization_failed"):
                context.reader.read(1, "USD", budget=context.copied)
            assert 0.07 <= time.monotonic() - start < 1
            assert guard(context.path)[0] is None
            assert guard(context.path)[2] >= now[0] + 180
            assert guard(source)[0] is not None
            assert context.reader.budget.next_read >= now[0] + 180
            assert context.report["provider_hold"]["state"] == "pending"
    finally:
        lock.rollback()
        lock.close()
    # Reopening/restarting, even after a long delay, does not release pending.
    now[0] += 86400
    with closing(sqlite3.connect(source)) as db:
        with pytest.raises(ReadSafetyHalt, match="api_guard_recovery_required"):
            DatabaseReadBudget(db, clock=lambda: now[0]).reserve()


def test_real_transient_busy_header_failure_eventually_finalizes_both(fixture):
    import threading

    source, _, now = fixture
    lock = sqlite3.connect(source, timeout=0, check_same_thread=False)
    timer = None

    def handle(request):
        nonlocal timer
        lock.execute("BEGIN IMMEDIATE")
        timer = threading.Timer(0.07, lock.rollback)
        timer.start()
        return httpx.Response(
            200,
            headers={"Retry-After": "180"},
            json={"num_for_sale": 0, "lowest_price": None},
        )

    try:
        with prepared_reader(fixture, handle) as context:
            context.reader.read(1, "USD", budget=context.copied)
            for path in (source, context.path):
                assert guard(path)[0] is None
                assert guard(path)[2] >= now[0] + 180
            assert context.report["provider_hold"]["state"] == "persisted"
    finally:
        if timer is not None:
            timer.join()
        lock.close()


def test_busy_timeout_restored_after_source_reservation_and_response(fixture):
    source, _, _ = fixture
    with closing(sqlite3.connect(source)) as db:
        expected = helper.intent_fingerprint(db)
    gate = helper.LiveReadGate(source, report(), expected)
    with gate.request_budget() as budget:
        budget.db.execute("PRAGMA busy_timeout=4321")
        budget.reserve()
        assert budget.db.execute("PRAGMA busy_timeout").fetchone()[0] == 4321
        budget.response(3)
        assert budget.db.execute("PRAGMA busy_timeout").fetchone()[0] == 4321


def test_preheader_failure_retains_both_tokens_and_blocks_source_restart(fixture):
    source, _, now = fixture
    calls = []

    def handle(request):
        calls.append(True)
        raise httpx.ConnectError("fixture-only error")

    with prepared_reader(fixture, handle) as context:
        with pytest.raises(ReadSafetyHalt, match="api_guard_response_unknown"):
            context.reader.read(1, "USD", budget=context.copied)
        assert guard(source)[0] is not None
        assert guard(context.path)[0] is not None
        assert context.report["response_outcome"] == "unknown"
    # Fresh guards also see the durable pending request. No clock-based recovery.
    with closing(sqlite3.connect(source)) as db:
        with pytest.raises(ReadBudgetBusy):
            DatabaseReadBudget(db, clock=lambda: now[0]).reserve()
        now[0] += 3600
        with pytest.raises(ReadSafetyHalt, match="api_guard_recovery_required"):
            DatabaseReadBudget(db, clock=lambda: now[0]).reserve()
    assert calls == [True]


def test_stale_token_cannot_clear_source_and_copy_still_finalizes(fixture):
    source, _, now = fixture
    replacement = "b" * 32

    def handle(request):
        with closing(sqlite3.connect(source)) as db, db:
            db.execute(
                "UPDATE discogs_api_read_guard SET pending_token=?,known_deadline=?",
                (replacement, now[0] + 86400),
            )
        return httpx.Response(200, headers={"Retry-After": "180"}, json={})

    with prepared_reader(fixture, handle) as context:
        with pytest.raises(ReadSafetyHalt, match="canary_api_guard_finalization_failed"):
            context.reader.read(1, "USD", budget=context.copied)
        assert guard(source)[0] == replacement
        assert guard(source)[2] == now[0] + 86400
        assert guard(context.path)[0] is None
        assert guard(context.path)[2] >= now[0] + 180
        assert replacement not in json.dumps(context.report)


def test_checkpoint_failure_does_not_skip_either_header_finalization(fixture):
    source, _, _ = fixture

    def checkpoint():
        if context.report.get("provider_hold"):
            raise OSError("fixture-only disk failure")

    with prepared_reader(
        fixture,
        lambda _: httpx.Response(200, headers={"Retry-After": "180"}, json={}),
        checkpoint=checkpoint,
    ) as context:
        with pytest.raises(ReadSafetyHalt, match="canary_api_guard_finalization_failed"):
            context.reader.read(1, "USD", budget=context.copied)
        assert guard(source)[0] is None
        assert guard(context.path)[0] is None
        assert context.report["provider_hold"]["state"] == "persisted"


def test_only_one_bounded_wait_before_copy_commit_and_none_before_source(fixture):
    source, _, now = fixture
    with prepared_reader(fixture, lambda _: pytest.fail("no HTTP")) as context:
        with context.db:
            context.db.execute("UPDATE discogs_monitor_control SET next_request=?", (now[0] + 1,))
        with closing(sqlite3.connect(source)) as db, db:
            db.execute("UPDATE discogs_monitor_control SET next_request=?", (now[0] + 2,))
        context.copied.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
        with pytest.raises(ReadSafetyHalt, match="source_api_reservation_incomplete"):
            context.reader._request(
                "/marketplace/stats/1",
                params={"curr_abbr": "USD"},
                budget=context.copied,
                wait=True,
            )
        assert now[0] == 1800000001
        assert guard(source)[0] is None
        assert guard(context.path)[0] is not None


def test_existing_known_guard_deadline_is_not_shortened(fixture, monkeypatch):
    source, app, now = fixture
    with closing(sqlite3.connect(source)) as db, db:
        db.execute("UPDATE discogs_api_read_guard SET known_deadline=?", (now[0] + 86400,))
    monkeypatch.setattr(
        helper.httpx, "HTTPTransport", lambda **kwargs: pytest.fail("must not construct HTTP")
    )
    with helper.private_copy(source) as path:
        with pytest.raises(helper.CanaryStop, match="provider_hold_exceeds_canary_window"):
            helper.run_copy(path, source, AppCredentials(app), report())
    assert guard(source) == (None, None, now[0] + 86400)


@pytest.mark.parametrize("checkpoint_hold", [False, True])
def test_supervisor_killed_child_preserves_pending_guard_and_hold_diagnostics(
    fixture, tmp_path, checkpoint_hold
):
    source, app, _ = fixture
    scratch = tmp_path / "supervisor-scratch"
    scratch.mkdir()
    result = run_subprocess_probe(
        """
import json, signal, sys, tempfile, time
from pathlib import Path
from scripts import sniper_polling_canary as h
from finder.discogs_read_guard import DatabaseReadBudget, guard_row
from contextlib import closing
import sqlite3
source, app, scratch = map(Path, sys.argv[1:4])
checkpoint_hold = sys.argv[4] == "True"
tempfile.tempdir = str(scratch)
h.MAX_SECONDS = .2
def stuck(source, app, directory, output):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGALRM, signal.SIG_IGN)
    with h.private_copy(source, directory=directory) as path:
        with closing(sqlite3.connect(path)) as db:
            report = h.new_report()
            gate = h.LiveReadGate(source, report, h.intent_fingerprint(db),
                                 lambda: h.write_report(output, report))
            with gate.request_budget() as live:
                copied = DatabaseReadBudget(db)
                budget = h.CoordinatedReadBudget(copied, live, gate)
                budget.reserve()
                if checkpoint_hold:
                    gate.note_hold(180)
                else:
                    output.unlink()  # Prove parent consults source even without a checkpoint.
                for suffix in ("-wal", "-shm", ".ntfy-send.lock"):
                    path.with_name(path.name + suffix).touch()
                while True:
                    time.sleep(60)
h.execute_child = stuck
begin = time.monotonic()
result = h.supervised_run(source, app)
with closing(sqlite3.connect(source)) as db:
    pending = guard_row(db)[0] is not None
    try:
        DatabaseReadBudget(db).reserve()
        blocked_restart = False
    except Exception:
        blocked_restart = True
print(json.dumps({"report": result, "pending": pending, "blocked_restart": blocked_restart,
                  "elapsed": time.monotonic() - begin, "clean": not list(scratch.iterdir())}))
""",
        [source, app, scratch, checkpoint_hold],
    )
    assert result["elapsed"] < 2.5
    assert result["clean"]
    assert result["pending"] and result["blocked_restart"]
    assert result["report"]["manual_recovery_required"]
    assert result["report"]["source_guard"]["state"] == "pending"
    assert result["report"]["resume_requires_operator_check"]
    assert "pending_token" not in result["report"]["source_guard"]
    if checkpoint_hold:
        assert result["report"]["blocker"] == "provider_hold_unpersisted"
        assert result["report"]["provider_hold"]["state"] == "pending"
        assert result["report"]["provider_hold"]["deadline"] > 0
        assert result["report"]["stop_reason"] == "deadline_reached"
    else:
        assert result["report"]["blocker"] == "deadline_reached"


def test_run_copy_rejects_source_or_hardlink_before_seeding(fixture, tmp_path):
    source, app, _ = fixture
    alias = tmp_path / "source-alias.db"
    alias.hardlink_to(source)
    before = state(source)
    for path in (source, alias):
        with pytest.raises(helper.CanaryStop, match="copy_is_source"):
            helper.run_copy(path, source, AppCredentials(app), report())
    assert state(source) == before


def test_seeding_changes_only_enabled_copy_next_check_fields(fixture):
    source, _, now = fixture
    with helper.private_copy(source) as path, closing(sqlite3.connect(path)) as db:
        before = state(path)
        sample_columns = [row[1] for row in db.execute("PRAGMA table_info(discogs_price_samples)")]
        enabled, _ = helper.scope_and_seed(path)
        after = state(path)
        next_check = sample_columns.index("next_check")
        release_id = sample_columns.index("release_id")
        expected_samples = []
        for sample in before["discogs_price_samples"]:
            expected = list(sample)
            if sample[release_id] in enabled:
                expected[next_check] = now[0]
            expected_samples.append(tuple(expected))
        before["discogs_price_samples"] = expected_samples
        assert after == before


def test_blocked_final_sqlite_inspection_is_killed_and_preserves_checkpoint(fixture, tmp_path):
    source, app, _ = fixture
    scratch = tmp_path / "bounded-inspection"
    scratch.mkdir()
    result = run_subprocess_probe(
        """
import json, multiprocessing, os, signal, sys, tempfile, time
from pathlib import Path
from scripts import sniper_polling_canary as h
source, app, scratch = map(Path, sys.argv[1:])
tempfile.tempdir = str(scratch)
h.MAX_SECONDS = .2
h.INSPECT_SECONDS = .1
parent_pid = os.getpid()
def completed(source, app, directory, output):
    report = h.new_report()
    report["source_guard"] = {"state": "pending", "pending_since": 1,
                              "known_deadline": 1234567890, "manual_recovery_required": True}
    report["provider_hold"] = {"state": "pending", "deadline": 2345678901}
    h.write_report(output, report)
h.execute_child = completed
def blocked_sqlite(*args, **kwargs):
    assert os.getpid() != parent_pid, "source SQLite must never run in parent"
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(60)
h.sqlite3.connect = blocked_sqlite
begin = time.monotonic()
report = h.supervised_run(source, app)
print(json.dumps({"report": report, "elapsed": time.monotonic() - begin,
                  "children": len(multiprocessing.active_children()),
                  "clean": not list(scratch.iterdir())}))
""",
        [source, app, scratch],
    )
    assert 1 <= result["elapsed"] < 2.5
    assert result["children"] == 0 and result["clean"]
    report = result["report"]
    assert report["source_inspection"] == "deadline_reached"
    assert report["source_guard"]["state"] == "unavailable"
    assert report["source_guard"]["known_deadline"] == 1234567890
    assert report["provider_hold"] == {"state": "pending", "deadline": 2345678901}
    assert report["blocker"] == "provider_hold_unpersisted"
    assert report["manual_recovery_required"]
    assert not report["counters_complete"]


@pytest.mark.parametrize("second_signal", ["SIGTERM", "SIGINT"])
def test_double_interrupt_cannot_abandon_term_ignoring_child(fixture, tmp_path, second_signal):
    source, app, _ = fixture
    scratch = tmp_path / "double-interruption"
    scratch.mkdir()
    result = run_subprocess_probe(
        """
import contextlib, io, json, multiprocessing, os, signal, sqlite3, sys, tempfile, threading, time
from pathlib import Path
from scripts import sniper_polling_canary as h
from finder.discogs_read_guard import DatabaseReadBudget, guard_row
source, app, scratch = map(Path, sys.argv[1:4])
second_signal = getattr(signal, sys.argv[4])
tempfile.tempdir = str(scratch)
h.MAX_SECONDS = 5
ready = multiprocessing.get_context("fork").Event()
def stuck(source, app, directory, output):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    with h.private_copy(source, directory=directory) as path:
        with contextlib.closing(sqlite3.connect(path)) as db:
            report = h.new_report()
            gate = h.LiveReadGate(source, report, h.intent_fingerprint(db),
                                 lambda: h.write_report(output, report))
            with gate.request_budget() as live:
                h.CoordinatedReadBudget(DatabaseReadBudget(db), live, gate).reserve()
                gate.note_hold(180)
                for suffix in ("-wal", "-shm", ".ntfy-send.lock"):
                    path.with_name(path.name + suffix).touch()
                ready.set()
                while True:
                    time.sleep(60)
h.execute_child = stuck
before = {kind: signal.getsignal(kind) for kind in (signal.SIGTERM, signal.SIGINT)}
def interrupts():
    if not ready.wait(2):
        return
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(.1)
    os.kill(os.getpid(), second_signal)
thread = threading.Thread(target=interrupts, daemon=True)
thread.start()
stream = io.StringIO()
begin = time.monotonic()
with contextlib.redirect_stdout(stream):
    status = h.main(["--database", str(source), "--discogs-app-file", str(app),
                     "--run-live-read-canary"])
thread.join(1)
with contextlib.closing(sqlite3.connect(source)) as db:
    pending = guard_row(db)[0] is not None
print(json.dumps({"report": json.loads(stream.getvalue()), "status": status,
                  "elapsed": time.monotonic() - begin, "pending": pending,
                  "children": len(multiprocessing.active_children()),
                  "signals_restored": all(signal.getsignal(kind) == handler
                                          for kind, handler in before.items()),
                  "clean": not list(scratch.iterdir())}))
""",
        [source, app, scratch, second_signal],
    )
    assert 1 <= result["elapsed"] < 2.5
    assert result["children"] == 0 and result["clean"]
    assert result["signals_restored"]
    assert result["status"] == 2 and result["pending"]
    report = result["report"]
    assert report["blocker"] == "provider_hold_unpersisted"
    assert report["stop_reason"] == "interrupted"
    assert report["source_guard"]["state"] == "pending"
    assert report["manual_recovery_required"]
    assert not report["counters_complete"]
    assert report["requests"] == 0
    assert report["request_count_semantics"] == "recorded_transport_attempts_lower_bound"


def test_repeated_pending_hold_reconciliation_keeps_original_stop_reason():
    report = helper.new_report()
    report.update(blocker="interrupted", provider_hold={"state": "pending", "deadline": 123})
    helper.preserve_pending_hold(report)
    helper.preserve_pending_hold(report)
    assert report["stop_reason"] == "interrupted"
