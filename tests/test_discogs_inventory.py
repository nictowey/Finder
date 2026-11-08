"""Synthetic official-shaped inventory and shared-budget tests; never live network."""

import copy
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass

import httpx
import pytest
from pydantic import ValidationError

from finder.discogs_inventory import (
    InventoryConfig,
    InventoryObserver,
    collect,
    initialize_inventory,
    main,
)
from finder.discogs_listing import ListingReadError
from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_read_guard import ReadSafetyHalt, guard_row
from finder.discogs_rules import RulesStore


def config(**changes):
    values = {
        "version": 1,
        "seed": {
            "source": "owner_supplied_known_listing",
            "seller": "fixture-seller",
            "listing_id": 100,
            "release_id": 123,
        },
        "targets": [
            {
                "release_id": 123,
                "currency": "USD",
                "under": "100",
                "min_media": "NM",
                "min_sleeve": "NM",
            }
        ],
    }
    values.update(changes)
    return InventoryConfig.model_validate(values)


def listing(ident=100, **changes):
    value = {
        "id": ident,
        "release": {"id": 123},
        "seller": {"username": "fixture-seller"},
        "price": {"currency": "USD", "value": "40"},
        "condition": "Near Mint (NM or M-)",
        "sleeve_condition": "Near Mint (NM or M-)",
        "comments": "Both records included. Clean copy.",
        "status": "For Sale",
        "shipping_is_blocked": False,
    }
    value.update(changes)
    return value


def page(rows=None, *, number=1, items=None):
    rows = [listing()] if rows is None else rows
    items = len(rows) if items is None else items
    return {
        "pagination": {
            "page": number,
            "per_page": 50,
            "pages": (items + 49) // 50,
            "items": items,
            "urls": {"next": "https://untrusted.invalid/never-follow"},
        },
        "listings": rows,
    }


@dataclass
class Clock:
    now: float = 1800000000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "rules.db"
    RulesStore(path, create=True).close()
    clock = Clock()
    aggregate = AggregatePriceAlerts(path, clock=clock)
    for release in range(123, 142):
        aggregate.save_rule(
            PriceLeadRule(
                release_id=release,
                public_title="Synthetic target",
                currency="USD",
                any_price=True,
                enabled=True,
            ),
            expected_revision=0,
        )
    aggregate.set_enabled(True, expected_revision=0)
    initialize_inventory(path, config())
    return path, clock


def observer(workspace, response, cfg=None, detail=None):
    path, clock = workspace
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.discogs.com"
        assert "authorization" not in request.headers
        with closing(sqlite3.connect(path)) as db:
            token, since, _ = guard_row(db)
            assert token and since == clock.now
        if request.url.path.startswith("/marketplace/listings/"):
            ident = int(request.url.path.rsplit("/", 1)[1])
            if request.url.query:
                assert dict(request.url.params) == {"curr_abbr": "USD"}
                return detail(request) if detail else httpx.Response(200, json=listing(ident))
            assert ident == 100
            return httpx.Response(200, json=listing())
        assert request.url.path == "/users/fixture-seller/inventory"
        assert dict(request.url.params) == {
            "sort": "listed",
            "sort_order": "desc",
            "status": "For Sale",
            "per_page": "50",
            "page": "1",
        }
        return response(request)

    worker = InventoryObserver(
        path, cfg or config(), transport=httpx.MockTransport(handle), clock=clock, sleep=clock.sleep
    )
    return worker, seen


def tables(path):
    with closing(sqlite3.connect(path)) as db:
        return {
            name: db.execute(f"SELECT * FROM {name}").fetchall()
            for name in (
                "discogs_price_rules",
                "discogs_price_samples",
                "discogs_price_control",
                "discogs_price_outbox",
                "targets",
                "owned",
            )
        }


