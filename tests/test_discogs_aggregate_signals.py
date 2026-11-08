"""Disposable database and intercepted HTTP proof for opt-in aggregate changes."""

import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from pydantic import create_model

from finder.discogs_aggregate_signals import change_message
from finder.discogs_listing import ListingReadError, ReleaseStatsReader
from finder.discogs_notification_outbox import LEASE_SECONDS
from finder.discogs_ntfy import PRIVATE_NTFY_ORIGIN, NtfySender, PrivateNtfyConfig, deliver_one
from finder.discogs_price_alerts import (
    DAILY_SEND_CAP,
    FRESH_SECONDS,
    AggregatePriceAlerts,
    PriceLeadRule,
)
from finder.discogs_price_worker import tick
from finder.discogs_rules import RulesStore, StrictInput


class Watch:
    def __init__(self, tmp_path, **options):
        self.database = tmp_path / "aggregate.db"
        RulesStore(self.database, create=True).close()
        self.now = 1800000000.0
        self.alerts = AggregatePriceAlerts(self.database, clock=lambda: self.now, poll_seconds=300)
        self.rule = PriceLeadRule.model_validate(
            dict(
                release_id=123,
                public_title="Synthetic public record",
                currency="USD",
                item_price_ceiling="10.00",
                enabled=True,
                alert_initial_low=False,
                alert_price_drops=True,
                alert_count_increases=True,
            )
            | options
        )
        self.revision = self.alerts.save_rule(self.rule, expected_revision=0)
        self.alerts.set_enabled(True, expected_revision=0)
        self.requests = []
        self.response = 200
        self.sender = NtfySender(
            "offline_fixture_destination_not_live",
            enabled=True,
            transport=httpx.MockTransport(self.handle),
        )

    def handle(self, request):
        self.requests.append(request)
        return httpx.Response(
            self.response, headers={"Retry-After": "900"} if self.response == 429 else {}
        )

    def read(self, price="10.00", count=5, **options):
        self.now += 1
        return self.alerts.observe(
            self.rule.release_id,
            self.revision,
            dict(
                release_id=self.rule.release_id,
                currency="USD",
                lowest_item_price=price,
                num_for_sale=count,
            )
            | options,
            observed_at=self.now,
        )

    def send(self):
        return deliver_one(self.alerts, self.sender)["state"]

    def sql(self, query, params=()):
        store = RulesStore(self.database)
        try:
            with store.db:
                return store.db.execute(query, params).fetchall()
        finally:
            store.close()

    def events(self):
        return self.sql(
            "SELECT event_key,state,episode,price,attempts,next_attempt,observed_at "
            "FROM discogs_price_outbox ORDER BY episode"
        )

    def payload(self):
        return json.loads(self.requests[-1].content)

    def restart(self):
        self.alerts = AggregatePriceAlerts(self.database, clock=lambda: self.now, poll_seconds=300)

    def configure(self, price, count):
        self.revision = self.alerts.configure_change_alerts(
            self.rule.release_id,
            alert_price_drops=price,
            alert_count_increases=count,
            expected_revision=self.revision,
        )


@pytest.fixture
def watch(tmp_path):
    return Watch(tmp_path)


@pytest.mark.parametrize("field", ["alert_price_drops", "alert_count_increases"])
@pytest.mark.parametrize("value", ["true", 1, None, [], {}])
def test_flags_are_strict_and_default_off(field, value):
    legacy = dict(release_id=123, public_title="Record", currency="USD", item_price_ceiling="10")
    rule = PriceLeadRule.model_validate(legacy)
    assert not rule.alert_price_drops and not rule.alert_count_increases
    with pytest.raises(ValueError):
        PriceLeadRule.model_validate(legacy | {field: value})


def test_price_new_low_only_including_cent_oscillation_and_restart(watch):
    watch.read()
    assert not watch.events()
    watch.read("9.00")
    assert watch.send() == "accepted"
    assert "decreased from USD 10.00 to USD 9.00" in watch.payload()["message"]
    for price in ["9", "9.01", "10", "9.01", "9.00"]:
        watch.now += 300
        watch.read(price)
        watch.restart()
        assert watch.send() == "idle"
    watch.read("8.99")
    assert watch.send() == "accepted"
    assert "decreased from USD 9.00 to USD 8.99" in watch.payload()["message"]
    assert len(watch.events()) == 2


