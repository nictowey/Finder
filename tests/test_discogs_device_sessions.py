"""Offline session/auth tests. Synthetic credentials only, no runtime activation."""

import io
import json
import re
import sqlite3
from contextlib import closing
from urllib.parse import urlencode

import pytest

from finder.discogs_device_sessions import (
    COOKIE,
    INTERNAL_PRINCIPAL,
    LIFETIME,
    MAX_DEVICES,
    DeviceSessionApp,
    SessionStore,
    cookie_token,
    validate_origin,
)
from finder.discogs_phone_review_server import PrivateBoundary, build_app


@pytest.fixture
def session(tmp_path):
    directory = tmp_path / "auth"
    directory.mkdir(mode=0o700)
    store = SessionStore.initialize(directory / "devices.db")
    now = [1800000000.0]
    store.clock = lambda: now[0]
    received = []

    def protected(env, start):
        received.append(env)
        start("200 OK", [("Content-Type", "application/json")])
        return [b'{"protected":true}']

    boundary = PrivateBoundary(None, principal="owner")
    app = DeviceSessionApp(
        protected,
        store=store,
        origin="https://sniper.example",
        hostname="sniper.example",
        principal="owner",
        basic_authenticate=boundary.authenticate,
        clock=store.clock,
    )
    return store, app, now, received


def call(
    app,
    path="/session/login",
    *,
    method="GET",
    token=None,
    csrf=None,
    basic=False,
    raw=None,
    **headers,
):
    body = urlencode({"csrf": csrf}).encode() if csrf is not None else b""
    if raw is not None:
        body = raw
    env = {
        "PATH_INFO": path,
        "REQUEST_METHOD": method,
        "HTTP_HOST": "sniper.example",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": str(len(body)),
        "HTTP_ORIGIN": "https://sniper.example",
        "wsgi.input": io.BytesIO(body),
    }
    if basic:
        env["HTTP_X_REVIEW_PRINCIPAL"] = "owner"
    if token:
        env["HTTP_COOKIE"] = f"{COOKIE}={token}"
    env.update(headers)
    result = []
    data = b"".join(app(env, lambda status, hdrs: result.extend([status, dict(hdrs)])))
    return *result, data


def challenge(app, *, path="/session/login", token=None, action="/session/start", basic=True):
    status, _, body = call(app, path, token=token, basic=basic)
    assert status == "200 OK"
    return re.search((f'action="{action}".*?name="csrf" value="([^"]+)"').encode(), body)[
        1
    ].decode()


def login(app, old=None):
    csrf = challenge(app)
    status, headers, _ = call(
        app, "/session/start", method="POST", csrf=csrf, basic=True, token=old
    )
    assert status == "303 See Other"
    return headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1], headers


def test_hash_only_fixed_expiry_and_restart(session):
    store, app, now, received = session
    token, headers = login(app)
    cookie = headers["Set-Cookie"]
    assert all(
        x in cookie
        for x in ["Secure", "HttpOnly", "SameSite=Strict", "Path=/", f"Max-Age={LIFETIME}"]
    )
    assert "Domain" not in cookie
    assert token.encode() not in store.path.read_bytes()
    reopened = SessionStore(store.path, clock=store.clock)
    assert reopened.authenticate(token, "owner")
    now[0] += LIFETIME - 1
    assert call(app, "/watches", token=token)[0] == "200 OK"
    assert "HTTP_COOKIE" not in received[-1]
    assert "HTTP_X_REVIEW_PRINCIPAL" not in received[-1]
    assert received[-1][INTERNAL_PRINCIPAL].startswith("owner:")
    assert token not in received[-1][INTERNAL_PRINCIPAL]
    now[0] += 1
    assert call(app, "/watches", token=token)[0] == "303 See Other"
    status, _, body = call(app, "/watches/state", token=token)
    assert status == "401 Unauthorized" and b"/session/login" in body
    assert not reopened.authenticate(token, "owner")


def test_login_requires_basic_origin_and_single_use_csrf(session):
    store, app, _, _ = session
    csrf = challenge(app)
    assert call(app, "/session/start", method="POST", csrf=csrf)[0] == "403 Forbidden"
    assert (
        call(
            app,
            "/session/start",
            method="POST",
            basic=True,
            csrf=csrf,
            HTTP_ORIGIN="https://evil.example",
        )[0]
        == "403 Forbidden"
    )
    assert call(app, "/session/start", method="POST", basic=True, csrf=csrf)[0] == "303 See Other"
    assert call(app, "/session/start", method="POST", basic=True, csrf=csrf)[0] == "403 Forbidden"
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_rotation_revokes_old_and_no_get_mutation(session):
    store, app, _, _ = session
    old, _ = login(app)
    token, _ = login(app, old)
    assert not store.authenticate(old, "owner")
    assert store.authenticate(token, "owner")
    for path in ("/session/logout", "/session/revoke-all"):
        assert call(app, path, token=token)[0] == "405 Method Not Allowed"
    assert store.authenticate(token, "owner")


