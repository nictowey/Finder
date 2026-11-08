import io
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

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
    assert headers["Referrer-Policy"] == "same-origin"
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
    from finder import discogs_review_backend as module

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


@pytest.mark.parametrize("origin", ["null", None, "", "https://evil.example"])
def test_form_policy_preserves_origin_gate_and_unused_token(setup, origin):
    app, calls, *_ = setup
    fields = form(app)
    status, headers, body = request(app)
    assert status == "200 OK"
    assert headers["Referrer-Policy"] == "same-origin"
    assert f'<form method="post" action="{PATH}">' in body
    assert headers["Cache-Control"] == "no-store"
    # Fetch §3.2 sets navigation POST Origin to null under no-referrer.
    # Fix the document policy, never accept an opaque/missing/foreign Origin.
    status, _, _ = request(app, "POST", fields, HTTP_ORIGIN=origin)
    assert status == "403 Forbidden" and calls == []
    status, _, body = request(app, "POST", fields, HTTP_SEC_FETCH_SITE="same-origin")
    assert status == "200 OK" and len(calls) == 1
    assert 'rel="noreferrer noopener" target="_blank"' in body
    assert request(app, "POST", fields)[0] == "409 Conflict"
    assert len(calls) == 1


class PageForms(HTMLParser):
    """Collect successful controls as a no-JavaScript browser would submit them."""

    def __init__(self, body):
        super().__init__()
        self.forms, self.copy_links = [], []
        self.current, self.select = None, None
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            assert self.current is None, "Forms must not be nested"
            self.current = dict(method=attrs["method"], action=attrs["action"], fields={})
            self.forms.append(self.current)
        if tag == "input":
            if "readonly" in attrs:
                self.copy_links.append(attrs["value"])
                assert "name" not in attrs, "The copy link must not submit as form data"
            if self.current is not None and "name" in attrs:
                self.current["fields"][attrs["name"]] = attrs.get("value", "")
        if tag == "select":
            self.select = attrs["name"]
        if tag == "option":
            self.selected = "selected" in attrs
        if tag == "button":
            assert attrs == {"type": "submit"}, "No method/action override or hidden intent"

    def handle_data(self, data):
        if self.select and self.selected:
            self.current["fields"][self.select] = data

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None
        if tag == "select":
            self.select = None
        if tag == "option":
            self.selected = False


def test_no_javascript_link_builder_to_prefill_to_review_and_replay(setup):
    app, calls, _, _, path = setup
    with closing(sqlite3.connect(path)) as db:
        before = list(db.iterdump())
    blank = PageForms(request(app)[2])
    review, builder = blank.forms
    assert review["method"] == "post" and builder["method"] == "get"
    assert review["action"] == builder["action"] == PATH
    assert "csrf" in review["fields"] and "csrf" not in builder["fields"]
    builder["fields"].update(
        listing="https://discogs.com/sell/item/123",
        release="456",
        under="150",
        media="VG+",
        sleeve="G+",
    )
    query = urlencode(builder["fields"])
    status, headers, body = request(app, QUERY_STRING=query)
    assert status == "200 OK" and calls == []
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "same-origin"
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "script-src" not in headers["Content-Security-Policy"]
    assert "<script" not in body and "<iframe" not in body
    assert "browser history" in body and "may sync between devices" in body
    assert "No Discogs read has been made" in body and "does not save a watch" in body
    page = PageForms(body)
    expected = dict(
        builder["fields"], listing="https://www.discogs.com/sell/item/123", under="150.00"
    )
    fields = page.forms[0]["fields"]
    assert {k: v for k, v in fields.items() if k != "csrf"} == expected
    assert page.forms[1]["fields"] == expected
    link = page.copy_links[0]
    assert link.startswith(app.origin + PATH + "?")
    assert "csrf" not in link and fields["csrf"] not in link
    assert "&amp;" in body and "&amp;" not in link
    assert "Location" not in headers
    # Reloading or opening the prepared link is still GET-only with a fresh token.
    again = PageForms(request(app, QUERY_STRING=urlsplit(link).query)[2])
    assert again.copy_links == [link]
    assert again.forms[0]["fields"]["csrf"] != fields["csrf"]
    with closing(sqlite3.connect(path)) as db:
        assert list(db.iterdump()) == before
    assert calls == []
    status, _, body = request(app, "POST", fields)
    assert status == "200 OK" and len(calls) == 1
    assert 'href="https://www.discogs.com/sell/item/123"' in body
    assert 'rel="noreferrer noopener" target="_blank"' in body
    assert request(app, "POST", fields)[0] == "409 Conflict"
    assert len(calls) == 1


def test_criteria_only_bookmark_leaves_listing_blank_and_is_editable(setup):
    app, calls, *_ = setup
    query = urlencode(dict(listing="", release="456", under="125", media="VG", sleeve="G"))
    status, _, body = request(app, QUERY_STRING=query)
    assert status == "200 OK" and calls == []
    page = PageForms(body)
    assert "listing=" not in page.copy_links[0]
    fields = page.forms[0]["fields"]
    assert fields["listing"] == "" and fields["under"] == "125.00"
    # Query values are suggestions, not hidden authorization or saved watch rules.
    fields.update(listing="123", under="100", media="M", sleeve="M")
    status, _, body = request(app, "POST", fields)
    assert status == "200 OK" and len(calls) == 1
    assert "strictly under 100.00 USD" in body
    assert "Minimum media: M; minimum sleeve: M" in body