def test_count_high_water_not_every_rise_or_individual_listing(watch):
    watch.read(count=5)
    watch.read(count=6)
    assert watch.send() == "accepted"
    payload = watch.payload()
    assert payload["title"] == "Synthetic public record | USD 10.00 min; offers 5 -> 6"
    assert "aggregate offers increased from 5 to 6" in payload["message"]
    assert "do not identify individual new listings" in payload["message"]
    assert "not a qualified purchase" in payload["message"]
    assert "Data provided by Discogs" in payload["message"]
    for count in [3, 4, 5, 6, 5, 6]:
        watch.now += 300
        watch.read(count=count)
        assert watch.send() == "idle"
    watch.read(count=7)
    assert watch.send() == "accepted"
    assert "increased from 6 to 7" in watch.payload()["message"]


def test_ceiling_eligibility_and_verified_exit_reset_extrema(watch):
    for price, count in [("20", 100), ("19", 101), ("11", 110)]:
        assert watch.read(price, count) == "outside"
        assert watch.send() == "idle"
    watch.read("9", 5)
    assert watch.send() == "accepted"  # Existing threshold-entry behavior.
    watch.now += 300
    watch.read("8", 6)
    assert watch.send() == "accepted"
    watch.read(None, 0)
    watch.now += 300
    watch.read("9", 1)
    assert watch.send() == "accepted"
    assert watch.sql("SELECT price_floor,count_high FROM discogs_price_extrema") == [("9.00", 1)]
    assert len(watch.events()) == 3


def test_pending_entry_absorbs_changes_without_duplicate(tmp_path):
    watch = Watch(tmp_path, alert_initial_low=True)
    watch.read("10", 5)
    watch.read("9", 6)
    watch.read("8", 7)
    assert len(watch.events()) == 1
    assert watch.sql("SELECT * FROM discogs_price_changes") == []
    assert watch.send() == "accepted"
    assert watch.payload()["title"] == "Synthetic public record | USD 8.00 min"
    assert "USD 8.00" in watch.payload()["message"]
    watch.now += 300
    watch.read("7", 8)
    assert watch.send() == "accepted"
    assert "decreased from USD 8.00 to USD 7.00" in watch.payload()["message"]
    assert "increased from 7 to 8" in watch.payload()["message"]


def test_coalesced_signals_keep_retry_backoff_attempts_and_destination(watch):
    watch.read()
    watch.read("9", 6)
    watch.response = 429
    assert watch.send() == "retry_wait"
    before = watch.events()[0]
    watch.now += 300
    watch.read("8", 7)
    watch.restart()
    after = watch.events()[0]
    assert after[:3] == before[:3]
    assert after[4:6] == before[4:6]
    assert after[3] == "8.00"
    assert watch.send() == "idle"
    watch.now += 300
    watch.read("7", 8)
    watch.now += 300
    watch.read("7", 8)
    wrong_sender = NtfySender(
        "different_offline_destination_fixture",
        enabled=True,
        transport=httpx.MockTransport(watch.handle),
    )
    assert deliver_one(watch.alerts, wrong_sender)["state"] == "idle"
    watch.response = 200
    assert watch.send() == "accepted"
    assert "decreased from USD 10.00 to USD 7.00" in watch.payload()["message"]
    assert "increased from 5 to 8" in watch.payload()["message"]
    assert len(watch.events()) == 1
    assert len(watch.requests) == 2
    assert watch.events()[0][4] == 2


def test_rebound_revokes_pending_change_and_repeated_extreme_does_not_requeue(watch):
    watch.read()
    watch.read("9", 6)
    watch.read("10", 5)
    assert watch.events()[0][1] == "revoked"
    for _ in range(3):
        watch.read("9", 6)
        assert watch.send() == "idle"
        watch.read("10", 5)
    assert len(watch.events()) == 1
    watch.read("8.99", 7)
    assert watch.send() == "accepted"
    assert len(watch.events()) == 2


