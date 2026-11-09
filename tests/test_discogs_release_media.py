"""Offline, synthetic public-catalog integration and shared-budget regressions."""

import json
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_cloud_app import ORIGIN, call, verifier
from test_discogs_completeness import metadata
from test_discogs_listing import payload
from test_discogs_rules import target
from test_discogs_web import request as local_request

from finder.cloud_app import CloudApp
from finder.discogs_completeness import RELEASE_MEDIA_TTL, current_release_media
from finder.discogs_listing import (
    ListingReader,
    ListingReadError,
    PublicReadBudget,
    ReleaseStatsReader,
    normalize,
    observation,
)
from finder.discogs_monitor import ReleaseMonitor, listing_review
from finder.discogs_rules import RulesStore, evaluate


class Clock:
    def __init__(self):
        self.rate = 100.0
        self.wall = datetime.now(UTC).timestamp()
        self.waits = []

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.rate += seconds

    def now(self):
        return datetime.fromtimestamp(self.wall, UTC)


@pytest.fixture
def setup():
    clock = Clock()
    requests = []
    responses = {
        "listing": {**payload(), "comments": "Includes both discs"},
        "release": metadata(),
        "status": 200,
    }

    def handle(request):
        requests.append((request, clock.rate))
        assert request.method == "GET" and request.url.host == "api.discogs.com"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if request.url.path.startswith("/releases/"):
            return httpx.Response(responses["status"], json=responses["release"])
        return httpx.Response(200, json=responses["listing"])

    reader = ListingReader(
        transport=httpx.MockTransport(handle),
        clock=lambda: clock.rate,
        wall_clock=lambda: clock.wall,
        sleep=clock.sleep,
    )
    return reader, clock, requests, responses


def cloud(tmp_path, reader, clock, *, stats=None):
    path = tmp_path / "rules.db"
    RulesStore(path, create=True).close()
    app = CloudApp(path, ORIGIN, verifier(), listing_reader=reader, stats_reader=stats)
    app.monitor.clock = lambda: clock.rate
    assert (
        call(app, "/api/target", {"target": target().model_dump(), "expected_revision": 0})[0]
        == 200
    )
    assert call(app, "/api/kill", {"killed": False, "expected_revision": 0})[0] == 200
    return app


def test_explicit_cold_read_has_two_spaced_bounded_fixed_public_gets(setup):
    reader, clock, requests, _ = setup
    listing = reader.read_for_release("123", 123)
    assert [str(request.url) for request, _ in requests] == [
        "https://api.discogs.com/marketplace/listings/123",
        "https://api.discogs.com/releases/123",
    ]
    assert [at for _, at in requests] == [100.0, 103.0]
    assert clock.waits == [3.0]
    for request, _ in requests:
        assert request.extensions["timeout"]["read"] == 8
    assert listing["media_completeness"]["status"] == "reported_complete"
    assert listing["media_completeness"]["expected_media_count"] == 2
    assert listing["release_media_metadata"] == metadata()
    assert listing["release_media_provenance"] == {
        "source": "discogs_release_api",
        "release_id": 123,
        "fetched_at": clock.now().isoformat(),
    }
    result = evaluate(target(), observation(listing), now=clock.now())
    assert result["media_completeness"]["status"] == "reported_complete"
    assert result["eligible_for_review"] is False and result["purchase_allowed"] is False


def test_no_wait_needed_if_listing_response_already_consumed_spacing(setup):
    reader, clock, requests, responses = setup
    original = reader.transport

    def handle(request):
        response = original.handle_request(request)
        if "/listings/" in request.url.path:
            clock.rate += 4
        return response

    reader.transport = httpx.MockTransport(handle)
    assert reader.read_for_release(123, 123)["media_completeness"]["status"] == "reported_complete"
    assert [at for _, at in requests] == [100, 104]
    assert clock.waits == []


def test_cache_hit_adds_no_catalog_request_and_returned_fields_cannot_mutate_cache(setup):
    reader, clock, requests, _ = setup
    first = reader.read_for_release(123, 123)
    first["release_media_metadata"]["formats"][0]["qty"] = "1"
    first["release_media_provenance"]["source"] = "seller"
    clock.rate += 3
    second = reader.read_for_release(123, 123)
    assert len(requests) == 3 and clock.waits == [3]
    assert second["media_completeness"]["expected_media_count"] == 2
    assert second["release_media_provenance"]["source"] == "discogs_release_api"