@pytest.mark.parametrize(
    "query,expected",
    [
        ("", {}),
        ("listing=", {}),
        (
            "listing=https%3A%2F%2Fwww.discogs.com%2Fsell%2Fitem%2F123",
            {"listing": "https://www.discogs.com/sell/item/123"},
        ),
        ("release=456", {"release": "456"}),
        ("under=000150.1", {"under": "150.10"}),
        ("media=VG%2B&sleeve=G%2B", {"media": "VG+", "sleeve": "G+"}),
    ],
)
def test_optional_prefills_are_individually_validated_without_provider_calls(
    setup, query, expected
):
    app, calls, *_ = setup
    status, _, body = request(app, QUERY_STRING=query)
    assert status == "200 OK" and calls == []
    page = PageForms(body)
    assert all(page.forms[0]["fields"][key] == value for key, value in expected.items())


@pytest.mark.parametrize(
    "listing",
    [
        "123",
        "http://www.discogs.com/sell/item/123",
        "//www.discogs.com/sell/item/123",
        "https://evil.invalid/sell/item/123",
        "https://www.discogs.com.evil.invalid/sell/item/123",
        "https://user:password@www.discogs.com/sell/item/123",
        "https://www.discogs.com:443/sell/item/123",
        "https://www.discogs.com/sell/item/123/",
        "https://www.discogs.com/sell/item/123?curr_abbr=USD",
        "https://www.discogs.com/sell/item/123#details",
        "https://www.discogs.com/release/123",
        "https://www.discogs.com/sell/item/0",
        "https://www.discogs.com/sell/item/0123",
        "https://www.discogs.com/sell/item/9007199254740992",
        "https://www.discogs.com/sell/item/12345678901234567",
        "https://www.discogs.com/sell/item/1\u0662\u0663",
        " https://www.discogs.com/sell/item/123",
        "https://www.discogs.com/sell/item/123\n",
        "https://www.discogs.com/sell/item/123\x00",
        "https://www.discogs.com\\@evil.invalid/123",
        "https://www.discogs.com/sell/item/../123",
        "https://www.discogs.com/sell/item/%31%32%33",
        '<script>alert("x")</script>',
        'https://www.discogs.com/sell/item/123" autofocus onfocus="x',
    ],
)
def test_prefill_invalid_urls_fail_before_token_or_provider_call(setup, listing):
    app, calls, *_ = setup
    status, headers, body = request(app, QUERY_STRING=urlencode({"listing": listing}))
    assert status == "400 Bad Request"
    assert headers["Cache-Control"] == "no-store" and "Location" not in headers
    assert not app.tokens and not calls
    assert "<form" not in body and "<script>" not in body and "evil.invalid" not in body


@pytest.mark.parametrize(
    "query",
    [
        "unknown=",
        "csrf=token",
        "release=456&release=789",
        "listing=&listing=",
        "release=456&%72elease=789",
        "media=VG&media=",
        "release=456&under=1&media=NM&sleeve=NM&listing=&extra=1",
        "release=",
        "under=",
        "media=",
        "sleeve=",
        "release=0",
        "release=01",
        "release=9007199254740992",
        "release=456%0a",
        "release=%EF%BC%94%EF%BC%95%EF%BC%96",
        "under=0",
        "under=-1",
        "under=NaN",
        "under=1e2",
        "under=150.001",
        "under=10000000",
        "under=%D9%A1%D9%A0",
        "media=Generic",
        "sleeve=unknown",
        "media=VG+",
        "release=%",
        "release=%4",
        "release=%GG",
        "release=%ff",
        "release=%25456",
        "release=456&",
        "release",
        "release=456;under=150",
        "release=4 56",
        "release=456\r\n",
        "release=\u0664\u0665\u0666",
        "release=" + "4" * 2048,
        "release=%3Cscript%3E",
        "under=1%22%20onfocus%3D%22x",
    ],
)
def test_prefill_rejects_unknown_duplicate_malformed_and_injected_values(setup, query):
    app, calls, *_ = setup
    status, _, body = request(app, QUERY_STRING=query)
    assert status == "400 Bad Request" and not app.tokens and not calls
    assert "<form" not in body and "<script>" not in body


def test_prepared_get_preserves_auth_and_cross_site_navigation_boundary(setup):
    app, calls, *_ = setup
    query = urlencode({"listing": "https://www.discogs.com/sell/item/123"})
    app.authenticate = None
    assert request(app, QUERY_STRING=query)[0] == "403 Forbidden"
    assert not app.tokens
    app.authenticate = lambda _: "owner"
    assert request(app, QUERY_STRING=query, HTTP_SEC_FETCH_SITE="cross-site")[0] == "403 Forbidden"
    assert not app.tokens
    assert (
        request(
            app,
            QUERY_STRING=query,
            HTTP_SEC_FETCH_SITE="cross-site",
            HTTP_SEC_FETCH_MODE="navigate",
            HTTP_SEC_FETCH_DEST="document",
        )[0]
        == "200 OK"
    )
    assert len(app.tokens) == 1 and not calls


@pytest.mark.parametrize("suffix", ["&listing=", "&csrf=", "&unknown=", "&under=%", "&media=%ff"])
def test_post_malformed_or_duplicate_parameters_never_read(setup, suffix):
    app, calls, *_ = setup
    fields = form(app)
    raw = (urlencode(fields) + suffix).encode("ascii")
    assert (
        request(
            app, "POST", fields, CONTENT_LENGTH=str(len(raw)), **{"wsgi.input": io.BytesIO(raw)}
        )[0]
        == "400 Bad Request"
    )
    assert not calls