def test_baseline_dedup_restart_preserve_all_aggregate_rows(workspace):
    path, clock = workspace
    before = tables(path)
    rows = [listing()]
    worker, requests = observer(workspace, lambda _: httpx.Response(200, json=page(rows)))
    first = worker.poll()
    assert first["status"] == "baseline" and first["candidates"] == []
    assert first["baseline_matches"] == 1
    assert first["pagination"]["complete"]
    assert first["requests"] == 2
    assert worker.poll()["status"] == "not_due"
    assert len(requests) == 2
    rows.append(listing(101))
    clock.sleep(600)
    result = worker.poll()
    assert [v["listing_id"] for v in result["candidates"]] == [101]
    assert result["candidate_semantics"] == "newly_observed_not_proven_newly_listed"
    assert result["candidates"][0]["result"]["item_criteria_match"]
    assert not result["candidates"][0]["result"]["eligible_for_notification"]
    assert not result["candidates"][0]["result"]["purchase_qualified"]
    clock.sleep(600)
    worker, _ = observer(workspace, lambda _: httpx.Response(200, json=page(rows)))
    assert worker.poll()["candidates"] == []
    assert tables(path) == before
    assert len(before["discogs_price_rules"]) == 19


def test_snapshot_does_not_baseline_and_reports_exact_release_only(workspace):
    _, clock = workspace
    rows = [listing(), listing(101, release={"id": 124})]
    worker, _ = observer(workspace, lambda _: httpx.Response(200, json=page(rows)))
    result = worker.poll(snapshot=True)
    assert result["status"] == "snapshot_only"
    assert [v["listing_id"] for v in result["candidates"]] == [100]
    clock.sleep(600)
    assert worker.poll()["status"] == "baseline"


@pytest.mark.parametrize(
    "changes,check",
    [
        ({"price": None}, "item_strictly_under"),
        ({"price": {"value": "40", "currency": "GBP"}}, "item_strictly_under"),
        ({"price": {"value": "99.999", "currency": "USD"}}, "item_strictly_under"),
        ({"condition": None}, "media_at_least_minimum"),
        ({"sleeve_condition": "Very Good Plus (VG+)"}, "sleeve_at_least_minimum"),
        ({"comments": None}, "comments_supported"),
        ({"comments": "Record skips during playback."}, "no_disclosed_media_defect"),
        ({"comments": "Missing disc 2."}, "no_explicit_media_omission"),
        ({"status": "Sold"}, "reported_for_sale"),
    ],
)
def test_missing_and_unsafe_evidence_never_qualifies(changes, check):
    result = collect(config(), listing(), lambda _: page([listing(**changes)]))
    candidate = result["candidates"][0]
    assert candidate["checks"][check] is False
    assert not candidate["item_criteria_match"]
    assert not candidate["purchase_qualified"]


def test_shipping_unknown_stays_unverified_and_original_currency_separate():
    row = listing(shipping_is_blocked=None, original_price={"curr_abbr": "GBP", "value": "30"})
    result = collect(config(), listing(), lambda _: page([row]))["candidates"][0]
    assert result["shipping"]["blocked_or_unknown"]
    assert not result["shipping"]["destination_verified"]
    assert result["original_price"]["currency"] == "GBP"
    assert not result["purchase_qualified"]


def test_seed_must_be_verified_before_any_inventory_request():
    for value in (listing(seller={"username": "another"}), listing(release={"id": 456})):
        with pytest.raises(ListingReadError, match="does not verify"):
            collect(config(), value, lambda _: pytest.fail("No inventory read"))


@pytest.mark.parametrize(
    "seller",
    [
        "https://discogs.com/x",
        "../other",
        "seller?x=1",
        "seller/user",
        " seller",
        "seller@host",
        "seller\n",
    ],
)
def test_no_arbitrary_urls_or_usernames(seller):
    seed = config().seed.model_dump()
    seed["seller"] = seller
    with pytest.raises(ValidationError):
        config(seed=seed)


