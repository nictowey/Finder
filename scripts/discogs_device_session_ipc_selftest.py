"""Isolated remembered-device HTTP/IPC gate, for CI or an unprivileged operator.

Run: python scripts/discogs_device_session_ipc_selftest.py
Reuses the synthetic watch gate, never production paths/providers/notifications.
Proxy identity assertions simulate the trusted Caddy side; no password is used.
Secure cookies are manually relayed over a private Unix socket, NOT browser/TLS
proof. Actual Caddy Basic validation, socket permissions between service users,
HTTPS and real-device persistence remain separate deployment gates.
"""

import argparse
import re
import sqlite3
import threading
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path

import httpx
from discogs_watch_ipc_selftest import main as watch_gate
from waitress import create_server

from finder.discogs_device_sessions import COOKIE, LIFETIME, SessionStore
from finder.discogs_phone_review_server import build_app
from finder.discogs_review_broker import BrokerClient

HOST = "watch.example"
ORIGIN = "https://" + HOST
OWNER = "fixture-owner"
IP_ORIGIN = False
# Public-address syntax fixture only. This address is never contacted or resolved.
IP_FIXTURE = "8.8.8.8"


def form_token(response, action):
    assert response.status_code == 200
    assert response.headers["referrer-policy"] == "same-origin"
    found = re.search(rf'action="{re.escape(action)}".*?name="csrf" value="([^"]+)"', response.text)
    assert found is not None, "Expected an action-bound CSRF form"
    return found[1]


def issued_cookie(response, issued_at):
    assert response.status_code == 303
    assert response.headers["location"] == "/session"
    assert response.headers["cache-control"] == "no-store"
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    assert set(cookies) == {COOKIE}
    morsel = cookies[COOKIE]
    assert morsel["secure"] and morsel["httponly"]
    assert morsel["samesite"] == "Strict" and morsel["path"] == "/"
    assert not morsel["domain"]
    assert morsel["max-age"] == str(LIFETIME)
    assert parsedate_to_datetime(morsel["expires"]).timestamp() == issued_at + LIFETIME
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", morsel.value)
    return f"{COOKIE}={morsel.value}"


def cleared_cookie(response):
    assert response.status_code == 200
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    assert cookie[COOKIE]["max-age"] == "0"
    assert cookie[COOKIE].value == ""


def session_app(broker_endpoint, watch_client, session_path, now):
    """Fresh app and reopened private session store, with explicit test clocks."""
    import os

    store = SessionStore(session_path, clock=lambda: now[0])
    app = build_app(
        backend=BrokerClient(broker_endpoint, broker_uid=os.getuid()),
        origin=ORIGIN,
        principal=OWNER,
        enable_watch_management=True,
        watch_backend=watch_client,
        enable_device_sessions=True,
        session_store=store,
        session_hostname=None if IP_ORIGIN else HOST,
        allow_ip_session_origin=IP_ORIGIN,
        session_ip_address=HOST if IP_ORIGIN else None,
        clock=lambda: now[0],
    )
    # A separate fake monotonic clock keeps this integration gate below the
    # unchanged 30/min preauth budget without waiting real minutes. Rate-limiter
    # behavior is covered separately; session expiry still uses `now` only.
    rate_clock = [0.0]
    app.clock = lambda: rate_clock[0]
    app.started = 0.0
    return app, rate_clock


