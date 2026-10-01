"""Anonymous measurements must not change work, deadlines, fences or failures."""

import json
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from itertools import count
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from finder import discovery_worker as worker
from finder import stage_timing, watch_worker
from finder.adapters.base import ListingObservation
from finder.adapters.discogs.adapter import AlternativeRetrieval
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.discovery import SearchPage
from finder.adapters.ebay.normalize import normalize_listing
from finder.discovery_store import SETTINGS_CHANGED, LostLease, StorageBudget, new_pass
from finder.stage_timing import STAGES, StageTimings
from finder.watch_store import SavedWatch

NOW = datetime(2026, 10, 1, tzinfo=UTC)
PRIVATE = "PRIVATE-FIXTURE-MUST-NOT-LEAK"
PRIVATE_PRICE = Decimal("876543.210987")


def test_exclusive_nested_spans_accumulate_before_rounding(monkeypatch):
    clock = iter([0, 100_000, 700_000, 800_000, 1_400_000, 2_000_000])
    monkeypatch.setattr(stage_timing, "perf_counter_ns", lambda: next(clock))
    timing = StageTimings()
    with timing.measure("setup"):
        assert timing.call("cached_review", lambda: PRIVATE) == PRIVATE
        assert timing.call("cached_review", lambda: PRIVATE) == PRIVATE
    report = timing.report()
    assert report["timing_total_ms"] == 2
    assert report["timing_setup_ms"] == 0
    assert report["timing_cached_review_ms"] == 1  # two 0.6 ms calls, truncated once
    assert timing.elapsed["cached_review"] == 1_200_000
    assert report["timing_cached_review_calls"] == 2
    assert report["timing_cached_review_completed"] == 2
    assert set(report) == {"timing_total_ms"} | {
        f"timing_{stage}_{suffix}" for stage in STAGES for suffix in ("ms", "calls", "completed")
    }
    assert all(type(value) is int and value >= 0 for value in report.values())
    assert PRIVATE not in json.dumps(report)


@pytest.mark.parametrize("error", [RuntimeError(PRIVATE), KeyboardInterrupt(PRIVATE)])
def test_failed_spans_are_measured_without_suppressing_or_replacing_exceptions(monkeypatch, error):
    clock = iter([0, 1_000_000, 4_000_000, 6_000_000])
    monkeypatch.setattr(stage_timing, "perf_counter_ns", lambda: next(clock))
    timing = StageTimings()
    with pytest.raises(type(error)) as caught, timing.measure("setup"):
        with timing.measure("cached_disposition"):
            raise error
    assert caught.value is error
    report = timing.report()
    assert report["timing_total_ms"] == 6
    assert report["timing_setup_ms"] == report["timing_cached_disposition_ms"] == 3
    assert report["timing_cached_disposition_calls"] == 1
    assert report["timing_cached_disposition_completed"] == report["timing_setup_completed"] == 0
    assert PRIVATE not in json.dumps(report)


