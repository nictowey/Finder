"""Synthetic credentials only; all provider requests intercepted offline."""

import json
import os

import httpx
import pytest

from finder.discogs_app_auth import AppCredentials, AuthenticatedReleaseStatsReader
from finder.discogs_listing import ListingReadError, ReleaseStatsReader
from finder.discogs_price_worker import main


@pytest.fixture
def credentials(tmp_path):
    path = tmp_path / "app.json"
    path.write_text(
        json.dumps({"consumer_key": "fixture_key", "consumer_secret": "fixture_secret"})
    )
    path.chmod(0o600)
    return path


def test_header_only_fixed_https_no_ambient_configuration(credentials, monkeypatch, caplog):
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid:1234")
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/certificate")
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://api.discogs.com/marketplace/stats/123?curr_abbr=USD"
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Discogs key=fixture_key, secret=fixture_secret"
        assert "Finder/" in request.headers["User-Agent"]
        return httpx.Response(200, json={"num_for_sale": 1, "lowest_price": None})

    auth = AppCredentials(credentials)
    reader = AuthenticatedReleaseStatsReader(auth, transport=httpx.MockTransport(handle))
    assert reader.read(123, "USD")["num_for_sale"] == 1
    assert len(calls) == 1
    assert "fixture_" not in repr(auth) + repr(reader) + caplog.text


@pytest.mark.parametrize("mode", [0o400, 0o600])
def test_private_modes(credentials, mode):
    credentials.chmod(mode)
    assert AppCredentials(credentials)


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "permissions",
        "symlink",
        "hardlink",
        "fifo",
        "directory",
        "owner",
        "malformed",
        "large",
        "token",
        "newline",
        "extra",
        "empty",
    ],
)
def test_unsafe_configuration_fails_closed(credentials, tmp_path, monkeypatch, kind):
    if kind == "missing":
        credentials = tmp_path / "absent"
    elif kind == "permissions":
        credentials.chmod(0o644)
    elif kind == "symlink":
        link = tmp_path / "link"
        link.symlink_to(credentials)
        credentials = link
    elif kind == "hardlink":
        os.link(credentials, tmp_path / "link")
    elif kind == "fifo":
        credentials = tmp_path / "fifo"
        os.mkfifo(credentials, 0o600)
    elif kind == "directory":
        credentials = tmp_path
    elif kind == "owner":
        monkeypatch.setattr(os, "geteuid", lambda: -1)
    else:
        credentials.write_text(
            {
                "malformed": "{",
                "large": "a" * 1025,
                "token": '{"token":"fixture_secret"}',
                "newline": '{"consumer_key":"fixture_key","consumer_secret":"bad\\nheader"}',
                "extra": '{"consumer_key":"k","consumer_secret":"s","token":"t"}',
                "empty": '{"consumer_key":"","consumer_secret":"s"}',
            }[kind]
        )
    with pytest.raises(
        ValueError, match="^Private Discogs application configuration unavailable.$"
    ):
        AppCredentials(credentials)


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 429])
def test_no_redirect_retry_or_anonymous_fallback(credentials, status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "http://untrusted.invalid/steal"})

    reader = AuthenticatedReleaseStatsReader(
        AppCredentials(credentials), transport=httpx.MockTransport(handle)
    )
    with pytest.raises(ListingReadError) as caught:
        reader.read(123, "USD")
    assert caught.value.access_restricted == (status in {401, 403})
    assert len(calls) == 1


@pytest.mark.parametrize(
    "path,params",
    [
        ("/marketplace/listings/123", None),
        ("/releases/123", None),
        ("https://untrusted.invalid/", None),
        ("//untrusted.invalid/", None),
        ("/marketplace/stats/123?token=x", {"curr_abbr": "USD"}),
        ("/marketplace/stats/123", {"curr_abbr": "USD", "key": "x"}),
    ],
)
def test_auth_scope_cannot_expand(credentials, path, params):
    reader = AuthenticatedReleaseStatsReader(
        AppCredentials(credentials),
        transport=httpx.MockTransport(lambda r: pytest.fail("Unexpected network access")),
    )
    with pytest.raises(ListingReadError):
        reader._request(path, params=params)


def test_default_stays_anonymous():
    def handle(request):
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"num_for_sale": 0, "lowest_price": None})

    assert ReleaseStatsReader(transport=httpx.MockTransport(handle)).read(123, "USD")


def test_missing_file_stops_before_database_or_network(tmp_path, capsys):
    db = tmp_path / "absent.db"
    assert (
        main(["--database", str(db), "--poll", "--discogs-app-file", str(tmp_path / "absent")]) == 1
    )
    assert not db.exists()
    assert json.loads(capsys.readouterr().out)["status"] == "price_alert_worker_unavailable"


def test_auth_option_requires_poll(tmp_path):
    with pytest.raises(SystemExit) as caught:
        main(["--database", str(tmp_path / "absent"), "--discogs-app-file", "absent"])
    assert caught.value.code == 2


@pytest.mark.parametrize("status", [401, 403])
def test_authenticated_access_restriction_durably_stops(credentials, tmp_path, status):
    from finder.discogs_price_alerts import AggregatePriceAlerts
    from finder.discogs_rules import RulesStore

    db = tmp_path / "rules.db"
    RulesStore(db, create=True).close()
    now = [1800000000.0]
    alerts = AggregatePriceAlerts(db, clock=lambda: now[0], poll_seconds=300)
    alerts.save_rule(
        {
            "release_id": 123,
            "public_title": "Fixture",
            "currency": "USD",
            "any_price": True,
            "enabled": True,
        },
        expected_revision=0,
    )
    alerts.set_enabled(True, expected_revision=0)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status)

    reader = AuthenticatedReleaseStatsReader(
        AppCredentials(credentials), transport=httpx.MockTransport(handle), clock=lambda: now[0]
    )
    assert alerts.poll_once(reader)["status"] == "unavailable"
    assert not alerts.status()["enabled"]
    now[0] += 86401
    reopened = AggregatePriceAlerts(db, clock=lambda: now[0], poll_seconds=300)
    assert reopened.poll_once(reader) == {"status": "stopped", "requests": 0}
    assert len(requests) == 1