@pytest.mark.parametrize("age", [RELEASE_MEDIA_TTL, RELEASE_MEDIA_TTL + 1, -1])
def test_stale_or_future_cache_refresh_failure_never_reuses_previous_complete(setup, age):
    reader, clock, requests, responses = setup
    assert reader.read_for_release(123, 123)["media_completeness"]["status"] == "reported_complete"
    clock.rate += 3
    clock.wall += age
    responses["status"] = 500
    result = reader.read_for_release(123, 123)
    assert len(requests) == 4
    assert result["media_completeness"]["status"] == "unknown"
    assert result["release_media_metadata"] is None and result["release_media_provenance"] is None
    assert 123 not in reader._media_cache


def test_success_cache_has_maximum_100_entries_and_keeps_only_projection(setup):
    reader, clock, requests, responses = setup
    for ident in range(1, 102):
        responses["listing"]["release"]["id"] = ident
        responses["release"] = {
            **metadata(ident=ident),
            "notes": "not retained",
            "tracklist": [1] * 50,
        }
        reader.read_for_release(123, ident)
        clock.rate += 3
    assert len(reader._media_cache) == 100 and 1 not in reader._media_cache
    cached = reader._media_cache[101]
    assert cached[0] == metadata(ident=101)
    assert set(cached[1]) == {"source", "release_id", "fetched_at"}
    assert len(requests) == 202


def test_listing_release_mismatch_never_requests_arbitrary_catalog_release(setup):
    reader, clock, requests, responses = setup
    responses["listing"]["release"]["id"] = 456
    result = reader.read_for_release(123, 123)
    assert len(requests) == 1 and clock.waits == []
    assert result["media_completeness"]["status"] == "unknown"
    assert result["release_id"] == 456


@pytest.mark.parametrize("ident", [True, 0, -1, "../orders", "https://evil.test/releases/123"])
def test_invalid_configured_identity_cannot_make_first_request(setup, ident):
    reader, _, requests, _ = setup
    with pytest.raises(ValueError):
        reader.read_for_release(123, ident)
    assert requests == []


def test_plain_read_remains_exactly_one_get_even_with_metadata_in_memory(setup):
    reader, clock, requests, _ = setup
    reader.read_for_release(123, 123)
    clock.rate += 3
    listing = reader.read(123)
    assert len(requests) == 3
    assert listing["media_completeness"]["status"] == "unknown"
    assert listing["release_media_metadata"] is None


@pytest.mark.parametrize(
    "release",
    [
        None,
        [],
        {},
        metadata(ident=456),
        {"id": "123", "formats": [{"name": "Vinyl", "qty": "2"}]},
        {"id": 123, "formats": [{"name": "Vinyl", "qty": None}], "tracklist": [1, 2]},
        {"id": 123, "formats": [{"name": "Vinyl", "qty": "1"}, {"name": "CD", "qty": "1"}]},
        {"id": 123, "formats": [{"name": "Vinyl", "qty": "0"}], "sides": ["A", "B"]},
        {"id": 123, "formats": [{"name": "Vinyl", "qty": "101"}]},
        {"id": 123, "title": "2LP", "tracklist": ["A1", "B1", "C1", "D1"]},
    ],
)
def test_bad_catalog_response_fails_closed_not_inferred_from_tracks_sides_or_title(setup, release):
    reader, _, requests, responses = setup
    responses["release"] = release
    result = reader.read_for_release(123, 123)
    assert len(requests) == 2 and result["media_completeness"]["status"] == "unknown"
    assert result["release_media_metadata"] is None and reader._media_cache == {}


@pytest.mark.parametrize("status", [301, 403, 404, 429, 500])
def test_catalog_http_failure_never_redirects_or_retries(setup, status):
    reader, _, requests, responses = setup
    responses["status"] = status
    listing = reader.read_for_release(123, 123)
    assert listing["media_completeness"]["status"] == "unknown"
    assert len(requests) == 2
    assert reader._media_cache == {}


@pytest.mark.parametrize("body", [b"bad json", b"x" * 262145])
def test_catalog_unreadable_or_oversized_payload_stays_unknown_without_raw_data(setup, body):
    reader, clock, requests, _ = setup

    def handle(request):
        requests.append((request, clock.rate))
        return (
            httpx.Response(200, content=body)
            if "/releases/" in request.url.path
            else httpx.Response(200, json=payload())
        )

    reader.transport = httpx.MockTransport(handle)
    result = reader.read_for_release(123, 123)
    assert result["media_completeness"]["status"] == "unknown" and len(requests) == 2
    assert "bad json" not in json.dumps(result)


