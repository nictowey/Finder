"""Offline monitoring regressions. All provider responses and owner sessions are synthetic."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from test_cloud_app import ORIGIN, call, verifier
from test_discogs_listing import payload
from test_discogs_rules import target as base_target

from finder.cloud_app import CloudApp
from finder.discogs_listing import ListingReader, ListingReadError, ReleaseStatsReader, normalize
from finder.discogs_monitor import CACHE_TTL, LISTING_INTERVAL, RELEASE_INTERVAL, ReleaseMonitor
from finder.discogs_rules import RulesStore, Target


def target(**changes):
    return Target.model_validate({**base_target().model_dump(), **changes})


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "rules.db"
    RulesStore(path, create=True).close()
    requests = []
    returned = {
        "stats": {"num_for_sale": 1, "lowest_price": {"value": 20, "currency": "USD"}},
        "listing": payload(),
        "status": 200,
    }
    clock = [datetime(2026, 1, 1, tzinfo=UTC).timestamp()]

    def handle(request):
        requests.append(request)
        data = returned["stats"] if "/stats/" in request.url.path else returned["listing"]
        return httpx.Response(returned["status"], json=data, headers={"Retry-After": "1200"})

    transport = httpx.MockTransport(handle)
    reader = ListingReader(transport=transport, clock=lambda: clock[0])
    stats = ReleaseStatsReader(transport=transport, clock=lambda: clock[0])
    app = CloudApp(path, ORIGIN, verifier(), listing_reader=reader, stats_reader=stats)
    app.monitor.clock = lambda: clock[0]
    assert (
        call(app, "/api/target", {"target": target().model_dump(), "expected_revision": 0})[0]
        == 200
    )
    yield app, clock, returned, requests


def state(app):
    code, _, body = call(app, "/api/snapshot")
    assert code == 200
    return json.loads(body)


def release(app):
    revision = state(app)["control_revision"]
    assert call(app, "/api/kill", {"killed": False, "expected_revision": revision})[0] == 200


def track(app, listing="123", *, remove=False, **changes):
    current = state(app)
    return call(
        app,
        "/api/monitor/track",
        {
            "release_id": 123,
            "listing": listing,
            "remove": remove,
            "expected_revision": current["revision"],
            "monitor_revision": current["monitor"]["revision"],
            **changes,
        },
    )


def tick(app):
    code, _, body = call(app, "/api/monitor/run", {})
    assert code == 200
    return json.loads(body)


def test_default_stop_and_unconfigured_monitor_never_request(setup, tmp_path):
    app, _, _, requests = setup
    assert state(app)["monitor"]["configured"] is True
    assert state(app)["monitor"]["worker_running"] is False
    assert tick(app) == {"status": "stopped", "requests": 0}
    path = tmp_path / "unconfigured.db"
    RulesStore(path, create=True).close()
    other = CloudApp(path, ORIGIN, verifier())
    release(other)
    assert tick(other) == {"status": "not_configured", "requests": 0}
    assert requests == []


def test_release_id_only_gets_aggregate_not_discovery_or_qualifying_listing(setup):
    app, _, _, requests = setup
    release(app)
    assert tick(app) == {"status": "ok", "requests": 1}
    monitor = state(app)["monitor"]
    assert monitor["automatic_listing_discovery"] is False
    assert monitor["email_monitoring"] is False and monitor["checkout_available"] is False
    assert len(monitor["rows"]) == 1 and monitor["rows"][0]["kind"] == "release"
    assert monitor["rows"][0]["data"]["num_for_sale"] == 1
    assert "review" not in monitor["rows"][0]
    assert str(requests[0].url) == "https://api.discogs.com/marketplace/stats/123?curr_abbr=USD"
    assert requests[0].method == "GET" and "authorization" not in requests[0].headers


def test_known_listing_poll_separates_conditions_from_missing_delivered_quote(setup):
    app, clock, _, requests = setup
    release(app)
    assert track(app)[0] == 200
    assert tick(app)["status"] == "ok"  # Known listing comes first in the due queue.
    row = state(app)["monitor"]["rows"][0]
    assert row["kind"] == "listing" and row["fresh"] is True
    assert row["data"]["reported_shipping"] == "2.50"
    assert row["review"]["status"] == "needs_total"
    assert row["review"]["total"] is None and row["review"]["qualifies_all_in"] is False
    assert row["review"]["purchase_allowed"] is False
    assert tick(app) == {"status": "cooldown", "requests": 0}
    clock[0] += 3
    assert tick(app)["status"] == "ok"  # Release-level aggregate is separate.
    assert len(requests) == 2
    assert tick(app)["requests"] == 0


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"condition": "Good (G)"}, "Media condition"),
        ({"sleeve_condition": "Generic"}, "Sleeve condition"),
        ({"seller": {"stats": {"rating": "98", "total": 1000}}}, "Seller rating"),
        ({"seller": {"stats": {"rating": "100", "total": 1}}}, "feedback count"),
        ({"price": {"value": "50.01", "currency": "USD"}}, "Item price alone"),
        ({"price": {"value": "1", "currency": "EUR"}}, "Currency"),
        ({"release": {"id": 456}}, "Reported release ID"),
        ({"shipping_is_blocked": True}, "Shipping availability"),
        ({"status": "Sold"}, "not reported available"),
    ],
)
def test_known_listing_filters_fail_closed(setup, changes, reason):
    app, _, returned, _ = setup
    returned["listing"].update(changes)
    release(app)
    track(app)
    tick(app)
    review = state(app)["monitor"]["rows"][0]["review"]
    assert review["status"] == "blocked"
    assert any(reason in text for text in review["reasons"])
    assert review["total"] is None and review["purchase_allowed"] is False


def test_duplicate_suppression_rule_re_evaluation_and_restart(setup):
    app, clock, _, requests = setup
    release(app)
    track(app)
    tick(app)
    clock[0] += 3
    tick(app)
    before = state(app)
    assert len(before["monitor"]["events"]) == 2
    clock[0] += LISTING_INTERVAL
    tick(app)
    assert len(state(app)["monitor"]["events"]) == 2
    # Changing rules evaluates cached fields against the current rule, without a network call.
    code, _, _ = call(
        app, "/api/target", {"target": target(max_total="19").model_dump(), "expected_revision": 1}
    )
    assert code == 200
    row = state(app)["monitor"]["rows"][0]
    assert row["review"]["status"] == "blocked" and row["rules_revision"] == 2
    reopened = ReleaseMonitor(
        app.database,
        stats_reader=app.monitor.stats_reader,
        listing_reader=app.reader,
        clock=lambda: clock[0],
    )
    assert reopened.run_once()["requests"] == 0
    assert len(requests) == 3


def test_rate_limit_survives_new_reader_and_worker_restart(setup):
    app, clock, returned, requests = setup
    returned["status"] = 429
    release(app)
    assert tick(app)["status"] == "unavailable"
    clock[0] += 1199
    reopened = ReleaseMonitor(
        app.database, stats_reader=app.monitor.stats_reader, clock=lambda: clock[0]
    )
    assert reopened.run_once() == {"status": "cooldown", "requests": 0}
    assert len(requests) == 1
    returned["status"] = 200
    clock[0] += 1
    assert reopened.run_once() == {"status": "ok", "requests": 1}


def test_manual_reads_share_persistent_worker_budget(setup):
    app, clock, returned, requests = setup
    release(app)
    returned["status"] = 429
    assert call(app, "/api/listing", {"release_id": 123, "listing": "123"})[0] == 502
    assert tick(app) == {"status": "cooldown", "requests": 0}
    reopened = CloudApp(
        app.database,
        ORIGIN,
        verifier(),
        listing_reader=app.reader,
        stats_reader=app.monitor.stats_reader,
    )
    reopened.monitor.clock = lambda: clock[0]
    assert call(reopened, "/api/listing", {"release_id": 123, "listing": "123"})[0] == 502
    assert len(requests) == 1
    returned["status"] = 200
    clock[0] += 1200
    assert call(reopened, "/api/listing", {"release_id": 123, "listing": "123"})[0] == 200
    assert tick(reopened) == {"status": "cooldown", "requests": 0}


def test_one_failed_listing_does_not_stall_other_due_release_checks(setup):
    app, clock, returned, requests = setup
    release(app)
    track(app)
    returned["status"] = 404
    assert tick(app)["status"] == "unavailable"
    clock[0] += 3
    returned["status"] = 200
    assert tick(app)["status"] == "ok"
    assert len(requests) == 2 and requests[1].url.path == "/marketplace/stats/123"


def test_rate_limit_survives_inflight_stop_and_subsequent_release(setup):
    app, clock, _, requests = setup
    release(app)
    track(app)

    def stopped(request):
        requests.append(request)
        assert call(app, "/api/kill", {"killed": True, "expected_revision": 1})[0] == 200
        return httpx.Response(429, headers={"Retry-After": "1200"})

    app.reader.transport = httpx.MockTransport(stopped)
    assert tick(app)["status"] == "discarded"
    release(app)
    clock[0] += 1199
    assert tick(app) == {"status": "cooldown", "requests": 0}
    assert len(requests) == 1


@pytest.mark.parametrize("action", ["kill", "edit", "owned", "remove"])
def test_inflight_results_discarded_after_control_or_rule_change(setup, action):
    app, _, _, requests = setup
    release(app)
    track(app)

    def changed(request):
        requests.append(request)
        if action == "kill":
            assert call(app, "/api/kill", {"killed": True, "expected_revision": 1})[0] == 200
        elif action == "edit":
            assert (
                call(
                    app,
                    "/api/target",
                    {"target": target(max_total="10").model_dump(), "expected_revision": 1},
                )[0]
                == 200
            )
        elif action == "owned":
            assert (
                call(app, "/api/owned", {"release_id": 123, "owned": True, "expected_revision": 1})[
                    0
                ]
                == 200
            )
        else:
            assert track(app, remove=True)[0] == 200
        return httpx.Response(200, json=payload())

    app.reader.transport = httpx.MockTransport(changed)
    assert tick(app) == {"status": "discarded", "requests": 1}
    assert state(app)["monitor"]["events"] == []
    assert all(row["data"] is None for row in state(app)["monitor"]["rows"])


def test_failed_refresh_not_empty_inventory_and_old_payloads_expire(setup):
    app, clock, returned, _ = setup
    release(app)
    track(app)
    tick(app)
    clock[0] += 3
    tick(app)
    clock[0] += LISTING_INTERVAL
    returned["status"] = 500
    tick(app)
    row = state(app)["monitor"]["rows"][0]
    assert row["status"] == "unavailable" and row["data"] is not None
    assert row["fresh"] is False and row["review"]["status"] == "blocked"
    clock[0] += CACHE_TTL + 1
    after = state(app)["monitor"]
    assert all(row["data"] is None for row in after["rows"])
    assert after["events"] == []
    store = RulesStore(app.database)
    assert (
        store.db.execute("SELECT count(*) FROM discogs_poll WHERE payload IS NOT NULL").fetchone()[
            0
        ]
        == 0
    )
    store.close()


def test_tracking_revisions_validation_remove_and_no_synthetic_source_endpoint(setup):
    app, _, _, requests = setup
    assert track(app)[0] == 200
    assert track(app)[0] == 400
    assert track(app, listing="456", monitor_revision=0)[0] == 409
    assert track(app, listing="https://evil.test/123")[0] == 400
    assert track(app, release_id=456)[0] == 400
    assert call(app, "/api/monitor/ingest", {"source": "synthetic"})[0] == 404
    assert track(app, remove=True)[0] == 200
    assert state(app)["monitor"]["rows"] == [] and requests == []
    assert call(app, "/api/checkout", {})[0] == 404


def test_paused_and_owned_targets_do_not_poll(setup):
    app, _, _, requests = setup
    release(app)
    assert (
        call(
            app,
            "/api/target",
            {"target": target(enabled=False).model_dump(), "expected_revision": 1},
        )[0]
        == 200
    )
    assert tick(app) == {"status": "idle", "requests": 0}
    assert (
        call(app, "/api/target", {"target": target().model_dump(), "expected_revision": 2})[0]
        == 200
    )
    assert (
        call(app, "/api/owned", {"release_id": 123, "owned": True, "expected_revision": 3})[0]
        == 200
    )
    assert tick(app) == {"status": "idle", "requests": 0}
    assert requests == []


def test_two_workers_cannot_claim_same_poll(setup):
    app, clock, _, requests = setup
    release(app)
    second = ReleaseMonitor(
        app.database, stats_reader=app.monitor.stats_reader, clock=lambda: clock[0]
    )

    def nested(request):
        requests.append(request)
        assert second.run_once() == {"status": "cooldown", "requests": 0}
        return httpx.Response(200, json={"num_for_sale": 0, "lowest_price": None})

    app.monitor.stats_reader.transport = httpx.MockTransport(nested)
    assert tick(app) == {"status": "ok", "requests": 1}
    assert len(requests) == 1


@pytest.mark.parametrize("data", [[], {}, {"num_for_sale": True}, {"num_for_sale": -1}])
def test_stats_invalid_count_not_reported_as_no_copies(data):
    reader = ReleaseStatsReader(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data))
    )
    with pytest.raises(ListingReadError):
        reader.read(123, "USD")


def test_stats_unknown_or_wrong_currency_price_never_converted():
    reader = ReleaseStatsReader(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"num_for_sale": 4, "lowest_price": {"value": 1, "currency": "EUR"}}
            )
        )
    )
    assert reader.read(123, "USD")["lowest_item_price"] is None


@pytest.mark.parametrize("ident", [True, "https://evil.test/123", "../orders", 0])
def test_stats_bad_release_never_requests(ident):
    def unexpected(request):
        pytest.fail("Invalid identity made a network request.")

    with pytest.raises(ValueError):
        ReleaseStatsReader(transport=httpx.MockTransport(unexpected)).read(ident, "USD")


def test_stale_and_owned_reviews_fail_closed_without_totals():
    from finder.discogs_monitor import listing_review

    listing = normalize(payload(), 123)
    assert listing_review(target(), listing, fresh=False)["status"] == "blocked"
    assert listing_review(target(), listing, fresh=True, owned=True)["status"] == "blocked"
    assert listing_review(target(max_total="20"), listing, fresh=True)["status"] == "needs_total"


def test_cloud_monitor_routes_require_owner_origin_csrf(setup):
    app, _, _, requests = setup
    assert call(app, "/api/monitor/run", {}, HTTP_COOKIE="")[0] == 401
    assert call(app, "/api/monitor/run", {}, HTTP_ORIGIN="https://evil.test")[0] == 403
    assert call(app, "/api/monitor/track", {}, HTTP_X_FINDER_CSRF="bad")[0] == 403
    assert requests == []
    assert RELEASE_INTERVAL >= 600 and LISTING_INTERVAL >= 300


def test_cloud_monitor_dom_uses_actual_authenticated_page_template(setup, tmp_path):
    import shutil
    import subprocess
    from pathlib import Path

    from test_cloud_app import COOKIE

    if not shutil.which("node") or not Path("node_modules/jsdom").exists():
        pytest.skip("Cloud DOM checks require installed Node/jsdom.")
    app, _, _, _ = setup
    code, _, body = call(app)
    assert code == 200
    page = tmp_path / "cloud.html"
    page.write_bytes(body)
    result = subprocess.run(
        ["node", "tests/test_discogs_monitor_ui.cjs", str(page), app.token(COOKIE)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_monitor_loop_stops_without_external_scheduler_or_browser():
    from finder.discogs_monitor import MonitorLoop

    calls = []

    class FixtureMonitor:
        def run_once(self):
            calls.append("tick")
            loop.stop.set()

    loop = MonitorLoop(FixtureMonitor())
    loop.start()
    loop.thread.join(timeout=2)
    assert calls == ["tick"] and not loop.thread.is_alive()
