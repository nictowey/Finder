"""Explicit IP opt-in tests. Public-address literals are fixtures, never contacted."""

import json
import re
from pathlib import Path

import pytest
from test_discogs_device_sessions import call

from finder.discogs_device_sessions import COOKIE, LIFETIME, SessionStore, validate_origin
from finder.discogs_phone_review_server import build_app, main

# A syntactically public IPv4 fixture, not deployment authorization or a target.
PUBLIC_IP = "8.8.8.8"
ORIGIN = "https://" + PUBLIC_IP


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "0.0.0.0",
        "100.64.0.1",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        "198.18.0.1",
        "224.0.0.1",
        "239.255.255.255",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "2001:4860::1",
        "::ffff:8.8.8.8",
        "008.008.008.008",
        "8.8.8.8.",
        " 8.8.8.8",
        "8.8.8.8 ",
        "134744072",
        "0x08080808",
        "8.8.8.8:443",
        "example.com",
        "8.8.8",
        "256.1.1.1",
    ],
)
def test_ip_must_be_canonical_global_unicast_ipv4(address):
    with pytest.raises(ValueError):
        validate_origin("https://" + address, None, allow_ip_origin=True, ip_address=address)


@pytest.mark.parametrize(
    "origin",
    [
        "http://8.8.8.8",
        "https://8.8.8.8:443",
        "https://8.8.8.8:8443",
        "https://8.8.4.4",
        "https://8.8.8.8/",
        "https://8.8.8.8/path",
        "https://user@8.8.8.8",
        "https://8.8.8.8?x=1",
        "https://8.8.8.8#fragment",
        "https://[8.8.8.8]",
        "https://sniper.example",
    ],
)
def test_ip_origin_is_exact_canonical_https(origin):
    with pytest.raises(ValueError):
        validate_origin(origin, None, allow_ip_origin=True, ip_address=PUBLIC_IP)


@pytest.mark.parametrize(
    "hostname,allow,address",
    [
        (PUBLIC_IP, False, None),
        (None, False, PUBLIC_IP),
        (None, True, None),
        ("sniper.example", True, PUBLIC_IP),
        (PUBLIC_IP, True, PUBLIC_IP),
        (None, 1, PUBLIC_IP),
        (None, True, 134744072),
    ],
)
def test_ip_requires_explicit_opt_in_address_and_no_hostname(hostname, allow, address):
    with pytest.raises(ValueError):
        validate_origin(ORIGIN, hostname, allow_ip_origin=allow, ip_address=address)


def test_dns_default_and_explicit_ip_are_separate():
    assert validate_origin("https://sniper.example", "sniper.example") == "sniper.example"
    assert validate_origin(ORIGIN, None, allow_ip_origin=True, ip_address=PUBLIC_IP) == PUBLIC_IP
    with pytest.raises(ValueError):
        validate_origin(ORIGIN, PUBLIC_IP)


def test_ip_cookie_login_watch_csrf_logout_and_wrong_hosts(tmp_path):
    directory = tmp_path / "auth"
    directory.mkdir(mode=0o700)
    store = SessionStore.initialize(directory / "devices.db")
    saved = []

    class Backend:
        def snapshot(self):
            return {"watches": [], "monitor_enabled": False}

        def save(self, fields):
            saved.append(fields)

        def status(self):
            return "ready"

    backend = Backend()
    options = dict(
        backend=backend,
        watch_backend=backend,
        enable_watch_management=True,
        origin=ORIGIN,
        principal="owner",
        session_store=store,
        allow_ip_session_origin=True,
        session_ip_address=PUBLIC_IP,
    )
    with pytest.raises(ValueError):
        build_app(**options)
    app = build_app(**options, enable_device_sessions=True)

    def request(path, **kwargs):
        return call(app, path, HTTP_HOST=PUBLIC_IP, HTTP_ORIGIN=ORIGIN, **kwargs)

    status, _, body = request("/session/login", basic=True)
    assert status == "200 OK"
    csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
    status, headers, _ = request("/session/start", method="POST", basic=True, csrf=csrf)
    assert status == "303 See Other"
    cookie = headers["Set-Cookie"]
    assert all(
        value in cookie
        for value in [
            COOKIE,
            "Secure",
            "HttpOnly",
            "SameSite=Strict",
            "Path=/",
            f"Max-Age={LIFETIME}",
        ]
    )
    assert "Domain" not in cookie
    token = cookie.split(";", 1)[0].split("=", 1)[1]
    assert request("/watches", token=token)[0] == "200 OK"
    assert request("/listing-review", token=token)[0] == "200 OK"
    for host in ["sniper.example", PUBLIC_IP + ":443", PUBLIC_IP + ":8443", "8.8.4.4"]:
        assert call(app, "/watches", token=token, HTTP_HOST=host)[0] == "421 Misdirected Request"
    state = json.loads(request("/watches/state", token=token)[2])
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
            token=token,
            HTTP_HOST=PUBLIC_IP,
            HTTP_ORIGIN="https://sniper.example",
            raw=raw,
            CONTENT_TYPE="application/json",
        )[0]
        == "403 Forbidden"
    )
    assert not saved
    assert (
        request(
            "/watches/save", method="POST", token=token, raw=raw, CONTENT_TYPE="application/json"
        )[0]
        == "200 OK"
    )
    assert len(saved) == 1
    body = request("/session", token=token)[2]
    csrf = re.search(rb'action="/session/logout".*?name="csrf" value="([^"]+)"', body)[1].decode()
    assert request("/session/logout", method="POST", token=token, csrf=csrf)[0] == "200 OK"
    assert request("/watches/state", token=token)[0] == "401 Unauthorized"