def test_catalog_timeout_leaves_listing_available_but_unknown(setup):
    reader, clock, requests, _ = setup

    def handle(request):
        requests.append((request, clock.rate))
        if "/releases/" in request.url.path:
            raise httpx.ReadTimeout("do not disclose provider detail")
        return httpx.Response(200, json=payload())

    reader.transport = httpx.MockTransport(handle)
    result = reader.read_for_release(123, 123)
    assert result["available"] is True and result["media_completeness"]["status"] == "unknown"
    assert len(requests) == 2 and "disclose" not in json.dumps(result)


@pytest.mark.parametrize("age", [RELEASE_MEDIA_TTL, RELEASE_MEDIA_TTL + 1, -1])
def test_live_evaluate_and_monitor_recheck_metadata_freshness(setup, age):
    reader, clock, _, _ = setup
    listing = reader.read_for_release(123, 123)
    now = clock.now() + timedelta(seconds=age)
    evaluated = evaluate(target(), observation(listing), now=now)
    reviewed = listing_review(target(), listing, fresh=True, now=now)
    assert evaluated["media_completeness"]["status"] == "unknown"
    assert reviewed["media_completeness"]["status"] == "unknown"
    assert not evaluated["eligible_for_review"] and reviewed["status"] == "blocked"


@pytest.mark.parametrize(
    "provenance",
    [
        None,
        {},
        {"source": "seller"},
        {"source": "discogs_release_api", "release_id": True},
        {"source": "discogs_release_api", "release_id": "123"},
        {"source": "discogs_release_api", "release_id": 456},
        {"source": "discogs_release_api", "release_id": 123, "fetched_at": 123},
        {
            "source": "discogs_release_api",
            "release_id": 123,
            "fetched_at": "2026-01-01T12:00:00+01:00",
        },
        {"source": "discogs_release_api", "release_id": 123, "fetched_at": "2026-01-01T12:00:00"},
    ],
)
def test_missing_or_malformed_provenance_never_qualifies(provenance):
    assert current_release_media(metadata(), provenance, 123, now=datetime.now(UTC)) is None
    listing = normalize(
        {**payload(), "comments": "Includes both discs"},
        123,
        release_metadata=metadata(),
        release_provenance=provenance,
    )
    assert listing["media_completeness"]["status"] == "unknown"


def test_explicit_incomplete_survives_missing_or_expired_catalog_metadata(setup):
    reader, clock, _, responses = setup
    responses["listing"]["comments"] = "VG+; NO DISC 1"
    listing = reader.read_for_release(123, 123)
    assert listing["media_completeness"]["status"] == "incomplete"
    result = listing_review(target(), listing, fresh=True, now=clock.now() + timedelta(days=1))
    assert result["media_completeness"]["status"] == "incomplete"


def test_second_request_waits_only_once_then_contention_stays_unknown(setup):
    reader, clock, requests, _ = setup

    def contended(seconds):
        clock.sleep(seconds)
        reader.budget.reserve()  # Another caller takes the just-due slot.

    reader.budget.sleep = contended
    result = reader.read_for_release(123, 123)
    assert result["media_completeness"]["status"] == "unknown"
    assert clock.waits == [3] and len(requests) == 1


def test_no_sleep_for_longer_shared_cooldown_or_retry(setup):
    reader, clock, requests, _ = setup
    claims = iter((0, 60))
    budget = PublicReadBudget(
        clock=lambda: clock.rate, sleep=clock.sleep, claim=lambda: next(claims)
    )
    result = reader.read_for_release(123, 123, budget=budget)
    assert result["media_completeness"]["status"] == "unknown"
    assert len(requests) == 1 and clock.waits == []


def test_both_explicit_endpoints_attach_metadata_with_unknown_purchase_permission(setup, tmp_path):
    reader, clock, requests, _ = setup
    app = cloud(tmp_path, reader, clock)
    code, _, body = call(app, "/api/listing", {"release_id": 123, "listing": "123"})
    result = json.loads(body)
    assert code == 200 and len(requests) == 2
    assert result["evaluation"]["media_completeness"]["status"] == "reported_complete"
    assert result["evaluation"]["purchase_allowed"] is False
    clock.rate += 3
    store = RulesStore(app.database)
    try:
        code, body = local_request(
            store, "/api/listing", {"release_id": 123, "listing": "123"}, reader=reader
        )
    finally:
        store.close()
    assert code == 200 and len(requests) == 3
    result = json.loads(body)
    assert result["evaluation"]["media_completeness"]["status"] == "reported_complete"
    assert result["evaluation"]["purchase_allowed"] is False


