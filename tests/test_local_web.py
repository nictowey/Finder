import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from finder.local_ui import render_page
from finder.local_web import (
    MAX_BODY_BYTES,
    LocalReviewHandler,
    LocalReviewServer,
    LocalWebSafetyError,
    local_boundary,
    main,
)
from finder.local_workspace import LocalWorkspace

_ORIGINAL_CONNECT = socket.socket.connect
PORT = 18765
ORIGIN = f"http://127.0.0.1:{PORT}"
TOKEN = "test-process-token"


class MemorySocket:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()
        self.timeout = None

    def makefile(self, *args):
        return self.input

    def sendall(self, body):
        self.output.extend(body)

    def settimeout(self, timeout):
        self.timeout = timeout


@pytest.fixture
def workspace(tmp_path):
    workspace = LocalWorkspace.create(tmp_path / "review.sqlite3")
    yield workspace
    workspace.close()


def bundle():
    observed = datetime.now(UTC).isoformat()
    return {
        "schema_version": 1,
        "source": "synthetic",
        "target": {"artist": "Example Ensemble", "album": "Offline Horizons", "colors": ["Blue"]},
        "settings": {
            "maximum_subtotal": "30.00",
            "gamble_max": "15.00",
            "currency": "USD",
            "country": "US",
            "postal_code": "00000",
            "tells": [{"kind": "color", "value": "Blue", "required": True}],
        },
        "listings": [
            {
                "id": "case-blue",
                "title": "Example Ensemble Offline Horizons blue vinyl",
                "observed_at": observed,
                "details_observed_at": observed,
                "current_price": "10.00",
                "currency": "USD",
                "shipping_cost": "4.00",
                "shipping_currency": "USD",
                "price_kind": "fixed_price",
                "delivery_country": "US",
                "delivery_postal_code": "00000",
            }
        ],
    }


def raw_request(workspace, raw):
    connection = MemorySocket(raw)
    server = SimpleNamespace(workspace=workspace, server_port=PORT, origin=ORIGIN, csrf_token=TOKEN)
    LocalReviewHandler(connection, ("127.0.0.1", 34567), server)
    response = bytes(connection.output)
    headers, _, body = response.partition(b"\r\n\r\n")
    return int(headers.split(b" ")[1]), headers.decode(), body


def request(workspace, path="/", *, method="GET", body=None, headers=None):
    default = [("Host", f"127.0.0.1:{PORT}")]
    if body is not None:
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        default += [
            ("Origin", ORIGIN),
            ("X-Finder-CSRF", TOKEN),
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ]
    if headers is not None:
        replace = {key.lower() for key, _ in headers}
        default = [(key, value) for key, value in default if key.lower() not in replace]
        default += [(key, value) for key, value in headers if value is not None]
    raw = f"{method} {path} HTTP/1.1\r\n".encode()
    raw += b"".join(f"{key}: {value}\r\n".encode() for key, value in default)
    return raw_request(workspace, raw + b"\r\n" + (body or b""))


def test_whole_http_review_flow_survives_restart(workspace, tmp_path):
    assert request(workspace, "/api/snapshot")[0] == 200
    status, _, body = request(workspace, "/api/import", method="POST", body=bundle())
    assert status == 200
    saved = json.loads(body)
    row = saved["rows"][0]
    assert row["review"]["subtotal"] == "14.00"
    assert row["review"]["status"] == "family_review"  # No catalog-coverage claim.
    assert saved["settings"]["maximum_subtotal"] == "30.00"
    for verdict in ("mine", "other", "unsure"):
        row = saved["rows"][0]
        status, _, body = request(
            workspace,
            "/api/verdict",
            method="POST",
            body={
                "id": row["id"],
                "verdict": verdict,
                "expected_revision": saved["revision"],
                "review_fingerprint": row["review_fingerprint"],
            },
        )
        assert status == 200
        saved = json.loads(body)
        assert saved["rows"][0]["verdict"] == verdict
    workspace.close()
    reopened = LocalWorkspace.open(tmp_path / "review.sqlite3")
    try:
        restored = json.loads(request(reopened, "/api/snapshot")[2])
        assert restored["rows"][0]["verdict"] == "unsure"
        status, headers, body = request(reopened, "/api/export")
        assert status == 200
        assert 'attachment; filename="finder-local-review.json"' in headers
        assert json.loads(body)["export_version"] == 1
    finally:
        reopened.close()


def test_stale_verdict_rejected_without_overwriting_saved_decision(workspace):
    saved = workspace.import_bundle(bundle())
    row = saved["rows"][0]
    verdict = {
        "id": row["id"],
        "verdict": "mine",
        "expected_revision": saved["revision"],
        "review_fingerprint": row["review_fingerprint"],
    }
    assert request(workspace, "/api/verdict", method="POST", body=verdict)[0] == 200
    verdict["verdict"] = "other"
    assert request(workspace, "/api/verdict", method="POST", body=verdict)[0] == 409
    assert workspace.snapshot()["rows"][0]["verdict"] == "mine"


