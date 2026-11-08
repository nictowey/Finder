"""Offline provider quota, complete twenty-rule cycles and truthful health."""

import sqlite3
from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
import pytest

from finder.discogs_listing import ReleaseStatsReader
from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_price_health import health_report
from finder.discogs_read_quota import quota_delay, retry_seconds
from finder.discogs_rules import RulesStore


def test_retry_dates_and_long_deadlines_are_not_shortened():
    now = 1800000000
    assert retry_seconds("172800", now) == 172800
    assert retry_seconds(format_datetime(datetime.fromtimestamp(now + 7200, UTC)), now) == 7200
    for value in ("bad", "-5", "nan", ""):
        assert retry_seconds(value, now) == 60


@pytest.mark.parametrize("remaining,expected", [("59", 3), ("13", 60), ("0", 60)])
def test_dynamic_headroom(remaining, expected):
    assert (
        quota_delay(
            httpx.Headers(
                {"X-Discogs-Ratelimit": "60", "X-Discogs-Ratelimit-Remaining": remaining}
            ),
            1800000000,
        )
        == expected
    )


def test_reset_and_used_are_conservative():
    headers = httpx.Headers(
        {
            "X-Discogs-Ratelimit": "60",
            "X-Discogs-Ratelimit-Remaining": "50",
            "X-Discogs-Ratelimit-Used": "60",
            "X-Discogs-Ratelimit-Reset": "1800000120",
        }
    )
    assert quota_delay(headers, 1800000000) == 120
    headers["X-Discogs-Ratelimit-Reset"] = "120"
    assert quota_delay(headers, 1800000000) == 120


def workspace(tmp_path, count=20):
    path = tmp_path / "rules.db"
    RulesStore(path, create=True).close()
    now = [1800000000.0]
    alerts = AggregatePriceAlerts(path, clock=lambda: now[0], poll_seconds=120)
    for release in range(1, count + 1):
        alerts.save_rule(
            PriceLeadRule(
                release_id=release,
                public_title=f"Record {release}",
                currency="USD",
                any_price=True,
                enabled=True,
            ),
            expected_revision=0,
        )
    alerts.set_enabled(True, expected_revision=alerts.status()["revision"])
    return path, now, alerts


@pytest.mark.parametrize("latency", [0, 1, 8])
def test_twenty_rules_complete_multiple_cycles_and_health(tmp_path, latency):
    path, now, alerts = workspace(tmp_path)
    calls = []

    def respond(request):
        calls.append((int(request.url.path.rsplit("/", 1)[1]), now[0]))
        now[0] += latency
        return httpx.Response(
            200,
            headers={"X-Discogs-Ratelimit": "60", "X-Discogs-Ratelimit-Remaining": "59"},
            json={"num_for_sale": 1, "lowest_price": {"value": 5, "currency": "USD"}},
        )

    reader = ReleaseStatsReader(
        transport=httpx.MockTransport(respond), clock=lambda: now[0], wall_clock=lambda: now[0]
    )
    for _ in range(1500):
        alerts.poll_once(reader)
        if len(calls) >= 60:
            break
        now[0] += 0.5
    assert len(calls) == 60
    assert [r for r, _ in calls] == list(range(1, 21)) * 3
    assert all(b[1] - a[1] >= 3 for a, b in zip(calls, calls[1:], strict=False))
    assert all(calls[i + 20][1] - calls[i][1] >= 120 for i in range(40))
    report = health_report(path, now=now[0], poll_seconds=120)
    assert all(r["freshness"] == "fresh" for r in report["rules"])
    assert not any("poll_overdue" in r["issues"] for r in report["rules"])
    if latency == 8:
        assert calls[20][1] - calls[0][1] > 120  # No impossible full-cycle promise.


@pytest.mark.parametrize("status", [200, 429, 503])
def test_provider_deadline_persists_for_all_statuses_across_restart(tmp_path, status):
    path, now, alerts = workspace(tmp_path)
    deadline = now[0] + 172800

    def respond(request):
        return httpx.Response(
            status,
            headers={"Retry-After": format_datetime(datetime.fromtimestamp(deadline, UTC))},
            json={"num_for_sale": 1, "lowest_price": {"value": 5, "currency": "USD"}},
        )

    reader = ReleaseStatsReader(
        transport=httpx.MockTransport(respond), clock=lambda: now[0], wall_clock=lambda: now[0]
    )
    assert alerts.poll_once(reader)["requests"] == 1
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT next_request FROM discogs_monitor_control").fetchone()[0] == deadline
        )
    now[0] += 3600
    reopened = AggregatePriceAlerts(path, clock=lambda: now[0], poll_seconds=120)
    assert reopened.poll_once(reader) == {"status": "cooldown", "requests": 0}
    report = health_report(path, now=now[0], poll_seconds=120)
    assert any("poll_overdue" in r["issues"] for r in report["rules"])


def test_twenty_rules_recover_after_quota_and_429(tmp_path):
    path, now, alerts = workspace(tmp_path)
    calls = []
    successes = []

    def respond(request):
        release = int(request.url.path.rsplit("/", 1)[1])
        calls.append((release, now[0]))
        now[0] += 1
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "180"})
        successes.append(release)
        return httpx.Response(
            200,
            headers={
                "X-Discogs-Ratelimit": "60",
                "X-Discogs-Ratelimit-Remaining": "13" if len(calls) == 2 else "59",
            },
            json={"num_for_sale": 1, "lowest_price": {"value": 5, "currency": "USD"}},
        )

    reader = ReleaseStatsReader(
        transport=httpx.MockTransport(respond), clock=lambda: now[0], wall_clock=lambda: now[0]
    )
    for _ in range(1800):
        alerts.poll_once(reader)
        if len(successes) == 40:
            break
        now[0] += 0.5
    assert len(successes) == 40
    assert all(successes.count(release) == 2 for release in range(1, 21))
    assert calls[1][1] - calls[0][1] >= 181
    assert calls[2][1] - calls[1][1] >= 61
    report = health_report(path, now=now[0], poll_seconds=120)
    assert all(r["sample_status"] == "ok" and r["freshness"] == "fresh" for r in report["rules"])


