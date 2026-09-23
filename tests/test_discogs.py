import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from finder.watchlist import WatchTarget, assess_watch_target


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
            assert "genre" not in request.url.params
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


def test_exact_numbered_target_survives_zero_search_results_for_buyer_review(
    discogs_settings, discogs_release, search_payload, observed_at
):
    """Synthetic positive with no seller identifier; no actual marketplace data is stored."""
    listing = normalize_listing(
        {
            **search_payload["itemSummaries"][0],
            "title": "Future DS2 purple hand-numbered vinyl",
            "localizedAspects": [
                {"name": "Artist", "value": "Future"},
                {"name": "Color", "value": "Purple"},
                {"name": "Features", "value": "Numbered"},
            ],
        },
        observed_at,
    )
    release = {
        **discogs_release,
        "id": 333,
        "master_id": 33,
        "title": "DS2",
        "artists": [{"name": "Future"}],
        "formats": [
            {
                "name": "Vinyl",
                "qty": "2",
                "descriptions": ["LP", "Club Edition", "Limited Edition", "Numbered"],
                "text": "Purple",
            }
        ],
        "identifiers": [],
    }
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/database/search":
            return httpx.Response(200, json={"results": []})
        assert request.url.path == "/releases/333"
        return httpx.Response(200, json=release)

    with DiscogsClient(discogs_settings, transport=httpx.MockTransport(handler)) as client:
        retrieval = DiscogsCatalogProvider(client, now=lambda: observed_at).search_for_listing(
            listing, target_release_id=333
        )
    assert calls == ["/releases/333", "/database/search", "/database/search"]
    assert retrieval.query_kinds == ["q", "target_family"]
    assert [variant.catalog_variant_id for variant in retrieval.variants] == ["333"]
    assert retrieval.target_not_in_search and retrieval.incomplete
    decision = decide_match(listing, retrieval.variants, retrieval_incomplete=retrieval.incomplete)
    assert decision.outcome == "family_only"
    assert decision.candidate_ids == ["333"]
    assert "pressing_identifier" in decision.missing_evidence
    target = WatchTarget(
        id="numbered-ds2",
        marketplace="ebay",
        catalog_source="discogs",
        variant_id="333",
        maximum_delivered_subtotal=Decimal("40"),
        currency="USD",
        destination_country="US",
        destination_postal_code="10001",
        created_at=observed_at,
    )
    assessment = assess_watch_target(
        target,
        listing,
        decision,
        as_of=observed_at,
        maximum_age=timedelta(hours=1),
        destination_quote_verified=False,
    )
    assert assessment.status == "review"
    assert "exact_pressing_unconfirmed" in assessment.reasons
    assert "catalog_search_incomplete" in assessment.reasons
    assert "numbered_structured_claim_missing" not in assessment.reasons

    # An otherwise similar ordinary-copy listing has less supporting evidence; both
    # remain review-only, but the missing numbered claim is visible to the reviewer.
    ordinary_listing = listing.model_copy(
        update={
            "title": "Future DS2 purple vinyl",
            "item_specifics": {
                key: values for key, values in listing.item_specifics.items() if key != "Features"
            },
        }
    )
    ordinary_decision = decide_match(
        ordinary_listing, retrieval.variants, retrieval_incomplete=retrieval.incomplete
    )
    ordinary_assessment = assess_watch_target(
        target,
        ordinary_listing,
        ordinary_decision,
        as_of=observed_at,
        maximum_age=timedelta(hours=1),
        destination_quote_verified=False,
    )
    assert ordinary_assessment.status == "review"
    assert "numbered_structured_claim_missing" in ordinary_assessment.reasons


def test_target_reserves_one_detail_slot_when_search_returns_competitors(
    discogs_settings, discogs_release, search_payload, observed_at
):
    listing = normalize_listing(search_payload["itemSummaries"][0], observed_at)
    details = []

    def handler(request):
        if request.url.path == "/database/search":
            return httpx.Response(200, json={"results": [{"id": 111}, {"id": 222}]})
        release_id = int(request.url.path.rsplit("/", 1)[1])
        details.append(release_id)
        return httpx.Response(200, json={**discogs_release, "id": release_id})

    with DiscogsClient(discogs_settings, transport=httpx.MockTransport(handler)) as client:
        result = DiscogsCatalogProvider(client).search_for_listing(
            listing, limit=1, target_release_id=333
        )
    assert details == [333]
    assert result.target_not_in_search and result.candidate_limit_reached
    assert result.incomplete


def test_target_catalog_search_adds_competing_pressings_without_claiming_seller_recall(
    discogs_settings, discogs_release, search_payload, observed_at
):
    listing = normalize_listing(
        {
            **search_payload["itemSummaries"][0],
            "title": "Example Artist Example Album numbered vinyl sealed collectible copy",
            "localizedAspects": [{"name": "Artist", "value": "Example Artist"}],
        },
        observed_at,
    )
    queries = []

    def handler(request):
        if request.url.path == "/database/search":
            query = request.url.params["q"]
            queries.append(query)
            ids = [111, 222] if query == "Example Artist Example Album" else []
            return httpx.Response(200, json={"results": [{"id": id} for id in ids]})
        id = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json={**discogs_release, "id": id})

    with DiscogsClient(discogs_settings, transport=httpx.MockTransport(handler)) as client:
        retrieval = DiscogsCatalogProvider(client).search_for_listing(
            listing, target_release_id=111
        )
    assert queries == [listing.title, "Example Artist Example Album"]
    assert retrieval.query_kinds == ["q", "target_family"]
    assert [variant.catalog_variant_id for variant in retrieval.variants] == ["111", "222"]
    assert retrieval.target_not_in_search and retrieval.incomplete
    decision = decide_match(listing, retrieval.variants, retrieval_incomplete=retrieval.incomplete)
    assert decision.outcome == "family_only"
    assert set(decision.candidate_ids) == {"111", "222"}


@pytest.mark.parametrize("invalid", [0, -1, True, "333"])
def test_target_release_id_must_be_positive_integer(
    discogs_settings, search_payload, observed_at, invalid
):
    listing = normalize_listing(search_payload["itemSummaries"][0], observed_at)
    with DiscogsClient(
        discogs_settings,
        transport=httpx.MockTransport(lambda _: pytest.fail("Invalid target used network")),
    ) as client:
        with pytest.raises(ConfigurationError, match="positive integer"):
            DiscogsCatalogProvider(client).search_for_listing(listing, target_release_id=invalid)


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
