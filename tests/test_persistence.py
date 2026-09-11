from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

from finder.adapters.ebay.normalize import normalize_listing
from finder.persistence import SqlAlchemyListingRepository


def test_update_preserves_identity_and_first_seen(repository, search_payload, observed_at):
    raw = search_payload["itemSummaries"][0]
    first = normalize_listing(raw, observed_at)
    assert repository.upsert(first) == "new"
    raw["price"]["value"] = "19.95"
    later = normalize_listing(raw, observed_at + timedelta(hours=1))
    assert repository.upsert(later) == "updated"
    stored = repository.get("ebay", first.marketplace_item_id)
    assert repository.count() == 1
    assert stored.current_price == Decimal("19.95")
    assert stored.total_acquisition_cost == Decimal("24.45")
    assert stored.first_observed_at == observed_at
    assert stored.last_observed_at == later.last_observed_at
    assert repository.upsert(first) == "updated"
    assert repository.get("ebay", first.marketplace_item_id).current_price == Decimal("19.95")


def test_identity_includes_marketplace(repository, search_payload, observed_at):
    listing = normalize_listing(search_payload["itemSummaries"][0], observed_at)
    repository.upsert(listing)
    repository.upsert(listing.model_copy(update={"marketplace": "other"}))
    assert repository.count() == 2


def test_storage_survives_reopen(tmp_path, search_payload, observed_at):
    url = f"sqlite:///{tmp_path / 'persist.db'}"
    repo = SqlAlchemyListingRepository.from_url(url)
    listing = normalize_listing(search_payload["itemSummaries"][0], observed_at)
    repo.upsert(listing)
    repo.close()
    repo = SqlAlchemyListingRepository.from_url(url)
    try:
        assert repo.get("ebay", listing.marketplace_item_id) == listing
    finally:
        repo.close()


def test_failed_enrichment_preserves_explicitly_stale_specifics(
    repository,
    detail_payload,
    observed_at,
):
    listing = normalize_listing(detail_payload, observed_at, details_loaded=True)
    repository.upsert(listing)
    partial = normalize_listing(
        {"itemId": listing.marketplace_item_id, "title": listing.title},
        observed_at + timedelta(hours=1),
    )
    repository.upsert(partial)
    saved = repository.get("ebay", listing.marketplace_item_id)
    assert saved.item_specifics == listing.item_specifics
    assert saved.details_observed_at == observed_at
    assert "item_specifics_stale" in saved.quality_flags
    fresh = normalize_listing(detail_payload, observed_at + timedelta(hours=2), details_loaded=True)
    repository.upsert(fresh)
    assert (
        "item_specifics_stale"
        not in repository.get("ebay", listing.marketplace_item_id).quality_flags
    )


def test_concurrent_insert_has_one_identity(repository, search_payload, observed_at):
    listing = normalize_listing(search_payload["itemSummaries"][0], observed_at)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(repository.upsert, [listing, listing]))
    assert sorted(results) == ["new", "updated"]
    assert repository.count() == 1