def test_partial_invalidation_removes_only_false_reason(watch):
    watch.read()
    watch.read("9", 6)
    watch.read("10", 7)
    assert watch.send() == "accepted"
    assert "minimum item price decreased" not in watch.payload()["message"]
    assert "reported minimum item price USD 10.00" in watch.payload()["message"]
    assert "increased from 5 to 7" in watch.payload()["message"]
    watch.now += 300
    watch.read("8", 8)
    watch.read("8.50", 7)
    assert watch.send() == "accepted"
    assert "decreased from USD 9.00 to USD 8.50" in watch.payload()["message"]
    assert "offers increased" not in watch.payload()["message"]


@pytest.mark.parametrize("count", [None, True, -1, 10000001, 5.0, "6"])
def test_unknown_count_blocks_then_recovery_silently_merges_extrema(watch, count):
    watch.read()
    watch.read("9", 6)
    assert watch.read("8", count) == "unknown"
    assert watch.send() == "idle"
    watch.read("7", 8)
    assert watch.send() == "idle"
    assert watch.events()[0][1] == "revoked"
    watch.read("9", 6)
    watch.read("7", 8)
    assert watch.send() == "idle"
    watch.read("6.99", 9)
    assert watch.send() == "accepted"
    assert "from USD 7.00 to USD 6.99" in watch.payload()["message"]
    assert "from 8 to 9" in watch.payload()["message"]


def test_error_and_stale_gaps_do_not_reset_or_replay_extrema(watch):
    class Failed:
        def read(self, *_):
            raise ListingReadError("synthetic_failure")

    watch.read()
    watch.read("9", 6)
    assert watch.send() == "accepted"
    watch.now += 300
    assert watch.alerts.poll_once(Failed())["status"] == "unavailable"
    watch.read("10", 5)
    watch.read("9", 6)
    assert watch.send() == "idle"  # Recovery did not raise the floor/lower the high-water.
    watch.now += FRESH_SECONDS
    watch.read("8", 7)
    assert watch.send() == "idle"
    watch.read("7.99", 8)
    assert watch.send() == "accepted"


def test_stale_future_and_duplicate_samples_cannot_generate_changes(watch):
    watch.read()
    value = dict(release_id=123, currency="USD", num_for_sale=50, lowest_item_price="1")
    for at in [watch.now, watch.now + 1, watch.now - FRESH_SECONDS]:
        assert watch.alerts.observe(123, 1, value, observed_at=at) == "stale"
    assert not watch.events()
    assert watch.sql("SELECT price_floor,count_high FROM discogs_price_extrema") == [("10.00", 5)]


def test_any_price_missing_price_preserves_count_but_price_recovery_is_silent(tmp_path):
    watch = Watch(tmp_path, any_price=True, item_price_ceiling=None)
    assert watch.read("100", 5) == "available"
    watch.read(None, 6)
    assert watch.send() == "accepted"
    assert "minimum item price" not in watch.payload()["message"]
    watch.now += 300
    watch.read("80", 6)
    assert watch.send() == "idle"
    watch.read("79.99", 6)
    assert watch.send() == "accepted"
    assert "from USD 80.00 to USD 79.99" in watch.payload()["message"]
    watch.read("invalid", 6)
    watch.read("100", 6)
    watch.now += 300
    watch.read("79.99", 6)
    assert watch.send() == "idle"
    assert watch.read(None, 0) == "unavailable"
    watch.read(None, 1)
    assert watch.send() == "accepted"
    assert watch.payload()["title"] == "Synthetic public record | available; any price"


def test_threshold_unknown_price_blocks_changes_and_never_assumes_zero(watch):
    watch.read()
    for price in [None, "NaN", "-1", True, "10.001"]:
        assert watch.read(price, 20) == "unknown"
        assert watch.send() == "idle"
    watch.read("9", 21)
    assert watch.send() == "idle"
    watch.read("8", 22)
    assert watch.send() == "accepted"
    assert "from 21 to 22" in watch.payload()["message"]


def test_pause_within_freshness_blocks_and_recovery_is_quiet(watch):
    watch.read()
    watch.alerts.set_enabled(False, expected_revision=1)
    assert watch.read("9", 6) == "discarded"
    assert watch.send() == "idle"
    watch.alerts.set_enabled(True, expected_revision=2)
    watch.read("8", 7)
    assert watch.send() == "idle"
    watch.read("7", 8)
    assert watch.send() == "accepted"


