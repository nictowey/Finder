"""Isolated watch editing only: never real market reads, sends or production data."""

import io
import json
import sqlite3

import pytest

from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_rules import RulesStore
from finder.discogs_watch_manager import WatchApp, WatchBackend
from finder.discogs_watch_ui import render_page


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / "fixture.db"
    RulesStore(db, create=True).close()
    alerts = AggregatePriceAlerts(db)
    for ident in range(1, 22):
        alerts.save_rule(
            PriceLeadRule(
                release_id=ident,
                public_title=f"Synthetic record {ident}",
                currency="USD",
                item_price_ceiling="40.00",
                enabled=ident <= 20,
                alert_price_drops=ident % 2 == 0,
                alert_count_increases=ident % 2 == 1,
            ),
            expected_revision=0,
        )
    backend = WatchBackend(alerts)
    app = WatchApp(
        backend=backend,
        origin="https://sniper.example",
        authenticate=lambda e: "owner" if e.get("HTTP_TEST_OWNER") == "owner" else None,
    )
    return db, alerts, backend, app


def call(app, path="/watches/state", method="GET", fields=None, **headers):
    raw = json.dumps(fields).encode() if fields is not None else b""
    env = {
        "HTTP_HOST": "sniper.example",
        "HTTP_TEST_OWNER": "owner",
        "PATH_INFO": path,
        "REQUEST_METHOD": method,
        "HTTP_ORIGIN": "https://sniper.example",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
        **headers,
    }
    result = {}
    body = b"".join(
        app(env, lambda status, values: result.update(status=status, headers=dict(values)))
    )
    result["body"] = (
        body.decode() if "text/html" in result["headers"]["Content-Type"] else json.loads(body)
    )
    return result


def fields(app, **overrides):
    return {
        "release": "https://www.discogs.com/release/99-Synthetic",
        "title": "Synthetic new record",
        "currency": "USD",
        "price": "39.99",
        "any_price": False,
        "enabled": True,
        "revision": 0,
        "csrf": call(app)["body"]["csrf"],
        **overrides,
    }


def test_add_edit_pause_resume_preserves_other_twenty_one(setup):
    db, _, backend, app = setup
    before = backend.snapshot()["watches"]
    body = fields(app)
    assert call(app, "/watches/save", "POST", body)["status"].startswith("200")
    saved = backend.snapshot()
    assert saved["watches"][:-1] == before
    assert not saved["monitor_enabled"]  # UI cannot switch on global monitoring.
    for enabled, revision in [(False, 1), (True, 2)]:
        body = fields(app, release="99", revision=revision, enabled=enabled, price="35.00")
        assert call(app, "/watches/save", "POST", body)["status"].startswith("200")
        assert backend.snapshot()["watches"][-1]["enabled"] is enabled
    assert backend.snapshot()["watches"][:-1] == before
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM discogs_price_outbox").fetchone()[0] == 0


def test_preserve_flags_and_noop_does_not_reset_sample(setup):
    db, _, backend, app = setup
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE discogs_price_samples SET status='fixture',count=3 WHERE release_id=1")
    body = fields(app, release="1", title="Synthetic record 1", price="40.00", revision=1)
    assert call(app, "/watches/save", "POST", body)["status"].startswith("200")
    rule = backend.snapshot()["watches"][0]
    assert rule["revision"] == 1 and rule["status"] == "fixture" and rule["count"] == 3
    body = fields(app, release="1", title="Synthetic renamed", price="30.00", revision=1)
    assert call(app, "/watches/save", "POST", body)["status"].startswith("200")
    rule = backend.snapshot()["watches"][0]
    assert rule["alert_count_increases"] and not rule["alert_price_drops"]


def test_conflict_and_duplicate_submission(setup):
    _, _, backend, app = setup
    body = fields(app, release="1")
    assert call(app, "/watches/save", "POST", body)["status"].startswith("409")
    assert call(app, "/watches/save", "POST", body)["status"].startswith("403")
    assert backend.snapshot()["watches"][0]["revision"] == 1
    body = fields(app)
    assert call(app, "/watches/save", "POST", body)["status"].startswith("200")
    assert call(app, "/watches/save", "POST", body)["status"].startswith("403")
    assert len(backend.snapshot()["watches"]) == 22


@pytest.mark.parametrize(
    "override",
    [
        {"release": "https://evil.example/release/99"},
        {"release": "https://www.discogs.com/master/99"},
        {"release": "https://www.discogs.com/sell/item/99"},
        {"price": "0"},
        {"price": "-10"},
        {"price": "NaN"},
        {"price": "1e2"},
        {"price": "10.001"},
        {"price": None},
        {"enabled": 1},
        {"any_price": "false"},
        {"revision": True},
        {"title": "a\nb"},
        {"currency": "ZZZ"},
        {"any_price": True},
    ],
)
def test_invalid_data_never_mutates(setup, override):
    _, _, backend, app = setup
    before = backend.snapshot()
    assert call(app, "/watches/save", "POST", fields(app, **override))["status"].startswith("400")
    assert backend.snapshot() == before


