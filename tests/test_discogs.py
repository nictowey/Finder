import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from finder.adapters.discogs.adapter import DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.config import DiscogsSettings
from finder.errors import (
    CatalogAuthenticationError,
    CatalogRateLimitError,
    CatalogRequestError,
    CatalogResponseError,
    ConfigurationError,
)
from finder.matching import decide_match


@pytest.fixture
def discogs_search():
    return json.loads((Path(__file__).parent / "fixtures/discogs_search.json").read_text())


@pytest.fixture
def discogs_release():
    return json.loads((Path(__file__).parent / "fixtures/discogs_release.json").read_text())


@pytest.fixture
def discogs_settings():
    return DiscogsSettings(token="private-token", user_agent="Finder/0.2 test@example.com")


def test_release_normalization(discogs_release, observed_at):
    variant = normalize_release(discogs_release, observed_at)
    assert variant.catalog_variant_id == "111"
    assert variant.catalog_product_id == "99"
    assert variant.artists == ["Example Artist"]
    assert variant.identifiers["Barcode"] == ["0123456789012"]
    assert variant.labels[0]["catno"] == "EX-101"
    assert variant.formats[0]["text"] == "Blue"
    assert variant.resource_url.startswith("https://www.discogs.com/release/111")


def test_catalog_search_uses_only_database_and_release_endpoints(
    discogs_settings, discogs_search, discogs_release, observed_at
):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        assert request.headers["Authorization"] == "Discogs token=private-token"
        assert request.headers["User-Agent"] == "Finder/0.2 test@example.com"
        if request.url.path == "/database/search":
            assert request.url.params["type"] == "release"
            assert request.url.params["format"] == "Vinyl"
            assert request.url.params["genre"] == "Hip Hop"
            return httpx.Response(200, json=discogs_search)
        detail = dict(discogs_release)
        detail["id"] = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=detail)

    with DiscogsClient(discogs_settings, transport=httpx.MockTransport(handler)) as client:
        provider = DiscogsCatalogProvider(client, now=lambda: observed_at)
        variants = provider.search_releases("Example Artist Example Album", limit=2)
    assert paths == ["/database/search", "/releases/111", "/releases/222"]
    assert [variant.catalog_variant_id for variant in variants] == ["111", "222"]
    product = provider.product_for(variants[0])
    assert product.catalog_product_id == "99"
    assert product.resource_url == "https://www.discogs.com/master/99"


def test_listing_retrieval_diversifies_queries_deduplicates_and_reports_shared_barcode(
    discogs_settings, discogs_release, search_payload
):
    listing = normalize_listing(
        {
            **search_payload["itemSummaries"][0],
            "title": "Example Artist Example Album vinyl LP",
            "localizedAspects": [
                {"name": "Artist", "value": "Example Artist"},
                {"name": "UPC", "value": "0123456789012"},
                {"name": "Catalog Number", "value": "EX-101"},
            ],
        },
        datetime(2026, 9, 23, tzinfo=UTC),
    )
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/database/search":
            params = request.url.params
            assert params["type"] == "release" and params["format"] == "Vinyl"
            assert params["per_page"] == "10"
            if "barcode" in params:
                assert params["barcode"] == "0123456789012"
                return httpx.Response(200, json={"results": [{"id": 111}, {"id": 222}]})
            if "catno" in params:
                assert params["catno"] == "EX-101"
                return httpx.Response(200, json={"results": [{"id": 222}]})
            assert "Example Album" in params["q"]
            return httpx.Response(200, json={"results": [{"id": 111}]})
        detail = {**discogs_release, "id": int(request.url.path.rsplit("/", 1)[1])}
        return httpx.Response(200, json=detail)

    with DiscogsClient(discogs_settings, transport=httpx.MockTransport(handler)) as client:
        result = DiscogsCatalogProvider(client).search_for_listing(listing)
    assert result.query_kinds == ["barcode", "catno", "q"]
    assert [variant.catalog_variant_id for variant in result.variants] == ["111", "222"]
    assert len(calls) == 5  # three searches, two unique release detail calls
    assert not result.incomplete
    assert decide_match(listing, result.variants).outcome == "ambiguous"