@pytest.mark.parametrize("change", ["count", "price", "unknown", "zero", "rule", "pause"])
def test_claim_rechecks_count_price_status_revision_and_pause(watch, change):
    watch.read()
    watch.read("9", 6)
    claim = watch.alerts.claim(watch.sender.binding)
    assert watch.alerts.delivery_current(claim)
    if change == "count":
        watch.read("9", 5)
    elif change == "price":
        watch.read("10", 6)
    elif change == "unknown":
        watch.read("9", None)
    elif change == "zero":
        watch.read(None, 0)
    elif change == "rule":
        watch.configure(False, False)
    else:
        watch.alerts.set_enabled(False, expected_revision=1)
    assert not watch.alerts.delivery_current(claim)
    assert watch.requests == []


def test_leased_event_absorbs_concurrent_changes_no_duplicate_or_replay(watch):
    watch.read()
    watch.read("9", 6)
    claim = watch.alerts.claim(watch.sender.binding)
    watch.read("8", 7)
    assert len(watch.events()) == 1
    assert not watch.alerts.delivery_current(claim)
    watch.alerts.finish(claim, "uncertain")
    watch.now += 300
    watch.restart()
    watch.read("8", 7)
    assert watch.send() == "idle"
    watch.read("7", 8)
    assert watch.send() == "accepted"  # Distinct later aggregate extreme, not a retry.


def test_claim_competition_and_global_daily_cap_cover_change_events(watch):
    watch.read()
    watch.read("9", 6)
    watch.sql(
        "UPDATE discogs_price_control SET send_day=?,sends=?",
        (int(watch.now // 86400), DAILY_SEND_CAP),
    )
    assert watch.send() == "idle"
    watch.sql("UPDATE discogs_price_control SET sends=0")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: watch.alerts.claim(watch.sender.binding), range(2)))
    assert sum(c is not None for c in claims) == 1


def test_expired_pending_change_is_not_resurrected_on_recovery(watch):
    watch.read()
    watch.read("9", 6)
    watch.now += FRESH_SECONDS
    watch.read("8", 7)
    assert watch.events()[0][1] == "expired"
    assert watch.send() == "idle"
    watch.read("8", 7)
    assert watch.send() == "idle"
    watch.read("7", 8)
    assert watch.send() == "accepted"


@pytest.mark.parametrize("any_price", [False, True])
def test_end_to_end_real_reader_worker_outbox_private_sender_is_intercepted(tmp_path, any_price):
    watch = Watch(
        tmp_path, **({"any_price": True, "item_price_ceiling": None} if any_price else {})
    )
    responses = [("10", 5), ("9", 6), ("9", 6), ("10", 5), ("9", 6), ("8", 7)]
    api_calls, posts = [], []

    def api(request):
        api_calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/marketplace/stats/123"
        assert dict(request.url.params) == {"curr_abbr": "USD"}
        price, count = responses.pop(0)
        return httpx.Response(
            200, json={"num_for_sale": count, "lowest_price": {"value": price, "currency": "USD"}}
        )

    path = tmp_path / "synthetic-private.json"
    path.write_text(
        json.dumps(
            dict(
                origin=PRIVATE_NTFY_ORIGIN,
                topic="offline_fixture_destination_not_live",
                token="tk_" + "offlinefixture" * 3,
            )
        )
    )
    path.chmod(0o600)
    private = NtfySender(
        private_config=PrivateNtfyConfig(path),
        enabled=True,
        transport=httpx.MockTransport(lambda r: posts.append(r) or httpx.Response(200)),
    )
    reader = ReleaseStatsReader(transport=httpx.MockTransport(api), clock=lambda: watch.now)
    states = []
    for _ in range(6):
        result = tick(watch.alerts, reader=reader, sender=private)
        states.append(result["delivery"]["state"])
        assert result["purchase_allowed"] is False
        watch.now += 300
        watch.restart()
    assert states == ["idle", "accepted", "idle", "idle", "idle", "accepted"]
    assert len(api_calls) == 6 and len(posts) == 2
    assert all(str(r.url) == PRIVATE_NTFY_ORIGIN + "/" for r in posts)
    for post in posts:
        payload = json.loads(post.content)
        assert payload["title"].startswith("Synthetic public record | USD ")
        assert "; offers " in payload["title"]
        assert "minimum item price decreased" in payload["message"]
        assert "aggregate offers increased" in payload["message"]
        assert (
            "Condition and shipping are unknown; delivered total is unverified"
            in payload["message"]
        )


