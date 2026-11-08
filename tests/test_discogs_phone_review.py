import io
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from urllib.parse import urlencode

import httpx
import pytest

from finder.discogs_phone_review import (
    PATH,
    PhoneReviewApp,
    _guard_authorizer,
    initialize_review_state,
)
from finder.discogs_price_alerts import AggregatePriceAlerts
from finder.discogs_read_guard import guard_row
from finder.discogs_rules import RulesStore


def payload():
    return {
        "id": 123,
        "release": {"id": 456},
        "condition": "Near Mint (NM or M-)",
        "sleeve_condition": "Very Good Plus (VG+)",
        "status": "For Sale",
        "price": {"value": "174.991", "currency": "USD"},
        "comments": "unplayed copy",
        "uri": "https://evil.invalid/",
        "shipping_is_blocked": True,
    }


def request(app, method="GET", fields=None, **overrides):
    raw = urlencode(fields or {}).encode()
    env = {
        "HTTP_HOST": "review.example",
        "PATH_INFO": PATH,
        "REQUEST_METHOD": method,
        "HTTP_ORIGIN": "https://review.example",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }
    env.update(overrides)
    output = []
    body = b"".join(
        app(env, lambda status, headers: output.extend([status, dict(headers)]))
    ).decode()
    return *output, body


def form(app, **values):
    status, headers, body = request(app)
    assert status == "200 OK"
    assert headers["Cache-Control"] == "no-store"
    return dict(
        csrf=re.search(r'name="csrf" value="([^"]+)"', body)[1],
        listing="123",
        release="456",
        under="175",
        media="NM",
        sleeve="VG+",
        **values,
    )


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "aggregate.db"
    RulesStore(path, create=True).close()
    AggregatePriceAlerts(path).set_enabled(True, expected_revision=0)
    with closing(sqlite3.connect(path)) as db:
        initialize_review_state(db)
    calls, data, now = [], payload(), [1800000000.0]

    def handler(req):
        calls.append(req)
        with closing(sqlite3.connect(path)) as db:
            assert guard_row(db)[0] is not None
        return httpx.Response(200, json=data)

    app = PhoneReviewApp(
        database=path,
        origin="https://review.example",
        authenticate=lambda _: "owner",
        transport=httpx.MockTransport(handler),
        clock=lambda: now[0],
    )
    return app, calls, data, now, path


def test_end_to_end_one_request_rounding_and_handoff(setup):
    app, calls, data, _, path = setup
    fields = form(app)
    status, headers, body = request(app, "POST", fields)
    assert status == "200 OK"
    assert "Needs review" in body and "175.00 USD" in body
    assert "sleeve minimum: Pass" in body
    assert "shipping, tax and fees" in body and "unverified" in body
    assert 'href="https://www.discogs.com/sell/item/123"' in body
    assert "evil.invalid" not in body and "Data provided by Discogs" in body
    assert len(calls) == 1
    assert str(calls[0].url) == "https://api.discogs.com/marketplace/listings/123?curr_abbr=USD"
    assert "authorization" not in calls[0].headers
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is None
    assert request(app, "POST", fields)[0] == "409 Conflict"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"HTTP_HOST": "evil.example"},
        {"HTTP_ORIGIN": "https://evil.example"},
        {"HTTP_ORIGIN": ""},
        {"HTTP_SEC_FETCH_SITE": "cross-site"},
        {"QUERY_STRING": "listing=123"},
        {"HTTP_TRANSFER_ENCODING": "chunked"},
        {"CONTENT_TYPE": "text/plain"},
        {"CONTENT_LENGTH": "99999"},
    ],
)
def test_request_protection(setup, change):
    app, calls, *_ = setup
    assert request(app, "POST", form(app), **change)[0].startswith(("400", "403"))
    assert not calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("listing", "https://evil.example/123"),
        ("release", "456?foo=1"),
        ("under", "NaN"),
        ("under", "0"),
        ("under", "175.001"),
        ("media", "Generic"),
        ("sleeve", "unknown"),
    ],
)
def test_invalid_input_no_network(setup, field, value):
    app, calls, *_ = setup
    fields = form(app)
    fields[field] = value
    assert request(app, "POST", fields)[0] == "400 Bad Request"
    assert not calls


