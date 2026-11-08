"""Synthetic notification fixtures only. All HTTP is intercepted; sockets fail closed."""

import json
import socket
import unicodedata
from decimal import Decimal

import httpx
import pytest

from finder.discogs_aggregate_signals import change_message
from finder.discogs_listing import ReleaseStatsReader
from finder.discogs_ntfy import PRIVATE_NTFY_ORIGIN, NtfySender, PrivateNtfyConfig
from finder.discogs_price_alerts import (
    AggregatePriceAlerts,
    PriceLeadRule,
    availability_message,
    lead_message,
    rule_message,
)
from finder.discogs_price_messages import PAYLOAD_BYTES, TITLE_BYTES, validate_price_message
from finder.discogs_price_worker import tick
from finder.discogs_rules import RulesStore


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Notification fixture attempted a real socket connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


# No real watch selection, identifiers or title-to-threshold associations.
SYNTHETIC_RULES = [
    dict(
        release_id=700001 + index,
        public_title=f"Fixture Artist {index + 1} - Synthetic Album {index + 1}",
        currency="USD",
        item_price_ceiling=(f"{20 + index * 10}.99" if index < 12 else "10.00")
        if index < 13
        else None,
        any_price=index >= 13,
        enabled=True,
        alert_price_drops=True,
        alert_count_increases=True,
    )
    for index in range(19)
]


@pytest.mark.parametrize("settings", SYNTHETIC_RULES, ids=[f"watch-{i}" for i in range(1, 20)])
def test_19_representative_rules_match_outbox_worker_private_sender_offline(tmp_path, settings):
    database = tmp_path / "fixture.db"
    RulesStore(database, create=True).close()
    now = [1800000000.0]
    rule = PriceLeadRule(**settings)
    alerts = AggregatePriceAlerts(database, clock=lambda: now[0], poll_seconds=120)
    alerts.save_rule(rule, expected_revision=0)
    alerts.set_enabled(True, expected_revision=0)
    store = RulesStore(database)
    before_rules = store.db.execute("SELECT * FROM discogs_price_rules").fetchall()
    before_targets = store.snapshot()
    store.close()
    price = rule.item_price_ceiling or "125.00"
    observations = [
        (price, 5),
        (format(Decimal(price) - 1, ".2f"), 6),
        (format(Decimal(price) - 1, ".2f"), 6),  # No duplicate after restart.
        (format(Decimal(price) - 1, ".2f"), 7),  # Count-only change, current minimum.
    ]
    gets, posts = [], []

    def stats(request):
        gets.append(request)
        assert request.method == "GET"
        assert str(request.url) == (
            f"https://api.discogs.com/marketplace/stats/{rule.release_id}?curr_abbr=USD"
        )
        assert "authorization" not in request.headers
        value, count = observations.pop(0)
        return httpx.Response(
            200, json={"num_for_sale": count, "lowest_price": {"currency": "USD", "value": value}}
        )

    def publish(request):
        assert request.method == "POST" and str(request.url) == PRIVATE_NTFY_ORIGIN + "/"
        posts.append(json.loads(request.content))
        assert len(request.content) < 4096
        return httpx.Response(200)

    config = tmp_path / "synthetic-private.json"
    config.write_text(
        json.dumps(
            dict(
                origin=PRIVATE_NTFY_ORIGIN,
                topic="offline_fixture_destination_not_live",
                token="tk_" + "offlinefixture" * 3,
            )
        )
    )
    config.chmod(0o600)
    sender = NtfySender(
        private_config=PrivateNtfyConfig(config),
        enabled=True,
        transport=httpx.MockTransport(publish),
    )
    states = []
    for _ in range(4):
        reader = ReleaseStatsReader(transport=httpx.MockTransport(stats), clock=lambda: now[0])
        result = tick(alerts, reader=reader, sender=sender)
        states.append(result["delivery"]["state"])
        assert not result["purchase_allowed"] and not result["delivery"]["delivered"]
        now[0] += 120
        alerts = AggregatePriceAlerts(database, clock=lambda: now[0], poll_seconds=120)
    assert states == ["accepted", "accepted", "idle", "accepted"]
    assert len(gets) == 4 and len(posts) == 3
    for payload in posts:
        assert payload["title"].startswith(rule.public_title + " | ")
        assert payload["message"].startswith(payload["title"] + "\n")
        assert "aggregate" in payload["message"].lower()
        assert (
            "Condition and shipping are unknown; delivered total is unverified"
            in payload["message"]
        )
        assert "not a qualified purchase" in payload["message"]
        assert "Data provided by Discogs" in payload["message"]
        assert payload["click"] == f"https://www.discogs.com/sell/release/{rule.release_id}"
        assert (
            "Watch: any price (USD)." in payload["message"]
            if rule.any_price
            else (
                f"Watch: item price at or below USD {rule.item_price_ceiling}."
                in payload["message"]
            )
        )
        assert "seller" not in payload["message"].lower()
        assert "offline_fixture" not in payload["message"]
        assert "offlinefixture" not in payload["message"]
        validate_price_message({key: value for key, value in payload.items() if key != "topic"})
    assert (
        f"USD {price} min; any price" in posts[0]["title"]
        if rule.any_price
        else (f"USD {price} min" in posts[0]["title"])
    )
    assert f"was USD {price}" in posts[1]["title"] and "offers 5 -> 6" in posts[1]["title"]
    assert "was" not in posts[2]["title"] and "offers 6 -> 7" in posts[2]["title"]
    store = RulesStore(database)
    assert store.db.execute("SELECT * FROM discogs_price_rules").fetchall() == before_rules
    assert store.snapshot() == before_targets
    assert store.db.execute("SELECT state,attempts FROM discogs_price_outbox").fetchall() == [
        ("accepted", 1),
        ("accepted", 1),
        ("accepted", 1),
    ]
    store.close()