def test_no_flood_configure_preserves_existing_samples_and_poll_schedule(tmp_path):
    watch = Watch(
        tmp_path, alert_initial_low=True, alert_price_drops=False, alert_count_increases=False
    )
    watch.read()
    assert watch.send() == "accepted"
    watch.sql("UPDATE discogs_price_samples SET next_check=?,failures=2", (watch.now + 1800,))
    before = watch.sql("SELECT * FROM discogs_price_samples")
    watch.configure(True, True)
    assert watch.sql("SELECT * FROM discogs_price_samples") == before
    saved = json.loads(watch.sql("SELECT data FROM discogs_price_rules")[0][0])
    assert saved["item_price_ceiling"] == "10.00" and saved["alert_initial_low"] is True
    assert watch.configure(True, True) is None  # wrapper; revision remains unchanged
    assert watch.revision == 2
    assert watch.alerts.poll_once(object())["status"] == "idle"
    watch.read("8", 8)
    watch.now += 300
    assert watch.send() == "idle"
    assert len(watch.events()) == 1
    watch.read("7", 9)
    assert watch.send() == "accepted"
    with pytest.raises(RuntimeError):
        watch.alerts.configure_change_alerts(
            123, alert_price_drops=True, alert_count_increases=True, expected_revision=1
        )


def test_reconfigure_revokes_pending_and_quarantines_claim_without_resetting_history(watch):
    watch.read()
    watch.read("9", 6)
    watch.configure(False, False)
    assert watch.events()[0][1] == "revoked"
    watch.configure(True, True)
    watch.read("8", 7)
    assert watch.send() == "idle"
    watch.read("7", 8)
    claim = watch.alerts.claim(watch.sender.binding)
    watch.configure(False, False)
    assert not watch.alerts.delivery_current(claim)
    assert watch.events()[-1][1] == "uncertain"
    assert watch.sql("SELECT low,seen FROM discogs_price_samples") == [(1, 1)]
    assert "alert_price_drops" not in json.loads(
        watch.sql("SELECT data FROM discogs_price_rules")[0][0]
    )


def test_additive_upgrade_preserves_old_rule_outbox_and_buying_state(tmp_path):
    watch = Watch(
        tmp_path, alert_initial_low=True, alert_price_drops=False, alert_count_increases=False
    )
    watch.read()
    old_events = watch.events()
    buying = watch.sql("SELECT * FROM state")
    watch.sql("DROP TABLE discogs_price_extrema")
    watch.sql("DROP TABLE discogs_price_changes")
    watch.restart()
    watch.restart()
    assert watch.events() == old_events
    assert watch.sql("SELECT * FROM state") == buying
    assert "alert_price_drops" not in json.loads(
        watch.sql("SELECT data FROM discogs_price_rules")[0][0]
    )
    assert watch.send() == "accepted"


