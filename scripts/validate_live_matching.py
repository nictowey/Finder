"""Exercise Finder's matching pipeline against live Discogs catalog data."""

import json
from datetime import UTC, datetime
from decimal import Decimal

from finder.adapters.discogs.adapter import DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.config import load_discogs_settings
from finder.domain import Listing, Variant
from finder.matching import rank_variants, score_variant
from finder.persistence import SqlAlchemyRepository


def _barcodes(variant: Variant) -> list[str]:
    return [
        value
        for name, values in variant.identifiers.items()
        if "barcode" in name.lower()
        for value in values
    ]


def _catalog_numbers(variant: Variant) -> list[str]:
    return [str(label["catno"]) for label in variant.labels if label.get("catno")]


def _synthetic_listing(variant: Variant, observed_at: datetime) -> Listing:
    specifics: dict[str, list[str]] = {"UPC": [_barcodes(variant)[0]]}
    if variant.artists:
        specifics["Artist"] = [variant.artists[0]]
    if variant.release_year:
        specifics["Release Year"] = [str(variant.release_year)]
    catalog_numbers = _catalog_numbers(variant)
    if catalog_numbers:
        specifics["Catalog Number"] = [catalog_numbers[0]]
    format_values = [
        str(value)
        for item in variant.formats
        for value in item.values()
        if isinstance(value, (str, int)) and value
    ]
    if format_values:
        specifics["Format"] = format_values
    return Listing(
        marketplace="ebay",
        marketplace_item_id="synthetic-live-discogs-validation",
        title=" ".join([*variant.artists, variant.title, "vinyl"]),
        current_price=Decimal("20.00"),
        currency="USD",
        shipping_cost=Decimal("5.00"),
        shipping_currency="USD",
        condition="New",
        item_specifics=specifics,
        first_observed_at=observed_at,
        last_observed_at=observed_at,
        details_observed_at=observed_at,
        quality_flags=["synthetic_validation_record"],
    )


def main() -> int:
    settings = load_discogs_settings()
    repository = SqlAlchemyRepository.from_url(settings.database_url.get_secret_value())
    observed_at = datetime.now(UTC)
    try:
        with DiscogsClient(settings) as client:
            provider = DiscogsCatalogProvider(client, now=lambda: observed_at)
            initial = provider.search_releases("Kendrick Lamar DAMN", limit=5)
        selected = next((variant for variant in initial if _barcodes(variant)), None)
        if selected is None:
            raise AssertionError("No barcode-bearing Discogs release was returned")

        listing = _synthetic_listing(selected, observed_at)
        assert listing.total_acquisition_cost == Decimal("25.00")
        assert repository.upsert(listing) == "new"

        with DiscogsClient(settings) as client:
            provider = DiscogsCatalogProvider(client, now=lambda: observed_at)
            retrieval = provider.search_for_listing(listing, limit=10)
        variants = retrieval.variants
        assert "barcode" in retrieval.query_kinds
        assert any(
            variant.catalog_variant_id == selected.catalog_variant_id for variant in variants
        )

        for variant in variants:
            repository.upsert_product(provider.product_for(variant))
            repository.upsert_variant(variant)
        candidates = rank_variants(listing, variants, observed_at)
        repository.replace_candidates(
            listing.marketplace,
            listing.marketplace_item_id,
            provider.name,
            candidates,
        )

        selected_candidate = next(
            candidate
            for candidate in candidates
            if candidate.catalog_variant_id == selected.catalog_variant_id
        )
        assert selected_candidate.status == "strong_candidate"
        assert selected_candidate.score >= 70
        assert selected_candidate.score == candidates[0].score
        assert any(
            evidence.field == "barcode" and evidence.matched
            for evidence in selected_candidate.evidence
        )

        conflicting_specifics = dict(listing.item_specifics)
        conflicting_specifics["UPC"] = ["0000000000000"]
        conflicting = listing.model_copy(
            update={
                "marketplace_item_id": "synthetic-conflicting-barcode-validation",
                "item_specifics": conflicting_specifics,
            }
        )
        rejected = score_variant(conflicting, selected, observed_at)
        assert rejected.status == "rejected"
        assert rejected.score <= 20

        stored = repository.get_candidates(
            listing.marketplace, listing.marketplace_item_id, provider.name
        )
        assert len(stored) == len(candidates)
        strong = [candidate for candidate in candidates if candidate.status == "strong_candidate"]
        print(
            json.dumps(
                {
                    "status": "passed",
                    "query": "Kendrick Lamar DAMN",
                    "query_kinds": retrieval.query_kinds,
                    "retrieval_incomplete": retrieval.incomplete,
                    "releases_evaluated": len(variants),
                    "selected_release_id": selected.catalog_variant_id,
                    "selected_score": selected_candidate.score,
                    "strong_candidates": len(strong),
                    "ambiguous": len(strong) > 1,
                    "conflicting_barcode_status": rejected.status,
                    "persisted_candidates": len(stored),
                }
            )
        )
        return 0
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