def test_device_bound_logout_and_revoke_all_recovery(session):
    store, app, _, _ = session
    first, _ = login(app)
    second, _ = login(app)
    csrf = challenge(app, path="/session", token=first, action="/session/logout", basic=False)
    assert (
        call(app, "/session/logout", method="POST", token=second, csrf=csrf)[0] == "403 Forbidden"
    )
    status, headers, _ = call(app, "/session/logout", method="POST", token=first, csrf=csrf)
    assert status == "200 OK" and "Max-Age=0" in headers["Set-Cookie"]
    assert not store.authenticate(first, "owner")
    assert store.authenticate(second, "owner")
    third, _ = login(app)
    csrf = challenge(app, path="/session", token=second, action="/session/revoke-all", basic=False)
    assert call(app, "/session/revoke-all", method="POST", token=second, csrf=csrf)[0] == "200 OK"
    assert not store.authenticate(third, "owner")
    assert not store.authenticate(second, "owner")
    recovery, _ = login(app)
    assert store.authenticate(recovery, "owner")


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_HOST": "sniper.example:8443"},
        {"HTTP_HOST": "ivc.example"},
        {"QUERY_STRING": "token=private"},
        {"HTTP_AUTHORIZATION": "Basic unverified"},
        {"HTTP_FORWARDED": "for=localhost"},
        {"HTTP_X_REVIEW_PRINCIPAL": "owner"},
    ],
)
def test_reject_host_identity_and_secret_urls(session, headers):
    _, app, _, received = session
    token, _ = login(app)
    assert not call(app, "/watches", token=token, **headers)[0].startswith("200")
    assert not received


@pytest.mark.parametrize(
    "headers",
    [
        {"CONTENT_LENGTH": "513"},
        {"CONTENT_TYPE": "application/json"},
        {"HTTP_TRANSFER_ENCODING": "chunked"},
        {"HTTP_SEC_FETCH_SITE": "cross-site"},
        {"HTTP_ORIGIN": None},
        {"CONTENT_LENGTH": "-1"},
    ],
)
def test_bootstrap_post_boundary(session, headers):
    _, app, _, _ = session
    csrf = challenge(app)
    assert (
        call(app, "/session/start", method="POST", basic=True, csrf=csrf, **headers)[0]
        == "403 Forbidden"
    )


def test_csrf_expiry_duplicates_and_restart(session):
    store, app, now, _ = session
    csrf = challenge(app)
    assert (
        call(
            app,
            "/session/start",
            method="POST",
            basic=True,
            raw=f"csrf={csrf}&csrf={csrf}".encode(),
        )[0]
        == "403 Forbidden"
    )
    now[0] += 600
    assert call(app, "/session/start", method="POST", basic=True, csrf=csrf)[0] == "403 Forbidden"
    csrf = challenge(app)
    app.tokens.clear()  # Process restart cannot resurrect an unsubmitted form.
    assert call(app, "/session/start", method="POST", basic=True, csrf=csrf)[0] == "403 Forbidden"
    assert not store.authenticate("x" * 43, "owner")


def test_session_limit_expiry_and_wrong_owner(session):
    store, _, now, _ = session
    tokens = [store.create("owner")[0] for _ in range(MAX_DEVICES)]
    with pytest.raises(RuntimeError):
        store.create("owner")
    assert not store.authenticate(tokens[0], "another-owner")
    now[0] -= 1
    assert not store.authenticate(tokens[0], "owner")
    now[0] += LIFETIME + 1
    assert store.create("owner")