def test_rollback_removes_only_new_fields_preserves_history_and_is_idempotent(watch):
    watch.read()
    watch.read("9", 6)
    assert watch.send() == "accepted"
    watch.now += 300
    watch.read("8", 7)
    claim = watch.alerts.claim(watch.sender.binding)
    before_sample = watch.sql("SELECT * FROM discogs_price_samples")
    before_json = json.loads(watch.sql("SELECT data FROM discogs_price_rules")[0][0])
    with pytest.raises(RuntimeError):
        watch.alerts.prepare_legacy_rollback(expected_control_revision=1)
    watch.alerts.set_enabled(False, expected_revision=1)
    with pytest.raises(RuntimeError):
        watch.alerts.prepare_legacy_rollback(expected_control_revision=1)
    result = watch.alerts.prepare_legacy_rollback(expected_control_revision=2)
    assert result == {"rules_updated": 1, "enabled": False, "history_preserved": True}
    after_json = json.loads(watch.sql("SELECT data FROM discogs_price_rules")[0][0])
    before_json.pop("alert_price_drops")
    before_json.pop("alert_count_increases")
    assert after_json == before_json
    legacy = create_model(
        "LegacyPriceLeadRule",
        __base__=StrictInput,
        **{
            name: (field.annotation, field.default)
            for name, field in PriceLeadRule.model_fields.items()
            if name not in {"alert_price_drops", "alert_count_increases"}
        },
    )
    legacy.model_validate(after_json)  # Old strict schema cannot accept either new flag.
    assert watch.sql("SELECT * FROM discogs_price_samples") == before_sample
    assert [row[1] for row in watch.events()] == ["accepted", "uncertain"]
    assert not watch.alerts.delivery_current(claim)
    assert watch.alerts.prepare_legacy_rollback(expected_control_revision=2)["rules_updated"] == 0
    watch.restart()
    watch.alerts.set_enabled(True, expected_revision=2)
    watch.revision += 1
    watch.read("7", 8)
    assert watch.send() == "uncertain_recovery"
    assert watch.send() == "idle"  # Abandoned reservation is consumed only once.
    assert watch.alerts.status()["purchase_allowed"] is False


@pytest.mark.parametrize(
    "previous_price,price,previous_count,count",
    [
        ("10.00", "9.00", None, None),
        (None, None, 5, 6),
        ("10.00", "9.00", 5, 6),
    ],
)
def test_sender_accepts_only_canonical_change_payload(
    watch, previous_price, price, previous_count, count
):
    payload = change_message(
        "Synthetic public record", 123, "USD", price, previous_price, count, previous_count
    )
    assert watch.sender.send(payload).outcome == "accepted"
    for field, bad in [
        ("title", "New listing"),
        ("message", payload["message"] + "Buy now!"),
        ("click", "https://www.discogs.com/sell/release/124"),
    ]:
        with pytest.raises(ValueError):
            watch.sender.send(payload | {field: bad})
    assert len(watch.requests) == 1


@pytest.mark.parametrize(
    "previous_price,price,previous_count,count",
    [
        ("9.00", "10.00", None, None),
        ("9.00", "9.00", None, None),
        (None, None, 5, 5),
        (None, None, 5, 4),
        (None, None, 0, 1),
        (None, None, 1, 10000001),
    ],
)
def test_sender_rejects_nonchanges_or_invalid_aggregate_bounds(
    watch, previous_price, price, previous_count, count
):
    payload = change_message(
        "Synthetic public record", 123, "USD", price, previous_price, count, previous_count
    )
    with pytest.raises(ValueError):
        watch.sender.send(payload)
    assert not watch.requests


def test_new_extreme_after_invalidation_cannot_bypass_provider_backoff(watch):
    watch.read()
    watch.read("9", 6)
    watch.response = 429
    assert watch.send() == "retry_wait"
    watch.read("10", 5)
    assert watch.events()[0][1] == "revoked"
    watch.now += 300
    watch.read("8", 7)
    watch.response = 200
    assert watch.send() == "idle"
    watch.now += 300
    watch.read("8", 7)
    assert watch.send() == "idle"
    watch.now += 300
    watch.read("8", 7)
    assert watch.send() == "accepted"
    assert len(watch.requests) == 2


def test_unknown_price_on_any_price_pending_change_cannot_revive_price_signal(tmp_path):
    watch = Watch(tmp_path, any_price=True, item_price_ceiling=None)
    watch.read("100", 5)
    watch.read("90", 6)
    watch.read(None, 6)
    assert watch.send() == "accepted"
    assert "minimum item price" not in watch.payload()["message"]
    watch.now += 300
    watch.read("90", 6)
    watch.read("90", 6)
    assert watch.send() == "idle"


def test_pending_and_retry_change_events_are_revoked_for_legacy_rollback(watch):
    watch.read()
    watch.read("9", 6)
    watch.response = 429
    assert watch.send() == "retry_wait"
    attempts = watch.events()[0][4]
    next_send = watch.sql("SELECT next_send FROM discogs_price_control")
    watch.alerts.set_enabled(False, expected_revision=1)
    watch.alerts.prepare_legacy_rollback(expected_control_revision=2)
    assert watch.events()[0][1] == "revoked"
    assert watch.events()[0][4] == attempts
    assert watch.sql("SELECT next_send FROM discogs_price_control") == next_send