@pytest.mark.parametrize(
    "headers,code",
    [
        ({"HTTP_TEST_OWNER": "stranger"}, 401),
        ({"HTTP_HOST": "evil.example"}, 421),
        ({"HTTP_ORIGIN": "https://evil.example"}, 403),
        ({"HTTP_SEC_FETCH_SITE": "cross-site"}, 403),
        ({"HTTP_SEC_FETCH_SITE": "same-site"}, 403),
        ({"CONTENT_TYPE": "text/plain"}, 403),
        ({"CONTENT_LENGTH": "9999"}, 413),
        ({"QUERY_STRING": "release=1"}, 400),
    ],
)
def test_request_boundary(setup, headers, code):
    _, _, backend, app = setup
    before = backend.snapshot()
    assert call(app, "/watches/save", "POST", fields(app), **headers)["status"].startswith(
        str(code)
    )
    assert backend.snapshot() == before


def test_expiry_and_other_principal(setup):
    _, _, backend, app = setup
    now = [1000]
    app.clock = lambda: now[0]
    body = fields(app)
    now[0] += 601
    assert call(app, "/watches/save", "POST", body)["status"].startswith("403")
    body = fields(app)
    app.authenticate = lambda e: "other-owner"
    assert call(app, "/watches/save", "POST", body)["status"].startswith("403")
    assert len(backend.snapshot()["watches"]) == 21


def test_any_price_and_get_no_mutations(setup):
    _, _, backend, app = setup
    before = backend.snapshot()
    page = call(app, "/watches")
    assert page["status"].startswith("200")
    assert "frame-ancestors 'none'" in page["headers"]["Content-Security-Policy"]
    assert "no-store" == page["headers"]["Cache-Control"]
    assert backend.snapshot() == before
    assert call(app, "/watches/save", "POST", fields(app, any_price=True, price=None))[
        "status"
    ].startswith("200")
    rule = backend.snapshot()["watches"][-1]
    assert rule["alert_count_increases"] and not rule["alert_price_drops"]
    assert rule["any_price"] and rule["item_price_ceiling"] is None


def test_ui_safety_copy_and_no_external_code():
    page = render_page('"evil')
    assert 'nonce="&quot;evil"' in page
    assert "No media/sleeve grade or US-seller filtering" in page
    assert "Prices exclude shipping, tax and fees" in page
    assert "Last observed aggregate prices and counts may have changed" in page
    assert "No automatic retry was made" in page
    assert "localStorage" not in page and "<script src" not in page
    assert "textContent" in page and "innerHTML" not in page


def test_concurrent_new_watches_enforce_atomic_limit(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _, alerts, backend, app = setup
    for ident in range(22, 200):
        alerts.save_rule(
            PriceLeadRule(
                release_id=ident, public_title="Synthetic", currency="USD", item_price_ceiling="1"
            ),
            expected_revision=0,
        )
    barrier = Barrier(2)
    original = backend.snapshot

    def synchronized_snapshot():
        result = original()
        barrier.wait(timeout=5)
        return result

    backend.snapshot = synchronized_snapshot

    def save(ident):
        try:
            return backend.save(
                {
                    "release": str(ident),
                    "title": "Synthetic",
                    "currency": "USD",
                    "price": "5",
                    "any_price": False,
                    "enabled": True,
                    "revision": 0,
                }
            )
        except ValueError:
            return "full"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [200, 201]))
    backend.snapshot = original
    assert sorted(map(str, results)) == ["1", "full"]
    assert len(backend.snapshot()["watches"]) == 200


def test_existing_over_limit_workspace_stays_manageable(setup):
    _, alerts, backend, app = setup
    for ident in range(22, 202):
        alerts.save_rule(
            PriceLeadRule(
                release_id=ident, public_title="Synthetic", currency="USD", item_price_ceiling="1"
            ),
            expected_revision=0,
        )
    assert len(backend.snapshot()["watches"]) == 201
    body = fields(
        app, release="1", title="Synthetic record 1", price="40", revision=1, enabled=False
    )
    assert call(app, "/watches/save", "POST", body)["status"].startswith("200")
    assert not backend.snapshot()["watches"][0]["enabled"]


def test_existing_store_factory_never_initializes_or_mutates(setup, tmp_path):
    database, _, backend, app = setup
    before = database.read_bytes()
    attached = AggregatePriceAlerts.for_existing_store(database)
    assert WatchBackend(attached).snapshot() == backend.snapshot()
    assert database.read_bytes() == before
    absent = tmp_path / "absent.db"
    with pytest.raises(ValueError):
        AggregatePriceAlerts.for_existing_store(absent)
    assert not absent.exists()
    incomplete = tmp_path / "incomplete.db"
    RulesStore(incomplete, create=True).close()
    before = incomplete.read_bytes()
    with pytest.raises(ValueError):
        AggregatePriceAlerts.for_existing_store(incomplete)
    assert incomplete.read_bytes() == before


