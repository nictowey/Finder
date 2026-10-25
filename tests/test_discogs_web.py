import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_discogs_rules import target
from test_local_web import MemorySocket

from finder.discogs_hosted_scheduler import HostedReleaseScheduler
from finder.discogs_rules import RulesStore
from finder.discogs_web import DiscogsHandler, DiscogsServer

_ORIGINAL_CONNECT = socket.socket.connect

PORT = 18765
ORIGIN = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def store(tmp_path):
    result = RulesStore(tmp_path / "rules.sqlite3", create=True)
    yield result
    result.close()


def request(store, path="/", data=None, *, token="token", origin=ORIGIN, host=None, reader=None):
    body = b"" if data is None else json.dumps(data).encode()
    method = "GET" if data is None else "POST"
    headers = f"Host: {host or f'127.0.0.1:{PORT}'}\r\n"
    if data is not None:
        headers += (
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            f"Origin: {origin}\r\nX-Finder-CSRF: {token}\r\n"
        )
    connection = MemorySocket(f"{method} {path} HTTP/1.1\r\n{headers}\r\n".encode() + body)
    server = SimpleNamespace(
        workspace=store, server_port=PORT, origin=ORIGIN, csrf_token="token", listing_reader=reader
    )
    DiscogsHandler(connection, ("127.0.0.1", 1234), server)
    head, _, result = bytes(connection.output).partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), result


def test_page_and_snapshot(store):
    status, body = request(store)
    assert status == 200 and b"Purchasing disabled" in body
    assert b"<title>Sniper \xc2\xb7 Discogs targets</title>" in body
    assert b"Sniper / Discogs targets" in body
    status, body = request(store, "/api/snapshot")
    assert status == 200 and json.loads(body)["purchasing_enabled"] is False
    assert json.loads(body)["hosted_schedule"]["paused"] is True


def test_local_snapshot_reports_real_stopped_schedule_without_cloud_control(store):
    database = Path(store.db.execute("PRAGMA database_list").fetchone()[2])
    scheduler = HostedReleaseScheduler(database, None)
    assert scheduler.health()["kill_switch"] is True
    request_bytes = f"GET /api/snapshot HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\n\r\n"
    connection = MemorySocket(request_bytes.encode())
    server = SimpleNamespace(
        workspace=store,
        server_port=PORT,
        origin=ORIGIN,
        csrf_token="token",
        listing_reader=None,
        hosted_schedule=scheduler,
    )
    DiscogsHandler(connection, ("127.0.0.1", 1234), server)
    _, _, body = bytes(connection.output).partition(b"\r\n\r\n")
    assert json.loads(body)["hosted_schedule"]["kill_switch"] is True


def test_sniper_restarts_on_same_loopback_port_without_sharing_listener(store, monkeypatch):
    with DiscogsServer(store, 0) as first:
        address = ("127.0.0.1", first.server_port)
        with pytest.raises(OSError):
            DiscogsServer(store, first.server_port)

        def connect_loopback_only(sock, target):
            assert target == address
            return _ORIGINAL_CONNECT(sock, target)

        monkeypatch.setattr(socket.socket, "connect", connect_loopback_only)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(address)
            client.sendall(
                f"GET /api/snapshot HTTP/1.1\r\nHost: {address[0]}:{address[1]}\r\n\r\n".encode()
            )
            first.handle_request()
            assert b"200 OK" in client.recv(65536)

    with DiscogsServer(store, address[1]) as restarted:
        assert restarted.server_address == address


def test_original_finder_review_branding_remains():
    from finder.local_ui import render_page

    assert "<title>Finder \u00b7 Local review</title>" in render_page("token", "nonce")


def test_save_conflict_and_unknown_purchase(store):
    data = {"target": target().model_dump(), "expected_revision": 0}
    assert request(store, "/api/target", data)[0] == 200
    assert request(store, "/api/target", data)[0] == 409
    assert request(store, "/api/buy", {"release_id": 123})[0] == 404
    assert len(store.snapshot()["targets"]) == 1


def test_guards(store):
    data = {"target": target().model_dump(), "expected_revision": 0}
    assert request(store, "/api/target", data, token="bad")[0] == 403
    assert request(store, "/api/target", data, origin="https://evil.test")[0] == 403
    assert request(store, host="evil.test")[0] == 421
    assert store.snapshot()["targets"] == []


def test_owned_marker_requires_revision(store):
    store.save(target().model_dump(), 0)
    data = {"release_id": 123, "owned": True, "expected_revision": 1}
    assert request(store, "/api/owned", data)[0] == 200
    assert request(store, "/api/owned", data)[0] == 409
    assert store.snapshot()["owned"] == [123]


def test_offline_ui_interactions(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path

    from finder.discogs_ui import render_page

    if not shutil.which("node") or not Path("node_modules/jsdom").exists():
        pytest.skip("Offline DOM verification requires installed Node/jsdom dependencies.")
    page = tmp_path / "discogs.html"
    page.write_text(render_page("dom-test-token", "dom-test-nonce"))
    result = subprocess.run(
        ["node", "tests/test_discogs_ui.cjs", str(page)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_live_read_opt_in_and_source_spoof_rejected(store):
    store.save(target().model_dump(), 0)
    assert request(store, "/api/listing", {"release_id": 123, "listing": "123"})[0] == 403
    assert (
        request(
            store,
            "/api/evaluate",
            {"release_id": 123, "observation": {"source": "discogs_public_listing"}},
        )[0]
        == 400
    )


def test_live_read_normalized_data_not_purchase(store):
    import httpx
    from test_discogs_listing import payload

    from finder.discogs_listing import ListingReader

    store.save(target().model_dump(), 0)
    clock = [100.0]
    reader = ListingReader(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload())),
        clock=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    status, body = request(
        store, "/api/listing", {"release_id": 123, "listing": "123"}, reader=reader
    )
    data = json.loads(body)
    assert status == 200 and data["listing"]["release_id"] == 123
    assert not data["evaluation"]["eligible_for_review"]
    assert data["evaluation"]["purchase_allowed"] is False
    assert data["evaluation"]["rules_revision"] == 1
    assert (
        request(store, "/api/listing", {"release_id": 123, "listing": "123"}, reader=reader)[0]
        == 502
    )