def test_cloud_catalog_429_persists_shared_worker_and_reader_cooldown(setup, tmp_path):
    reader, clock, requests, _ = setup

    def handle(request):
        requests.append((request, clock.rate))
        if "/releases/" in request.url.path:
            return httpx.Response(429, headers={"Retry-After": "1200"})
        return httpx.Response(200, json=payload())

    reader.transport = httpx.MockTransport(handle)
    stats = ReleaseStatsReader(transport=reader.transport, clock=lambda: clock.rate)
    app = cloud(tmp_path, reader, clock, stats=stats)
    code, _, body = call(app, "/api/listing", {"release_id": 123, "listing": "123"})
    assert (
        code == 200 and json.loads(body)["evaluation"]["media_completeness"]["status"] == "unknown"
    )
    assert reader.next_read == clock.rate + 1200
    reopened = ReleaseMonitor(app.database, stats_reader=stats, clock=lambda: clock.rate)
    assert reopened.run_once() == {"status": "cooldown", "requests": 0}
    with pytest.raises(ListingReadError):
        reader.read(123)
    assert len(requests) == 2


def test_cloud_stats_can_take_second_slot_without_extra_catalog_or_unbounded_retry(setup, tmp_path):
    reader, clock, requests, _ = setup
    stats_requests = []
    stats = ReleaseStatsReader(
        transport=httpx.MockTransport(
            lambda request: (
                stats_requests.append(request) or httpx.Response(200, json={"num_for_sale": 0})
            )
        ),
        clock=lambda: clock.rate,
    )
    app = cloud(tmp_path, reader, clock, stats=stats)

    def concurrent_tick(seconds):
        clock.sleep(seconds)
        assert app.monitor.run_once() == {"status": "ok", "requests": 1}

    reader.budget.sleep = concurrent_tick
    code, _, body = call(app, "/api/listing", {"release_id": 123, "listing": "123"})
    assert (
        code == 200 and json.loads(body)["evaluation"]["media_completeness"]["status"] == "unknown"
    )
    assert len(requests) == 1 and len(stats_requests) == 1 and clock.waits == [3]
    assert app.monitor.run_once() == {"status": "cooldown", "requests": 0}


@pytest.mark.parametrize("change", ["stop", "pause", "owned"])
def test_cloud_stop_and_rule_changes_remain_available_during_wait_and_block_second_request(
    setup, tmp_path, change
):
    reader, clock, requests, _ = setup
    app = cloud(tmp_path, reader, clock)
    waiting, proceed = threading.Event(), threading.Event()
    results = []

    def blocked_sleep(seconds):
        waiting.set()
        assert proceed.wait(3), "Test did not release the synthetic wait"
        clock.sleep(seconds)

    reader.budget.sleep = blocked_sleep
    thread = threading.Thread(
        target=lambda: results.append(
            call(app, "/api/listing", {"release_id": 123, "listing": "123"})
        )
    )
    thread.start()
    try:
        assert waiting.wait(3)
        if change == "stop":
            response = call(app, "/api/kill", {"killed": True, "expected_revision": 1})
        elif change == "pause":
            response = call(
                app,
                "/api/target",
                {"target": {**target().model_dump(), "enabled": False}, "expected_revision": 1},
            )
        else:
            response = call(
                app, "/api/owned", {"release_id": 123, "owned": True, "expected_revision": 1}
            )
        assert response[0] == 200
    finally:
        proceed.set()
        thread.join(3)
    assert not thread.is_alive() and results[0][0] == 502
    assert len(requests) == 1 and clock.waits == [3]
    assert call(app, "/api/snapshot")[0] == 200  # Lock always reacquired/released safely.


def test_cloud_rate_limit_is_saved_even_if_stop_occurs_in_catalog_response(setup, tmp_path):
    reader, clock, requests, _ = setup
    app = cloud(tmp_path, reader, clock)

    def handle(request):
        requests.append((request, clock.rate))
        if "/releases/" in request.url.path:
            assert call(app, "/api/kill", {"killed": True, "expected_revision": 1})[0] == 200
            return httpx.Response(429, headers={"Retry-After": "1200"})
        return httpx.Response(200, json=payload())

    reader.transport = httpx.MockTransport(handle)
    assert call(app, "/api/listing", {"release_id": 123, "listing": "123"})[0] == 502
    store = RulesStore(app.database)
    try:
        assert (
            store.db.execute("SELECT next_request FROM discogs_monitor_control").fetchone()[0]
            == clock.rate + 1200
        )
    finally:
        store.close()
    assert len(requests) == 2