def test_auth_fail_closed_and_principal_bound(setup):
    app, calls, *_ = setup
    fields = form(app)
    app.authenticate = None
    assert request(app)[0] == "403 Forbidden"
    assert request(app, "POST", fields)[0] == "403 Forbidden"
    app.authenticate = lambda _: "different-owner"
    assert request(app, "POST", fields)[0] == "409 Conflict"
    assert not calls


def test_expired_and_restart_tokens_do_not_read(setup):
    app, calls, _, now, path = setup
    fields = form(app)
    now[0] += 601
    assert request(app, "POST", fields)[0] == "409 Conflict"
    other = PhoneReviewApp(database=path, origin=app.origin, authenticate=lambda _: "owner")
    assert request(other, "POST", fields)[0] == "409 Conflict"
    assert not calls


def test_concurrent_double_submit_and_shared_pending(setup):
    app, calls, _, _, path = setup
    entered, finish = threading.Event(), threading.Event()

    def handler(req):
        calls.append(req)
        entered.set()
        assert finish.wait(5)
        return httpx.Response(200, json=payload())

    app.transport = httpx.MockTransport(handler)
    fields, independent = form(app), form(app)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(request, app, "POST", fields)
        assert entered.wait(5)
        assert request(app, "POST", fields)[0] == "409 Conflict"
        assert request(app, "POST", independent)[0] == "503 Service Unavailable"
        finish.set()
        assert future.result()[0] == "200 OK"
    assert len(calls) == 1


def test_interrupted_request_persists_guard_and_blocks_restart(setup):
    app, calls, _, now, path = setup

    def fail(req):
        calls.append(req)
        raise httpx.ConnectError("secret private transport message")

    app.transport = httpx.MockTransport(fail)
    status, _, body = request(app, "POST", form(app))
    assert status == "503 Service Unavailable" and "secret" not in body
    now[0] += 100000
    assert request(app, "POST", form(app))[0] == "503 Service Unavailable"
    assert len(calls) == 1
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is not None


@pytest.mark.parametrize("code", [301, 401, 403, 429, 503])
def test_provider_failures_no_retry_cooldown(setup, code):
    app, calls, *_ = setup

    def handler(req):
        calls.append(req)
        return httpx.Response(
            code, headers={"Location": "https://evil.example", "Retry-After": "60"}
        )

    app.transport = httpx.MockTransport(handler)
    assert request(app, "POST", form(app))[0] == "502 Bad Gateway"
    if code in (401, 403):
        assert "Page reads stopped" in request(app)[2]
    else:
        assert request(app, "POST", form(app))[0] == "503 Service Unavailable"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "comment", ["missing disc 2", "record is scratched", "<script>alert(1)</script>"]
)
def test_description_blocks_and_escapes(setup, comment):
    app, _, data, *_ = setup
    data["comments"] = comment
    data["price"]["value"] = "100"
    status, _, body = request(app, "POST", form(app))
    assert status == "200 OK" and "Needs review" in body
    assert "<script>" not in body


def test_match_is_not_purchase_qualified(setup):
    app, _, data, *_ = setup
    data["price"]["value"] = "100"
    body = request(app, "POST", form(app))[2]
    assert "Reported item criteria match" in body
    assert "Physical completeness is unverified" in body
    assert "No cart, checkout, order, payment" in body


def test_missing_db_no_replacement_and_write_restrictions(setup, tmp_path):
    app, calls, _, _, path = setup
    fields = form(app)
    app.database = tmp_path / "missing.db"
    assert request(app, "POST", fields)[0] == "503 Service Unavailable"
    assert not app.database.exists() and not calls
    with closing(sqlite3.connect(path)) as db:
        db.set_authorizer(_guard_authorizer)
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("DELETE FROM discogs_api_read_guard")
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("UPDATE discogs_monitor_control SET enabled=1")