@pytest.mark.parametrize(
    "host", ["localhost:18765", "127.0.0.1", "evil.test:18765", "127.0.0.1:80", None]
)
@pytest.mark.parametrize("method,path", [("GET", "/"), ("POST", "/api/import"), ("DELETE", "/")])
def test_exact_host_required_on_every_route(workspace, host, method, path):
    status, _, _ = request(workspace, path, method=method, headers=[("Host", host)])
    assert status == 421
    assert workspace.snapshot()["revision"] == 0


@pytest.mark.parametrize(
    "headers",
    [
        [("Origin", None)],
        [("Origin", "null")],
        [("Origin", "http://localhost:18765")],
        [("Origin", "https://evil.test")],
        [("X-Finder-CSRF", None)],
        [("X-Finder-CSRF", "wrong")],
        [("X-Finder-CSRF", TOKEN), ("X-Finder-CSRF", TOKEN)],
        [("Origin", ORIGIN), ("Origin", ORIGIN)],
    ],
)
def test_mutations_require_exact_origin_and_one_process_token(workspace, headers):
    assert (
        request(workspace, "/api/import", method="POST", body=bundle(), headers=headers)[0] == 403
    )
    assert workspace.snapshot()["revision"] == 0


@pytest.mark.parametrize(
    "headers,expected",
    [
        ([("Content-Length", None)], 400),
        ([("Content-Length", "-1")], 400),
        ([("Content-Length", "+1")], 400),
        ([("Content-Length", "abc")], 400),
        ([("Content-Length", "0")], 400),
        ([("Content-Length", "9" * 5000)], 413),
        ([("Content-Length", str(MAX_BODY_BYTES + 1))], 413),
        ([("Content-Length", "2"), ("Content-Length", "2")], 400),
        ([("Transfer-Encoding", "chunked")], 400),
        ([("Content-Type", "text/plain")], 415),
        ([("Content-Type", None)], 415),
    ],
)
def test_invalid_framing_and_size_close_connection_without_writing(workspace, headers, expected):
    status, response_headers, _ = request(
        workspace, "/api/import", method="POST", body=b"{}", headers=headers
    )
    assert status == expected
    assert "Connection: close" in response_headers
    assert workspace.snapshot()["revision"] == 0


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b"null",
        b"broken",
        b"\xff",
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1,"x":2}',
        b'{"x":{"y":1,"y":2}}',
    ],
)
def test_invalid_json_is_rejected_without_mutation(workspace, body):
    assert request(workspace, "/api/import", method="POST", body=body)[0] == 400
    assert workspace.snapshot()["revision"] == 0


def test_batch_validation_is_atomic_through_http(workspace):
    data = bundle()
    data["listings"].append({**data["listings"][0], "id": "invalid", "photo_url": "secret"})
    status, _, body = request(workspace, "/api/import", method="POST", body=data)
    assert status == 400
    assert b"secret" not in body
    assert workspace.snapshot()["rows"] == []


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "ODD"])
def test_other_methods_are_rejected(workspace, method):
    assert request(workspace, method=method)[0] == 405


@pytest.mark.parametrize(
    "path",
    ["/api/import", "/api/verdict", "/.env", "/etc/passwd", "/?x=1", "http://127.0.0.1:18765/"],
)
def test_get_never_mutates_or_browses_files(workspace, path):
    assert request(workspace, path)[0] == 404
    assert workspace.snapshot()["revision"] == 0


def test_malformed_request_errors_do_not_reflect_payloads(workspace):
    for raw in (
        b"GET / SECRET HTTP/1.1\r\n\r\n",
        b"SECRET x y z\r\n\r\n",
        b"GET / HTTP/9.9\r\n\r\n",
        b"GET / HTTP/1.SECRET\r\n\r\n",
    ):
        connection = MemorySocket(raw)
        server = SimpleNamespace(
            workspace=workspace, server_port=PORT, origin=ORIGIN, csrf_token=TOKEN
        )
        LocalReviewHandler(connection, ("127.0.0.1", 123), server)
        assert b"SECRET" not in connection.output
        assert b"local request" in connection.output


def test_security_headers_nonce_and_no_browser_storage(workspace):
    status, headers, body = request(workspace)
    assert status == 200
    page = body.decode()
    nonce = re.search(r"script-src 'nonce-([^']+)'", headers)[1]
    assert f'<script nonce="{nonce}">' in page
    assert f'<style nonce="{nonce}">' in page
    for value in (
        "default-src 'none'",
        "frame-ancestors 'none'",
        "Cache-Control: no-store",
        "X-Content-Type-Options: nosniff",
        "connect-src 'self'",
    ):
        assert value in headers
    assert "Access-Control-Allow-Origin" not in headers
    for forbidden in (
        "innerHTML",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "<img",
        "<iframe",
    ):
        assert forbidden not in page
    assert "textContent" in page
    assert TOKEN in page
    assert request(workspace)[2] != body  # Fresh response nonce.
    assert workspace.snapshot()["revision"] == 0


