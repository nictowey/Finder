from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from finder import watch_worker
from finder.adapters.discogs.adapter import AlternativeRetrieval
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.normalize import normalize_listing
from finder.adapters.ebay.target_search import InventoryCursor
from finder.categories.vinyl_target import target_from_release
from finder.errors import CatalogRequestError
from finder.service import ScanSummary
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
    watch = SavedWatch(release_id=123)
    unchecked = assess_review(watch, row, variant, now=observed_at)
    assert unchecked["status"] == "possible_pressing" and not unchecked["notify"]
    competitor = variant.model_copy(update={"catalog_variant_id": "222"})
    ambiguous = assess_review(watch, row, variant, now=observed_at, alternatives=[competitor])
    assert ambiguous["status"] == "possible_pressing" and not ambiguous["notify"]
    assert "other_pressings_not_ruled_out" in ambiguous["verify"]
    assert ambiguous["alternatives_not_ruled_out"] == 1
    assert ambiguous["policy"] == "private-target-review-v3"


def test_pilot_capacity(store):
    for release_id in range(1, 4):
        store.add(SavedWatch(release_id=release_id))
    with pytest.raises(ValueError):
        store.add(SavedWatch(release_id=4))


@pytest.mark.parametrize("catalog_failed", [False, True])
def test_worker_reuses_alternatives_and_keeps_review_when_catalog_fails(
    repository, store, settings, discogs_release, search_payload, monkeypatch, catalog_failed
):
    now = datetime.now(UTC)
    target = normalize_release(discogs_release, now)
    calls = []

    class Provider:
        def __init__(self, client):
            pass

        def get_release(self, release_id):
            return target

        def search_alternatives(self, variant):
            calls.append(variant.catalog_variant_id)
            if catalog_failed:
                raise CatalogRequestError("Synthetic lookup failure")
            other = variant.model_copy(update={"catalog_variant_id": "222"})
            return AlternativeRetrieval([other], True)

    items = [
        {
            **search_payload["itemSummaries"][0],
            "itemId": f"synthetic-{index}",
            "title": "Example Artist Example Album LP",
            "localizedAspects": [{"name": "Barcode", "value": "0123456789012"}],
        }
        for index in range(2)
    ]

    def handler(request):
        if request.url.path.endswith("/rate_limit/"):
            return httpx.Response(
                200,
                json={
                    "rateLimits": [
                        {
                            "apiContext": "buy",
                            "apiName": "browse",
                            "resources": [
                                {
                                    "name": "buy.browse",
                                    "rates": [
                                        {"limit": 5000, "remaining": 4500, "timeWindow": 86400}
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"total": 2, "itemSummaries": items})
        item_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=next(item for item in items if item["itemId"] == item_id))

    monkeypatch.setattr(watch_worker, "DiscogsClient", lambda _: nullcontext(None))
    monkeypatch.setattr(watch_worker, "DiscogsCatalogProvider", Provider)
    monkeypatch.setattr(
        watch_worker,
        "EbayClient",
        lambda config: EbayClient(config, transport=httpx.MockTransport(handler)),
    )
    store.add(SavedWatch(release_id=111), now=now)
    report = watch_worker.run_due_watches(repository, settings, None)
    assert report == {
        "attempted": 1,
        "completed": 1,
        "failed": 0,
        "quota_paused": 0,
        "new_inbox_rows": 2,
    }
    assert calls == ["111"]  # Once for the watch, not once per listing.
    with repository.engine.connect() as conn:
        rows = conn.execute(select(inbox.c.data)).scalars().all()
        assert all(row["status"] == "possible_pressing" for row in rows)
        assert all(not row["notify"] for row in rows)
        assert not conn.execute(select(outbox)).all()
        summary = conn.execute(select(watches.c.summary)).scalar_one()
        assert summary["catalog_check_failed"] == catalog_failed
        assert summary["ambiguous_leads"] == (0 if catalog_failed else 2)


def test_first_refresh_recovers_prior_lead_outside_newest_page(
    repository, store, settings, discogs_release, search_payload, monkeypatch
):
    started = datetime.now(UTC) - timedelta(minutes=31)
    target = normalize_release(discogs_release, started)
    item = {
        **search_payload["itemSummaries"][0],
        "itemId": "synthetic-older-listing",
        "title": "Example Artist Example Album LP",
        "localizedAspects": [{"name": "Barcode", "value": "0123456789012"}],
    }
    old_listing = normalize_listing(item, started, details_loaded=True).model_copy(
        update={"seller_id": "synthetic-seller"}
    )
    repository.upsert(old_listing)
    store.add(SavedWatch(release_id=111), now=started)
    store.finish(
        store.claim(now=started),
        [(old_listing, {"status": "possible_pressing", "notify": False, "policy": "old"})],
        now=started,
    )

    class Provider:
        def __init__(self, client):
            pass

        def get_release(self, release_id):
            return target

        def search_alternatives(self, variant):
            return AlternativeRetrieval([], True)

    searches = []

    def handler(request):
        if request.url.path.endswith("/rate_limit/"):
            return httpx.Response(
                200,
                json={
                    "rateLimits": [
                        {
                            "apiContext": "buy",
                            "apiName": "browse",
                            "resources": [
                                {
                                    "name": "buy.browse",
                                    "rates": [
                                        {"limit": 5000, "remaining": 4500, "timeWindow": 86400}
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if request.url.path.endswith("/search"):
            searches.append((request.url.params.get("sort"), request.url.params["offset"]))
            return httpx.Response(
                200,
                json={"total": 0, "itemSummaries": []}
                if request.url.params.get("sort") == "newlyListed"
                else {"total": 20, "itemSummaries": [item]},
            )
        return httpx.Response(200, json=item)

    monkeypatch.setattr(watch_worker, "DiscogsClient", lambda _: nullcontext(None))
    monkeypatch.setattr(watch_worker, "DiscogsCatalogProvider", Provider)
    monkeypatch.setattr(
        watch_worker,
        "EbayClient",
        lambda config: EbayClient(config, transport=httpx.MockTransport(handler)),
    )
    report = watch_worker.run_due_watches(repository, settings, None)
    assert report == {
        "attempted": 1,
        "completed": 1,
        "failed": 0,
        "quota_paused": 0,
        "new_inbox_rows": 0,
    }
    assert searches == [("newlyListed", "0"), (None, "0")]
    with repository.engine.connect() as conn:
        record = conn.execute(select(inbox.c.data, inbox.c.last_seen_at)).one()
        assert record.data["policy"] == "private-target-review-v3"
        assert record.data["alternatives_checked"] == 0
        assert datetime.fromisoformat(record.last_seen_at) > started
        summary = conn.execute(select(watches.c.summary)).scalar_one()
        assert summary["discovery"]["inventory_sample"]["query_position"] == 1
        assert summary["discovery"]["newest"][0]["returned"] == 0
        assert summary["discovery"]["marketplace_recall_measured"] is False
        assert summary["inventory"]["offsets"] == [6]
    with repository.engine.begin() as conn:
        conn.execute(update(watches).values(next_scan_at=started.isoformat()))
    report = watch_worker.run_due_watches(repository, settings, None)
    assert report == {
        "attempted": 1,
        "completed": 1,
        "failed": 0,
        "quota_paused": 0,
        "new_inbox_rows": 0,
    }
    assert searches == [("newlyListed", "0"), (None, "0"), ("newlyListed", "0")]
    with repository.engine.connect() as conn:
        summary = conn.execute(select(watches.c.summary)).scalar_one()
        assert summary["discovery"]["candidate_recheck"] == "updated"
        assert summary["discovery"]["browse_requests"] == 2
        assert summary["inventory"]["offsets"] == [6]
        assert summary["inventory"]["due"] is True


def test_known_lead_404_withholds_pending_alert_and_stops_rechecks(
    repository, store, settings, discogs_release, search_payload, monkeypatch
):
    started = datetime.now(UTC) - timedelta(minutes=31)
    target = normalize_release(discogs_release, started)
    item = search_payload["itemSummaries"][0]
    row = normalize_listing(item, started, details_loaded=True)
    repository.upsert(row)
    plan = target_from_release(target)
    cursor = InventoryCursor.load(plan, 1, None).after_success(
        ScanSummary(monitor="inventory", limit_reached=True)
    )
    store.add(SavedWatch(release_id=111), now=started)
    store.finish(
        store.claim(now=started),
        [(row, {"status": "possible_pressing", "notify": True})],
        summary={"inventory": cursor.as_summary()},
        now=started,
    )
    with repository.engine.begin() as conn:
        conn.execute(update(outbox).values(status="pending"))

    class Provider:
        def __init__(self, client):
            pass

        def get_release(self, release_id):
            return target

        def search_alternatives(self, variant):
            return AlternativeRetrieval([], True)

    def handler(request):
        if request.url.path.endswith("/rate_limit/"):
            return httpx.Response(
                200,
                json={
                    "rateLimits": [
                        {
                            "apiContext": "buy",
                            "apiName": "browse",
                            "resources": [
                                {
                                    "name": "buy.browse",
                                    "rates": [
                                        {"limit": 5000, "remaining": 4500, "timeWindow": 86400}
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"total": 0, "itemSummaries": []})
        return httpx.Response(404)

    monkeypatch.setattr(watch_worker, "DiscogsClient", lambda _: nullcontext(None))
    monkeypatch.setattr(watch_worker, "DiscogsCatalogProvider", Provider)
    monkeypatch.setattr(
        watch_worker,
        "EbayClient",
        lambda config: EbayClient(config, transport=httpx.MockTransport(handler)),
    )
    assert watch_worker.run_due_watches(repository, settings, None)["completed"] == 1
    with repository.engine.connect() as conn:
        lead = conn.execute(select(inbox.c.data)).scalar_one()
        assert lead["availability"] == "unavailable_on_recheck"
        assert lead["notify"] is False
        assert not conn.execute(select(outbox)).all()
        summary = conn.execute(select(watches.c.summary)).scalar_one()
        assert summary["discovery"]["candidate_recheck"] == "unavailable"
        assert (
            store.recheck_candidate(
                {"id": conn.execute(select(watches.c.id)).scalar_one()}, excluded=set()
            )
            is None
        )


@pytest.mark.parametrize(
    "remaining,reason", [(275, "insufficient_budget"), (None, "quota_unavailable")]
)
def test_insufficient_or_unavailable_quota_preserves_due_watch(
    repository, store, settings, discogs_release, monkeypatch, remaining, reason
):
    now = datetime.now(UTC) - timedelta(minutes=31)
    store.add(SavedWatch(release_id=111), now=now)
    before = store.claim(now=now)
    store.finish(before, [], summary={"inventory": {"cursor": "unchanged"}}, now=now)
    with repository.engine.connect() as conn:
        prior = conn.execute(select(watches)).mappings().one()

    class Provider:
        def __init__(self, client):
            pass

        def get_release(self, release_id):
            return normalize_release(discogs_release, now)

        def search_alternatives(self, variant):
            return AlternativeRetrieval([], True)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        assert request.url.path.endswith("/rate_limit/"), "Browse was called despite quota"
        if remaining is None:
            return httpx.Response(200, json={"rateLimits": []})
        return httpx.Response(
            200,
            json={
                "rateLimits": [
                    {
                        "apiContext": "buy",
                        "apiName": "browse",
                        "resources": [
                            {
                                "name": "buy.browse",
                                "rates": [
                                    {"limit": 5000, "remaining": remaining, "timeWindow": 86400}
                                ],
                            }
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(watch_worker, "DiscogsClient", lambda _: nullcontext(None))
    monkeypatch.setattr(watch_worker, "DiscogsCatalogProvider", Provider)
    monkeypatch.setattr(
        watch_worker,
        "EbayClient",
        lambda config: EbayClient(config, transport=httpx.MockTransport(handler)),
    )
    report = watch_worker.run_due_watches(repository, settings, None)
    assert report == {
        "attempted": 1,
        "completed": 0,
        "failed": 0,
        "quota_paused": 1,
        "new_inbox_rows": 0,
    }
    with repository.engine.connect() as conn:
        row = conn.execute(select(watches)).mappings().one()
        assert row["status"] == "quota_paused"
        assert row["next_scan_at"] == prior["next_scan_at"]
        assert row["last_success_at"] == prior["last_success_at"]
        assert row["last_started_at"] == prior["last_started_at"]
        assert row["lease_token"] is None and row["lease_until"] is None
        assert row["summary"]["inventory"] == {"cursor": "unchanged"}
        assert row["summary"]["quota_pause"]["reason"] == reason


def test_failed_scan_preserves_inventory_cursor_for_retry(
    repository, store, discogs_release, observed_at
):
    target = normalize_release(discogs_release, observed_at)
    plan = target_from_release(target)
    saved = (
        InventoryCursor.load(plan, 1, None)
        .after_success(ScanSummary(monitor="inventory", limit_reached=True))
        .as_summary()
    )
    store.add(SavedWatch(release_id=111), now=observed_at)
    store.finish(store.claim(now=observed_at), [], summary={"inventory": saved}, now=observed_at)
    later = observed_at + timedelta(minutes=31)
    store.finish(
        store.claim(now=later),
        [],
        summary={"error": "scan_failed"},
        success=False,
        now=later,
    )
    with repository.engine.connect() as conn:
        summary = conn.execute(select(watches.c.summary)).scalar_one()
    assert summary["inventory"] == saved
    assert InventoryCursor.load(plan, 1, summary).offsets == (6,)