@pytest.fixture
def chunk_case(monkeypatch, settings, discogs_release, search_payload):
    """Exercise real chunk control flow; fake I/O only, with independent clock streams."""
    settings = settings.model_copy(
        update={
            "ebay_client_id": SecretStr(PRIVATE + "-client"),
            "ebay_client_secret": SecretStr(PRIVATE + "-secret"),
        }
    )
    tick = count(step=1_000_000)
    monkeypatch.setattr(stage_timing, "perf_counter_ns", lambda: next(tick))
    budget_clock = Mock(return_value=0)
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=budget_clock))
    variant = normalize_release(discogs_release, NOW)
    source = normalize_listing(search_payload["itemSummaries"][0], NOW).model_copy(
        update={
            "marketplace_item_id": PRIVATE + "-item",
            "title": PRIVATE + "-title",
            "seller_id": PRIVATE + "-seller",
            "seller_username": PRIVATE + "-username",
            "listing_url": "https://example.invalid/" + PRIVATE,
            "current_price": PRIVATE_PRICE,
            "item_specifics": {PRIVATE + "-field": [PRIVATE + "-value"]},
            "source_metadata": {"delivery_country": None, "delivery_postal_code": None},
        }
    )
    claim = {
        "id": PRIVATE + "-watch",
        "config": SavedWatch(release_id=123, label=PRIVATE, extra_queries=[PRIVATE]).model_dump(
            mode="json"
        ),
    }
    state = {
        "queries": [
            {
                "baseline": new_pass("1990-01-01T00:00:00Z", NOW.isoformat()),
                "incremental": new_pass("2026-09-30T00:00:00Z", NOW.isoformat()),
                "reconciliation": None,
                "watermark": None,
            }
        ],
        "round_robin": 0,
        "barcode_recovery_version": 1,
    }
    cached = {"item_id": PRIVATE + "-cached", "reason": SETTINGS_CHANGED, "failures": 0}
    detail = {
        "item_id": PRIVATE + "-detail",
        "reason": None,
        "failures": 0,
        "status": "evaluated",
        "kind": "new_to_finder",
    }
    queue = Mock()
    queue.load.return_value = state
    queue.due.side_effect = lambda *a, limit, pending: [cached] if pending else []
    queue.disposition.return_value = ("evaluated", 0)
    queue.coverage.return_value = {
        "pending": 0,
        "unique_retrieved": 8,
        "outcomes": {"evaluated": 8},
        "initial": {"queries_exhausted": 1, "pages": 2},
    }
    store = Mock()
    store.finish.return_value = 0
    repository = Mock()
    repository.get.return_value = source
    catalog = SimpleNamespace(requests=1, retries=0)
    provider = Mock()
    provider.get_release.return_value = variant
    provider.search_alternatives.return_value = AlternativeRetrieval([], False)
    client = SimpleNamespace(
        get=Mock(return_value={}),
        search_requests=0,
        detail_requests=0,
        browse_requests=0,
        browse_retries=0,
        quota_requests=0,
    )
    adapter = Mock()
    adapter.refresh_known.return_value = ListingObservation(listing=source)
    assess = Mock(return_value={"status": "family_review", "verify": [], "notify": False})
    monkeypatch.setattr(worker, "WatchStore", lambda _: store)
    monkeypatch.setattr(worker, "DiscoveryStore", lambda _: queue)
    monkeypatch.setattr(worker, "DiscogsClient", lambda _: nullcontext(catalog))
    monkeypatch.setattr(worker, "DiscogsCatalogProvider", lambda _: provider)
    monkeypatch.setattr(worker, "load_profile", lambda *args: None)
    monkeypatch.setattr(worker, "watch_queries", lambda *args: [PRIVATE])
    monkeypatch.setattr(worker, "poll_minutes", lambda _: 10)
    monkeypatch.setattr(worker, "EbayClient", lambda _: nullcontext(client))
    monkeypatch.setattr(worker, "EbayAdapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(worker, "detail_batch", Mock(return_value=[detail]))
    monkeypatch.setattr(worker, "search_page", Mock(return_value=SearchPage([], 0, False, PRIVATE)))
    monkeypatch.setattr(
        worker,
        "summarize_browse_quota",
        lambda _: {"resources": [{"name": "buy.browse", "rates": [{"remaining": 5000}]}]},
    )
    monkeypatch.setattr(watch_worker, "assess_review", assess)
    return SimpleNamespace(
        run=lambda: worker.run_chunk(repository, settings, None, claim, now_fn=lambda: NOW),
        queue=queue,
        store=store,
        repository=repository,
        provider=provider,
        adapter=adapter,
        client=client,
        assess=assess,
        clock=budget_clock,
        claim=claim,
    )


def test_chunk_fixed_timing_keys_counts_and_anonymous_report(chunk_case):
    result = chunk_case.run()
    assert result["completed"] == 1 and result["failed"] == 0
    assert result["timing_total_ms"] == 19
    for stage in STAGES:
        assert result[f"timing_{stage}_calls"] == 1
        assert result[f"timing_{stage}_completed"] == 1
        expected = 4 if stage == "setup" else 7 if stage == "search" else 1
        assert result[f"timing_{stage}_ms"] == expected
    assert chunk_case.clock.call_count == 8  # deadline, five search checks, cached, detail
    assert chunk_case.assess.call_count == 2
    assert chunk_case.queue.disposition.call_count == 2
    assert chunk_case.adapter.refresh_known.call_count == 1
    assert PRIVATE not in json.dumps(result)
    assert str(PRIVATE_PRICE) not in json.dumps(result)
    assert all(type(value) is int for value in result.values())
    # Snapshots remain snapshots; timing call counts are this chunk's actual work.
    assert result["evaluated"] == 8 and result["timing_cached_disposition_completed"] == 1


@pytest.mark.parametrize("stop", ["execution_budget", "chunk_budget", "storage_budget", "quota"])
def test_partial_stops_keep_original_control_flow_and_deadline_reads(chunk_case, monkeypatch, stop):
    case = chunk_case
    if stop == "execution_budget":
        case.clock.side_effect = [0] * 6 + [worker.CHUNK_SECONDS] * 2
    elif stop == "chunk_budget":
        monkeypatch.setattr(worker, "SEARCH_CALLS", 1)
    elif stop == "storage_budget":
        case.queue.checkpoint.side_effect = [None, StorageBudget(), None]
    else:
        monkeypatch.setattr(
            worker,
            "summarize_browse_quota",
            lambda _: {"resources": [{"name": "buy.browse", "rates": [{"remaining": 0}]}]},
        )
    result = case.run()
    reason = "quota_or_attempt_budget" if stop == "quota" else stop
    assert case.store.finish.call_args.kwargs["summary"]["coverage"]["partial_reason"] == reason
    assert result["completed"] == (stop != "quota")
    assert result["quota_paused"] == (stop == "quota")
    assert result["failed"] == 0
    expected_calls = 1 if stop == "chunk_budget" else 0
    assert result["timing_cached_review_calls"] == expected_calls
    assert result["timing_detail_calls"] == expected_calls
    assert (
        case.clock.call_count
        == {"execution_budget": 8, "chunk_budget": 4, "storage_budget": 2, "quota": 1}[stop]
    )
    assert result["timing_finalize_calls"] == result["timing_finalize_completed"] == 1
    assert PRIVATE not in json.dumps(result)
    assert str(PRIVATE_PRICE) not in json.dumps(result)


@pytest.mark.parametrize("error", [LostLease(PRIVATE), RuntimeError(PRIVATE)])
def test_cached_storage_failure_and_lease_loss_keep_original_cleanup(chunk_case, error):
    case = chunk_case
    case.queue.disposition.side_effect = error
    result = case.run()
    assert result.get("superseded" if isinstance(error, LostLease) else "failed") == 1
    assert result["timing_cached_disposition_calls"] == 1
    assert result["timing_cached_disposition_completed"] == 0
    assert result["timing_search_completed"] == 0
    assert result["timing_finalize_completed"] == 1
    assert result["timing_detail_calls"] == 0
    assert case.clock.call_count == 7
    assert case.store.finish.call_args.kwargs["success"] is False
    assert PRIVATE not in json.dumps(result)
    assert str(PRIVATE_PRICE) not in json.dumps(result)


def test_catalog_failure_still_finishes_without_provider_followup(chunk_case):
    case = chunk_case
    case.provider.get_release.side_effect = RuntimeError(PRIVATE)
    result = case.run()
    assert result["failed"] == result["timing_catalog_calls"] == 1
    assert result["timing_catalog_completed"] == result["timing_search_calls"] == 0
    assert result["timing_finalize_completed"] == 1
    assert case.clock.call_count == 1
    case.client.get.assert_not_called()
    assert PRIVATE not in json.dumps(result)
    assert str(PRIVATE_PRICE) not in json.dumps(result)


def test_cleanup_failure_is_not_hidden_or_replaced(chunk_case):
    case = chunk_case
    original, cleanup = RuntimeError(PRIVATE + "-original"), RuntimeError(PRIVATE + "-cleanup")
    case.provider.get_release.side_effect = original
    case.store.finish.side_effect = cleanup
    with pytest.raises(RuntimeError) as caught:
        case.run()
    assert caught.value is cleanup
    assert caught.value.__context__ is original
    assert case.clock.call_count == 1


def test_run_aggregation_preserves_injected_budget_clock_and_sums_all_timing_fields(
    chunk_case, monkeypatch, settings
):
    case = chunk_case
    original_chunk = worker.run_chunk
    reports = []

    def chunk(repo, settings, discogs_settings, claim):
        report = original_chunk(repo, settings, discogs_settings, claim, now_fn=lambda: NOW)
        reports.append(report)
        return report

    run_clock = Mock(side_effect=[0, 0, 100, 200, 300])
    monkeypatch.setattr(worker, "run_chunk", chunk)
    monkeypatch.setattr(watch_worker, "WatchStore", lambda _: case.store)
    case.store.claim.return_value = case.claim
    result = watch_worker.run_due_watches(
        case.repository, settings, None, run_seconds=400, clock=run_clock
    )
    assert result["attempted"] == result["completed"] == case.store.claim.call_count == 3
    assert run_clock.call_count == 5
    for key in StageTimings().report():
        assert result[key] == sum(report[key] for report in reports)
    assert result["timing_cached_review_calls"] == 3
    assert result["evaluated"] == 24  # summed snapshots, not 24 newly evaluated rows
    assert PRIVATE not in json.dumps(result)
    assert str(PRIVATE_PRICE) not in json.dumps(result)


def test_missing_cached_source_is_not_counted_as_a_review_or_disposition(chunk_case):
    case = chunk_case
    case.repository.get.side_effect = [None, case.repository.get.return_value]
    result = case.run()
    assert result["completed"] == 1
    assert result["timing_cached_read_calls"] == result["timing_cached_read_completed"] == 1
    assert result["timing_cached_review_calls"] == result["timing_cached_disposition_calls"] == 0
    assert result["timing_detail_completed"] == case.assess.call_count == 1
    assert case.queue.disposition.call_count == 1


def test_cached_review_value_error_keeps_existing_quota_pause_semantics(chunk_case):
    case = chunk_case
    case.assess.side_effect = ValueError(PRIVATE)
    result = case.run()
    assert result["quota_paused"] == 1 and result["completed"] == 0
    assert result["timing_cached_review_calls"] == 1
    assert result["timing_cached_review_completed"] == result["timing_detail_calls"] == 0
    case.queue.disposition.assert_not_called()
    assert case.clock.call_count == 7
    assert PRIVATE not in json.dumps(result)
    assert str(PRIVATE_PRICE) not in json.dumps(result)


def test_handled_detail_error_is_not_reported_as_newly_evaluated_work(chunk_case):
    case = chunk_case
    case.adapter.refresh_known.side_effect = RuntimeError(PRIVATE)
    result = case.run()
    assert result["completed"] == 1
    assert result["timing_detail_calls"] == result["timing_detail_completed"] == 1
    assert case.queue.disposition.call_args.kwargs["status"] == "error"
    assert case.queue.disposition.call_args.kwargs["reason"] == "detail_failed"
    assert case.assess.call_count == 1  # only the cached row was reviewed
    assert PRIVATE not in json.dumps(result)
    assert str(PRIVATE_PRICE) not in json.dumps(result)


@pytest.mark.parametrize("has_budget", [False, True])
def test_empty_run_has_zero_fixed_timing_fields_without_reading_measurement_clock(
    chunk_case, monkeypatch, settings, has_budget
):
    case = chunk_case
    measurement_clock = Mock(side_effect=AssertionError("No chunk was measured"))
    monkeypatch.setattr(stage_timing, "perf_counter_ns", measurement_clock)
    monkeypatch.setattr(watch_worker, "WatchStore", lambda _: case.store)
    case.store.claim.return_value = None
    clock = Mock(side_effect=[0, 0 if has_budget else 400])
    result = watch_worker.run_due_watches(
        case.repository, settings, None, run_seconds=400, clock=clock
    )
    assert result["attempted"] == 0
    assert case.store.claim.call_count == has_budget
    assert clock.call_count == 2
    assert all(result[key] == 0 for key in StageTimings().report())
    measurement_clock.assert_not_called()