@contextmanager
def http_app(directory, broker_endpoint, watch_client, session_path, now, generation):
    """Fresh app, SessionStore, HTTP listener and client for every restart."""
    app, rate_clock = session_app(broker_endpoint, watch_client, session_path, now)
    endpoint = Path(directory) / f"session-http-{generation}.sock"
    server = create_server(
        app,
        unix_socket=str(endpoint),
        threads=2,
        clear_untrusted_proxy_headers=True,
        asyncore_loop_timeout=0.1,
        max_request_body_size=4096,
        max_request_header_size=8192,
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    http = httpx.Client(
        transport=httpx.HTTPTransport(uds=str(endpoint)),
        base_url="http://" + HOST,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
    )

    call = requester(http, rate_clock)

    try:
        yield call
    finally:
        http.close()
        server.task_dispatcher.shutdown()
        server.close()
        server.asyncore.close_all(map=server._map)
        thread.join(timeout=3)
        assert not thread.is_alive(), "Synthetic HTTP listener did not stop"


def requester(http, rate_clock):
    """Explicit cookie relay shared by real HTTP gate and socket-free unit test."""

    def call(path, *, cookie=None, basic=False, fields=None, csrf=None, headers=None):
        rate_clock[0] += 61
        # No automatic cookie-jar behavior can accidentally authenticate a test.
        # Manual Cookie is intentional on this trusted synthetic non-TLS socket.
        http.cookies.clear()
        request_headers = {"Origin": ORIGIN}
        if cookie:
            request_headers["Cookie"] = cookie
        if basic:
            request_headers["X-Review-Principal"] = OWNER
        request_headers.update(headers or {})
        options = {}
        if fields is not None:
            options["json"] = fields
        if csrf is not None:
            assert fields is None
            options["data"] = {"csrf": csrf}
        return http.request("POST" if options else "GET", path, headers=request_headers, **options)

    return call


def session_gate(directory, broker_endpoint, client, database, baseline):
    auth_dir = Path(directory) / "synthetic-auth"
    auth_dir.mkdir(mode=0o700)
    session_path = auth_dir / "devices.db"
    SessionStore.initialize(session_path)
    now = [1_800_000_000.0]

    def login(call):
        challenge = call("/session/login", basic=True)
        assert "set-cookie" not in challenge.headers
        csrf = form_token(challenge, "/session/start")
        response = call("/session/start", basic=True, csrf=csrf)
        cookie = issued_cookie(response, now[0])
        assert call("/session/start", basic=True, csrf=csrf).status_code == 403
        return cookie

    def state(call, cookie):
        response = call("/watches/state", cookie=cookie)
        assert response.status_code == 200
        return response.json()

    def saved(revision, enabled, price):
        snapshot = client.snapshot()
        assert snapshot["watches"][:-1] == baseline["watches"]
        assert len(snapshot["watches"]) == len(baseline["watches"]) + 1
        watch = snapshot["watches"][-1]
        assert watch["release_id"] == 1000
        assert watch["revision"] == revision and watch["enabled"] is enabled
        assert watch["item_price_ceiling"] == price
        assert all(count == 0 for count in watch["notifications"].values())
        assert snapshot["monitor_enabled"] is False
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT COUNT(*) FROM discogs_price_outbox").fetchone()[0] == 0
        return snapshot

    with http_app(directory, broker_endpoint, client, session_path, now, 1) as call:
        for wrong_host in ("wrong.example", HOST + ":443", "8.8.4.4"):
            assert (
                call("/session/login", basic=True, headers={"Host": wrong_host}).status_code == 421
            )
        assert call("/session/login").status_code == 403
        assert (
            call("/session/login", headers={"X-Review-Principal": "wrong-owner"}).status_code == 403
        )
        assert call("/watches/state").status_code == 401
        assert call("/watches/state", basic=True).status_code == 403
        csrf = form_token(call("/session/login", basic=True), "/session/start")
        assert call("/session/start", csrf=csrf).status_code == 403
        assert (
            call(
                "/session/start", basic=True, csrf=csrf, headers={"Origin": "https://wrong.example"}
            ).status_code
            == 403
        )
        # Matching host with HTTP or an explicit :443 is not the exact HTTPS origin.
        for wrong_origin in ("http://" + HOST, ORIGIN + ":443", "https://wrong.example"):
            assert (
                call(
                    "/session/start", basic=True, csrf=csrf, headers={"Origin": wrong_origin}
                ).status_code
                == 403
            )
        assert (
            call("/session/start", basic=True, csrf=csrf, headers={"Origin": "null"}).status_code
            == 403
        )
        first = issued_cookie(call("/session/start", basic=True, csrf=csrf), now[0])
        second = login(call)
        assert first != second
        assert call("/watches", cookie=first).status_code == 200
        assert call("/watches", cookie=first, basic=True).status_code == 403
        fields = dict(
            release="1000",
            title="Synthetic session fixture",
            currency="USD",
            price="9.99",
            any_price=False,
            enabled=True,
            revision=0,
            csrf=state(call, first)["csrf"],
        )
        # Cross-device watch CSRF must fail before any broker mutation.
        assert call("/watches/save", cookie=second, fields=fields).status_code == 403
        assert client.snapshot() == baseline
        assert call("/watches/save", cookie=first, fields=fields).status_code == 200
        saved(1, True, "9.99")
        assert call("/watches/save", cookie=first, fields=fields).status_code == 403
        fields.update(price="8.99", revision=1, csrf=state(call, first)["csrf"])
        assert call("/watches/save", cookie=first, fields=fields).status_code == 200
        saved(2, True, "8.99")
        fields.update(enabled=False, revision=2, csrf=state(call, second)["csrf"])
        assert call("/watches/save", cookie=second, fields=fields).status_code == 200
        after = saved(3, False, "8.99")
        stale_watch_csrf = state(call, first)["csrf"]
        stale_logout_csrf = form_token(call("/session", cookie=first), "/session/logout")
        for cookie in (first, second):
            assert cookie.split("=", 1)[1].encode() not in session_path.read_bytes()

    # A real HTTP listener/app restart reopens the same private session store;
    # bearer sessions persist, in-memory CSRF challenges do not.
    with http_app(directory, broker_endpoint, client, session_path, now, 2) as call:
        assert state(call, first) and state(call, second)
        fields.update(revision=3, csrf=stale_watch_csrf)
        assert call("/watches/save", cookie=first, fields=fields).status_code == 403
        assert call("/session/logout", cookie=first, csrf=stale_logout_csrf).status_code == 403
        csrf = form_token(call("/session", cookie=first), "/session/logout")
        assert call("/session/logout", cookie=second, csrf=csrf).status_code == 403
        cleared_cookie(call("/session/logout", cookie=first, csrf=csrf))
        assert call("/watches/state", cookie=first).status_code == 401
        assert state(call, second)
        third = login(call)
        csrf = form_token(call("/session", cookie=second), "/session/revoke-all")
        assert call("/session/revoke-all", cookie=third, csrf=csrf).status_code == 403
        cleared_cookie(call("/session/revoke-all", cookie=second, csrf=csrf))
        assert call("/watches/state", cookie=second).status_code == 401
        assert call("/watches/state", cookie=third).status_code == 401
        recovery = login(call)
        created = now[0]
        assert state(call, recovery)
        assert client.snapshot() == after

    with http_app(directory, broker_endpoint, client, session_path, now, 3) as call:
        for cookie in (first, second, third):
            assert call("/watches/state", cookie=cookie).status_code == 401
        now[0] = created + LIFETIME - 1
        assert state(call, recovery)
        now[0] += 1
        assert call("/watches/state", cookie=recovery).status_code == 401
        fields["csrf"] = "expired-session-cannot-write"
        assert call("/watches/save", cookie=recovery, fields=fields).status_code == 401
        response = call("/watches", cookie=recovery)
        assert response.status_code == 303
        assert response.headers["location"] == "/session/login"
        assert "Max-Age=0" in response.headers["set-cookie"]
        assert client.snapshot() == after
    print(
        f"PASS synthetic {'IPv4' if IP_ORIGIN else 'DNS'} session HTTP/Unix IPC: "
        "proxy-asserted CSRF bootstrap, cookie attributes, "
        "two-device watch add/edit/pause and CSRF separation, logout/revoke-all, fresh HTTP/app "
        "restart persistence, fixed expiry; zero notifications/provider/production access. "
        "NOT Caddy Basic password validation, TLS, browser cookie handling or iOS persistence."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ip-origin",
        action="store_true",
        help="Use public IPv4 Host/origin syntax over Unix sockets only; no DNS/TCP traffic",
    )
    options = parser.parse_args()
    if options.ip_origin:
        IP_ORIGIN = True
        HOST = IP_FIXTURE
        ORIGIN = "https://" + HOST
    watch_gate(extra_gate=session_gate)
