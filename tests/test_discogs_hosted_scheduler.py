"""Disposable state and synthetic pages; no Discogs network or saved session."""

import json
import os
import socket
import stat
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_cloud_app import ORIGIN, verifier
from test_discogs_release_snapshots import listing
from test_discogs_rules import target

from finder import discogs_hosted_scheduler as hosted_module
from finder.cloud_app import CloudApp
from finder.discogs_hosted_scheduler import (
    MIN_INTERVAL,
    HostedReadError,
    HostedReleaseScheduler,
    HostedSocketReader,
)
from finder.discogs_release_snapshots import FIELD_NAMES
from finder.discogs_rules import RulesStore

LOCAL_SOCKET_CONNECT = socket.socket.connect


def page(now, ids=(1001,), *, complete=True):
    rows = [listing(ident, price="$10.00", media="Very Good Plus (VG+)") for ident in ids]
    return {
        "source": "discogs_rendered_release",
        "purchase_allowed": False,
        "release_id": 123,
        "url": "https://www.discogs.com/sell/release/123",
        "observed_at": datetime.fromtimestamp(now, UTC).isoformat(),
        "status": "observed" if complete else "incomplete",
        "complete": complete,
        "range_start": 1 if rows else 0,
        "range_end": len(rows),
        "total": len(rows),
        "page": 1,
        "has_next": False,
        "rows": rows,
        "assertions": {
            "release_identity": True,
            "listing_container": True,
            "pagination": True,
            "stable": True,
        },
    }


@pytest.fixture
def setup(tmp_path):
    database = tmp_path / "rules.db"
    RulesStore(database, create=True).close()
    CloudApp(database, ORIGIN, verifier())
    clock = [datetime(2026, 10, 25, tzinfo=UTC).timestamp()]
    store = RulesStore(database)
    store.save(target().model_dump(), 0)
    with store.db:
        store.db.execute("UPDATE cloud_control SET killed=0,revision=1")
    store.close()
    return database, clock


def scheduler(database, clock, reader):
    return HostedReleaseScheduler(database, reader, clock=lambda: clock[0])


def resume(worker):
    worker.set_paused(False, expected_revision=worker.health()["revision"])


def change_ids(database):
    store = RulesStore(database)
    try:
        return [
            tuple(row)
            for row in store.db.execute(
                "SELECT kind,listing_id FROM discogs_release_changes ORDER BY sequence"
            )
        ]
    finally:
        store.close()


def test_disabled_by_default_and_requires_saved_enabled_unowned_target(setup):
    database, clock = setup
    calls = []
    worker = scheduler(database, clock, lambda ident: calls.append(ident))
    assert worker.tick()["status"] == "idle"
    resume(worker)
    store = RulesStore(database)
    store.mark_owned(123, True, 1)
    store.close()
    assert worker.tick()["status"] == "idle"
    assert calls == []


def test_unwired_reader_stops_if_schedule_was_resumed(setup):
    database, clock = setup
    worker = scheduler(database, clock, None)
    resume(worker)
    assert worker.tick()["code"] == "reader_unavailable"
    assert worker.health()["paused"] is True


def test_local_rule_editor_without_cloud_control_reports_stopped(tmp_path):
    database = tmp_path / "rules.db"
    RulesStore(database, create=True).close()
    worker = HostedReleaseScheduler(database, reader=None)
    assert worker.health()["kill_switch"] is True
    resume(worker)
    assert worker.tick()["status"] == "idle"


def test_restart_cadence_baseline_new_reappeared_and_duplicate_suppression(setup):
    database, clock = setup
    pages = [(1001,), (1001, 1002), (1001,), (1001, 1002), (1001, 1002)]

    def read(_):
        return page(clock[0], pages.pop(0))

    worker = scheduler(database, clock, read)
    resume(worker)
    assert worker.tick()["status"] == "baseline"
    assert change_ids(database) == []
    assert scheduler(database, clock, read).tick()["status"] == "idle"
    for expected in ("changed", "changed", "changed", "unchanged"):
        clock[0] += MIN_INTERVAL + 1
        assert scheduler(database, clock, read).tick()["status"] == expected
    assert change_ids(database) == [
        ("new", "1002"),
        ("disappeared", "1002"),
        ("reappeared", "1002"),
    ]
    assert worker.health()["purchase_allowed"] is False


