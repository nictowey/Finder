"""Offline aggregate episodes and ntfy delivery; every HTTP call is intercepted."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest

from finder.discogs_listing import ListingReadError, ReleaseStatsReader
from finder.discogs_notification_outbox import LEASE_SECONDS, NotificationBinding
from finder.discogs_ntfy import DeliveryResult, NtfySender, deliver_one
from finder.discogs_price_alerts import (
    DAILY_SEND_CAP,
    FRESH_SECONDS,
    POLL_SECONDS,
    AggregatePriceAlerts,
    PriceLeadRule,
)
from finder.discogs_rules import RulesStore


@pytest.fixture
def setup(tmp_path):
    database = tmp_path / "rules.db"
    RulesStore(database, create=True).close()
    now = [1800000000.0]
    alerts = AggregatePriceAlerts(database, clock=lambda: now[0])
    rule = PriceLeadRule(
        release_id=28720177,
        public_title="56 Nights",
        currency="USD",
        item_price_ceiling="10",
        enabled=True,
    )
    assert alerts.save_rule(rule.model_dump(), expected_revision=0) == 1
    return database, now, alerts, rule


def enable(alerts):
    alerts.set_enabled(True, expected_revision=alerts.status()["revision"])


def observe(setup, price="8.00", *, count=17, currency="USD", revision=1):
    _, now, alerts, rule = setup
    now[0] += 1
    return alerts.observe(
        rule.release_id,
        revision,
        {
            "release_id": rule.release_id,
            "num_for_sale": count,
            "lowest_item_price": price,
            "currency": currency,
        },
        observed_at=now[0],
    )


def rows(database):
    store = RulesStore(database)
    try:
        return store.db.execute(
            "SELECT state,episode,price,attempts FROM discogs_price_outbox ORDER BY episode"
        ).fetchall()
    finally:
        store.close()


def sender(handler):
    # Synthetic destination only; MockTransport prevents all real provider access.
    return NtfySender(
        "offline_fixture_destination_not_live", enabled=True, transport=httpx.MockTransport(handler)
    )


def test_defaults_never_poll_or_send_and_do_not_change_buying_rules(setup):
    database, _, alerts, _ = setup
    before = RulesStore(database)
    initial = before.snapshot()
    before.close()
    assert alerts.poll_once() == {"status": "disabled", "requests": 0}
    assert alerts.poll_once(object()) == {"status": "stopped", "requests": 0}
    assert observe(setup) == "discarded"
    assert deliver_one(alerts) == {"state": "disabled", "delivered": False}
    assert deliver_one(alerts, NtfySender()) == {"state": "disabled", "delivered": False}
    after = RulesStore(database)
    assert after.snapshot() == initial
    after.close()
    assert rows(database) == []


def test_unchanged_count_price_crossing_repeat_exit_reentry_and_restart(setup):
    database, now, alerts, _ = setup
    enable(alerts)
    assert observe(setup, "15.00") == "outside"
    assert observe(setup) == "low"
    assert observe(setup, "7.00") == "low"
    assert rows(database) == [("pending", 1, "7.00", 0)]
    reopened = AggregatePriceAlerts(database, clock=lambda: now[0])
    setup = database, now, reopened, setup[3]
    assert observe(setup, "7.00") == "low"
    assert len(rows(database)) == 1
    assert observe(setup, "12.00") == "outside"
    assert rows(database)[0][0] == "revoked"
    assert observe(setup) == "low"
    assert rows(database) == [("revoked", 1, "7.00", 0), ("pending", 2, "8.00", 0)]


@pytest.mark.parametrize(
    "currency,price,count",
    [
        (None, "8", 17),
        ("EUR", "8", 17),
        ("USD", None, 17),
        ("USD", "NaN", 17),
        ("USD", "-1", 17),
        ("USD", True, 17),
        ("USD", "8", True),
        ("USD", "8", 0),
    ],
)
def test_unknown_never_qualifies_or_rearms(setup, currency, price, count):
    database, _, alerts, _ = setup
    enable(alerts)
    assert observe(setup) == "low"
    assert observe(setup, price, count=count, currency=currency) == "unknown"
    assert observe(setup) == "low"
    assert len(rows(database)) == 1


def test_zero_stock_is_verified_exit_unlike_error_or_missing_price(setup):
    database, now, alerts, rule = setup
    enable(alerts)
    observe(setup)

    class ErrorReader:
        def read(self, *_):
            raise ListingReadError("synthetic_failure")

    now[0] += POLL_SECONDS
    assert alerts.poll_once(ErrorReader())["status"] == "unavailable"
    assert observe(setup) == "low"
    assert len(rows(database)) == 1
    assert observe(setup, None, count=0) == "outside"
    observe(setup)
    assert rows(database)[-1][1] == 2
    assert rule.item_price_ceiling == "10.00"


def test_initial_low_policy_and_rule_revisions(setup):
    database, _, alerts, rule = setup
    rule = rule.model_copy(update={"alert_initial_low": False})
    assert alerts.save_rule(rule.model_dump(), expected_revision=1) == 2
    enable(alerts)
    assert observe(setup, revision=1) == "discarded"
    assert observe(setup, revision=2) == "low"
    assert rows(database) == []
    observe(setup, "11", revision=2)
    observe(setup, revision=2)
    assert len(rows(database)) == 1
    with pytest.raises(RuntimeError):
        alerts.save_rule(rule.model_dump(), expected_revision=1)
    rule = rule.model_copy(update={"enabled": False})
    alerts.save_rule(rule.model_dump(), expected_revision=2)
    assert rows(database)[0][0] == "revoked"


def test_stale_future_out_of_order_and_wrong_release_are_not_price_signals(setup):
    _, now, alerts, rule = setup
    enable(alerts)
    value = {
        "release_id": rule.release_id,
        "num_for_sale": 17,
        "lowest_item_price": "8",
        "currency": "USD",
    }
    assert alerts.observe(rule.release_id, 1, value, observed_at=now[0] + 1) == "stale"
    assert alerts.observe(rule.release_id, 1, value, observed_at=now[0] - FRESH_SECONDS) == "stale"
    assert alerts.observe(rule.release_id, 1, value, observed_at=now[0]) == "low"
    assert alerts.observe(rule.release_id, 1, value, observed_at=now[0]) == "stale"
    now[0] += 1
    assert (
        alerts.observe(rule.release_id, 1, {**value, "release_id": 123}, observed_at=now[0])
        == "unknown"
    )


def test_real_api_adapter_polls_only_stats_and_backoff_is_durable(setup):
    database, now, alerts, _ = setup
    enable(alerts)
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/marketplace/stats/28720177"
        assert dict(request.url.params) == {"curr_abbr": "USD"}
        return httpx.Response(429, headers={"Retry-After": "1800"})

    reader = ReleaseStatsReader(transport=httpx.MockTransport(handle), clock=lambda: now[0])
    assert alerts.poll_once(reader) == {"status": "unavailable", "requests": 1}
    reopened = AggregatePriceAlerts(database, clock=lambda: now[0])
    assert reopened.poll_once(reader)["requests"] == 0
    now[0] += 1799
    assert reopened.poll_once(reader)["requests"] == 0
    assert len(requests) == 1
    now[0] += 2
    reader = ReleaseStatsReader(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"num_for_sale": 17, "lowest_price": {"value": 8, "currency": "USD"}}
            )
        ),
        clock=lambda: now[0],
    )
    assert reopened.poll_once(reader)["status"] == "low"
    assert rows(database) == [("pending", 1, "8.00", 0)]


def test_poll_discards_inflight_rule_or_control_change(setup):
    database, _, alerts, rule = setup
    enable(alerts)

    class ChangedReader:
        def read(self, *_):
            alerts.save_rule(rule.model_dump(), expected_revision=1)
            return {
                "release_id": rule.release_id,
                "num_for_sale": 17,
                "lowest_item_price": "8",
                "currency": "USD",
            }

    assert alerts.poll_once(ChangedReader())["status"] == "discarded"
    assert rows(database) == []


def test_public_ntfy_json_payload_no_topic_url_or_logs_and_no_redirects(setup, caplog):
    caplog.set_level(logging.DEBUG)
    database, _, alerts, _ = setup
    enable(alerts)
    observe(setup)
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://ntfy.sh/"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert set(body) == {"topic", "title", "message", "click"}
        assert "USD 8.00" in body["message"]
        assert "Condition and delivered total are unverified" in body["message"]
        assert body["click"] == "https://www.discogs.com/sell/release/28720177"
        return httpx.Response(200, json={"event": "message"})

    destination = sender(handle)
    assert deliver_one(alerts, destination) == {"state": "accepted", "delivered": False}
    assert deliver_one(alerts, destination)["state"] == "idle"
    assert len(calls) == 1
    assert destination._topic not in caplog.text
    assert destination._topic not in repr(destination)
    store = RulesStore(database)
    assert destination._topic not in "\n".join(store.db.iterdump())
    store.close()
    assert rows(database)[0][0] == "accepted"


@pytest.mark.parametrize(
    "status,expected",
    [
        (301, "failed"),
        (400, "failed"),
        (401, "failed"),
        (403, "failed"),
        (429, "retry_wait"),
        (500, "uncertain"),
        (503, "uncertain"),
    ],
)
def test_delivery_http_outcomes_are_conservative(setup, status, expected):
    _, _, alerts, _ = setup
    enable(alerts)
    observe(setup)
    destination = sender(
        lambda r: httpx.Response(
            status, headers={"Location": "https://unrelated.invalid/", "Retry-After": "1800"}
        )
    )
    result = deliver_one(alerts, destination)
    assert result["state"] == expected
    assert result["delivered"] is False


def test_known_failure_retries_after_backoff_but_timeout_and_restart_do_not(setup):
    database, now, alerts, _ = setup
    enable(alerts)
    observe(setup)
    calls = []

    def handle(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "900"})
            if len(calls) == 1
            else httpx.Response(200)
        )

    destination = sender(handle)
    assert deliver_one(alerts, destination)["state"] == "retry_wait"
    assert deliver_one(alerts, destination)["state"] == "idle"
    now[0] += 900
    observe(setup)  # New successful data; no new episode.
    reopened = AggregatePriceAlerts(database, clock=lambda: now[0])
    assert deliver_one(reopened, destination)["state"] == "accepted"
    assert rows(database) == [("accepted", 1, "8.00", 2)]


@pytest.mark.parametrize(
    "error,expected",
    [
        (httpx.ReadTimeout, "uncertain"),
        (httpx.WriteError, "uncertain"),
        (httpx.ConnectError, "retry_wait"),
    ],
)
def test_transport_errors_are_sanitized_and_never_log_topic(setup, caplog, error, expected):
    _, _, alerts, _ = setup
    enable(alerts)
    observe(setup)

    def handle(request):
        raise error("synthetic secret must not be logged", request=request)

    assert deliver_one(alerts, sender(handle))["state"] == expected
    assert "synthetic secret" not in caplog.text


def test_crash_after_claim_expires_to_uncertain_and_cannot_reroute(setup):
    database, now, alerts, _ = setup
    enable(alerts)
    observe(setup)
    destination = sender(lambda r: httpx.Response(200))
    claimed = alerts.claim(destination.binding)
    assert claimed
    now[0] += LEASE_SECONDS + 1
    reopened = AggregatePriceAlerts(database, clock=lambda: now[0])
    assert reopened.status()["states"] == {"uncertain": 1}
    assert reopened.claim(destination.binding) is None
    assert reopened.claim(NotificationBinding("ntfy", "different_opaque_destination")) is None
    assert reopened.finish(claimed, "accepted")["state"] == "uncertain"


def test_claim_competition_and_price_change_before_dispatch(setup):
    _, _, alerts, _ = setup
    enable(alerts)
    observe(setup)
    destination = sender(lambda r: httpx.Response(200))
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: alerts.claim(destination.binding), range(2)))
    assert sum(c is not None for c in claims) == 1
    reservation = next(c for c in claims if c)
    observe(setup, "9.00")
    assert not alerts.delivery_current(reservation)
    assert alerts.finish(reservation, "failed")["state"] == "failed"


def test_pause_staleness_ratecap_and_rule_change_stop_delivery(setup):
    database, now, alerts, rule = setup
    enable(alerts)
    observe(setup)
    destination = sender(lambda r: httpx.Response(200))
    store = RulesStore(database)
    with store.db:
        store.db.execute(
            "UPDATE discogs_price_control SET send_day=?,sends=?",
            (int(now[0] // 86400), DAILY_SEND_CAP),
        )
    store.close()
    assert alerts.claim(destination.binding) is None
    now[0] += FRESH_SECONDS
    assert alerts.status()["states"] == {"expired": 1}
    alerts.set_enabled(False, expected_revision=1)
    assert alerts.claim(destination.binding) is None
    assert rule.enabled is True  # Alert control does not edit any rule.


def test_sender_disabled_validation_and_fixed_result():
    assert NtfySender().send({}) == DeliveryResult("disabled")
    for topic in ["short", "https://ntfy.sh/x", "../escape", "x" * 65, "x\n" * 25]:
        with pytest.raises(ValueError, match="private high-entropy"):
            NtfySender(topic, enabled=True)
    with pytest.raises(ValueError):
        sender(lambda r: httpx.Response(200)).send({"topic": "untrusted"})
    assert replace(DeliveryResult("accepted"), outcome="uncertain").outcome == "uncertain"


def test_actual_worker_tick_intercepts_api_through_price_outbox_to_ntfy(setup):
    from finder.discogs_price_worker import tick

    database, now, alerts, _ = setup
    enable(alerts)
    calls = []

    def handle(request):
        calls.append((request.method, request.url.host, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200, json={"num_for_sale": 17, "lowest_price": {"value": 8, "currency": "USD"}}
            )
        return httpx.Response(200)

    reader = ReleaseStatsReader(transport=httpx.MockTransport(handle), clock=lambda: now[0])
    result = tick(alerts, reader=reader, sender=sender(handle))
    assert result["poll"] == {"status": "low", "requests": 1}
    assert result["delivery"] == {"state": "accepted", "delivered": False}
    assert calls == [
        ("GET", "api.discogs.com", "/marketplace/stats/28720177"),
        ("POST", "ntfy.sh", "/"),
    ]
    reopened = AggregatePriceAlerts(database, clock=lambda: now[0])
    assert tick(reopened, reader=reader, sender=sender(handle))["delivery"]["state"] == "idle"
    assert len(calls) == 2


def test_private_topic_file_and_worker_default_are_inert(setup, tmp_path, capsys):
    from finder.discogs_price_worker import main, private_topic

    database, _, _, _ = setup
    assert main(["--database", str(database)]) == 0
    text = capsys.readouterr().out
    assert "disabled" in text
    topic = tmp_path / "private-topic"
    topic.write_text("offline_fixture_destination_not_live\n")
    topic.chmod(0o600)
    assert private_topic(topic) == "offline_fixture_destination_not_live"
    topic.chmod(0o644)
    with pytest.raises(ValueError, match="Private notification configuration unavailable"):
        private_topic(topic)
    topic.chmod(0o600)
    linked = tmp_path / "linked"
    linked.symlink_to(topic)
    with pytest.raises(ValueError):
        private_topic(linked)
    assert main(["--database", str(database), "--deliver", "--topic-file", str(linked)]) == 1
    assert "offline_fixture" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "private changed title"),
        ("message", "arbitrary private data"),
        ("click", "https://unrelated.invalid/"),
    ],
)
def test_sender_cannot_publish_noncanonical_payload(setup, field, value):
    from finder.discogs_price_alerts import lead_message

    calls = []
    destination = sender(lambda request: calls.append(request))
    payload = lead_message("56 Nights", 28720177, "USD", "8.00")
    payload[field] = value
    with pytest.raises(ValueError):
        destination.send(payload)
    assert not calls


def test_delivery_revocation_before_send_and_retry_exhaustion(setup):
    database, now, alerts, rule = setup
    enable(alerts)
    observe(setup)
    destination = sender(lambda r: httpx.Response(429))
    reservation = alerts.claim(destination.binding)
    alerts.set_enabled(False, expected_revision=1)
    assert not alerts.delivery_current(reservation)
    assert alerts.finish(reservation, "accepted")["state"] == "uncertain"
    enable(alerts)
    observe(setup, "12")
    observe(setup)
    for attempt in range(1, 6):
        # Continue real polling during backoff; an otherwise stale event expires.
        remaining = max(900, 900 * 2 ** max(0, attempt - 2))
        while remaining > 0:
            step = min(600, remaining)
            now[0] += step
            observe(setup)
            remaining -= step
        result = deliver_one(alerts, destination)
        assert result["state"] == ("failed" if attempt == 5 else "retry_wait")
    assert rows(database)[-1][3] == 5
    assert rule.item_price_ceiling == "10.00"


def test_old_binding_never_moves_pending_retry_to_another_topic(setup):
    _, now, alerts, _ = setup
    enable(alerts)
    observe(setup)
    destination = sender(lambda r: httpx.Response(429))
    assert deliver_one(alerts, destination)["state"] == "retry_wait"
    now[0] += 901
    observe(setup)
    changed = NtfySender(
        "second_offline_fixture_not_a_real_topic",
        enabled=True,
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    assert deliver_one(alerts, changed)["state"] == "idle"
    assert deliver_one(alerts, destination)["state"] == "retry_wait"


def test_changed_claim_payload_cannot_pass_final_delivery_check(setup):
    from finder.discogs_price_alerts import lead_message

    _, _, alerts, _ = setup
    enable(alerts)
    observe(setup)
    reservation = alerts.claim(sender(lambda r: httpx.Response(200)).binding)
    assert alerts.delivery_current(reservation)
    reservation["payload"] = lead_message("Different public record", 123, "USD", "8.00")
    assert not alerts.delivery_current(reservation)


def test_shutdown_during_poll_does_not_start_delivery(setup):
    import threading

    from finder.discogs_price_worker import tick

    _, _, alerts, _ = setup
    enable(alerts)
    stop = threading.Event()
    calls = []

    class StoppingReader:
        def read(self, release, currency):
            stop.set()
            return {
                "release_id": release,
                "currency": currency,
                "num_for_sale": 17,
                "lowest_item_price": "8.00",
            }

    result = tick(
        alerts, reader=StoppingReader(), sender=sender(lambda r: calls.append(r)), stop=stop
    )
    assert result["poll"]["status"] == "low"
    assert result["delivery"]["state"] == "disabled"
    assert calls == []


@pytest.mark.parametrize("status", [401, 403])
def test_api_access_restriction_durably_stops_without_retry_or_fallback(setup, status):
    database, now, alerts, _ = setup
    enable(alerts)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status)

    reader = ReleaseStatsReader(transport=httpx.MockTransport(handle), clock=lambda: now[0])
    assert alerts.poll_once(reader)["status"] == "unavailable"
    assert alerts.status()["enabled"] is False
    now[0] += 86401
    reopened = AggregatePriceAlerts(database, clock=lambda: now[0])
    assert reopened.poll_once(reader) == {"status": "stopped", "requests": 0}
    assert len(requests) == 1


def test_fresh_low_cannot_resurrect_expired_same_episode_without_prior_status_read(setup):
    database, now, alerts, _ = setup
    enable(alerts)
    observe(setup)
    now[0] += FRESH_SECONDS + 1
    observe(setup)
    assert rows(database) == [("expired", 1, "8.00", 0)]
    assert alerts.claim(sender(lambda r: httpx.Response(200)).binding) is None
    observe(setup, "12")
    observe(setup)
    assert rows(database)[-1] == ("pending", 2, "8.00", 0)
