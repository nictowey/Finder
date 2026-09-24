from datetime import timedelta

import pytest
from sqlalchemy import insert, select, update

from finder import watch_worker
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.watch_store import (
    MAX_WATCHES,
    SavedWatch,
    WatchStore,
    inbox,
    migrate,
    outbox,
    rollback_pilot_schema,
    verdicts,
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


def test_judged_listing_is_not_renotified_after_price_or_policy_change(
    repository, store, search_payload, observed_at
):
    row = listing(search_payload, observed_at)
    repository.upsert(row)
    watch_id = store.add(SavedWatch(release_id=123), now=observed_at)
    store.finish(store.claim(now=observed_at), [(row, {"notify": False})], now=observed_at)
    with repository.engine.begin() as conn:
        conn.execute(
            insert(verdicts).values(
                watch_id=watch_id,
                marketplace="ebay",
                marketplace_item_id=row.marketplace_item_id,
                verdict="other",
                tier="family_review",
                decided_at=observed_at.isoformat(),
            )
        )
    later = observed_at + timedelta(minutes=31)
    store.finish(store.claim(now=later), [(row, {"notify": True})], now=later)
    with repository.engine.connect() as conn:
        assert not conn.execute(select(outbox)).all()
        assert conn.execute(select(verdicts.c.verdict)).scalar_one() == "other"


def test_expired_lease_recovery_rejects_old_worker_and_edits(repository, store, observed_at):
    key = store.add(SavedWatch(release_id=123), now=observed_at)
    old = store.claim(now=observed_at)
    recovered = store.claim(now=observed_at + timedelta(minutes=13))
    assert recovered["lease_token"] != old["lease_token"]
    assert store.finish(old, [], now=observed_at) is None
    with repository.engine.begin() as conn:
        conn.execute(update(watches).where(watches.c.id == key).values(revision=2))
    assert store.finish(recovered, [], now=observed_at) is None
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
    watch = SavedWatch(release_id=123, alert_mode="strict")
    result = assess_review(
        watch, row, variant, now=observed_at, alternatives=[], search_incomplete=False
    )
    assert result["notify"]
    assert result["status"] == "possible_pressing"
    incomplete = assess_review(
        watch, row, variant, now=observed_at, alternatives=[], search_incomplete=True
    )
    assert incomplete["status"] == "possible_pressing"
    assert "catalog_alternative_search_incomplete" in incomplete["verify"]
    assert not incomplete["notify"]
    budget = watch.model_copy(
        update={"maximum_subtotal": 100, "country": "US", "postal_code": "12345"}
    )
    row = row.model_copy(update={"shipping_cost": None})
    assert not assess_review(
        budget, row, variant, now=observed_at, alternatives=[], search_incomplete=False
    )["notify"]
    condition = watch.model_copy(update={"condition_ids": ["99999"]})
    assert not assess_review(
        condition, row, variant, now=observed_at, alternatives=[], search_incomplete=False
    )["notify"]
    assert not assess_review(
        watch,
        row,
        variant,
        now=observed_at + timedelta(hours=2),
        alternatives=[],
        search_incomplete=False,
    )["notify"]


def test_ambiguous_and_unchecked_leads_stay_visible_without_alerts(
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
    watch = SavedWatch(release_id=123, alert_mode="strict")
    unchecked = assess_review(watch, row, variant, now=observed_at)
    assert unchecked["status"] == "possible_pressing" and not unchecked["notify"]
    competitor = variant.model_copy(update={"catalog_variant_id": "222"})
    ambiguous = assess_review(watch, row, variant, now=observed_at, alternatives=[competitor])
    assert ambiguous["status"] == "family_review" and not ambiguous["notify"]
    assert "other_pressings_not_ruled_out" in ambiguous["verify"]
    assert ambiguous["alternatives_not_ruled_out"] == 1
    assert ambiguous["policy"] == "private-target-review-v6"


def test_pilot_capacity(store):
    for release_id in range(1, MAX_WATCHES + 1):
        store.add(SavedWatch(release_id=release_id))
    with pytest.raises(ValueError):
        store.add(SavedWatch(release_id=MAX_WATCHES + 1))


def test_worker_claims_due_watches_until_its_time_budget_is_spent(
    repository, store, settings, monkeypatch
):
    from finder import discovery_worker

    for release_id in range(1, 6):
        store.add(SavedWatch(release_id=release_id))
    clock = [0.0]
    seen = []

    def chunk(repo, settings, discogs_settings, claim):
        seen.append(claim["id"])
        clock[0] += 100
        store.finish(claim, [], next_delay_minutes=30)
        return {"completed": 1, "new_inbox_rows": 2}

    monkeypatch.setattr(discovery_worker, "run_chunk", chunk)
    report = watch_worker.run_due_watches(
        repository, settings, None, run_seconds=400, clock=lambda: clock[0]
    )
    # 0, 100 and 200 seconds leave room for a whole chunk; 300 does not.
    assert report["attempted"] == report["completed"] == len(set(seen)) == 3
    assert report["new_inbox_rows"] == 6


def test_worker_stops_claiming_after_a_quota_pause(repository, store, settings, monkeypatch):
    from finder import discovery_worker

    for release_id in range(1, 4):
        store.add(SavedWatch(release_id=release_id))

    def chunk(repo, settings, discogs_settings, claim):
        store.finish(claim, [], next_delay_minutes=30)
        return {"quota_paused": 1}

    monkeypatch.setattr(discovery_worker, "run_chunk", chunk)
    report = watch_worker.run_due_watches(repository, settings, None)
    assert report["attempted"] == report["quota_paused"] == 1


def test_review_alerts_keep_uncertainty_but_block_conflicts_failures_and_staleness(
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
    watch = SavedWatch(release_id=123, alert_mode="review_leads")
    competitor = variant.model_copy(update={"catalog_variant_id": "222"})
    review = assess_review(
        watch, row, variant, now=observed_at, alternatives=[competitor], search_incomplete=True
    )
    assert not review["notify"]
    assert review["status"] == "family_review"
    assert "other_pressings_not_ruled_out" in review["verify"]
    assert "catalog_alternative_search_incomplete" in review["verify"]
    # A failed alternatives lookup is shown as uncertainty; it no longer holds the alert.
    unchecked = assess_review(watch, row, variant, now=observed_at, alternatives=None)
    assert unchecked["notify"] and "catalog_alternatives_not_checked" in unchecked["verify"]
    assert not assess_review(
        watch, row, variant, now=observed_at + timedelta(hours=2), alternatives=[competitor]
    )["notify"]
    conflict = row.model_copy(update={"item_specifics": {"Barcode": ["9999999999999"]}})
    assert not assess_review(watch, conflict, variant, now=observed_at, alternatives=[])["notify"]