def test_truncated_catalog_search_prevents_probable_pressing_claim(
    discogs_settings, discogs_release, search_payload
):
    listing = normalize_listing(
        {
            **search_payload["itemSummaries"][0],
            "title": "Example Artist Example Album vinyl LP",
            "localizedAspects": [
                {"name": "Artist", "value": "Example Artist"},
                {"name": "UPC", "value": "0123456789012"},
            ],
        },
        datetime(2026, 9, 23, tzinfo=UTC),
    )
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/database/search":
            if "barcode" in request.url.params:
                return httpx.Response(
                    200, json={"pagination": {"pages": 3}, "results": [{"id": 111}]}
                )
            return httpx.Response(200, json={"results": [{"id": 111}]})
        return httpx.Response(200, json=discogs_release)

    with DiscogsClient(discogs_settings, transport=httpx.MockTransport(handler)) as client:
        result = DiscogsCatalogProvider(client).search_for_listing(listing, limit=3)
    assert len(calls) == 3
    assert result.incomplete and result.search_truncated
    decision = decide_match(listing, result.variants, retrieval_incomplete=result.incomplete)
    assert decision.outcome == "family_only"
    assert "catalog_search_incomplete" in decision.missing_evidence


def test_listing_retrieval_caps_detail_requests_and_flags_omitted_identifiers(
    discogs_settings, discogs_release, search_payload
):
    listing = normalize_listing(
        {
            **search_payload["itemSummaries"][0],
            "localizedAspects": [
                {"name": "UPC", "value": "0123456789012"},
                {"name": "UPC", "value": "0123456789013"},
            ],
        },
        datetime(2026, 9, 23, tzinfo=UTC),
    )
    detail_ids = []

    def handler(request):
        if request.url.path == "/database/search":
            results = (
                [{"id": 111}, {"id": 222}] if "barcode" in request.url.params else [{"id": 222}]
            )
            return httpx.Response(200, json={"results": results})
        detail_ids.append(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=discogs_release)

    with DiscogsClient(discogs_settings, transport=httpx.MockTransport(handler)) as client:
        result = DiscogsCatalogProvider(client).search_for_listing(listing, limit=1)
    assert result.incomplete and result.identifiers_omitted
    assert result.candidate_limit_reached
    assert detail_ids == ["111"]


@pytest.mark.parametrize(
    "status,error",
    [
        (401, CatalogAuthenticationError),
        (403, CatalogAuthenticationError),
        (429, CatalogRateLimitError),
        (500, CatalogRequestError),
        (400, CatalogRequestError),
    ],
)
def test_discogs_failures_are_typed(discogs_settings, status, error):
    with DiscogsClient(
        discogs_settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(status)),
        sleep=lambda _: None,
    ) as client:
        with pytest.raises(error):
            client.get("/database/search")


def test_discogs_retry_after(discogs_settings):
    calls, delays = [], []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "3"})
            if len(calls) == 1
            else httpx.Response(200, json={"results": []})
        )

    with DiscogsClient(
        discogs_settings, transport=httpx.MockTransport(handler), sleep=delays.append
    ) as client:
        assert client.get("/database/search") == {"results": []}
    assert delays == [3]


@pytest.mark.parametrize("payload", [[], "bad", {"results": None}])
def test_malformed_search_response(discogs_settings, payload):
    with DiscogsClient(
        discogs_settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    ) as client:
        provider = DiscogsCatalogProvider(client)
        with pytest.raises(CatalogResponseError):
            provider.search_releases("query")


def test_empty_query_rejected_before_request(discogs_settings):
    with DiscogsClient(
        discogs_settings,
        transport=httpx.MockTransport(lambda request: pytest.fail("unexpected request")),
    ) as client:
        with pytest.raises(ConfigurationError, match="cannot be empty"):
            DiscogsCatalogProvider(client).search_releases(" ")


def test_client_rejects_non_catalog_endpoint(discogs_settings):
    with DiscogsClient(
        discogs_settings,
        transport=httpx.MockTransport(lambda request: pytest.fail("unexpected request")),
    ) as client:
        with pytest.raises(CatalogRequestError, match="CC0 catalog"):
            client.get("/marketplace/stats/111")
