import json
import socket
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_discogs_rules import target
from test_local_web import MemorySocket

from finder.discogs_change_outbox import InAppChangeOutbox
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


def request(
    store,
    path="/",
    data=None,
    *,
    token="token",
    origin=ORIGIN,
    host=None,
    reader=None,
    schedule=None,
    checkout_status_path=None,
):
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
    if schedule is not None:
        server.hosted_schedule = schedule
    server.checkout_status_path = checkout_status_path
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
    assert json.loads(body)["in_app_alerts"] == {
        "channel": "in_app_only",
        "messages_sent": 0,
        "purchase_allowed": False,
        "control_revision": None,
        "pending": [],
    }
    assert json.loads(body)["capabilities"] == {
        "listing_read_enabled": False,
        "order_authorization_configured": False,
        "order_submission_enabled": False,
        "order_reconciliation_configured": False,
    }


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


def test_snapshot_reports_recorded_checkout_block_without_enabling_actions(store, tmp_path):
    status_path = tmp_path / "checkout-status.json"
    status_path.write_text(
        json.dumps(
            {
                "schema": "sniper_checkout_status_v1",
                "checkout_kind": "enhanced_v2",
                "payment_access": "blocked_browser_security_challenge",
                "recorded_at": time.time() - 10,
            }
        )
    )
    status_path.chmod(0o600)
    _, body = request(store, "/api/snapshot", checkout_status_path=status_path)
    state = json.loads(body)
    ready = state["checkout_readiness"]
    assert ready["payment_access"] == "blocked_browser_security_challenge"
    assert ready["observed_checkout_kind"] == "enhanced_v2"
    assert ready["manual_unpaid_order_creation"] == "unverified"
    assert ready["enhanced_routes_to_legacy"] is False
    assert ready["live_status_checked"] is False
    assert ready["purchase_allowed"] is False
    assert state["capabilities"]["order_submission_enabled"] is False
    # A process restart re-reads the same report; it cannot clear a provider block.
    _, again = request(store, "/api/snapshot", checkout_status_path=status_path)
    assert json.loads(again)["checkout_readiness"]["payment_access"] == ready["payment_access"]
    assert status_path.read_text().count("blocked_browser_security_challenge") == 1


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


def test_in_app_change_review_is_loopback_scoped_and_csrf_protected(store):
    database = Path(store.db.execute("PRAGMA database_list").fetchone()[2])
    InAppChangeOutbox(database)
    with store.db:
        store.db.execute(
            "INSERT INTO discogs_release_changes "
            "(release_id,revision,observed_at,kind,listing_id,fields) "
            "VALUES(123,1,1792886400,'new','456','{}')"
        )
    status, body = request(store, "/api/snapshot")
    alert = json.loads(body)["in_app_alerts"]
    assert status == 200 and len(alert["pending"]) == 1
    assert alert["pending"][0]["listing_id"] == "456"
    assert alert["pending"][0]["delivery"] == "in_app_only"
    assert alert["pending"][0]["purchase_allowed"] is False
    sequence = alert["pending"][0]["change_sequence"]
    ack = {
        "change_sequences": [sequence],
        "expected_revision": 0,
        "expected_control_revision": None,
    }
    assert request(store, "/api/change-alert/ack", ack, token="wrong")[0] == 403
    assert request(store, "/api/change-alert/ack", ack, origin="https://evil.test")[0] == 403
    assert request(store, "/api/change-alert/ack", ack, host="evil.test")[0] == 421
    assert request(store, "/api/change-alert/ack", {**ack, "expected_revision": 1})[0] == 409
    assert len(json.loads(request(store, "/api/snapshot")[1])["in_app_alerts"]["pending"]) == 1
    status, body = request(store, "/api/change-alert/ack", ack)
    assert status == 200 and json.loads(body)["messages_sent"] == 0
    assert request(store, "/api/change-alert/ack", ack)[0] == 200
    assert json.loads(request(store, "/api/snapshot")[1])["in_app_alerts"]["pending"] == []
    assert request(store, "/api/change-alert/ack", {**ack, "change_sequences": [999]})[0] == 400


def test_three_monitor_only_ids_save_and_pause_without_buying_rules_or_reads(store):
    database = Path(store.db.execute("PRAGMA database_list").fetchone()[2])
    schedule = HostedReleaseScheduler(database, reader=None)
    supplied = [
        "https://www.discogs.com/release/28720177-DJ-Esco-Hosted-By-Future-4-56-Nights",
        "5520534",
        "https://www.discogs.com/release/13053315-Metro-Boomin-Not-All-Heroes-Wear-Capes",
    ]
    for revision, value in enumerate(supplied):
        data = {"release_id": value, "enabled": True, "expected_revision": revision}
        assert request(store, "/api/monitor-release", data, schedule=schedule)[0] == 200
    snapshot = json.loads(request(store, "/api/snapshot", schedule=schedule)[1])
    assert snapshot["targets"] == []
    assert snapshot["purchasing_enabled"] is False
    assert snapshot["hosted_schedule"]["paused"] is True
    assert snapshot["hosted_schedule"]["reader_ready"] is False
    assert snapshot["hosted_schedule"]["monitor_releases"] == [
        {"release_id": ident, "enabled": True} for ident in (5520534, 13053315, 28720177)
    ]
    assert (
        request(
            store,
            "/api/monitor-release",
            {
                "release_id": 5520534,
                "enabled": False,
                "expected_revision": 3,
            },
            schedule=schedule,
        )[0]
        == 200
    )
    assert schedule.health()["paused"] is True
    assert schedule.health()["monitor_releases"][0] == {
        "release_id": 5520534,
        "enabled": False,
    }
    assert store.snapshot()["targets"] == []


def test_monitor_only_save_requires_current_revision_and_origin_csrf(store):
    database = Path(store.db.execute("PRAGMA database_list").fetchone()[2])
    schedule = HostedReleaseScheduler(database, reader=None)
    data = {"release_id": 28720177, "enabled": True, "expected_revision": 0}
    assert request(store, "/api/monitor-release", data, schedule=schedule, token="wrong")[0] == 403
    assert (
        request(store, "/api/monitor-release", data, schedule=schedule, origin="https://evil.test")[
            0
        ]
        == 403
    )
    assert request(store, "/api/monitor-release", {**data, "extra": 1}, schedule=schedule)[0] == 404
    assert (
        request(store, "/api/monitor-release", {**data, "enabled": "true"}, schedule=schedule)[0]
        == 400
    )
    assert schedule.health()["monitor_releases"] == []
    assert request(store, "/api/monitor-release", data, schedule=schedule)[0] == 200
    assert request(store, "/api/monitor-release", data, schedule=schedule)[0] == 409
    schedule.set_paused(False, expected_revision=0)
    assert (
        request(
            store,
            "/api/monitor-release",
            {
                "release_id": 5520534,
                "enabled": True,
                "expected_revision": 1,
            },
            schedule=schedule,
        )[0]
        == 409
    )
    assert schedule.health()["monitor_releases"] == [{"release_id": 28720177, "enabled": True}]


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