@pytest.mark.parametrize("failure", ["incomplete", "socket_error", "challenge"])
def test_failed_and_partial_reads_preserve_baseline_and_backoff(setup, failure):
    database, clock = setup

    def read(_):
        if failure == "incomplete":
            return page(clock[0], (), complete=False)
        if failure == "socket_error":
            raise OSError("synthetic disconnect")
        raise HostedReadError("access_restricted", stop=True)

    worker = scheduler(database, clock, lambda _: page(clock[0]))
    resume(worker)
    assert worker.tick()["status"] == "baseline"
    clock[0] += MIN_INTERVAL + 1
    worker.reader = read
    assert worker.tick()["status"] == "blocked"
    assert worker.health()["targets"][0]["failures"] == 1
    assert worker.snapshots.snapshot()["releases"][0]["baseline_revision"] == 1
    assert change_ids(database) == []
    assert worker.tick()["status"] == "idle"
    assert worker.health()["paused"] is (failure == "challenge")


@pytest.mark.parametrize("change", ["pause", "rules", "control", "baseline"])
def test_inflight_revision_or_pause_change_rejects_observation(setup, change):
    database, clock = setup
    worker = None

    def read(_):
        store = RulesStore(database)
        try:
            if change == "pause":
                worker.set_paused(True, expected_revision=worker.health()["revision"])
            elif change == "rules":
                store.save(target().model_dump(), 1)
            elif change == "control":
                with store.db:
                    store.db.execute("UPDATE cloud_control SET revision=revision+1")
            else:
                # A concurrent accepted baseline advances the optimistic revision.
                with store.db:
                    store.db.execute(
                        "INSERT INTO discogs_release_baselines "
                        "(release_id,revision,attempted_at,status) VALUES(123,1,?, 'observed')",
                        (clock[0] - 1,),
                    )
        finally:
            store.close()
        return page(clock[0])

    worker = scheduler(database, clock, read)
    resume(worker)
    assert worker.tick()["status"] == "blocked"
    assert change_ids(database) == []
    assert worker.health()["last_error"] == "read_or_revision_failed"


def test_socket_client_fixed_action_and_rejects_wrong_or_incomplete_response(monkeypatch):
    path = Path(f"/tmp/sniper-dry-{uuid.uuid4().hex[:12]}.sock")
    captured = []
    response = {
        "status": "observed",
        "source": "hosted_browser_read_only",
        "stop_required": True,
        "purchase_allowed": False,
        "observation": page(datetime(2026, 10, 25, tzinfo=UTC).timestamp()),
    }
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    path.chmod(0o600)
    server.listen(1)
    server.settimeout(2)

    def serve():
        connection, _ = server.accept()
        with connection:
            captured.append(connection.recv(512))
            connection.sendall((json.dumps(response) + "\n").encode())
        server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(socket.socket, "connect", LOCAL_SOCKET_CONNECT)
        reader = HostedSocketReader(path)
        assert reader.ready() is True
        assert reader(123)["release_id"] == 123
    finally:
        thread.join(timeout=2)
        server.close()
        path.unlink(missing_ok=True)
    assert json.loads(captured[0]) == {"action": "observe_release", "release_id": "123"}
    assert all(field in response["observation"]["rows"][0]["fields"] for field in FIELD_NAMES)


