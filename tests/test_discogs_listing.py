from datetime import UTC, datetime

import httpx
import pytest
from test_discogs_rules import target

from finder.discogs_completeness import RELEASE_MEDIA_TTL
from finder.discogs_listing import (
    ListingReader,
    ListingReadError,
    PublicReadBudget,
    listing_id,
    normalize,
    observation,
)
from finder.discogs_rules import evaluate


def payload():
    return dict(
        id=123,
        release={"id": 123},
        status="For Sale",
        condition="Mint (M)",
        sleeve_condition="Very Good Plus (VG+)",
        seller={"stats": {"rating": "100.0", "total": 100}},
        price={"value": 20.0, "currency": "USD"},
        shipping_price={"value": 2.50, "currency": "USD"},
        shipping_is_blocked=False,
    )


def test_public_read_fixed_route_and_unknown_final_costs():
    def handle(request):
        assert str(request.url) == "https://api.discogs.com/marketplace/listings/123"
        assert request.method == "GET" and "authorization" not in request.headers
        return httpx.Response(200, json=payload())

    listing = ListingReader(transport=httpx.MockTransport(handle)).read(
        "https://www.discogs.com/sell/item/123"
    )
    assert listing["reported_shipping"] == "2.50"
    obs = observation(listing)
    assert obs.shipping is None and obs.tax is None and obs.fees is None
    assert obs.destination_country is None and obs.pressing_evidence == "unknown"
    result = evaluate(target(), obs, now=datetime.now(UTC))
    assert not result["eligible_for_review"] and not result["purchase_allowed"]
    assert result["mode"] == "live_read_only" and result["total"] is None


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.test/123",
        "https://www.discogs.com/release/123",
        "https://www.discogs.com/sell/item/123?redirect=evil",
        "https://www.discogs.com@evil.test/sell/item/123",
        "0",
        True,
        1.2,
        "123/../../orders",
        "https://api.discogs.com/marketplace/orders",
    ],
)
def test_invalid_id_no_network(value):
    with pytest.raises(ListingReadError):
        listing_id(value)


@pytest.mark.parametrize("status", [301, 403, 404, 500])
def test_error_no_retry(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://evil.test/"})

    reader = ListingReader(transport=httpx.MockTransport(handle))
    with pytest.raises(ListingReadError, match=f"HTTP {status}"):
        reader.read(123)
    assert len(calls) == 1


def test_rate_limit_and_local_cooldown():
    clock = [0.0]
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})

    reader = ListingReader(transport=httpx.MockTransport(handle), clock=lambda: clock[0])
    with pytest.raises(ListingReadError, match="rate limit"):
        reader.read(123)
    clock[0] = 119
    with pytest.raises(ListingReadError, match="cooldown"):
        reader.read(123)
    assert len(calls) == 1


def test_timeout():
    def handle(request):
        raise httpx.ReadTimeout("secret provider body")

    with pytest.raises(ListingReadError, match="timed out") as error:
        ListingReader(transport=httpx.MockTransport(handle)).read(123)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("price", {}),
        ("price", {"value": "NaN", "currency": "USD"}),
        ("price", {"value": True, "currency": "USD"}),
        ("price", {"value": "1.001", "currency": "USD"}),
        ("price", {"value": "1", "currency": "usd"}),
        ("seller", {"stats": {"rating": "NaN", "total": True}}),
        ("seller", {"stats": {"rating": False, "total": -1}}),
        ("seller", None),
        ("condition", []),
        ("release", None),
    ],
)
def test_malformed_fields_fail_closed(field, value):
    data = payload()
    data[field] = value
    if field == "release":
        with pytest.raises(ListingReadError):
            normalize(data, 123)
        return
    listing = normalize(data, 123)
    assert not evaluate(target(), observation(listing), now=datetime.now(UTC))[
        "eligible_for_review"
    ]
    if field == "price":
        assert listing["item"] is None
    if field == "seller":
        assert listing["seller_percent"] is None and listing["feedback"] is None


def test_identity_currency_and_shipping_blocked():
    with pytest.raises(ListingReadError):
        normalize(payload(), 456)
    data = payload()
    data["shipping_price"]["currency"] = "EUR"
    data["shipping_is_blocked"] = True
    listing = normalize(data, 123)
    assert listing["shipping_blocked"] and listing["reported_shipping_currency"] == "EUR"
    assert observation(listing).shipping is None


