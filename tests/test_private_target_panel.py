"""A private panel must bound calls and keep raw catalog/seller identities out of reports."""

import json
from types import SimpleNamespace

import pytest

from finder.adapters.base import ListingObservation
from finder.adapters.discogs.adapter import AlternativeRetrieval
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.errors import ConfigurationError
from scripts.measure_private_target_panel import CONFLICT_REASONS, parse_private_ids, probe_batch


def test_private_target_list_rejects_duplicates_and_oversized_input():
    with pytest.raises(ConfigurationError):
        parse_private_ids("42,42")
    with pytest.raises(ConfigurationError):
        parse_private_ids(",".join(map(str, range(1, 14))))
    with pytest.raises(ConfigurationError):
        parse_private_ids("42,not-an-id")
    assert parse_private_ids("https://www.discogs.com/release/42-Album,43") == [42, 43]


def test_panel_aggregates_one_batch_without_serializing_identity(
    discogs_release, search_payload, observed_at
):
    variant = normalize_release(
        {
            **discogs_release,
            "id": 55555,
            "title": "Example Album",
            "artists": [{"name": "Example Artist"}],
            "formats": [{"name": "Vinyl", "descriptions": ["LP"]}],
        },
        observed_at,
    )
    listing = normalize_listing(
        {
            **search_payload["itemSummaries"][0],
            "itemId": "v1|987654321|0",
            "title": "Example Artist Example Album vinyl",
        },
        observed_at,
    )

    class Catalog:
        def get_release(self, release_id):
            assert release_id == 55555
            return variant

        def search_alternatives(self, release):
            assert release is variant
            return AlternativeRetrieval([], True)

    class Adapter:
        def __init__(self):
            self.stats = SimpleNamespace(limit_reached=False)
            self.queries = 0

        def search(self, monitor, *, seen_item_ids):
            self.queries += 1
            self.stats.limit_reached = True
            if listing.marketplace_item_id not in seen_item_ids:
                seen_item_ids.add(listing.marketplace_item_id)
                yield ListingObservation(listing=listing)

    adapter = Adapter()
    # Batch selection exposes the private target's ordinal, never its ID or title.
    result = probe_batch([1, 2, 3, 4, 55555], batch=1, catalog=Catalog(), adapter=adapter)
    serialized = json.dumps(result)
    assert result["status"] == "complete"
    assert result["results"][0]["distinct_listings"] == 1
    assert result["results"][0]["ordinal"] == 5
    assert result["results"][0]["queries_capped"] == adapter.queries
    assert set(result["results"][0]["conflict_reasons"]) <= set(CONFLICT_REASONS)
    assert (
        result["results"][0]["possible_with_catalog_uncertainty"]
        == result["results"][0]["review_counts"]["possible_pressing"]
    )
    assert "55555" not in serialized
    assert "987654321" not in serialized
    assert "Example Album" not in serialized


def test_optional_coverage_audit_counts_new_leads_without_exposing_ids(
    discogs_release, search_payload, observed_at
):
    variant = normalize_release(
        {
            **discogs_release,
            "id": 55555,
            "artists": [{"name": "Example Artist"}, {"name": "Second Artist"}],
        },
        observed_at,
    )

    def listing(item_id):
        return normalize_listing(
            {
                **search_payload["itemSummaries"][0],
                "itemId": item_id,
                "title": "Example Artist Example Album LP",
            },
            observed_at,
        )

    first, second = listing("v1|987654321|0"), listing("v1|123456789|0")

    class Catalog:
        def get_release(self, release_id):
            return variant

        def search_alternatives(self, release):
            return AlternativeRetrieval([], True)

    class Adapter:
        stats = SimpleNamespace(limit_reached=False)

        def search(self, monitor, *, seen_item_ids):
            assert monitor.source_options["page_size"] == 5
            chosen = [first, second] if "audit-newest" in monitor.id else [first]
            if "audit-artist" in monitor.id:
                assert monitor.query == "Second Artist Example Album"
            for row in chosen:
                if row.marketplace_item_id not in seen_item_ids:
                    seen_item_ids.add(row.marketplace_item_id)
                    yield ListingObservation(listing=row)

    result = probe_batch(
        [55555], batch=0, catalog=Catalog(), adapter=Adapter(), coverage_audit=True
    )
    audit = result["results"][0]["coverage_audit"]
    assert audit["queries"] == 2
    assert audit["overlap_with_initial"] == 1
    assert audit["additional_sampled"] == 1
    assert audit["additional_review_counts"]["family_review"] == 1
    assert result["maximum_browse_requests_without_retries"] == 30
    assert "123456789" not in json.dumps(result)
    assert "55555" not in json.dumps(result)