def test_noop_rechecks_revision_inside_writer_transaction(setup):
    _, alerts, backend, app = setup
    original = backend.snapshot
    stale = original()
    rule = PriceLeadRule(
        release_id=1,
        public_title="Newer synthetic edit",
        currency="USD",
        item_price_ceiling="5",
        enabled=False,
    )
    alerts.save_rule(rule, expected_revision=1)
    backend.snapshot = lambda: stale
    with pytest.raises(RuntimeError):
        backend.save(
            {
                "release": "1",
                "title": "Synthetic record 1",
                "currency": "USD",
                "price": "40.00",
                "any_price": False,
                "enabled": True,
                "revision": 1,
            }
        )
    backend.snapshot = original
    assert backend.snapshot()["watches"][0]["public_title"] == "Newer synthetic edit"


def test_notification_counts_use_accepted_events_not_attempts_or_queue(setup):
    database, alerts, backend, app = setup
    entries = [
        ("accepted", 1, 1),
        ("accepted", 1, 5),
        ("accepted", 0, 2),
        ("pending", 1, 0),
        ("retry_wait", 1, 3),
        ("leased", 1, 1),
        ("uncertain", 1, 1),
        ("failed", 1, 3),
        ("revoked", 1, 0),
        ("expired", 1, 0),
    ]
    with sqlite3.connect(database) as db:
        for episode, (state, revision, attempts) in enumerate(entries, 1):
            db.execute(
                "INSERT INTO discogs_price_outbox(event_key,release_id,rule_revision,episode,"
                "price,observed_at,state,attempts,next_attempt) VALUES(?,1,?,?,?,0,?,?,0)",
                (f"synthetic-{episode}", revision, episode, "12.00", state, attempts),
            )
    before = database.read_bytes()
    saved = backend.snapshot()["watches"]
    assert saved[0]["notifications"] == {
        "accepted": 3,
        "accepted_current_revision": 2,
        "pending": 2,
        "in_flight": 1,
        "uncertain": 1,
    }
    assert saved[1]["notifications"] == dict.fromkeys(saved[0]["notifications"], 0)
    assert database.read_bytes() == before  # Viewing never reconciles/rewrites history.
    body = fields(
        app, release="1", title="Synthetic record 1", price="30", revision=1, enabled=False
    )
    assert call(app, "/watches/save", "POST", body)["status"].startswith("200")
    paused = backend.snapshot()["watches"][0]
    assert paused["notifications"] == {
        "accepted": 3,
        "accepted_current_revision": 0,
        "pending": 0,
        "in_flight": 0,
        "uncertain": 2,
    }
    assert not paused["enabled"]


def test_adding_watch_needs_only_release_and_price_not_a_label(setup):
    _, _, backend, app = setup
    assert call(app, "/watches/save", "POST", fields(app, title=""))["status"].startswith("200")
    assert backend.snapshot()["watches"][-1]["public_title"] == "Discogs release 99"


def test_preferences_are_narrow_noop_revision_checked_and_reject_removed(setup):
    db, alerts, backend, app = setup
    before = backend.snapshot()["watches"]
    data = dict(
        action="preferences",
        release="1",
        revision=1,
        alert_price_drops=True,
        alert_count_increases=False,
    )
    assert backend.save(data) == 2
    after = backend.snapshot()["watches"]
    assert after[1:] == before[1:]
    changed = {key for key in before[0] if before[0][key] != after[0][key]}
    assert changed == {"revision", "alert_price_drops", "alert_count_increases"}
    data["revision"] = 2
    with sqlite3.connect(db) as conn:
        before_dump = list(conn.iterdump())
    assert backend.save(data) == 2
    with sqlite3.connect(db) as conn:
        assert list(conn.iterdump()) == before_dump
    with pytest.raises(RuntimeError):
        backend.save({**data, "revision": 1})
    alerts.set_removed(1, removed=True, expected_revision=2)
    from finder.discogs_watch_removals import RemovedWatchError

    with pytest.raises(RemovedWatchError):
        backend.save({**data, "revision": 3})


def test_preference_backend_keeps_observations_pacing_and_accepted_history(tmp_path):
    from test_discogs_aggregate_signals import Watch

    watch = Watch(
        tmp_path, alert_initial_low=True, alert_price_drops=False, alert_count_increases=False
    )
    watch.read()
    assert watch.send() == "accepted"
    watch.sql("UPDATE discogs_price_samples SET next_check=?,failures=2", (watch.now + 1800,))
    samples = watch.sql("SELECT * FROM discogs_price_samples")
    history = watch.events()
    backend = WatchBackend(watch.alerts)
    watch.revision = backend.save(
        dict(
            action="preferences",
            release="123",
            revision=1,
            alert_price_drops=True,
            alert_count_increases=True,
        )
    )
    assert watch.sql("SELECT * FROM discogs_price_samples") == samples
    assert watch.events() == history
    assert watch.alerts.poll_once(object())["status"] == "idle"
    watch.read("8", 8)
    watch.now += 300
    assert watch.send() == "idle"
    watch.read("7", 9)
    assert watch.send() == "accepted"