def test_ui_preserves_untrusted_token_as_script_data():
    page = render_page("</script><img src=x>", '" onclick="evil')
    assert "</script><img" not in page
    assert 'nonce="&quot; onclick=&quot;evil"' in page


def test_loopback_listener_under_runtime_boundary(workspace, tmp_path, monkeypatch):
    # The only test exception to conftest's deny-connect fixture is this exact listener.
    with LocalReviewServer(workspace, 0) as server:
        expected = ("127.0.0.1", server.server_port)

        def connect_loopback_only(sock, address):
            assert address == expected
            return _ORIGINAL_CONNECT(sock, address)

        monkeypatch.setattr(socket.socket, "connect", connect_loopback_only)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(expected)
            client.sendall(
                f"GET /api/snapshot HTTP/1.1\r\nHost: {expected[0]}:{expected[1]}\r\n\r\n".encode()
            )
            with local_boundary((tmp_path / "review.sqlite3").resolve(), server.server_port):
                server.handle_request()
            response = client.recv(65536)
            assert b"200 OK" in response
            assert server.server_address == expected


def test_boundary_allows_listener_but_blocks_cost_causing_io(tmp_path):
    database = (tmp_path / "allowed.sqlite3").resolve()
    with local_boundary(database, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            with pytest.raises(LocalWebSafetyError):
                listener.bind(("0.0.0.0", 0))
        with pytest.raises(LocalWebSafetyError):
            socket.getaddrinfo("example.invalid", 443)
        with pytest.raises(LocalWebSafetyError):
            socket.gethostname()
        with pytest.raises(LocalWebSafetyError):
            subprocess.run([sys.executable, "-c", "pass"])
        with pytest.raises(LocalWebSafetyError):
            sqlite3.connect(tmp_path / "other.sqlite3")
        with pytest.raises(LocalWebSafetyError):
            (tmp_path / ".env").read_text()
        with sqlite3.connect(database) as connection:
            assert connection.execute("select 1").fetchone() == (1,)
        # Calling the audit event directly avoids weakening the suite's connect block.
        with pytest.raises(LocalWebSafetyError):
            sys.audit("socket.connect", None, ("127.0.0.1", 443))
    assert not (tmp_path / "other.sqlite3").exists()


def test_cli_refuses_live_environment_before_file_creation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEON_SECRET", "do-not-display")
    path = tmp_path / "never.sqlite3"
    assert main(["--database", str(path), "--init"]) == 2
    assert not path.exists()
    output = capsys.readouterr().err
    assert "NEON_SECRET" in output
    assert "do-not-display" not in output


def test_cli_invalid_port_does_not_create_file(tmp_path):
    for key in list(os.environ):
        if key.startswith(("NEON_", "PG", "DATABASE_")):
            pytest.skip("This test requires the existing clean local test environment")
    path = tmp_path / "never.sqlite3"
    assert main(["--database", str(path), "--init", "--port", "65536"]) == 2
    assert not path.exists()


def test_cli_initializes_and_reopens_under_guard(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cli.sqlite3"
    runs = []

    def serve_once(server, **kwargs):
        runs.append(server.workspace.snapshot()["revision"])
        if len(runs) == 1:
            server.workspace.import_bundle(bundle())
        raise KeyboardInterrupt

    monkeypatch.setattr(LocalReviewServer, "serve_forever", serve_once)
    assert main(["--database", str(path), "--init", "--port", "0"]) == 0
    assert main(["--database", str(path), "--port", "0"]) == 0
    assert runs == [0, 1]
    assert "http://127.0.0.1:" in capsys.readouterr().out


def test_cli_preserves_sidecars_and_reports_sanitized_reason(tmp_path, capsys):
    path = tmp_path / "review.sqlite3"
    workspace = LocalWorkspace.create(path)
    workspace.close()
    sidecar = path.with_name(path.name + "-wal")
    sidecar.write_text("preserve this")
    before = path.read_bytes()
    assert main(["--database", str(path), "--port", "0"]) == 2
    assert "sidecar" in capsys.readouterr().err.lower()
    assert sidecar.read_text() == "preserve this"
    assert path.read_bytes() == before


def test_short_body_and_duplicate_host_fail_closed(workspace):
    assert (
        request(
            workspace, "/api/import", method="POST", body=b"{}", headers=[("Content-Length", "3")]
        )[0]
        == 400
    )
    assert (
        request(workspace, headers=[("Host", f"127.0.0.1:{PORT}"), ("Host", f"127.0.0.1:{PORT}")])[
            0
        ]
        == 421
    )
    assert request(workspace, body=b"{}", headers=[("Content-Length", "2")])[0] == 400
    assert workspace.snapshot()["revision"] == 0


def test_error_does_not_log_or_reflect_payload(workspace, monkeypatch, capsys):
    def fail():
        raise RuntimeError("secret-payload-path")

    monkeypatch.setattr(workspace, "snapshot", fail)
    status, _, body = request(workspace, "/api/snapshot")
    assert status == 500
    assert b"secret-payload-path" not in body
    assert capsys.readouterr() == ("", "")
