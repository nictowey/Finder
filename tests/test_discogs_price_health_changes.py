"""Combined health and opt-in aggregate-change compatibility, entirely offline."""

import json
import sqlite3
from contextlib import closing

import pytest

from finder.discogs_ntfy import NtfySender
from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_price_health import health_report
from finder.discogs_rules import RulesStore


@pytest.fixture
def change(tmp_path):
    path = tmp_path / "rules.db"
    RulesStore(path, create=True).close()
    now = [1800000000.0]
    alerts = AggregatePriceAlerts(path, clock=lambda: now[0], poll_seconds=300)
    rule = PriceLeadRule(
        release_id=123,
        public_title="Fixture record",
        currency="USD",
        item_price_ceiling="10.00",
        enabled=True,
        alert_initial_low=False,
        alert_price_drops=True,
        alert_count_increases=True,
    )
    alerts.save_rule(rule, expected_revision=0)
    alerts.set_enabled(True, expected_revision=0)
    for price, count in [("10.00", 5), ("9.00", 6)]:
        now[0] += 1
        alerts.observe(
            123,
            1,
            {
                "release_id": 123,
                "num_for_sale": count,
                "currency": "USD",
                "lowest_item_price": price,
            },
            observed_at=now[0],
        )
    topic = tmp_path / "topic"
    topic.write_text("offline_fixture_destination_not_live")
    topic.chmod(0o600)
    return path, now, alerts, topic


def inspect(change):
    path, now, _, topic = change
    with closing(sqlite3.connect(path)) as db:
        before = list(db.iterdump())
        result = health_report(path, now=now[0], poll_seconds=300, topic_file=topic)
        assert list(db.iterdump()) == before
    return result


def test_change_health_eligibility_agrees_with_actual_claim(change):
    result = inspect(change)
    assert result["queue"]["eligible_now"] == 1
    _, _, alerts, _ = change
    sender = NtfySender("offline_fixture_destination_not_live", enabled=True)
    assert alerts.claim(sender.binding) is not None


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE discogs_price_changes SET previous_price='8.00'",
        "UPDATE discogs_price_changes SET count=7",
        "UPDATE discogs_price_changes SET previous_count=7",
    ],
)
def test_unsupported_change_is_blocked_even_if_original_price_gate_passes(change, sql):
    path, _, alerts, _ = change
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(sql)
    result = inspect(change)
    assert result["queue"]["eligible_now"] == 0
    assert result["queue"]["blocked_reasons"]["change_not_supported"] == 1
    sender = NtfySender("offline_fixture_destination_not_live", enabled=True)
    assert alerts.claim(sender.binding) is None


def test_corrupt_change_details_fail_closed_without_exception_values(change):
    path, _, _, _ = change
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE discogs_price_changes SET previous_price='DO_NOT_PRINT_SECRET'")
    result = inspect(change)
    assert result["status"] == "unavailable"
    assert result["issues"] == ["incompatible_change_evidence"]
    assert "DO_NOT_PRINT" not in json.dumps(result)