@pytest.mark.parametrize("change", ["revision", "pause"])
def test_inflight_poll_after_rollout_or_pause_cannot_create_signal(watch, change):
    watch.read()

    class ChangedReader:
        def read(self, release, currency):
            if change == "revision":
                watch.configure(False, False)
            else:
                watch.alerts.set_enabled(False, expected_revision=1)
            return dict(
                release_id=release, currency=currency, lowest_item_price="1", num_for_sale=100
            )

    assert watch.alerts.poll_once(ChangedReader())["status"] == "discarded"
    assert watch.events() == []


def test_paused_pending_change_cannot_dispatch_on_resume_before_fresh_sample(watch):
    watch.read()
    watch.read("9", 6)
    watch.alerts.set_enabled(False, expected_revision=1)
    watch.alerts.set_enabled(True, expected_revision=2)
    assert watch.send() == "idle"
    assert watch.events()[0][1] == "revoked"
    watch.read("8", 7)
    assert watch.send() == "idle"
    watch.read("7", 8)
    assert watch.send() == "accepted"


def test_19_existing_rules_enable_without_initial_flood_or_threshold_changes(tmp_path):
    watch = Watch(
        tmp_path, alert_initial_low=True, alert_price_drops=False, alert_count_increases=False
    )
    watch.read()
    assert watch.send() == "accepted"
    # Mixed synthetic any-price/threshold rules reflect the deployed rule count,
    # without copying the user's private watch identities or configuration.
    ids = [123] + list(range(200, 218))
    for release in ids[1:]:
        any_price = release % 2 == 0
        rule = PriceLeadRule(
            release_id=release,
            public_title=f"Synthetic record {release}",
            currency="USD",
            any_price=any_price,
            item_price_ceiling=None if any_price else "10.00",
            enabled=True,
            alert_initial_low=False,
        )
        watch.alerts.save_rule(rule, expected_revision=0)
        watch.alerts.observe(
            release,
            1,
            dict(release_id=release, currency="USD", num_for_sale=5, lowest_item_price="10.00"),
            observed_at=watch.now,
        )
    old_sample = watch.sql(
        "SELECT release_id,low,seen,next_check FROM discogs_price_samples ORDER BY release_id"
    )
    old_rules = {
        r: json.loads(raw)
        for r, raw in watch.sql("SELECT release_id,data FROM discogs_price_rules")
    }
    for release in ids:
        watch.alerts.configure_change_alerts(
            release, alert_price_drops=True, alert_count_increases=True, expected_revision=1
        )
    assert (
        watch.sql(
            "SELECT release_id,low,seen,next_check FROM discogs_price_samples ORDER BY release_id"
        )
        == old_sample
    )
    for release, raw in watch.sql("SELECT release_id,data FROM discogs_price_rules"):
        current = json.loads(raw)
        current.pop("alert_price_drops")
        current.pop("alert_count_increases")
        assert current == old_rules[release]
    for release in ids:
        watch.now += 3
        watch.alerts.observe(
            release,
            2,
            dict(release_id=release, currency="USD", num_for_sale=6, lowest_item_price="9.00"),
            observed_at=watch.now,
        )
        assert watch.send() == "idle"
    assert len(watch.events()) == 1  # Only the previously accepted old entry.
    assert watch.sql("SELECT count(*) FROM discogs_price_extrema") == [(19,)]


def test_exhausted_event_still_respects_provider_cooldown_for_next_extreme(watch):
    watch.read()
    watch.read("9", 6)
    watch.sql("UPDATE discogs_price_outbox SET attempts=4")
    watch.response = 429
    assert watch.send() == "failed"
    next_send = watch.sql("SELECT next_send FROM discogs_price_control")[0][0]
    assert next_send >= watch.now + 900
    watch.now += 300
    watch.read("8", 7)
    watch.response = 200
    assert watch.send() == "idle"
    assert len(watch.requests) == 1


