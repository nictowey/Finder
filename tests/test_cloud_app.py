import io
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_discogs_rules import target

from finder.cloud_app import CloudApp, OwnerSession, create_app
from finder.discogs_rules import RulesStore

ORIGIN = "https://finder.example"
COOKIE = "synthetic-session=owner"


def session(**changes):
    data = {
        "user": {"email": "owner@example.test", "emailVerified": True},
        "session": {"expiresAt": (datetime.now(UTC) + timedelta(minutes=10)).isoformat()},
    }
    data.update(changes)
    return data


def verifier(data=None, status=200):
    def handle(request):
        assert str(request.url) == "https://auth.example/get-session"
        assert request.method == "GET" and request.headers["Cookie"] == COOKIE
        assert request.headers["Origin"] == ORIGIN
        return httpx.Response(status, json=session() if data is None else data)

    return OwnerSession(
        "https://auth.example", "owner@example.test", ORIGIN, transport=httpx.MockTransport(handle)
    )


def call(app, path="/", body=None, **headers):
    env = {
        "HTTP_HOST": "finder.example",
        "REQUEST_METHOD": "GET" if body is None else "POST",
        "PATH_INFO": path,
        "HTTP_COOKIE": COOKIE,
        "HTTP_ORIGIN": ORIGIN,
        "CONTENT_TYPE": "application/json",
        "HTTP_X_FINDER_CSRF": app.token(COOKIE),
    }
    raw = json.dumps(body).encode() if body is not None else b""
    env["CONTENT_LENGTH"] = str(len(raw))
    env.update(headers)
    env["wsgi.input"] = io.BytesIO(raw)
    result = []
    data = b"".join(
        app(env, lambda status, heads: result.extend([int(status.split()[0]), dict(heads)]))
    )
    return result[0], result[1], data


@pytest.fixture
def app(tmp_path):
    path = tmp_path / "rules.db"
    RulesStore(path, create=True).close()
    return CloudApp(path, ORIGIN, verifier())


def test_owner_page_csrf_cookie_binding_and_headers(app):
    code, headers, body = call(app)
    assert code == 200 and b"credentials:'same-origin'" in body
    assert headers["Cache-Control"] == "no-store" and headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors" in headers["Content-Security-Policy"]
    assert COOKIE.encode() not in body
    assert app.token("other") != app.token(COOKIE)
    assert call(app, HTTP_HOST="evil.example")[0] == 421
    assert call(app, HTTP_COOKIE="")[0] == 401
    assert call(app, "/api/target", {}, HTTP_ORIGIN="https://evil.example")[0] == 403
    assert call(app, "/api/target", {}, HTTP_X_FINDER_CSRF="wrong")[0] == 403
    assert call(app, "/api/target", {}, HTTP_SEC_FETCH_SITE="cross-site")[0] == 403
    assert call(app, HTTP_COOKIE="", HTTP_X_OWNER="owner@example.test")[0] == 401


@pytest.mark.parametrize(
    "data",
    [
        {
            "user": {"email": "other@example.test", "emailVerified": True},
            "session": {"expiresAt": "2099-01-01T00:00:00Z"},
        },
        {
            "user": {"email": "owner@example.test", "emailVerified": False},
            "session": {"expiresAt": "2099-01-01T00:00:00Z"},
        },
        {
            "user": {"email": "owner@example.test", "emailVerified": True},
            "session": {"expiresAt": "2000-01-01T00:00:00Z"},
        },
        {
            "user": {"email": "owner@example.test", "emailVerified": True},
            "session": {"expiresAt": "2099-01-01"},
        },
        {},
        [],
        {"user": None},
    ],
)
def test_invalid_session_fail_closed(data):
    assert verifier(data).verify(COOKIE) is False


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_auth_failure_no_redirect(status):
    assert verifier(status=status).verify(COOKIE) is False


def test_auth_timeout():
    def fail(request):
        raise httpx.ReadTimeout("secret")

    v = OwnerSession(
        "https://auth.example", "owner@example.test", ORIGIN, transport=httpx.MockTransport(fail)
    )
    assert v.verify(COOKIE) is False