@pytest.mark.parametrize("code", [401, 403])
def test_refusal_latch_survives_restart_without_stopping_aggregate(setup, code):
    app, calls, _, now, path = setup
    old_form = form(app)

    def refuse(req):
        calls.append(req)
        return httpx.Response(code)

    app.transport = httpx.MockTransport(refuse)
    assert request(app, "POST", form(app))[0] == "502 Bad Gateway"
    now[0] += 4
    assert request(app, "POST", old_form)[0] == "503 Service Unavailable"
    now[0] += 100000
    other = PhoneReviewApp(database=path, origin=app.origin, authenticate=lambda _: "owner")
    assert "Page reads stopped" in request(other)[2]
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is None
        assert db.execute("SELECT enabled FROM discogs_price_control").fetchone()[0] == 1
        initialize_review_state(db)  # Provisioning is idempotent, never resets refusal.
        assert db.execute("SELECT access_denied FROM discogs_phone_review_state").fetchone()[0] == 1
    assert len(calls) == 1


def test_stopped_control_and_revision_change_discard_result(setup):
    app, calls, _, _, path = setup
    fields = form(app)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE discogs_price_control SET enabled=0,revision=revision+1")
    assert request(app, "POST", fields)[0] == "503 Service Unavailable"
    assert not calls
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE discogs_price_control SET enabled=1,revision=revision+1")

    def stop(req):
        calls.append(req)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE discogs_price_control SET enabled=0,revision=revision+1")
        return httpx.Response(200, json=payload())

    app.transport = httpx.MockTransport(stop)
    status, _, body = request(app, "POST", form(app))
    assert status == "503 Service Unavailable" and "Reported item price:" not in body


def test_cross_site_subresource_does_not_mint_and_tokens_bounded(setup):
    app, calls, *_ = setup
    assert (
        request(
            app,
            HTTP_SEC_FETCH_SITE="cross-site",
            HTTP_SEC_FETCH_MODE="no-cors",
            HTTP_SEC_FETCH_DEST="image",
        )[0]
        == "403 Forbidden"
    )
    assert not app.tokens
    assert (
        request(
            app,
            HTTP_SEC_FETCH_SITE="cross-site",
            HTTP_SEC_FETCH_MODE="navigate",
            HTTP_SEC_FETCH_DEST="document",
        )[0]
        == "200 OK"
    )
    for _ in range(128):
        request(app)
    assert len(app.tokens) == 128
    assert "Too many open forms" in request(app)[2]
    assert not calls


def test_latch_write_failure_retains_guard_and_blocks_future_reads(setup, monkeypatch):
    from finder import discogs_phone_review as module

    app, calls, _, now, path = setup
    original = module._guard_authorizer

    def deny(action, table, *args):
        if action == sqlite3.SQLITE_UPDATE and table == "discogs_phone_review_state":
            return sqlite3.SQLITE_DENY
        return original(action, table, *args)

    def refuse(req):
        calls.append(req)
        return httpx.Response(403)

    monkeypatch.setattr(module, "_guard_authorizer", deny)
    app.transport = httpx.MockTransport(refuse)
    assert request(app, "POST", form(app))[0] == "503 Service Unavailable"
    now[0] += 4
    assert request(app, "POST", form(app))[0] == "503 Service Unavailable"
    assert len(calls) == 1
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is not None


def test_original_and_shipping_are_separate(setup):
    app, _, data, *_ = setup
    data["original_price"] = {"curr_abbr": "GBP", "value": "125"}
    body = request(app, "POST", form(app))[2]
    assert "Original provider price: 125.00 GBP" in body
    assert "Reported shipping: Unknown. Eligibility: Blocked or unknown" in body
    assert "not affiliated with Discogs" in body


@pytest.mark.parametrize("change", ["missing", "malformed", "missing_row"])
def test_missing_or_invalid_latch_fails_closed_without_repair(setup, change):
    app, calls, _, _, path = setup
    fields = form(app)
    with closing(sqlite3.connect(path)) as db, db:
        if change == "missing":
            db.execute("DROP TABLE discogs_phone_review_state")
        elif change == "malformed":
            db.execute("UPDATE discogs_phone_review_state SET schema_version=2")
        else:
            db.execute("DELETE FROM discogs_phone_review_state")
    assert request(app, "POST", fields)[0] == "503 Service Unavailable"
    assert request(app)[0] == "503 Service Unavailable"
    assert not calls


def test_authorizer_cannot_change_durability_or_identity(setup):
    *_, path = setup
    with closing(sqlite3.connect(path)) as db:
        db.set_authorizer(_guard_authorizer)
        for command in (
            "PRAGMA synchronous=OFF",
            "PRAGMA journal_mode=OFF",
            "PRAGMA application_id=0",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                db.execute(command)