def test_store_permissions_missing_and_wrong_schema(tmp_path):
    with pytest.raises(FileNotFoundError):
        SessionStore(tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists()
    directory = tmp_path / "auth"
    directory.mkdir(mode=0o700)
    store = SessionStore.initialize(directory / "devices.db")
    with pytest.raises(FileExistsError):
        SessionStore.initialize(store.path)
    store.path.chmod(0o644)
    with pytest.raises(ValueError):
        SessionStore(store.path)
    store.path.chmod(0o600)
    link = directory / "link.db"
    link.symlink_to(store.path)
    with pytest.raises(ValueError):
        SessionStore(link)
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("PRAGMA application_id=0")
    with pytest.raises(ValueError):
        SessionStore(store.path)


@pytest.mark.parametrize(
    "origin,hostname",
    [
        ("https://sniper.example:8443", "sniper.example"),
        ("https://127.0.0.1", "127.0.0.1"),
        ("https://sniper.example", None),
        ("http://sniper.example", "sniper.example"),
        ("https://ivc.example", "sniper.example"),
        ("https://sniper.example/", "sniper.example"),
        ("https://sniper.localhost", "sniper.localhost"),
        ("https://user@sniper.example", "sniper.example"),
    ],
)
def test_dedicated_origin_exact_required(origin, hostname):
    with pytest.raises(ValueError):
        validate_origin(origin, hostname)


def test_duplicate_cookie_rejected(session):
    _, app, _, _ = session
    token, _ = login(app)
    assert cookie_token({"HTTP_COOKIE": f"{COOKIE}={token}; {COOKIE}={token}"}) is None
    assert cookie_token({"HTTP_COOKIE": f"{COOKIE}=invalid"}) is None
    assert cookie_token({"HTTP_COOKIE": f"other=ok; {COOKIE}={token}"}) == token


def test_manifest_scope_and_icon(session):
    _, app, _, _ = session
    status, _, body = call(app, "/manifest.webmanifest")
    manifest = json.loads(body)
    assert status == "200 OK"
    assert manifest["scope"] == "/" and manifest["start_url"] == "/watches"
    assert manifest["display"] == "standalone"
    assert call(app, "/session/icon.png")[2].startswith(b"\x89PNG")


def test_builder_default_off_and_remembered_device(session):
    store, _, _, _ = session

    class Backend:
        def snapshot(self):
            return {"watches": [], "monitor_enabled": False}

        def status(self):
            return "ready"

    options = dict(
        backend=Backend(),
        watch_backend=Backend(),
        enable_watch_management=True,
        origin="https://sniper.example",
        principal="owner",
    )
    with pytest.raises(ValueError):
        build_app(**options, session_store=store, session_hostname="sniper.example")
    app = build_app(
        **options,
        enable_device_sessions=True,
        session_store=store,
        session_hostname="sniper.example",
        clock=store.clock,
    )
    token, _ = login(app)
    status, _, body = call(app, "/watches", token=token)
    assert status == "200 OK" and b"Device sign-in" in body
    assert b"/manifest.webmanifest" in body
    assert call(app, "/listing-review", token=token)[0] == "200 OK"
    assert call(app, "/listing-review", basic=True)[0] == "403 Forbidden"
    # Preauth password-work limit is unchanged: no provider/broker calls.
    for _ in range(30):
        call(app, "/session/login", basic=True)
    assert call(app, "/session/login", basic=True)[0] == "429 Too Many Requests"


def test_unknown_store_outcome_fails_closed(session):
    store, app, _, received = session
    token, _ = login(app)
    store.path.chmod(0o644)
    assert call(app, "/watches", token=token)[0] == "503 Service Unavailable"
    assert not received


def test_headers_and_bootstrap_no_session_created(session):
    store, app, _, _ = session
    status, headers, body = call(app, basic=True)
    assert status == "200 OK" and "Set-Cookie" not in headers
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "form-action 'self'" in headers["Content-Security-Policy"]
    nonce = re.search(rb'<style nonce="([^"]+)"', body)[1].decode()
    assert f"style-src 'nonce-{nonce}'" in headers["Content-Security-Policy"]
    assert b"/manifest.webmanifest" in body
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_caddy_session_template_identity_contract():
    from pathlib import Path

    text = (
        Path(__file__).parents[1] / "deploy/phone-review/Caddyfile.sessions.fragment"
    ).read_text()
    bootstrap = text[text.index("    @bootstrap") : text.index("    @session")]
    normal = text[text.index("    @session") :]
    assert bootstrap.index("import sniper_session_gate") < bootstrap.index("basicauth")
    assert bootstrap.index("basicauth") < bootstrap.index("reverse_proxy")
    assert "header_up X-Review-Principal {http.auth.user.id}" in bootstrap
    assert "header_up -X-Review-Principal" in normal
    assert "basicauth" not in normal
    assert "header_up -Authorization" in text
    assert "max_size 4096" in text
    assert "@session path /watches /watches/state /watches/save /listing-review" in text


def test_watch_csrf_is_bound_to_device_not_only_owner(session):
    store, _, _, _ = session
    saves = []

    class Backend:
        def snapshot(self):
            return {"watches": [], "monitor_enabled": False}

        def save(self, fields):
            saves.append(fields)
            return 1

    backend = Backend()
    app = build_app(
        backend=backend,
        watch_backend=backend,
        enable_watch_management=True,
        origin="https://sniper.example",
        principal="owner",
        enable_device_sessions=True,
        session_store=store,
        session_hostname="sniper.example",
        clock=store.clock,
    )
    first, _ = login(app)
    second, _ = login(app)
    state = json.loads(call(app, "/watches/state", token=first)[2])
    fields = dict(
        release="123",
        title="Fixture",
        currency="USD",
        price="40.00",
        any_price=False,
        enabled=False,
        revision=0,
        csrf=state["csrf"],
    )
    raw = json.dumps(fields).encode()
    assert (
        call(
            app,
            "/watches/save",
            method="POST",
            token=second,
            raw=raw,
            CONTENT_TYPE="application/json",
        )[0]
        == "403 Forbidden"
    )
    assert not saves
    assert (
        call(
            app,
            "/watches/save",
            method="POST",
            token=first,
            raw=raw,
            CONTENT_TYPE="application/json",
        )[0]
        == "200 OK"
    )
    assert len(saves) == 1


def test_full_capacity_bootstrap_rotation_is_atomic(session):
    store, app, _, _ = session
    tokens = [store.create("owner")[0] for _ in range(MAX_DEVICES)]
    replacement, _ = login(app, old=tokens[0])
    assert store.authenticate(replacement, "owner")
    assert not store.authenticate(tokens[0], "owner")
    assert all(store.authenticate(token, "owner") for token in tokens[1:])
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == MAX_DEVICES


def test_rotation_insert_failure_restores_old_session(session, monkeypatch):
    store, _, _, _ = session
    old, _ = store.create("owner")
    collision, _ = store.create("owner")
    monkeypatch.setattr("finder.discogs_device_sessions.secrets.token_urlsafe", lambda _: collision)
    with pytest.raises(sqlite3.IntegrityError):
        store.rotate("owner", old)
    assert store.authenticate(old, "owner")
    assert store.authenticate(collision, "owner")
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2


def test_capacity_failure_cannot_revoke_unknown_or_other_owner(session):
    store, _, _, _ = session
    foreign, _ = store.create("other-owner")
    tokens = [store.create("owner")[0] for _ in range(MAX_DEVICES - 1)]
    for token in (None, "invalid", "x" * 43, foreign):
        with pytest.raises(RuntimeError):
            store.rotate("owner", token)
    assert store.authenticate(foreign, "other-owner")
    assert all(store.authenticate(token, "owner") for token in tokens)


def test_rotation_failure_response_preserves_current_device(session, monkeypatch):
    store, app, _, _ = session
    old, _ = login(app)
    csrf = challenge(app)

    def fail(*_):
        raise sqlite3.OperationalError("synthetic write failure")

    monkeypatch.setattr(store, "rotate", fail)
    status, headers, _ = call(
        app, "/session/start", method="POST", csrf=csrf, basic=True, token=old
    )
    assert status == "503 Service Unavailable" and "Set-Cookie" not in headers
    assert store.authenticate(old, "owner")


def test_listing_query_only_reaches_independently_validating_handler(session):
    _, app, _, received = session
    token, _ = login(app)

    def checker(env, start):
        from urllib.parse import parse_qs

        assert "HTTP_COOKIE" not in env
        fields = parse_qs(env.get("QUERY_STRING", ""))
        # Fake checker owns semantic validation, not session middleware.
        valid = fields.get("release") == ["123"] and fields.get("under") == ["40"]
        received.append(env)
        start("200 OK" if valid else "400 Bad Request", [])
        return [b"validated-prefill" if valid else b"invalid-criteria"]

    app.app = checker
    query = "release=123&under=40&media=NM&sleeve=VG%2B"
    status, _, body = call(app, "/listing-review", token=token, QUERY_STRING=query)
    assert status == "200 OK" and body == b"validated-prefill"
    assert received[-1]["QUERY_STRING"] == query
    assert (
        call(app, "/listing-review", token=token, QUERY_STRING="release=not-an-id")[0]
        == "400 Bad Request"
    )
    count = len(received)
    for bad in (
        "token=secret",
        "session=secret",
        "csrf=secret",
        "release=1&release=2",
        "release=" + "1" * 2048,
    ):
        assert call(app, "/listing-review", token=token, QUERY_STRING=bad)[0] == "400 Bad Request"
    assert (
        call(app, "/listing-review", method="POST", token=token, QUERY_STRING=query)[0]
        == "400 Bad Request"
    )
    assert call(app, "/watches", token=token, QUERY_STRING=query)[0] == "400 Bad Request"
    assert len(received) == count


def test_listing_query_does_not_authenticate_or_leak_in_login_redirect(session):
    _, app, _, received = session
    status, headers, _ = call(app, "/listing-review", QUERY_STRING="release=123&under=40")
    assert status == "303 See Other" and headers["Location"] == "/session/login"
    assert not received