def test_public_listing_preserves_generic_sleeve_without_treating_it_as_grade():
    data = payload()
    data["sleeve_condition"] = "Generic"
    listing = normalize(data, 123)
    assert listing["sleeve"] == "Generic"
    assert not evaluate(target(), observation(listing), now=datetime.now(UTC))[
        "eligible_for_review"
    ]


@pytest.mark.parametrize("body", [b"bad json", b"[]", b"{}", b"x" * 262145])
def test_bad_response(body):
    with pytest.raises(ListingReadError):
        ListingReader(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
        ).read(123)


def test_release_media_uses_exact_public_route_shared_budget_and_cached_projection():
    rate = [10.0]
    wall = [1_000_000_000.0]
    paths = []
    claims = []
    release = {"id": 123, "formats": [{"name": "Vinyl", "qty": "2"}], "secret": "discard"}

    def handle(request):
        paths.append(str(request.url))
        assert request.method == "GET"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(200, json=release)

    budget = PublicReadBudget(
        clock=lambda: rate[0],
        sleep=lambda seconds: rate.__setitem__(0, rate[0] + seconds),
        claim=lambda: claims.append("claim") or 0,
    )
    reader = ListingReader(
        transport=httpx.MockTransport(handle),
        clock=lambda: rate[0],
        wall_clock=lambda: wall[0],
    )
    first = reader.read_release_media("https://www.discogs.com/release/123-Example", budget=budget)
    assert first == (
        {"id": 123, "formats": [{"name": "Vinyl", "qty": "2"}]},
        {
            "source": "discogs_release_api",
            "release_id": 123,
            "fetched_at": datetime.fromtimestamp(wall[0], UTC).isoformat(),
        },
    )
    assert paths == ["https://api.discogs.com/releases/123"]
    assert claims == ["claim"]
    first[0]["formats"][0]["qty"] = "1"
    first[1]["source"] = "seller"
    second = reader.read_release_media(123, budget=budget)
    assert second[0]["formats"][0]["qty"] == "2"
    assert second[1]["source"] == "discogs_release_api"
    assert paths == ["https://api.discogs.com/releases/123"] and claims == ["claim"]


@pytest.mark.parametrize("value", [True, 0, -1, "../orders", "https://evil.test/releases/123"])
def test_release_media_invalid_identity_never_requests(value):
    calls = []
    reader = ListingReader(
        transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200))
    )
    with pytest.raises(ValueError):
        reader.read_release_media(value)
    assert calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"id": 124, "formats": [{"name": "Vinyl", "qty": "2"}]},
        {"id": 123, "formats": [{"name": "CD", "qty": "1"}]},
        {"id": 123, "formats": [{"name": "Vinyl", "qty": "unknown"}]},
        [123],
    ],
)
def test_release_media_wrong_identity_or_unsupported_format_is_not_cached(body):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=body)

    rate = [10.0]
    reader = ListingReader(
        transport=httpx.MockTransport(handle),
        clock=lambda: rate[0],
    )
    assert reader.read_release_media(123) is None
    assert reader._media_cache == {}
    rate[0] += 3
    assert reader.read_release_media(123) is None
    assert len(calls) == 2


@pytest.mark.parametrize("status", [403, 429, 500])
def test_release_media_failure_returns_none_without_cache(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"message": "not retained"})

    reader = ListingReader(transport=httpx.MockTransport(handle))
    assert reader.read_release_media(123) is None
    assert len(calls) == 1 and reader._media_cache == {}


def test_release_media_expired_cache_is_not_reused_after_refresh_failure():
    wall = [1_000_000_000.0]
    rate = [10.0]
    responses = [200, 500]
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            responses.pop(0), json={"id": 123, "formats": [{"name": "Vinyl", "qty": "2"}]}
        )

    reader = ListingReader(
        transport=httpx.MockTransport(handle),
        clock=lambda: rate[0],
        wall_clock=lambda: wall[0],
    )
    assert reader.read_release_media(123) is not None
    wall[0] += RELEASE_MEDIA_TTL
    rate[0] += 3
    assert reader.read_release_media(123) is None
    assert len(calls) == 2 and reader._media_cache == {}


def test_plain_scheduled_listing_read_still_makes_only_one_get():
    paths = []

    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=payload())

    reader = ListingReader(transport=httpx.MockTransport(handle))
    assert reader.read(123)["release_media_metadata"] is None
    assert paths == ["/marketplace/listings/123"]