def test_complete_multipage_and_pagination_shift_markers():
    first = [listing(i) for i in range(100, 150)]
    cfg = config(max_pages=2)
    complete = collect(
        cfg,
        listing(),
        lambda p: page(first, items=51) if p == 1 else page([listing(150)], number=2, items=51),
    )
    assert complete["pagination"]["complete"] and complete["pagination"]["pages_fetched"] == 2
    assert len(complete["candidates"]) == 51
    shifted = collect(
        cfg,
        listing(),
        lambda p: page(first, items=51) if p == 1 else page([listing(149)], number=2, items=51),
    )
    assert not shifted["pagination"]["complete"]
    assert shifted["pagination"]["reason"] == "pagination_shifted"
    changed = collect(
        cfg,
        listing(),
        lambda p: (
            page(first, items=51)
            if p == 1
            else page([listing(150), listing(151)], number=2, items=52)
        ),
    )
    assert changed["pagination"]["reason"] == "pagination_changed"


def test_page_cap_never_establishes_baseline_or_emits_candidates(workspace):
    first = [listing(i) for i in range(100, 150)]
    worker, _ = observer(workspace, lambda _: httpx.Response(200, json=page(first, items=51)))
    result = worker.poll()
    assert result["status"] == "incomplete_no_new_candidates"
    assert not result["pagination"]["complete"]
    assert result["candidates"] == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("pagination"),
        lambda p: p["pagination"].update(items=True),
        lambda p: p["pagination"].update(page=2),
        lambda p: p["pagination"].update(per_page=100),
        lambda p: p["pagination"].update(items=2),
        lambda p: p["listings"][0].update(id=True),
        lambda p: p["listings"][0].update(release={"id": "123"}),
    ],
)
def test_invalid_pages_fail_closed(mutate):
    payload = page()
    mutate(payload)
    with pytest.raises(ListingReadError):
        collect(config(), listing(), lambda _: payload)


@pytest.mark.parametrize("status", [401, 403])
def test_denial_latches_before_guard_release_and_survives_restart(workspace, status):
    path, clock = workspace
    worker, requests = observer(workspace, lambda _: httpx.Response(status))
    assert worker.poll()["status"] == "access_denied"
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is None
        assert json.loads(
            db.execute("SELECT data FROM discogs_seeded_inventory_state").fetchone()[0]
        )["denied"]
    initialize_inventory(path, config())  # Explicit init cannot clear denial.
    clock.sleep(100000)
    with pytest.raises(ReadSafetyHalt):
        worker.poll()
    assert len(requests) == 2


def test_429_respects_shared_retry_after_no_replay(workspace):
    path, clock = workspace
    worker, requests = observer(
        workspace, lambda _: httpx.Response(429, headers={"Retry-After": "1200"})
    )
    assert worker.poll()["status"] == "cooldown"
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[2] >= clock.now + 1200
    clock.sleep(600)
    assert worker.poll()["status"] == "not_due"
    assert len(requests) == 2


def test_unknown_request_keeps_shared_pending_and_observer_stopped(workspace):
    path, clock = workspace

    def fail(_):
        raise httpx.ConnectError("Do not leak provider detail")

    worker, requests = observer(workspace, fail)
    with pytest.raises(ReadSafetyHalt, match="response_unknown"):
        worker.poll()
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0]
    clock.sleep(100000)
    with pytest.raises(ReadSafetyHalt):
        worker.poll()
    assert len(requests) == 2


def test_control_stop_and_config_change_no_network(workspace):
    path, _ = workspace
    worker, requests = observer(workspace, lambda _: pytest.fail("No network"))
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE discogs_price_control SET enabled=0")
    with pytest.raises(ReadSafetyHalt):
        worker.poll()
    with pytest.raises(ReadSafetyHalt):
        initialize_inventory(path, config(poll_seconds=120))
    assert requests == []