def test_socket_reader_assembles_only_verified_pages(monkeypatch):
    now = datetime(2026, 10, 25, tzinfo=UTC).timestamp()
    first = page(now, (1001,))
    first.update(page=1, range_start=1, range_end=1, total=2, has_next=True)
    first = {key: value for key, value in first.items() if key not in {"status", "complete"}}
    second = {
        **first,
        "url": "https://www.discogs.com/sell/release/123?page=2",
        "page": 2,
        "range_start": 2,
        "range_end": 2,
        "has_next": False,
        "rows": [listing(1002)],
    }
    response = {
        "status": "observed",
        "source": "hosted_browser_read_only",
        "stop_required": True,
        "purchase_allowed": False,
        "pages": [first, second],
    }
    for pages, valid in (([first, second], True), ([first], False)):
        response["pages"] = pages
        path = Path(f"/tmp/sniper-dry-{uuid.uuid4().hex[:12]}.sock")
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(path))
        path.chmod(0o600)
        server.listen(1)
        server.settimeout(2)

        def serve(server=server, response=response):
            connection, _ = server.accept()
            with connection:
                connection.recv(512)
                connection.sendall((json.dumps(response) + "\n").encode())
            server.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            monkeypatch.setattr(socket.socket, "connect", LOCAL_SOCKET_CONNECT)
            reader = HostedSocketReader(path)
            if valid:
                assert reader(123)["coverage_pages"] == 2
            else:
                with pytest.raises(HostedReadError, match="unverified_page_coverage"):
                    reader(123)
        finally:
            thread.join(timeout=2)
            server.close()
            path.unlink(missing_ok=True)


def test_socket_reader_rejects_nonprivate_path_without_connecting(tmp_path):
    path = tmp_path / "reader.sock"
    path.write_text("not a socket")
    reader = HostedSocketReader(path)
    assert reader.ready() is False
    with pytest.raises(HostedReadError, match="socket_not_private"):
        reader(123)


def test_shared_socket_requires_fixed_owner_group_and_directory(monkeypatch):
    path = hosted_module.SHARED_SOCKET
    socket_info = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=10001, st_gid=os.getgid())
    directory_info = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o2710, st_uid=10001, st_gid=os.getgid()
    )
    monkeypatch.setattr(Path, "lstat", lambda self: socket_info if self == path else directory_info)
    reader = HostedSocketReader(path)
    assert reader.ready() is True
    socket_info.st_mode = stat.S_IFSOCK | 0o666
    assert reader.ready() is False
    socket_info.st_mode = stat.S_IFSOCK | 0o660
    directory_info.st_mode = stat.S_IFDIR | 0o2730
    assert reader.ready() is False


def test_socket_to_durable_baseline_is_an_integrated_offline_dry_run(setup, monkeypatch):
    database, clock = setup
    path = Path(f"/tmp/sniper-dry-{uuid.uuid4().hex[:12]}.sock")
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    path.chmod(0o600)
    server.listen(1)
    server.settimeout(2)
    received = []

    def serve():
        connection, _ = server.accept()
        with connection:
            received.append(json.loads(connection.recv(512)))
            response = {
                "status": "observed",
                "source": "hosted_browser_read_only",
                "stop_required": True,
                "purchase_allowed": False,
                "observation": page(clock[0]),
            }
            connection.sendall((json.dumps(response) + "\n").encode())
        server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(socket.socket, "connect", LOCAL_SOCKET_CONNECT)
        worker = scheduler(database, clock, HostedSocketReader(path))
        resume(worker)
        assert worker.tick()["status"] == "baseline"
        assert worker.snapshots.snapshot()["releases"][0]["baseline_revision"] == 1
        assert worker.tick()["status"] == "idle"
    finally:
        thread.join(timeout=2)
        server.close()
        path.unlink(missing_ok=True)
    assert received == [{"action": "observe_release", "release_id": "123"}]


def test_overlapping_tick_cannot_start_a_second_read(setup):
    database, clock = setup
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def read(ident):
        calls.append(ident)
        entered.set()
        assert release.wait(2)
        return page(clock[0])

    worker = scheduler(database, clock, read)
    resume(worker)
    thread = threading.Thread(target=worker.tick)
    thread.start()
    assert entered.wait(2)
    try:
        assert scheduler(database, clock, read).tick()["status"] == "idle"
    finally:
        release.set()
        thread.join(timeout=2)
    assert calls == [123]