def test_state_persists_revision_audit_and_kill(app):
    initial = json.loads(call(app, "/api/snapshot")[2])
    assert initial["kill_switch"] is True
    assert initial["hosted_schedule"]["paused"] is True
    assert initial["hosted_schedule"]["targets"] == []
    assert initial["hosted_schedule"]["purchase_allowed"] is False
    data = {"target": target().model_dump(), "expected_revision": 0}
    assert call(app, "/api/target", data)[0] == 200
    assert call(app, "/api/target", data)[0] == 409
    assert call(app, "/api/listing", {"release_id": 123, "listing": "123"})[0] == 409
    assert call(app, "/api/kill", {"killed": False, "expected_revision": 0})[0] == 200
    reopened = CloudApp(app.database, ORIGIN, verifier())
    snapshot = json.loads(call(reopened, "/api/snapshot")[2])
    assert snapshot["revision"] == 1 and snapshot["kill_switch"] is False
    assert snapshot["capabilities"]["checkout_available"] is False
    assert call(reopened, "/api/buy", {})[0] == 404
    db = sqlite3.connect(app.database)
    assert db.execute("SELECT count(*) FROM cloud_audit").fetchone()[0] == 5
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM cloud_audit")
    db.close()


def test_no_unconfigured_startup(monkeypatch):
    for key in [
        "FINDER_CLOUD_ORIGIN",
        "FINDER_CLOUD_OWNER_EMAIL",
        "FINDER_CLOUD_AUTH_URL",
        "FINDER_CLOUD_DATABASE",
    ]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(KeyError):
        create_app()


def test_oversize_and_source_spoof(app):
    assert call(app, "/api/target", {}, CONTENT_LENGTH="70000")[0] == 413
    assert (
        call(
            app,
            "/api/evaluate",
            {"release_id": 123, "observation": {"source": "discogs_public_listing"}},
        )[0]
        == 400
    )
    assert call(app, "/api/snapshot", HTTP_X_FORWARDED_HOST="evil.example")[0] == 200


def test_stale_kill_cannot_release_new_stop(app):
    assert call(app, "/api/kill", {"killed": False, "expected_revision": 0})[0] == 200
    assert call(app, "/api/kill", {"killed": True, "expected_revision": 1})[0] == 200
    assert call(app, "/api/kill", {"killed": False, "expected_revision": 0})[0] == 409
    assert json.loads(call(app, "/api/snapshot")[2])["kill_switch"] is True


def test_audit_failure_before_mutation_prevents_change(app):
    db = sqlite3.connect(app.database)
    db.execute(
        "CREATE TRIGGER fail_audit BEFORE INSERT ON cloud_audit "
        "BEGIN SELECT RAISE(ABORT,'blocked'); END;"
    )
    db.commit()
    db.close()
    assert (
        call(app, "/api/target", {"target": target().model_dump(), "expected_revision": 0})[0]
        == 503
    )
    assert json.loads(call(app, "/api/snapshot")[2])["revision"] == 0


def test_audit_failure_after_commit_leaves_intent_and_uncertain_response(app):
    db = sqlite3.connect(app.database)
    db.execute(
        "CREATE TRIGGER fail_completion BEFORE INSERT ON cloud_audit "
        "WHEN NEW.outcome='success' BEGIN SELECT RAISE(ABORT,'blocked'); END;"
    )
    db.commit()
    db.close()
    assert (
        call(app, "/api/target", {"target": target().model_dump(), "expected_revision": 0})[0]
        == 503
    )
    assert json.loads(call(app, "/api/snapshot")[2])["revision"] == 1
    db = sqlite3.connect(app.database)
    assert db.execute("SELECT outcome FROM cloud_audit").fetchall() == [("intent",)]
    db.close()


def test_non_ascii_csrf_and_deep_auth_fail_closed(app):
    assert call(app, "/api/target", {}, HTTP_X_FINDER_CSRF="é")[0] == 403
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=b"[" * 10000 + b"]" * 10000)
    )
    auth = OwnerSession("https://auth.example", "owner@example.test", ORIGIN, transport=transport)
    assert auth.verify(COOKIE) is False