@pytest.mark.parametrize(
    "cap,price,expected",
    [
        ("99.99", "100.00", "outside"),
        ("99.99", "99.99", "low"),
        ("10.00", "10.00", "low"),
    ],
)
def test_exact_saved_cap_never_infers_a_round_dollar_threshold(tmp_path, cap, price, expected):
    path = tmp_path / "boundary.db"
    RulesStore(path, create=True).close()
    alerts = AggregatePriceAlerts(path, clock=lambda: 1800000000.0)
    rule = PriceLeadRule(
        release_id=123,
        public_title="Fixture Artist - Boundary Album",
        currency="USD",
        item_price_ceiling=cap,
        enabled=True,
    )
    alerts.save_rule(rule, expected_revision=0)
    alerts.set_enabled(True, expected_revision=0)
    assert (
        alerts.observe(
            123,
            1,
            dict(release_id=123, currency="USD", lowest_item_price=price, num_for_sale=5),
            observed_at=1800000000.0,
        )
        == expected
    )
    payload = rule_message(rule, cap)
    assert f"item price at or below USD {cap}" in payload["message"]
    assert "under USD" not in payload["message"]
    validate_price_message(payload)


@pytest.mark.parametrize(
    "title",
    [
        "Artist\r\nInjected: value\t- Album\x00\x7f",
        "Artist\u0085\u202e - Album\u2066\u2028\u2029",
        "\ud800Artist\udfff - Album",
        "\x00\u202e\t",
        "録音🎵" * 200,
        "Artist | Band - Album | Deluxe",
    ],
)
def test_malformed_and_long_unicode_titles_are_inert_bounded_and_sender_round_trip(title):
    payload = change_message(
        title,
        123,
        "USD",
        "9999998.99",
        "9999999.99",
        10000000,
        9999999,
        item_price_ceiling="9999999.99",
    )
    assert len(payload["title"].encode("utf-8")) <= TITLE_BYTES
    assert len(json.dumps(payload).encode("utf-8")) <= PAYLOAD_BYTES
    assert payload["title"].endswith(
        "USD 9999998.99 min (was USD 9999999.99); offers 9999999 -> 10000000"
    )
    assert not any(
        unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for c in payload["title"]
    )
    assert payload["title"].encode().decode() == payload["title"]
    validate_price_message(payload)


def test_unicode_saved_rule_title_is_safe_without_changing_saved_identity():
    rule = PriceLeadRule(release_id=123, public_title="音🎵" * 60, currency="EUR", any_price=True)
    payload = rule_message(rule, "")
    assert "... | available; any price" in payload["title"]
    assert payload["message"].startswith(rule.public_title + " | ")
    assert "Watch: any price (EUR)." in payload["message"]
    validate_price_message(payload)