def test_zero_remaining_without_limit_still_backs_off():
    assert quota_delay(httpx.Headers({"X-Discogs-Ratelimit-Remaining": "0"}), 1800000000) == 60


@pytest.mark.parametrize("raw", ["1000000000000", "9" * 5000])
def test_huge_retry_stays_durable_visible_and_json_finite(tmp_path, raw):
    import json
    import math

    path, now, alerts = workspace(tmp_path)
    reader = ReleaseStatsReader(
        transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": raw})),
        clock=lambda: now[0],
        wall_clock=lambda: now[0],
    )
    assert alerts.poll_once(reader)["status"] == "unavailable"
    report = health_report(path, now=now[0], poll_seconds=120)
    assert report["controls"]["request_manual_attention"] is True
    assert "request_provider_manual_attention" in report["issues"]
    assert math.isfinite(report["controls"]["request_cooldown_seconds"])
    assert report["controls"]["request_cooldown_seconds"] >= 1e12
    json.dumps(report, allow_nan=False)
    now[0] += 86400
    assert (
        AggregatePriceAlerts(path, clock=lambda: now[0], poll_seconds=120).poll_once(reader)[
            "status"
        ]
        == "cooldown"
    )


def test_known_low_limit_incomplete_usage_fails_closed():
    for value in ("", "bad", "11"):
        assert (
            quota_delay(
                httpx.Headers(
                    {"X-Discogs-Ratelimit": "10", "X-Discogs-Ratelimit-Remaining": value}
                ),
                1800000000,
            )
            >= 60
        )


@pytest.mark.parametrize("status", [200, 503])
def test_non429_external_budget_also_defers_local_reader(status):
    from finder.discogs_listing import ListingReader, ListingReadError, PublicReadBudget

    now = [1800000000.0]
    external = PublicReadBudget(clock=lambda: now[0])
    reader = ListingReader(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, headers={"Retry-After": "180"}, json={})
        ),
        clock=lambda: now[0],
        wall_clock=lambda: now[0],
    )
    try:
        reader._request("/marketplace/listings/1", budget=external)
    except ListingReadError:
        pass
    now[0] += 4
    with pytest.raises(ListingReadError, match="cooldown"):
        reader._request("/marketplace/listings/1")
    assert external.next_read == reader.budget.next_read == 1800000180


def test_aggregate_header_hold_committed_before_body_allows_other_worker(tmp_path):
    path, now, alerts = workspace(tmp_path)
    other = AggregatePriceAlerts(path, clock=lambda: now[0], poll_seconds=120)

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            now[0] += 4
            assert other.poll_once(object()) == {"status": "cooldown", "requests": 0}
            yield b'{"num_for_sale":1,"lowest_price":{"value":5,"currency":"USD"}}'

    reader = ReleaseStatsReader(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"X-Discogs-Ratelimit": "60", "X-Discogs-Ratelimit-Remaining": "0"},
                stream=Body(),
            )
        ),
        clock=lambda: now[0],
        wall_clock=lambda: now[0],
    )
    assert alerts.poll_once(reader)["status"] == "available"


@pytest.mark.parametrize("count", [19, 20])
@pytest.mark.parametrize("latency,limit", [(0, 60), (1, 60), (8, 60), (1, 10)])
def test_real_loop_cadence_with_moving_quota(tmp_path, count, latency, limit):
    path, now, alerts = workspace(tmp_path, count=count)
    calls = []

    def respond(request):
        calls.append((int(request.url.path.rsplit("/", 1)[1]), now[0]))
        now[0] += latency
        used = sum(now[0] - at < 60 for _, at in calls)
        assert used <= limit
        return httpx.Response(
            200,
            headers={
                "X-Discogs-Ratelimit": str(limit),
                "X-Discogs-Ratelimit-Used": str(used),
                "X-Discogs-Ratelimit-Remaining": str(limit - used),
            },
            json={"num_for_sale": 1, "lowest_price": {"value": 5, "currency": "USD"}},
        )

    reader = ReleaseStatsReader(
        transport=httpx.MockTransport(respond), clock=lambda: now[0], wall_clock=lambda: now[0]
    )
    for _ in range(5000):
        started = now[0]
        alerts.poll_once(reader)
        if len(calls) >= count * 3:
            break
        now[0] += max(0.5, 3 - (now[0] - started))
    assert [release for release, _ in calls] == list(range(1, count + 1)) * 3
    assert all(calls[i + count][1] - calls[i][1] >= 120 for i in range(count * 2))
    if limit == 60:
        assert calls[count][1] - calls[0][1] == max(120, count * max(3, latency + 0.5))
    else:
        assert calls[count][1] - calls[0][1] > 120
    with sqlite3.connect(path) as db:
        for release, started in calls[-count:]:
            assert db.execute(
                "SELECT last_success,next_check FROM discogs_price_samples WHERE release_id=?",
                (release,),
            ).fetchone() == (started + latency, started + 120)
    report = health_report(path, now=now[0], poll_seconds=120)
    for rule in report["rules"]:
        assert rule["freshness"] == "fresh"
        assert ("poll_overdue" in rule["issues"]) == (rule["poll_overdue_seconds"] >= 120)
