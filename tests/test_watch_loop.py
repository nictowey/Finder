from datetime import timedelta

import pytest
from sqlalchemy import select, update

from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.watch_store import (
    SavedWatch,
    WatchStore,
    inbox,
    migrate,
    outbox,
    rollback_pilot_schema,
    watches,
)
from finder.watch_worker import assess_review


@pytest.fixture
def store(repository):
    migrate(repository.engine)
    return WatchStore(repository.engine)


def listing(search_payload, observed_at):
    return normalize_listing(search_payload["itemSummaries"][0], observed_at).model_copy(
        update={"seller_id": "synthetic-seller", "details_observed_at": observed_at}
    )


def test_repeated_scan_deduplicates_and_seller_deletion_cascades(
    repository, store, search_payload, observed_at
):
    row = listing(search_payload, observed_at)
    repository.upsert(row)
    store.add(SavedWatch(release_id=123), now=observed_at)
    claim = store.claim(now=observed_at)
    assert store.claim(now=observed_at) is None
    review = {"notify": True, "status": "possible_pressing"}
    assert store.finish(claim, [(row, review)], now=observed_at) == 1
    later = observed_at + timedelta(minutes=31)
    claim = store.claim(now=later)
    assert store.finish(claim, [(row, review)], now=later) == 0
    with repository.engine.connect() as conn:
        assert len(conn.execute(select(inbox)).all()) == 1
        assert len(conn.execute(select(outbox)).all()) == 1
    repository.delete_ebay_seller("synthetic-seller")
    with repository.engine.connect() as conn:
        assert not conn.execute(select(inbox)).all()
        assert not conn.execute(select(outbox)).all()
    later += timedelta(minutes=31)
    claim = store.claim(now=later)
    assert store.finish(claim, [(row, review)], now=later) == 0


def test_expired_lease_recovery_rejects_old_worker_and_edits(repository, store, observed_at):
    key = store.add(SavedWatch(release_id=123), now=observed_at)
    old = store.claim(now=observed_at)
    recovered = store.claim(now=observed_at + timedelta(minutes=13))
    assert recovered["lease_token"] != old["lease_token"]
    assert store.finish(old, [], now=observed_at) == 0
    with repository.engine.begin() as conn:
        conn.execute(update(watches).where(watches.c.id == key).values(revision=2))
    assert store.finish(recovered, [], now=observed_at) == 0
    with repository.engine.connect() as conn:
        assert conn.execute(select(watches.c.last_success_at)).scalar() is None


def test_failed_scan_does_not_notify_or_fake_success(
    repository, store, search_payload, observed_at
):
    row = listing(search_payload, observed_at)
    repository.upsert(row)
    store.add(SavedWatch(release_id=123), now=observed_at)
    store.finish(
        store.claim(now=observed_at), [(row, {"notify": True})], success=False, now=observed_at
    )
    with repository.engine.connect() as conn:
        assert not conn.execute(select(outbox)).all()
        result = conn.execute(select(watches)).mappings().one()
        assert result["status"] == "failed"
        assert result["last_success_at"] is None


def test_migration_roundtrip_preserves_existing_listings(repository, search_payload, observed_at):
    repository.upsert(listing(search_payload, observed_at))
    migrate(repository.engine)
    migrate(repository.engine)
    rollback_pilot_schema(repository.engine)
    assert repository.count() == 1
    migrate(repository.engine)
    WatchStore(repository.engine).add(SavedWatch(release_id=123))


@pytest.mark.parametrize(
    "changes",
    [
        {"maximum_subtotal": "NaN"},
        {"maximum_subtotal": "0"},
        {"country": "US"},
        {"condition_ids": ["anything"]},
    ],
)
def test_invalid_watch_settings(changes):
    with pytest.raises(ValueError):
        SavedWatch(release_id=123, **changes)


def test_sparse_lead_can_notify_but_unknown_shipping_and_condition_cannot(
    search_payload, discogs_release, observed_at
):
    variant = normalize_release(discogs_release, observed_at)
    row = listing(search_payload, observed_at).model_copy(
        update={
            "title": "Example Artist Example Album LP",
            "item_specifics": {"Barcode": ["0123456789012"]},
            "quality_flags": [],
            "price_kind": "fixed_price",
            "listing_ends_at": None,
        }
    )
    watch = SavedWatch(release_id=123)
    result = assess_review(watch, row, variant, now=observed_at)
    assert result["notify"]
    assert result["status"] == "possible_pressing"
    budget = watch.model_copy(
        update={"maximum_subtotal": 100, "country": "US", "postal_code": "12345"}
    )
    row = row.model_copy(update={"shipping_cost": None})
    assert not assess_review(budget, row, variant, now=observed_at)["notify"]
    condition = watch.model_copy(update={"condition_ids": ["99999"]})
    assert not assess_review(condition, row, variant, now=observed_at)["notify"]
    assert not assess_review(watch, row, variant, now=observed_at + timedelta(hours=2))["notify"]


def test_pilot_capacity(store):
    for release_id in range(1, 4):
        store.add(SavedWatch(release_id=release_id))
    with pytest.raises(ValueError):
        store.add(SavedWatch(release_id=4))