@pytest.mark.parametrize("invalidation", ["exit", "pause", "rule", "lease_expired"])
def test_inflight_429_retains_shared_backoff_after_event_quarantine(watch, invalidation):
    watch.read()
    watch.read("9", 6)
    calls = []

    def rejecting_provider(request):
        # The real sender is already inside its HTTP request, after the final
        # current-delivery check, when a concurrent observer/operator intervenes.
        calls.append(request)
        if invalidation == "exit":
            watch.read("11", 6)
        elif invalidation == "pause":
            watch.alerts.set_enabled(False, expected_revision=1)
            watch.alerts.set_enabled(True, expected_revision=2)
        elif invalidation == "rule":
            watch.configure(False, False)
        else:
            watch.now += LEASE_SECONDS + 1
        return httpx.Response(429, headers={"Retry-After": "900"})

    destination = NtfySender(
        "offline_fixture_destination_not_live",
        enabled=True,
        transport=httpx.MockTransport(rejecting_provider),
    )
    assert deliver_one(watch.alerts, destination)["state"] == "uncertain"
    rejected_at = watch.now
    assert len(calls) == 1
    assert watch.sql("SELECT next_send FROM discogs_price_control") == [(rejected_at + 900,)]
    assert watch.sql("SELECT state,lease_until FROM discogs_price_outbox") == [("uncertain", None)]
    # A separate genuine entry must respect the rejection even though the first
    # event cannot be retried. Continuously refresh its observation during backoff.
    watch.read("11", 6)
    watch.read("9", 6)
    watch.now = rejected_at + 91
    watch.read("9", 6)
    assert watch.send() == "idle"
    watch.now = rejected_at + 899
    watch.read("9", 6)
    assert watch.now == rejected_at + 900
    assert watch.send() == "accepted"
    assert [row[1] for row in watch.events()] == ["uncertain", "accepted"]


@pytest.mark.parametrize("field", ["event_key", "token", "provider_id", "destination_ref"])
def test_mismatched_quarantined_reservation_cannot_impose_cooldown(watch, field):
    watch.read()
    watch.read("9", 6)
    reservation = watch.alerts.claim(watch.sender.binding)
    watch.read("11", 6)
    before = watch.sql("SELECT next_send FROM discogs_price_control")
    with pytest.raises(RuntimeError, match="reservation changed"):
        watch.alerts.finish(reservation | {field: "incorrect"}, "rejected", retry_after=86400)
    assert watch.sql("SELECT next_send FROM discogs_price_control") == before
    # Rejecting the forged result must not consume the genuine in-flight attempt.
    assert watch.sql("SELECT lease_until FROM discogs_price_outbox")[0][0] is not None
    assert watch.alerts.finish(reservation, "rejected", retry_after=900)["state"] == "uncertain"
    assert watch.sql("SELECT next_send FROM discogs_price_control")[0][0] == watch.now + 900


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "failed", "uncertain"])
def test_quarantined_result_consumed_once_cannot_extend_cooldown(watch, outcome):
    watch.read()
    watch.read("9", 6)
    reservation = watch.alerts.claim(watch.sender.binding)
    watch.read("11", 6)
    assert watch.alerts.finish(reservation, outcome, retry_after=900)["state"] == "uncertain"
    before = watch.sql("SELECT next_send FROM discogs_price_control")
    assert watch.sql("SELECT lease_until FROM discogs_price_outbox") == [(None,)]
    watch.now += 300
    assert watch.alerts.finish(reservation, "rejected", retry_after=86400)["state"] == "uncertain"
    assert watch.sql("SELECT next_send FROM discogs_price_control") == before
    assert watch.events()[0][4] == 1


def test_prior_attempt_token_cannot_impose_new_shared_cooldown(watch):
    watch.read()
    watch.read("9", 6)
    first = watch.alerts.claim(watch.sender.binding)
    assert watch.alerts.finish(first, "rejected", retry_after=900)["state"] == "retry_wait"
    watch.now += 900
    watch.read("9", 6)
    second = watch.alerts.claim(watch.sender.binding)
    assert second and first["token"] != second["token"]
    watch.read("11", 6)
    before = watch.sql("SELECT next_send FROM discogs_price_control")
    with pytest.raises(RuntimeError, match="reservation changed"):
        watch.alerts.finish(first, "rejected", retry_after=86400)
    assert watch.sql("SELECT next_send FROM discogs_price_control") == before
    assert watch.alerts.finish(second, "rejected", retry_after=900)["state"] == "uncertain"
