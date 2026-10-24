import json
from types import SimpleNamespace

import pytest
from test_discogs_rules import target
from test_local_web import MemorySocket

from finder.discogs_rules import RulesStore
from finder.discogs_web import DiscogsHandler

PORT = 18765
ORIGIN = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def store(tmp_path):
    result = RulesStore(tmp_path / "rules.sqlite3", create=True)
    yield result
    result.close()


def request(store, path="/", data=None, *, token="token", origin=ORIGIN, host=None):
    body = b"" if data is None else json.dumps(data).encode()
    method = "GET" if data is None else "POST"
    headers = f"Host: {host or f'127.0.0.1:{PORT}'}\r\n"
    if data is not None:
        headers += (
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            f"Origin: {origin}\r\nX-Finder-CSRF: {token}\r\n"
        )
    connection = MemorySocket(f"{method} {path} HTTP/1.1\r\n{headers}\r\n".encode() + body)
    server = SimpleNamespace(workspace=store, server_port=PORT, origin=ORIGIN, csrf_token="token")
    DiscogsHandler(connection, ("127.0.0.1", 1234), server)
    head, _, result = bytes(connection.output).partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), result


def test_page_and_snapshot(store):
    status, body = request(store)
    assert status == 200 and b"Purchasing disabled" in body
    status, body = request(store, "/api/snapshot")
    assert status == 200 and json.loads(body)["purchasing_enabled"] is False


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