@pytest.mark.parametrize(
    "extra",
    [
        ["--allow-ip-session-origin"],
        ["--session-ip-address", PUBLIC_IP],
        ["--allow-ip-session-origin", "--session-ip-address", PUBLIC_IP],
    ],
)
def test_ip_cli_options_never_enable_sessions_on_their_own(tmp_path, extra):
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--broker-socket",
                str(tmp_path / "broker.sock"),
                "--broker-user",
                "fixture",
                "--origin",
                ORIGIN,
                "--principal",
                "owner",
                "--socket-directory",
                str(tmp_path),
                *extra,
            ]
        )
    assert caught.value.code == 2


def test_ip_proxy_template_has_explicit_external_tls_and_scoped_challenge():
    text = (
        Path(__file__).parents[1] / "deploy/phone-review/Caddyfile.ip-sessions.fragment"
    ).read_text()
    assert "https://{$REVIEW_IP_ADDRESS}" in text
    assert "tls {$REVIEW_IP_CERT_FILE} {$REVIEW_IP_KEY_FILE}" in text
    assert "http://{$REVIEW_IP_ADDRESS}" in text
    assert "path /.well-known/acme-challenge/*" in text
    assert "method GET HEAD" in text
    assert "root * {$REVIEW_IP_ACME_WEBROOT}" in text
    assert 'respond "Not found" 404' in text
    assert "header_up X-Review-Principal {http.auth.user.id}" in text
    assert "header_up -X-Review-Principal" in text
    assert "basicauth" in text
    assert "header_up -Authorization" in text
    assert "max_size 4096" in text
    assert PUBLIC_IP not in text


def test_ip_instance_template_preserves_old_socket_and_db_isolation():
    root = Path(__file__).parents[1] / "deploy/phone-review"
    proxy = (root / "Caddyfile.ip-sessions.fragment").read_text()
    unit = (root / "sniper-session.service.example").read_text()
    old = (root / "sniper-review.service").read_text()
    assert "unix//run/sniper-session/review.sock" in proxy
    assert "unix//run/sniper-review/review.sock" not in proxy
    assert "RuntimeDirectory=sniper-session\n" in unit
    assert "--socket-directory /run/sniper-session" in unit
    assert "--broker-socket /run/sniper-review-broker/broker.sock" in unit
    assert "User=sniper-review\n" in unit
    assert "ReadWritePaths=/run/sniper-session /var/lib/sniper-review-auth" in unit
    assert "InaccessiblePaths=/var/lib/sniper " in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "REPLACE_WITH_APPROVED_PUBLIC_IPV4" in unit
    assert "--allow-ip-session-origin" in unit
    assert "--session-ip-address ${SNIPER_SESSION_IP}" in unit
    assert "--session-database /var/lib/sniper-review-auth/devices.db" in unit
    assert "--session-database" not in old
    assert "--socket-directory /run/sniper-review" in old


def test_ip_cli_builds_only_separate_private_socket(tmp_path, monkeypatch):
    import types

    import waitress

    auth = tmp_path / "auth"
    auth.mkdir(mode=0o700)
    store = SessionStore.initialize(auth / "devices.db")
    runtime = tmp_path / "session"
    runtime.mkdir(mode=0o750)
    monkeypatch.setattr("pwd.getpwnam", lambda _: types.SimpleNamespace(pw_uid=1234))
    monkeypatch.setattr("logging.disable", lambda _: None)
    launches = []
    monkeypatch.setattr(waitress, "serve", lambda app, **options: launches.append((app, options)))
    main(
        [
            "--broker-socket",
            str(tmp_path / "broker.sock"),
            "--broker-user",
            "fixture",
            "--origin",
            ORIGIN,
            "--principal",
            "owner",
            "--socket-directory",
            str(runtime),
            "--enable-device-sessions",
            "--allow-ip-session-origin",
            "--session-ip-address",
            PUBLIC_IP,
            "--session-database",
            str(store.path),
        ]
    )
    app, options = launches[0]
    assert options["unix_socket"] == str(runtime / "review.sock")
    assert options["unix_socket_perms"] == "660"
    assert not {"host", "port", "listen", "trusted_proxy"} & options.keys()
    assert options["threads"] == 2 and options["connection_limit"] == 8
    assert call(app, basic=True, HTTP_HOST=PUBLIC_IP, HTTP_ORIGIN=ORIGIN)[0] == "200 OK"
