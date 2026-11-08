import io
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest

from finder.discogs_phone_review import initialize_review_state
from finder.discogs_phone_review_server import build_app, validate_socket_directory
from finder.discogs_price_alerts import AggregatePriceAlerts
from finder.discogs_rules import RulesStore


@pytest.fixture
def hosted(tmp_path):
    db = tmp_path / "rules.db"
    RulesStore(db, create=True).close()
    AggregatePriceAlerts(db).set_enabled(True, expected_revision=0)
    with closing(sqlite3.connect(db)) as connection:
        initialize_review_state(connection)
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": 123,
                "release": {"id": 456},
                "condition": "Near Mint (NM or M-)",
                "sleeve_condition": "Very Good Plus (VG+)",
                "status": "For Sale",
                "price": {"value": "50", "currency": "USD"},
                "comments": "unplayed copy",
            },
        )

    return build_app(
        database=db,
        origin="https://review.example:8443",
        principal="review-owner",
        transport=httpx.MockTransport(provider),
    ), calls


def request(app, method="GET", fields=None, **headers):
    raw = urlencode(fields or {}).encode()
    env = {
        "HTTP_HOST": "review.example:8443",
        "PATH_INFO": "/listing-review",
        "REQUEST_METHOD": method,
        "HTTP_ORIGIN": "https://review.example:8443",
        "HTTP_X_REVIEW_PRINCIPAL": "review-owner",
        "CONTENT_LENGTH": str(len(raw)),
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": io.BytesIO(raw),
    }
    env.update(headers)
    result = []
    body = b"".join(
        app(env, lambda status, response_headers: result.extend([status, dict(response_headers)]))
    ).decode()
    return *result, body


def test_trusted_boundary_get_post_and_replay(hosted):
    app, calls = hosted
    status, headers, body = request(app)
    assert status == "200 OK"
    assert headers["Cache-Control"] == "no-store"
    fields = dict(
        csrf=re.search(r'name="csrf" value="([^"]+)"', body)[1],
        listing="123",
        release="456",
        under="75",
        media="NM",
        sleeve="VG+",
    )
    status, _, body = request(app, "POST", fields)
    assert status == "200 OK"
    assert "https://www.discogs.com/sell/item/123" in body
    assert len(calls) == 1
    assert request(app, "POST", fields)[0] == "409 Conflict"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_X_REVIEW_PRINCIPAL": None},
        {"HTTP_X_REVIEW_PRINCIPAL": "ntfy-owner"},
        {"HTTP_X_REVIEW_PRINCIPAL": "review-owner,other"},
        {"HTTP_AUTHORIZATION": "Basic attacker"},
        {"HTTP_COOKIE": "session=attacker"},
        {"HTTP_FORWARDED": "for=127.0.0.1"},
        {"HTTP_HOST": "review.example"},
        {"HTTP_HOST": "evil.example"},
        {"PATH_INFO": "/"},
        {"PATH_INFO": "/listing-review/"},
        {"QUERY_STRING": "secret=value"},
    ],
)
def test_boundary_rejects_misrouting_and_identity(hosted, headers):
    app, calls = hosted
    assert not request(app, **headers)[0].startswith("200")
    assert not calls


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_ORIGIN": "https://review.example"},
        {"HTTP_ORIGIN": "https://ntfy.example"},
        {"HTTP_ORIGIN": None},
        {"HTTP_SEC_FETCH_SITE": "cross-site"},
        {"HTTP_TRANSFER_ENCODING": "chunked"},
        {"CONTENT_LENGTH": "2049"},
    ],
)
def test_post_origin_and_body_rejected(hosted, headers):
    app, calls = hosted
    assert not request(app, "POST", {}, **headers)[0].startswith("200")
    assert not calls


def test_forwarded_identity_does_not_authenticate(hosted):
    app, calls = hosted
    assert (
        request(
            app,
            HTTP_X_REVIEW_PRINCIPAL=None,
            HTTP_X_FORWARDED_USER="review-owner",
            REMOTE_ADDR="127.0.0.1",
        )[0]
        == "403 Forbidden"
    )
    assert not calls


