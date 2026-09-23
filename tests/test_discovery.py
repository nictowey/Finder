"""Synthetic retrieval/state invariants; no production item or watch fixtures."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select, update

from finder.adapters.ebay.budget import reconcile
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.discovery import SearchPage, search_page
from finder.adapters.ebay.normalize import normalize_listing
from finder.adapters.ebay.target_search import EbaySearchTarget
from finder.discovery_store import DiscoveryStore, LostLease, advance_pass, new_pass, work
from finder.discovery_worker import next_task, prepare_passes, run_chunk
from finder.errors import RateLimitError, ResponseError
from finder.watch_store import (
    SavedWatch,
    WatchStore,
    inbox,
    migrate,
    outbox,
    subscriptions,
    watches,
)

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
LOW = "1990-01-01T00:00:00.000Z"
HIGH = "2026-09-23T12:00:00.000Z"
TARGET = EbaySearchTarget(
    id="test", catalog_variant_id=123, queries=["Artist Album", "Artist Alias"]
)


def raw(i):
    return {
        "itemId": f"v1|{100000 + i}|0",
        "itemOriginDate": "2026-09-22T12:00:00.000Z",
        "title": "Sparse album",
    }


@pytest.fixture
def discogs_settings():
    return None


@pytest.fixture
def setup(repository):
    migrate(repository.engine)
    store = WatchStore(repository.engine)
    store.add(SavedWatch(release_id=123), now=NOW)
    claim = store.claim(now=NOW)
    queue = DiscoveryStore(repository.engine)
    state = queue.load(claim, TARGET, NOW)
    return repository, store, claim, queue, state


def page(items, total, more, fingerprint="page"):
    return SearchPage(items, total, more, fingerprint)


def test_many_pages_overlap_durable_outcomes_and_completion(setup):
    repo, _, claim, queue, state = setup
    items = [raw(i) for i in range(451)]
    for index in range(2):
        current = state["queries"][index]["baseline"]
        while current["status"] != "search_exhausted":
            offset = current["frontier"][0]["offset"]
            batch = page(items[offset : offset + 200], 451, offset + 200 < 451, str(offset))
            current = advance_pass(current, batch, NOW)
            state["queries"][index]["baseline"] = current
            queue.checkpoint(claim, state, NOW, items=batch.items)
    report = queue.coverage(claim, state)
    assert report["unique_retrieved"] == report["pending"] == 451
    assert report["initial"]["status"] == "search_exhausted_evaluation_pending"
    assert report["initial"]["pages"] == 12  # two full traversals of each query
    # Every stable identity past both former caps is accountable and resumable.
    for item in queue.due(claim, NOW, limit=1000, pending=True):
        queue.disposition(claim, item, NOW, status="unavailable", reason="item_unavailable")
    assert queue.coverage(claim, state)["outcomes"] == {"unavailable": 451}
    assert queue.coverage(claim, state)["initial"]["status"] == "search_exhausted"


def test_page_transaction_rolls_back_cursor_and_items_on_crash(setup, monkeypatch):
    repo, _, claim, queue, state = setup
    state["queries"][0]["baseline"]["pages"] = 1
    import finder.discovery_store as module

    monkeypatch.setattr(module, "MAX_REFERENCES", 1)
    with pytest.raises(module.StorageBudget):
        queue.checkpoint(claim, state, NOW, items=[raw(1), raw(2)])
    recovered = queue.load(claim, TARGET, NOW)
    assert recovered["queries"][0]["baseline"]["pages"] == 0
    assert queue.coverage(claim, recovered)["unique_retrieved"] == 0
    monkeypatch.setattr(module, "MAX_REFERENCES", 10)
    queue.checkpoint(claim, state, NOW, items=[raw(1)])
    queue.checkpoint(claim, state, NOW, items=[raw(1)])
    assert queue.coverage(claim, queue.load(claim, TARGET, NOW))["unique_retrieved"] == 1


@pytest.mark.parametrize("stale", ["expired", "edited", "deleted", "reclaimed"])
def test_all_checkpoints_and_detail_writes_are_fenced(setup, stale, search_payload):
    repo, store, claim, queue, state = setup
    queue.checkpoint(claim, state, NOW, items=[raw(1)])
    if stale == "reclaimed":
        store.claim(now=NOW + timedelta(minutes=13))
    elif stale in ("edited", "deleted"):
        with repo.engine.begin() as conn:
            if stale == "edited":
                conn.execute(update(watches).values(revision=2))
            else:
                conn.execute(watches.delete())
    check_time = NOW + timedelta(minutes=13) if stale == "expired" else NOW
    with pytest.raises(LostLease):
        queue.checkpoint(claim, state, check_time, items=[raw(2)])
    listing = normalize_listing(search_payload["itemSummaries"][0], NOW)
    with pytest.raises(LostLease):
        queue.disposition(
            claim,
            {"item_id": raw(1)["itemId"], "failures": 0},
            check_time,
            status="evaluated",
            listing=listing,
            repository=repo,
        )
    assert repo.count() == 0


def test_replay_recovers_offset_removal_and_late_indexing(setup):
    _, _, claim, queue, state = setup
    original = [raw(i) for i in range(401)]
    current = new_pass(LOW, HIGH)
    first = page(original[:200], 401, True, "first")
    current = advance_pass(current, first, NOW)
    queue.checkpoint(claim, state, NOW, items=first.items)
    # Deleting an earlier row shifts item 200 before the next offset; late indexing
    # adds one older item. The second traversal recovers both in this bounded case.
    shifted = original[1:] + [raw(500)]
    while current["status"] != "search_exhausted":
        offset = current["frontier"][0]["offset"]
        batch = page(
            shifted[offset : offset + 200], len(shifted), offset + 200 < len(shifted), str(offset)
        )
        current = advance_pass(current, batch, NOW)
        queue.checkpoint(claim, state, NOW, items=batch.items)
    assert queue.coverage(claim, state)["unique_retrieved"] == 402
    # Search absence never sets an existing item's availability.
    assert set(queue.coverage(claim, state)["outcomes"]) == {"pending"}


def test_repeated_page_is_interrupted_and_provider_cap_splits_inclusively():
    current = advance_pass(new_pass(LOW, HIGH), page([raw(1)], 401, True, "same"), NOW)
    assert advance_pass(current, page([raw(1)], 401, True, "same"), NOW)["status"] == "interrupted"
    split = advance_pass(new_pass(LOW, HIGH), page([raw(1)], 10000, True), NOW)
    assert split["frontier"][0]["lower"] == split["frontier"][1]["upper"]
    tied = advance_pass(new_pass(HIGH, HIGH), page([raw(1)], 10001, True), NOW)
    assert tied["status"] == "partial_provider_limit"
    assert tied["reason"] == "time_partition_cannot_reduce_result_ceiling"
    recovered = advance_pass(
        {**new_pass(LOW, HIGH), "reason": "old_failure"}, page([], 0, False), NOW
    )
    assert "reason" not in recovered


def test_incremental_watermark_outage_equal_timestamps_and_fair_lanes(setup):
    _, _, _, _, state = setup
    state["queries"][0]["watermark"] = HIGH
    inc = state["queries"][0]["incremental"]
    inc.update(status="search_exhausted", finished_at=HIGH)
    prepare_passes(state, NOW + timedelta(days=3), 24)
    resumed = state["queries"][0]["incremental"]
    assert resumed["frontier"][0]["lower"] == "2026-09-22T12:00:00.000Z"
    assert resumed["frontier"][0]["upper"] == "2026-09-26T12:00:00.000Z"
    assert state["queries"][0]["watermark"] == HIGH
    chosen = []
    for _ in range(4):
        i, lane, rotation = next_task(state)
        chosen.append((i, lane))
        state["round_robin"] = rotation
    assert set(chosen) == {(0, "baseline"), (1, "baseline"), (0, "incremental"), (1, "incremental")}
    current = resumed
    data = [raw(i) for i in range(250)]  # all share one timestamp, must paginate
    while current["status"] != "search_exhausted":
        offset = current["frontier"][0]["offset"]
        current = advance_pass(
            current, page(data[offset : offset + 200], 250, offset + 200 < 250, str(offset)), NOW
        )
    assert current["pages"] == 4


def test_query_failure_prevents_whole_watch_exhaustion(setup):
    _, _, claim, queue, state = setup
    state["queries"][0]["baseline"].update(status="search_exhausted", finished_at=HIGH)
    state["queries"][1]["baseline"]["status"] = "interrupted"
    report = queue.coverage(claim, state)["initial"]
    assert report["status"] == "interrupted" and report["queries_exhausted"] == 1


def test_atomic_detail_evaluation_digest_dedup_dismissal_and_deletion(setup, search_payload):
    repo, _, claim, queue, state = setup
    rows = [raw(1), raw(2)]
    queue.checkpoint(claim, state, NOW, items=rows)
    for item in queue.due(claim, NOW, limit=10, pending=True):
        listing = normalize_listing(
            {**search_payload["itemSummaries"][0], "itemId": item["item_id"]}, NOW
        ).model_copy(update={"seller_id": "synthetic-seller", "details_observed_at": NOW})
        queue.disposition(
            claim,
            item,
            NOW,
            status="evaluated",
            listing=listing,
            repository=repo,
            review={"notify": True, "status": "possible_pressing"},
            state=state,
        )
    with repo.engine.begin() as conn:
        assert len(conn.execute(select(inbox)).all()) == 2
        assert len(conn.execute(select(outbox)).all()) == 1
        conn.execute(update(inbox).values(dismissed=True))
        assert len(conn.execute(select(work).where(work.c.status == "evaluated")).all()) == 2
    queue.checkpoint(claim, state, NOW, items=rows + rows)
    assert not queue.due(claim, NOW, limit=10, pending=True)
    repo.delete_ebay_seller("synthetic-seller")
    with repo.engine.connect() as conn:
        assert not conn.execute(select(work)).all()
        assert not conn.execute(select(inbox)).all()
        assert not conn.execute(select(outbox)).all()
    queue.checkpoint(claim, state, NOW, items=[raw(1)])
    item = queue.due(claim, NOW, limit=1, pending=True)[0]
    status, _ = queue.disposition(
        claim,
        item,
        NOW,
        status="evaluated",
        listing=listing.model_copy(update={"marketplace_item_id": item["item_id"]}),
        repository=repo,
        review={"notify": True},
        state=state,
    )
    assert status == "suppressed" and repo.count() == 0


def test_detail_failure_remains_pending_and_cannot_count_as_evaluated(setup):
    _, _, claim, queue, state = setup
    queue.checkpoint(claim, state, NOW, items=[raw(1)])
    item = queue.due(claim, NOW, limit=1, pending=True)[0]
    queue.disposition(claim, item, NOW, status="error", reason="detail_failed", refresh_hours=1)
    assert queue.coverage(claim, state)["pending"] == 1
    assert not queue.due(claim, NOW, limit=10, pending=True)
    assert queue.due(claim, NOW + timedelta(hours=1), limit=10, pending=True)


def test_summary_refresh_does_not_refresh_details_and_policy_requeues(setup, search_payload):
    repo, _, claim, queue, state = setup
    queue.checkpoint(claim, state, NOW, items=[raw(1)])
    item = queue.due(claim, NOW, limit=1, pending=True)[0]
    listing = normalize_listing(
        {**search_payload["itemSummaries"][0], "itemId": item["item_id"]}, NOW
    ).model_copy(update={"details_observed_at": NOW - timedelta(hours=2)})
    queue.disposition(claim, item, NOW, status="evaluated", listing=listing, repository=repo)
    queue.checkpoint(claim, state, NOW, items=[raw(1)])
    assert repo.get("ebay", item["item_id"]).details_observed_at == NOW - timedelta(hours=2)
    state["evaluation_signature"] = "new-policy"
    queue.checkpoint(claim, state, NOW)
    assert queue.coverage(claim, state)["pending"] == 1


def test_transport_scope_window_and_unsafe_next(settings):
    client = SimpleNamespace(
        settings=settings, get=Mock(return_value={"total": 1, "itemSummaries": [raw(1)]})
    )
    search_page(client, TARGET, 0, lower=LOW, upper=HIGH, offset=0)
    params = client.get.call_args.kwargs["params"]
    assert params["limit"] == 200 and params["sort"] == "newlyListed"
    assert (
        "AUCTION" in params["filter"]
        and "WORLDWIDE" in params["filter"]
        and "condition" not in params["filter"]
    )
    client.get.return_value["next"] = (
        "https://attacker.test/buy/browse/v1/item_summary/search?offset=200"
    )
    with pytest.raises(ResponseError):
        search_page(client, TARGET, 0, lower=LOW, upper=HIGH, offset=0)
    del client.get.return_value["next"]
    client.get.return_value["itemSummaries"][0]["itemOriginDate"] = "2026-09-24T00:00:00Z"
    with pytest.raises(ResponseError):
        search_page(client, TARGET, 0, lower=LOW, upper=HIGH, offset=0)


def quota(remaining=5000, reset=None):
    return {
        "rateLimits": [
            {
                "apiContext": "buy",
                "apiName": "browse",
                "resources": [
                    {
                        "name": "buy.browse",
                        "rates": [
                            {
                                "remaining": remaining,
                                "limit": 5000,
                                "timeWindow": 86400,
                                "reset": reset or (NOW + timedelta(days=1)).isoformat(),
                            }
                        ],
                    }
                ],
            }
        ]
    }


def test_budget_telemetry_conservatively_debits_and_reset_requires_provider():
    saved = reconcile({}, lambda: quota(), NOW)
    saved["rates"][0]["remaining"] = 4500
    result = reconcile(saved, lambda: quota(4999), NOW + timedelta(minutes=6))
    assert result["rates"][0]["remaining"] == 4500
    result = reconcile(result, lambda: quota(4000), NOW + timedelta(minutes=12))
    assert result["rates"][0]["remaining"] == 4000
    result = reconcile(
        result,
        lambda: quota(4990, (NOW + timedelta(days=2)).isoformat()),
        NOW + timedelta(days=1, minutes=1),
    )
    assert result["rates"][0]["remaining"] == 4990
    with pytest.raises(RateLimitError):
        reconcile({}, lambda: {}, NOW)


@pytest.mark.parametrize("api_context,api_name", [("buy", "Browse"), ("BUY", "BROWSE")])
def test_shared_budget_accepts_case_insensitive_api_metadata(api_context, api_name):
    payload = quota()
    payload["rateLimits"][0].update(apiContext=api_context, apiName=api_name)
    assert reconcile({}, lambda: payload, NOW)["rates"][0]["remaining"] == 5000


def test_shared_budget_expired_reset_fails_with_safe_diagnostic():
    from finder.adapters.ebay.budget import BudgetTelemetryError

    with pytest.raises(BudgetTelemetryError) as exc:
        reconcile({}, lambda: quota(reset=NOW.isoformat()), NOW)
    assert exc.value.reason == "reset_missing_or_expired"


def test_retry_attempt_cap_and_external_hosts(settings):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return (
            httpx.Response(200, json={"access_token": "synthetic", "expires_in": 3600})
            if "oauth2" in request.url.path
            else httpx.Response(500)
        )

    with EbayClient(
        settings, transport=httpx.MockTransport(handle), sleep=lambda _: None
    ) as client:
        client.request_limit = 2
        with pytest.raises(RateLimitError):
            client.get("/buy/browse/v1/item_summary/search", headers={})
        assert client.browse_requests == 2 and client.browse_retries == 1
        with pytest.raises(ResponseError):
            client.get("https://attacker.test/", headers={})
    assert len(calls) == 3


def test_migration_preserves_watches_subscription_history_and_initializes_honestly(setup):
    repo, _, claim, queue, state = setup
    with repo.engine.begin() as conn:
        conn.execute(subscriptions.insert().values(id="device", data={"synthetic": True}))
        conn.execute(update(watches).values(summary={"complete": True, "page_cap_reached": True}))
    migrate(repo.engine)
    migrate(repo.engine)
    assert queue.coverage(claim, state)["initial"]["status"] == "not_started"
    with repo.engine.connect() as conn:
        assert conn.execute(select(subscriptions.c.id)).scalar() == "device"
        assert conn.execute(select(watches.c.config)).scalar()["alert_mode"] == "review_leads"


def test_worker_end_to_end_pages_resume_and_no_duplicate_hydration(
    setup, settings, discogs_settings, discogs_release, search_payload, monkeypatch
):
    from contextlib import nullcontext

    from finder import discovery_worker as worker
    from finder.adapters.discogs.adapter import AlternativeRetrieval
    from finder.adapters.discogs.normalize import normalize_release

    repo, store, claim, queue, _ = setup
    variant = normalize_release(discogs_release, NOW)
    catalog = SimpleNamespace(requests=2, retries=0)
    provider = SimpleNamespace(
        get_release=lambda _: variant,
        search_alternatives=lambda _: AlternativeRetrieval(variants=[], search_incomplete=False),
    )
    monkeypatch.setattr(worker, "DiscogsClient", lambda _: nullcontext(catalog))
    monkeypatch.setattr(worker, "DiscogsCatalogProvider", lambda _: provider)
    items = [raw(i) for i in range(81)]
    detail_ids = []
    clock = [NOW]

    def handle(request):
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "synthetic", "expires_in": 3600})
        if "analytics" in request.url.path:
            return httpx.Response(200, json=quota())
        if "item_summary/search" in request.url.path:
            offset = int(request.url.params["offset"])
            return httpx.Response(
                200, json={"total": len(items), "itemSummaries": items[offset : offset + 200]}
            )
        item_id = request.url.path.split("/item/")[1]
        from urllib.parse import unquote

        item_id = unquote(item_id)
        detail_ids.append(item_id)
        data = {
            **search_payload["itemSummaries"][0],
            "itemId": item_id,
            "itemEndDate": None,
            "seller": {"userId": "synthetic-seller"},
        }
        return httpx.Response(200, json=data)

    monkeypatch.setattr(
        worker,
        "EbayClient",
        lambda settings: EbayClient(settings, transport=httpx.MockTransport(handle)),
    )
    total_added = 0
    for _ in range(6):
        result = run_chunk(repo, settings, discogs_settings, claim, now_fn=lambda: clock[0])
        assert not result.get("failed"), result
        total_added += result["new_inbox_rows"]
        clock[0] += timedelta(minutes=2)
        claim = store.claim(now=clock[0])
        if claim is None:
            break
    with repo.engine.connect() as conn:
        summary = conn.execute(select(watches.c.summary)).scalar()
    assert summary["coverage"]["initial"]["status"] == "search_exhausted"
    assert summary["coverage"]["unique_retrieved"] == 81
    assert summary["coverage"]["pending"] == 0
    assert len(detail_ids) == len(set(detail_ids)) == 81
    assert total_added == 81
    # Once the initial queue is drained, a large due-refresh backlog must still
    # fill the chunk and resume promptly rather than waiting another 30 minutes.
    with repo.engine.begin() as conn:
        conn.execute(update(work).values(next_check_at=clock[0].isoformat()))
        conn.execute(update(watches).values(next_scan_at=clock[0].isoformat()))
    claim = store.claim(now=clock[0])
    result = run_chunk(repo, settings, discogs_settings, claim, now_fn=lambda: clock[0])
    assert result["pending"] == 0 and len(detail_ids) == 97
    with repo.engine.connect() as conn:
        due_at = conn.execute(select(watches.c.next_scan_at)).scalar()
    assert datetime.fromisoformat(due_at) == clock[0] + timedelta(minutes=1)


def test_three_watches_progress_fairly_and_telemetry_failure_preserves_cursor(
    repository, settings, discogs_settings, discogs_release, monkeypatch
):
    from contextlib import nullcontext

    from finder import discovery_worker as worker
    from finder.adapters.discogs.adapter import AlternativeRetrieval
    from finder.adapters.discogs.normalize import normalize_release

    migrate(repository.engine)
    store = WatchStore(repository.engine)
    for i in range(3):
        store.add(SavedWatch(release_id=i + 1), now=NOW)
    seen = []
    for _ in range(3):
        claim = store.claim(now=NOW)
        seen.append(claim["id"])
        store.finish(claim, [], next_delay_minutes=1, now=NOW)
    assert len(set(seen)) == 3
    claim = store.claim(now=NOW + timedelta(minutes=2))
    variant = normalize_release(discogs_release, NOW)
    monkeypatch.setattr(
        worker, "DiscogsClient", lambda _: nullcontext(SimpleNamespace(requests=2, retries=0))
    )
    monkeypatch.setattr(
        worker,
        "DiscogsCatalogProvider",
        lambda _: SimpleNamespace(
            get_release=lambda _: variant,
            search_alternatives=lambda _: AlternativeRetrieval(
                variants=[], search_incomplete=False
            ),
        ),
    )

    def handler(request):
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json={"rateLimits": []})

    monkeypatch.setattr(
        worker, "EbayClient", lambda s: EbayClient(s, transport=httpx.MockTransport(handler))
    )
    result = run_chunk(
        repository, settings, discogs_settings, claim, now_fn=lambda: NOW + timedelta(minutes=2)
    )
    assert result.get("quota_paused") or result.get("failed")
    from finder.discovery_store import progress

    with repository.engine.connect() as conn:
        state = conn.execute(
            select(progress.c.data).where(progress.c.watch_id == claim["id"])
        ).scalar()
    assert all(q["watermark"] is None and q["baseline"]["pages"] == 0 for q in state["queries"])


def test_crash_after_details_before_inbox_commit_leaves_work_retryable(
    setup, search_payload, monkeypatch
):
    repo, _, claim, queue, state = setup
    queue.checkpoint(claim, state, NOW, items=[raw(1)])
    item = queue.due(claim, NOW, limit=1, pending=True)[0]
    listing = normalize_listing(
        {**search_payload["itemSummaries"][0], "itemId": item["item_id"]}, NOW
    )

    def crash(*args, **kwargs):
        raise RuntimeError("synthetic crash")

    monkeypatch.setattr(WatchStore, "finish", crash)
    with pytest.raises(RuntimeError):
        queue.disposition(
            claim,
            item,
            NOW,
            status="evaluated",
            listing=listing,
            repository=repo,
            review={"notify": True},
            state=state,
        )
    assert repo.count() == 0
    assert not state["initial_digest_sent"]
    assert queue.coverage(claim, state)["pending"] == 1


def test_confirmed_out_of_stock_differs_from_transport_error(settings, search_payload):
    from finder.adapters.ebay.adapter import EbayAdapter

    data = {
        **search_payload["itemSummaries"][0],
        "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "OUT_OF_STOCK"}],
    }
    client = SimpleNamespace(settings=settings, get=lambda *a, **k: data)
    assert (
        EbayAdapter(client, now=lambda: NOW).refresh_known(data["itemId"]).skip_reason
        == "item_unavailable"
    )


def test_due_refreshes_use_spare_pending_capacity_without_starving_either_lane():
    from finder.discovery_worker import detail_batch

    class Queue:
        def __init__(self, pending_count):
            self.pending_count = pending_count

        def due(self, claim, now, *, limit, pending):
            return [
                ("new" if pending else "old", i)
                for i in range(min(limit, self.pending_count if pending else 30))
            ]

    full = detail_batch(Queue(30), {}, NOW)
    assert full[:4] == [("old", i) for i in range(4)]
    assert len([x for x in full if x[0] == "new"]) == 12
    quiet = detail_batch(Queue(2), {}, NOW)
    assert len(quiet) == len(set(quiet)) == 16
    assert len([x for x in quiet if x[0] == "old"]) == 14
    assert len(detail_batch(Queue(0), {}, NOW)) == 16