def test_default_validate_and_fixture_modes_have_no_network_or_db(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(httpx.Client, "stream", lambda *_a, **_k: pytest.fail("No network"))
    path = tmp_path / "config.json"
    path.write_text(config().model_dump_json())
    assert main(["--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "validated_no_poll"
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"seed_listing": listing(), "pages": [page()]}))
    assert main(["--config", str(path), "--fixture", str(fixture)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["source"] == "offline_fixture" and result["freshness"] == "unknown"
    assert result["candidates"][0]["listing_id"] == "100"


def test_redirect_never_followed_and_payload_errors_do_not_leak(workspace):
    worker, requests = observer(
        workspace,
        lambda _: httpx.Response(302, headers={"Location": "https://bad.invalid/private"}),
    )
    result = worker.poll()
    assert result["status"] == "unavailable" and len(requests) == 2
    assert "bad.invalid" not in json.dumps(result)


def test_repeated_rows_fail_not_empty_baseline():
    with pytest.raises(ListingReadError, match="repeated"):
        collect(config(), listing(), lambda _: page([listing(), copy.deepcopy(listing())]))


def test_detail_stage_uses_requested_currency_and_keeps_inventory_currency_separate(workspace):
    worker, requests = observer(
        workspace,
        lambda _: httpx.Response(
            200, json=page([listing(price={"currency": "GBP", "value": "30"})])
        ),
        detail=lambda _: httpx.Response(
            200,
            json=listing(
                price={"currency": "USD", "value": "40.123456"},
                original_price={"curr_abbr": "GBP", "value": "30"},
            ),
        ),
    )
    result = worker.poll(snapshot=True)
    assert len(requests) == 3
    assert "curr_abbr" not in requests[1].url.params
    record = result["candidates"][0]
    assert record["state"] == "review_ready"
    assert record["result"]["price"]["display_value"] == "40.13"
    assert record["result"]["original_price"]["currency"] == "GBP"
    assert record["result"]["detail_recheck_required"] is False
    assert not record["result"]["purchase_qualified"]
    assert worker.results()["results"][0] == record


def test_seed_is_verified_once_and_a_disappearing_seed_does_not_stop_inventory(workspace):
    _, clock = workspace
    worker, requests = observer(workspace, lambda _: httpx.Response(200, json=page()))
    assert worker.poll()["status"] == "baseline"
    clock.sleep(600)
    second = worker.poll()
    assert second["requests"] == 1
    assert second["seed_verified"]
    assert len([r for r in requests if r.url.path == "/marketplace/listings/100"]) == 1


def test_bounded_details_survive_restart_and_do_not_drop_or_duplicate_queue(workspace):
    _, clock = workspace
    rows = [listing()]
    worker, _ = observer(workspace, lambda _: httpx.Response(200, json=page(rows)))
    worker.poll()
    rows.extend(listing(i) for i in (101, 102, 103))
    clock.sleep(600)
    first = worker.poll()
    assert len(first["candidates"]) == 2
    assert first["pending_detail_count"] == 1
    assert worker.results()["pending"][0]["listing_id"] == 103
    clock.sleep(600)
    worker, requests = observer(workspace, lambda _: httpx.Response(200, json=page(rows)))
    second = worker.poll()
    assert [v["listing_id"] for v in second["candidates"]] == [103]
    assert second["pending_detail_count"] == 0
    assert len(worker.results()["results"]) == 3
    assert len(requests) == 2  # One inventory + the remaining detail, no seed reread.
    clock.sleep(600)
    assert worker.poll()["candidates"] == []


def test_detail_rate_limit_preserves_pending_and_consumes_no_candidates(workspace):
    _, clock = workspace
    rows = [listing()]
    worker, _ = observer(workspace, lambda _: httpx.Response(200, json=page(rows)))
    worker.poll()
    rows.append(listing(101))
    clock.sleep(600)
    worker, requests = observer(
        workspace,
        lambda _: httpx.Response(200, json=page(rows)),
        detail=lambda _: httpx.Response(429, headers={"Retry-After": "1200"}),
    )
    failed = worker.poll()
    assert failed["status"] == "cooldown"
    assert failed["pending_detail_count"] == 1 and not failed["candidates"]
    assert len(requests) == 2
    clock.sleep(1200)
    worker, _ = observer(workspace, lambda _: httpx.Response(200, json=page(rows)))
    retried = worker.poll()
    assert [v["listing_id"] for v in retried["candidates"]] == [101]
    assert not retried["pending_detail_count"]


@pytest.mark.parametrize(
    "changes",
    [
        {"release": {"id": 999}},
        {"seller": {"username": "not-seeded"}},
        {"seller": {}},
        {"status": "Sold"},
    ],
)
def test_changed_detail_association_or_availability_is_retained_but_blocked(workspace, changes):
    worker, _ = observer(
        workspace,
        lambda _: httpx.Response(200, json=page()),
        detail=lambda _: httpx.Response(200, json=listing(**changes)),
    )
    result = worker.poll(snapshot=True)
    assert result["candidates"][0]["state"] == "criteria_blocked"
    assert not result["candidates"][0]["result"]["item_criteria_match"]
    assert not result["pending_detail_count"]


def test_missing_existing_state_row_never_resets_denial(workspace):
    path, _ = workspace
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM discogs_seeded_inventory_state")
    with pytest.raises(ReadSafetyHalt, match="state_missing"):
        initialize_inventory(path, config())
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM discogs_seeded_inventory_state").fetchone()[0] == 0


def test_retained_results_expire_without_losing_dedup_refs(workspace):
    _, clock = workspace
    worker, _ = observer(workspace, lambda _: httpx.Response(200, json=page()))
    worker.poll(snapshot=True)
    clock.sleep(21601)
    result = worker.results()["results"][0]
    assert result["state"] == "expired" and result["result"] is None
    assert result["listing_id"] == 100


def test_source_denial_during_detail_is_durable(workspace):
    path, clock = workspace
    worker, requests = observer(
        workspace, lambda _: httpx.Response(200, json=page()), detail=lambda _: httpx.Response(403)
    )
    result = worker.poll(snapshot=True)
    assert result["status"] == "access_denied" and result["pending_detail_count"] == 1
    assert worker.results()["access_denied"]
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is None
    clock.sleep(600)
    with pytest.raises(ReadSafetyHalt):
        worker.poll()
    assert len(requests) == 3


def test_unavailable_detail_rotates_without_losing_pending_or_starving_later_ids(workspace):
    _, clock = workspace
    rows = [listing()]
    worker, _ = observer(workspace, lambda _: httpx.Response(200, json=page(rows)))
    worker.poll()
    rows.extend([listing(101), listing(102)])
    clock.sleep(600)
    worker, requests = observer(
        workspace,
        lambda _: httpx.Response(200, json=page(rows)),
        detail=lambda request: (
            httpx.Response(404)
            if request.url.path.endswith("/101")
            else httpx.Response(200, json=listing(102))
        ),
    )
    failed = worker.poll()
    assert failed["status"] == "unavailable" and not failed["candidates"]
    assert [v["listing_id"] for v in worker.results()["pending"]] == [102, 101]
    clock.sleep(600)
    result = worker.poll()
    assert [v["listing_id"] for v in result["candidates"]] == [102]
    assert result["pending_detail_count"] == 1
    assert result["pending_candidates"][0]["listing_id"] == 101
    assert any(request.url.path.endswith("/102") for request in requests)


def test_explicit_snapshot_refreshes_same_event_without_new_notification_identity(workspace):
    _, clock = workspace
    worker, _ = observer(workspace, lambda _: httpx.Response(200, json=page()))
    first = worker.poll(snapshot=True)["candidates"][0]
    clock.sleep(21601)
    worker.results()
    refreshed = worker.poll(snapshot=True)["candidates"][0]
    assert refreshed["event_key"] == first["event_key"]
    assert refreshed["checked_at"] > first["checked_at"]
    assert len(worker.results()["results"]) == 1


def test_minimum_120_second_due_time_persists_across_observer_restart(tmp_path):
    path = tmp_path / "minimum.db"
    RulesStore(path, create=True).close()
    clock = Clock()
    aggregate = AggregatePriceAlerts(path, clock=clock)
    aggregate.set_enabled(True, expected_revision=0)
    cfg = config(poll_seconds=120)
    initialize_inventory(path, cfg)
    workspace = path, clock
    worker, _ = observer(workspace, lambda _: httpx.Response(200, json=page()), cfg=cfg)
    started = clock.now
    assert worker.poll()["status"] == "baseline"
    clock.now = started + 119
    restarted, requests = observer(workspace, lambda _: httpx.Response(200, json=page()), cfg=cfg)
    assert restarted.poll()["status"] == "not_due"
    assert not requests
    clock.now = started + 120
    assert restarted.poll()["status"] == "observed"
    assert len(requests) == 1


@pytest.mark.parametrize("seconds", [119, 86401, True, 120.0])
def test_poll_cadence_rejects_below_minimum_outside_bounds_or_wrong_types(seconds):
    with pytest.raises(ValidationError):
        config(poll_seconds=seconds)


@pytest.mark.parametrize("empty_pages", [0, 1])
def test_both_empty_pagination_forms_establish_baseline_and_allow_first_discovery(
    workspace, empty_pages
):
    _, clock = workspace
    rows = []

    def inventory(_):
        payload = page(rows)
        if not rows:
            payload["pagination"]["pages"] = empty_pages
        return httpx.Response(200, json=payload)

    raw = page([])
    raw["pagination"]["pages"] = empty_pages
    collected = collect(config(), listing(), lambda _: raw)
    assert collected["pagination"]["complete"]
    assert collected["pagination"]["reason"] == "reported_pages_observed"
    assert collected["pagination"]["pages_fetched"] == 1
    assert collected["pagination"]["reported_items"] == 0
    assert collected["observed_ids"] == []
    assert collected["candidates"] == []

    worker, _ = observer(workspace, inventory)
    baseline = worker.poll()
    assert baseline["status"] == "baseline"
    assert baseline["pagination"]["complete"]
    assert baseline["baseline_matches"] == 0
    assert baseline["candidates"] == []
    rows.append(listing(101))
    clock.sleep(600)
    discovered = worker.poll()
    assert [row["listing_id"] for row in discovered["candidates"]] == [101]
    assert discovered["candidates"][0]["state"] == "review_ready"


def pending_fixture(workspace, *, ids=(101,), snapshot=True):
    """Discover normally, then leave durable detail work after a safe provider hold."""
    _, clock = workspace
    rows = [listing()] if not snapshot else [listing(ident) for ident in ids]
    worker, _ = observer(
        workspace,
        lambda _: httpx.Response(200, json=page(rows)),
        detail=lambda _: httpx.Response(429, headers={"Retry-After": "60"}),
    )
    if not snapshot:
        assert worker.poll()["status"] == "baseline"
        rows.extend(listing(ident) for ident in ids)
        clock.sleep(600)
    assert worker.poll(snapshot=snapshot)["status"] == "cooldown"
    assert len(worker.results()["pending"]) == len(ids)
    return worker


def only_detail_observer(workspace, response):
    path, clock = workspace
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.path.startswith("/marketplace/listings/")
        assert dict(request.url.params) == {"curr_abbr": "USD"}
        assert request.method == "GET" and request.url.host == "api.discogs.com"
        with closing(sqlite3.connect(path)) as db:
            assert guard_row(db)[0] is not None
        return response(request)

    return InventoryObserver(
        path, config(), transport=httpx.MockTransport(handle), clock=clock, sleep=clock.sleep
    ), calls


def inventory_state(path):
    with closing(sqlite3.connect(path)) as db:
        return json.loads(
            db.execute("SELECT data FROM discogs_seeded_inventory_state").fetchone()[0]
        )


def test_empty_drain_never_verifies_seed_or_reads_inventory_and_preserves_state(workspace):
    path, _ = workspace
    worker, calls = only_detail_observer(
        workspace, lambda _: pytest.fail("No HTTP for empty drain")
    )
    before, aggregate = inventory_state(path), tables(path)
    result = worker.poll(drain_pending=True)
    assert result["status"] == "no_pending_details" and result["requests"] == 0
    assert calls == []
    assert inventory_state(path) == before and tables(path) == aggregate


@pytest.mark.parametrize("snapshot", [True, False])
def test_drain_only_reads_retained_detail_preserves_evidence_origin_and_dedup(workspace, snapshot):
    path, clock = workspace
    pending_fixture(workspace, snapshot=snapshot)
    before, aggregate = inventory_state(path), tables(path)
    clock.sleep(600)
    worker, calls = only_detail_observer(
        workspace,
        lambda request: httpx.Response(200, json=listing(int(request.url.path.rsplit("/", 1)[1]))),
    )
    result = worker.poll(drain_pending=True)
    assert result["status"] == "pending_details"
    assert result["requests"] == len(calls) == 1
    assert result["source"] == "official_public_listing_get"
    assert result["inventory_refreshed"] is False and "pagination" not in result
    assert result["pending_detail_count"] == 0
    event = result["candidates"][0]
    assert event["listing_id"] == 101 and event["state"] == "review_ready"
    assert event["origin"] == ("snapshot" if snapshot else "newly_observed")
    after = inventory_state(path)
    for name in ("baseline", "seen", "seed_verified_at"):
        assert after[name] == before[name]
    assert tables(path) == aggregate
    assert worker.results()["results"][-1] == event
    clock.sleep(600)
    assert worker.poll(drain_pending=True)["status"] == "no_pending_details"
    assert len(calls) == 1


def test_not_due_drain_retains_pending_without_http(workspace):
    path, _ = workspace
    pending_fixture(workspace)
    before = inventory_state(path)
    worker, calls = only_detail_observer(workspace, lambda _: pytest.fail("No due work"))
    result = worker.poll(drain_pending=True)
    assert result["status"] == "not_due" and result["requests"] == 0
    assert calls == [] and inventory_state(path) == before


def test_drain_still_respects_shared_budget_without_resetting_next_poll(workspace):
    path, clock = workspace
    pending_fixture(workspace)
    clock.sleep(600)
    before = inventory_state(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE discogs_monitor_control SET next_request=?", (clock.now + 1200,))
    worker, calls = only_detail_observer(
        workspace, lambda _: pytest.fail("Shared hold blocks HTTP")
    )
    result = worker.poll(drain_pending=True)
    assert result["status"] == "cooldown" and result["requests"] == 0
    after = inventory_state(path)
    assert after["pending"] == before["pending"] and after["results"] == before["results"]
    assert after["next_poll"] >= clock.now + 1200
    assert not after["in_progress"] and not calls


@pytest.mark.parametrize("status", [429, 403])
def test_drain_denial_and_rate_hold_preserve_pending_without_replay(workspace, status):
    path, clock = workspace
    pending_fixture(workspace)
    clock.sleep(600)
    pending = inventory_state(path)["pending"]
    worker, calls = only_detail_observer(
        workspace, lambda _: httpx.Response(status, headers={"Retry-After": "1200"})
    )
    result = worker.poll(drain_pending=True)
    assert result["status"] == ("access_denied" if status == 403 else "cooldown")
    assert result["requests"] == len(calls) == 1
    assert inventory_state(path)["pending"] == pending
    assert not result["candidates"]
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is None
        assert guard_row(db)[2] >= clock.now + 1200
    if status == 403:
        clock.sleep(1500)
        with pytest.raises(ReadSafetyHalt):
            worker.poll(drain_pending=True)
    else:
        assert worker.poll(drain_pending=True)["status"] == "not_due"
    assert len(calls) == 1


def test_uncertain_drain_leaves_durable_pending_guard_and_manual_recovery_stop(workspace):
    path, clock = workspace
    pending_fixture(workspace)
    clock.sleep(600)
    pending = inventory_state(path)["pending"]

    def fail(_):
        raise httpx.ConnectError("Uncertain request must not be replayed")

    worker, calls = only_detail_observer(workspace, fail)
    with pytest.raises(ReadSafetyHalt, match="response_unknown"):
        worker.poll(drain_pending=True)
    after = inventory_state(path)
    assert after["pending"] == pending and after["in_progress"]
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is not None
    clock.sleep(100000)
    with pytest.raises(ReadSafetyHalt):
        worker.poll(drain_pending=True)
    assert len(calls) == 1


def test_drain_caps_detail_reads_and_resumes_remaining_without_inventory(workspace):
    _, clock = workspace
    pending_fixture(workspace, ids=(101, 102, 103))
    clock.sleep(600)
    worker, calls = only_detail_observer(
        workspace,
        lambda request: httpx.Response(200, json=listing(int(request.url.path.rsplit("/", 1)[1]))),
    )
    result = worker.poll(drain_pending=True)
    assert result["requests"] == len(calls) == 2
    assert [v["listing_id"] for v in result["candidates"]] == [101, 102]
    assert result["pending_detail_count"] == 1
    clock.sleep(600)
    restarted, resumed_calls = only_detail_observer(
        workspace, lambda _: httpx.Response(200, json=listing(103))
    )
    result = restarted.poll(drain_pending=True)
    assert result["requests"] == len(resumed_calls) == 1
    assert [v["listing_id"] for v in result["candidates"]] == [103]
    assert result["pending_detail_count"] == 0


def test_drain_requires_persisted_verified_seed_for_existing_pending_work(workspace):
    path, clock = workspace
    pending_fixture(workspace)
    clock.sleep(600)
    state = inventory_state(path)
    state["seed_verified_at"] = None
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE discogs_seeded_inventory_state SET data=?", (json.dumps(state),))
    worker, calls = only_detail_observer(workspace, lambda _: pytest.fail("No missing-seed HTTP"))
    with pytest.raises(ReadSafetyHalt, match="seed_evidence_missing"):
        worker.poll(drain_pending=True)
    assert not calls and inventory_state(path) == state


def test_drain_cli_is_explicit_and_incompatible_with_new_inventory_modes(
    workspace, tmp_path, capsys, monkeypatch
):
    path, _ = workspace
    cfg = tmp_path / "config.json"
    cfg.write_text(config().model_dump_json())
    monkeypatch.setattr(
        httpx.Client, "stream", lambda *_a, **_k: pytest.fail("No HTTP on empty drain")
    )
    assert main(["--config", str(cfg), "--database", str(path), "--drain-pending"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "no_pending_details"
    for args in (
        ["--drain-pending"],
        ["--drain-pending", "--poll"],
        ["--drain-pending", "--snapshot"],
    ):
        with pytest.raises(SystemExit):
            main(["--config", str(cfg), *args])


def test_stopped_control_blocks_drain_without_consuming_pending(workspace):
    path, clock = workspace
    pending_fixture(workspace)
    clock.sleep(600)
    before = inventory_state(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE discogs_price_control SET enabled=0,revision=revision+1")
    worker, calls = only_detail_observer(workspace, lambda _: pytest.fail("Stopped control"))
    with pytest.raises(ReadSafetyHalt, match="control_stopped"):
        worker.poll(drain_pending=True)
    assert inventory_state(path) == before and not calls


def test_drain_keeps_completed_result_if_second_detail_is_rate_limited(workspace):
    path, clock = workspace
    pending_fixture(workspace, ids=(101, 102, 103))
    clock.sleep(600)
    worker, calls = only_detail_observer(
        workspace,
        lambda request: (
            httpx.Response(200, json=listing(101))
            if request.url.path.endswith("/101")
            else httpx.Response(429, headers={"Retry-After": "1200"})
        ),
    )
    result = worker.poll(drain_pending=True)
    assert result["status"] == "cooldown" and result["requests"] == len(calls) == 2
    assert [row["listing_id"] for row in result["candidates"]] == [101]
    assert [row["listing_id"] for row in inventory_state(path)["pending"]] == [102, 103]
    assert [row["listing_id"] for row in worker.results()["results"]] == [101]