def test_rate_bound(hosted):
    app, calls = hosted
    for _ in range(30):
        assert request(app)[0] == "200 OK"
    assert request(app)[0] == "429 Too Many Requests"
    app.started -= 61
    assert request(app)[0] == "200 OK"
    assert not calls


@pytest.mark.parametrize(
    "origin",
    [
        "http://review.example",
        "https://a/",
        "https://a/path",
        "https://user@a",
        "https://a?x=1",
        "https://a#x",
        "https://a:bad",
    ],
)
def test_bad_origin(tmp_path, origin):
    with pytest.raises(ValueError):
        build_app(database=tmp_path / "db", origin=origin, principal="review-owner")


def test_socket_permissions_and_symlink(tmp_path):
    directory = tmp_path / "socket"
    directory.mkdir(mode=0o750)
    assert validate_socket_directory(directory) == directory / "review.sock"
    directory.chmod(0o770)
    with pytest.raises(ValueError):
        validate_socket_directory(directory)
    directory.chmod(0o750)
    link = tmp_path / "link"
    link.symlink_to(directory)
    with pytest.raises(ValueError):
        validate_socket_directory(link)


def test_wrong_socket_owner(tmp_path, monkeypatch):
    tmp_path.chmod(0o750)
    monkeypatch.setattr(os, "getuid", lambda: -1)
    with pytest.raises(ValueError):
        validate_socket_directory(tmp_path)


def test_proxy_template_auth_then_replace_identity():
    # Static contract check, NOT a real Caddy authentication or spoofing test.
    text = (Path(__file__).parents[1] / "deploy/phone-review/Caddyfile.fragment").read_text()
    assert text.index("forward_auth unix/") < text.index("basicauth {")
    assert text.index("basicauth {") < text.index("reverse_proxy unix/")
    assert "header_up X-Review-Principal {http.auth.user.id}" in text
    assert "header_up -Authorization" in text
    assert "header_up -Cookie" in text
    assert "header_up Host" not in text
    assert "@review path /listing-review" in text


def test_private_gate_limits_before_auth(hosted):
    app, calls = hosted
    for _ in range(30):
        assert (
            request(app, PATH_INFO="/_proxy_gate", HTTP_X_REVIEW_PRINCIPAL=None)[0]
            == "204 No Content"
        )
    assert (
        request(app, PATH_INFO="/_proxy_gate", HTTP_X_REVIEW_PRINCIPAL=None)[0]
        == "429 Too Many Requests"
    )
    assert not calls


def test_private_gate_wrong_method(hosted):
    app, calls = hosted
    assert request(app, "POST", PATH_INFO="/_proxy_gate")[0] == "405 Method Not Allowed"
    assert not calls


def test_launcher_only_private_socket(tmp_path, monkeypatch):
    import waitress

    from finder.discogs_phone_review_server import main

    directory = tmp_path / "run"
    directory.mkdir(mode=0o750)
    db = tmp_path / "rules.db"
    db.touch()
    calls = []
    monkeypatch.setattr(waitress, "serve", lambda app, **options: calls.append(options))
    monkeypatch.setattr("logging.disable", lambda _: None)
    main(
        [
            "--database",
            str(db),
            "--origin",
            "https://review.example:8443",
            "--principal",
            "review-owner",
            "--socket-directory",
            str(directory),
        ]
    )
    options = calls[0]
    assert options["unix_socket"] == str(directory / "review.sock")
    assert options["unix_socket_perms"] == "660"
    assert not {"host", "port", "listen", "trusted_proxy"} & options.keys()
    assert options["threads"] == 2
    assert options["connection_limit"] == options["backlog"] == 8
    assert options["max_request_body_size"] == 2048
    assert options["clear_untrusted_proxy_headers"]
    assert not options["expose_tracebacks"]