@pytest.mark.parametrize("currency", ["USD", "EUR", "GBP", "CAD", "AUD"])
def test_currency_is_explicit_without_fx_or_invented_legacy_threshold(currency):
    payload = lead_message("Fixture Artist - Album", 123, currency, "9.99")
    assert payload["title"] == f"Fixture Artist - Album | {currency} 9.99 min"
    assert "Watch:" not in payload["message"]
    validate_price_message(payload)
    validate_price_message(availability_message("Fixture Artist - Album", 123))
    available = availability_message("Fixture Artist - Album", 123, currency=currency, price="9.99")
    assert f"{currency} 9.99 min; any price" in available["title"]
    validate_price_message(available)
    validate_price_message(
        change_message("Fixture Artist - Album", 123, currency, None, None, 6, 5)
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "New exact listing ready to buy"),
        ("message", "Private arbitrary content"),
        ("click", "https://www.discogs.com/sell/item/123"),
        ("click", "https://www.discogs.com/sell/release/124"),
        ("title", "\ud800"),
        ("message", "x" * 5000),
    ],
)
def test_invalid_payload_is_rejected_before_transport(field, value):
    calls = []
    sender = NtfySender(
        "offline_fixture_destination_not_live",
        enabled=True,
        transport=httpx.MockTransport(lambda req: calls.append(req)),
    )
    payload = lead_message("Fixture Artist - Album", 123, "USD", "9.99", item_price_ceiling="10.00")
    with pytest.raises(ValueError):
        sender.send(payload | {field: value})
    assert not calls


@pytest.mark.parametrize(
    "replace",
    [
        {"Watch: item price at or below USD 10.00.": "Watch: item price at or below EUR 10.00."},
        {"Watch: item price at or below USD 10.00.": "Watch: item price at or below USD 0.00."},
        {"Watch: item price at or below USD 10.00.": "Watch: any price (USD)."},
        {
            "Condition and shipping are unknown; delivered total is unverified.": (
                "Condition verified."
            )
        },
        {"Data provided by Discogs.": ""},
    ],
)
def test_context_currency_and_disclaimers_are_canonical(replace):
    payload = lead_message("Fixture Artist - Album", 123, "USD", "9.99", item_price_ceiling="10.00")
    for old, new in replace.items():
        payload["message"] = payload["message"].replace(old, new)
    with pytest.raises(ValueError):
        validate_price_message(payload)


def test_count_only_unknown_price_does_not_invent_price_or_price_drop():
    payload = change_message("Fixture Artist - Album", 123, "USD", None, None, 6, 5, any_price=True)
    assert payload["title"] == "Fixture Artist - Album | offers 5 -> 6"
    assert "USD 0" not in payload["message"] and "minimum item price" not in payload["message"]
    validate_price_message(payload)


@pytest.mark.parametrize("known_price", [False, True])
def test_any_price_count_update_does_not_invalidate_claimed_entry(tmp_path, known_price):
    """Presentation must not introduce a new count-dependent dispatch gate."""
    path = tmp_path / "count-race.db"
    RulesStore(path, create=True).close()
    now = [1800000000.0]
    alerts = AggregatePriceAlerts(path, clock=lambda: now[0])
    rule = PriceLeadRule(
        release_id=123,
        public_title="Fixture Artist - Count Race",
        currency="USD",
        any_price=True,
        enabled=True,
        alert_price_drops=known_price,
    )
    alerts.save_rule(rule, expected_revision=0)
    alerts.set_enabled(True, expected_revision=0)
    sample = dict(release_id=123, currency="USD", lowest_item_price="10.00", num_for_sale=5)
    alerts.observe(123, 1, sample, observed_at=now[0])
    sender = NtfySender(
        "offline_fixture_destination_not_live",
        enabled=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    claim = alerts.claim(sender.binding)
    now[0] += 1
    alerts.observe(123, 1, sample | {"num_for_sale": 6}, observed_at=now[0])
    assert alerts.delivery_current(claim)
    assert sender.send(claim["payload"]).outcome == "accepted"
    assert alerts.finish(claim, "accepted")["state"] == "accepted"
    assert "5 offers" not in claim["payload"]["title"]
    assert (
        "USD 10.00 min" in claim["payload"]["title"]
        if known_price
        else ("available; any price" in claim["payload"]["title"])
    )
