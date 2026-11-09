"""Synthetic durable removal lifecycle; never real watches, provider traffic or sends."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier, Event

import pytest
from test_discogs_review_broker import ClientStream
from test_discogs_watch_broker import fields, request
from test_discogs_watch_broker import integration as integration

from finder.discogs_notification_outbox import NotificationBinding
from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_rules import RulesStore
from finder.discogs_watch_broker import WatchBrokerClient, mutation_fields, project_snapshot
from finder.discogs_watch_manager import WatchBackend
from finder.discogs_watch_removals import (
    SCHEMA,
    RemovedWatchError,
    initialize_removal_state,
    main,
    require_removal_schema,
)


def action(csrf="unused", *, kind="remove", release="1", revision=1):
    return dict(action=kind, release=release, revision=revision, csrf=csrf)


def contents(path):
    """Compare every persisted row, including JSON bytes, without changing storage."""
    with closing(sqlite3.connect(path)) as db:
        return {
            name: db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def remove(integration, *, release="1", revision=1):
    _, _, _, _, app, _ = integration
    _, state = request(app)
    return request(app, "/watches/save", action(state["csrf"], release=release, revision=revision))


def test_remove_restore_preserves_all_settings_history_and_other_twenty(integration):
    path, watch, _, client, app, _ = integration
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE discogs_price_samples SET episode=4,low=1,seen=1,last_success=20,"
            "last_observed=20,price='12.00',count=5,status='ok',next_check=100,failures=2,"
            "claim='synthetic',claim_until=200 WHERE release_id=1"
        )
        db.execute("INSERT INTO discogs_price_extrema VALUES(1,'12.00',5,1)")
        for number, state in enumerate(
            ["pending", "retry_wait", "leased", "accepted", "uncertain", "failed"], 1
        ):
            db.execute(
                "INSERT INTO discogs_price_outbox(event_key,release_id,rule_revision,episode,"
                "price,observed_at,state,attempts,next_attempt,token,lease_until) "
                "VALUES(?,1,1,?,'12.00',20,?,2,20,'synthetic',200)",
                (f"synthetic-{number}", number, state),
            )
            db.execute(
                "INSERT INTO discogs_price_changes VALUES(?,'15.00',3,5)",
                (f"synthetic-{number}",),
            )
    before = contents(path)
    initial = client.snapshot()
    assert not any(row["removed"] for row in initial["watches"])
    assert remove(integration) == ("200 OK", {"saved": True})
    removed = client.snapshot()["watches"][0]
    assert removed["removed"] and not removed["enabled"] and removed["revision"] == 2
    assert removed["last_success"] == 20 and removed["count"] == 5
    assert removed["notifications"] == {
        "accepted": 1,
        "accepted_current_revision": 0,
        "pending": 0,
        "in_flight": 0,
        "uncertain": 2,
    }
    after = contents(path)
    assert after["discogs_price_rules"][1:] == before["discogs_price_rules"][1:]
    assert after["discogs_price_samples"][1:] == before["discogs_price_samples"][1:]
    old_rule, new_rule = (json.loads(rows["discogs_price_rules"][0][2]) for rows in [before, after])
    assert new_rule == {**old_rule, "enabled": False}
    assert after["discogs_price_samples"][0][:-2] == before["discogs_price_samples"][0][:-2]
    assert after["discogs_price_samples"][0][-2:] == (None, None)
    assert after["discogs_price_changes"] == before["discogs_price_changes"]
    assert [row[6] for row in after["discogs_price_outbox"]] == [
        "revoked",
        "revoked",
        "uncertain",
        "accepted",
        "uncertain",
        "failed",
    ]
    for table in before.keys() - {
        "discogs_price_rules",
        "discogs_price_samples",
        "discogs_price_extrema",
        "discogs_price_outbox",
        "discogs_price_removed",
        "discogs_review_receipts",
    }:
        assert after[table] == before[table]
    # Re-open the canonical backend: recovery state is durable, not process-local.
    reopened = WatchBackend(AggregatePriceAlerts.for_existing_store(path))
    assert reopened.snapshot() == watch.snapshot()
    _, state = request(app)
    assert request(app, "/watches/save", action(state["csrf"], kind="restore", revision=2)) == (
        "200 OK",
        {"saved": True},
    )
    restored = client.snapshot()["watches"][0]
    assert restored["revision"] == 3 and not restored["removed"] and not restored["enabled"]
    assert restored["notifications"] == removed["notifications"]
    assert restored["last_success"] == removed["last_success"]
    _, state = request(app)
    assert (
        request(app, "/watches/save", {**fields(state["csrf"], 3), "enabled": True})[0] == "200 OK"
    )
    resumed = client.snapshot()["watches"][0]
    assert resumed["enabled"] and resumed["revision"] == 4


@pytest.mark.parametrize("revision", [0, 1, 2])
def test_removed_rejects_duplicate_add_edit_toggle_even_current_revision(integration, revision):
    path, _, _, _, app, _ = integration
    assert remove(integration)[0] == "200 OK"
    before = contents(path)
    _, state = request(app)
    status, body = request(app, "/watches/save", fields(state["csrf"], revision))
    assert status == "409 Conflict" and "Removed" in body["error"] and "restore" in body["error"]
    after = contents(path)
    assert after["discogs_price_rules"] == before["discogs_price_rules"]
    assert after["discogs_price_removed"] == before["discogs_price_removed"]
    alerts = AggregatePriceAlerts.for_existing_store(path)
    with pytest.raises(RemovedWatchError):
        alerts.configure_change_alerts(
            1, alert_price_drops=True, alert_count_increases=False, expected_revision=2
        )
    with pytest.raises(RemovedWatchError):
        alerts.save_rule(
            PriceLeadRule(
                release_id=1,
                public_title="Overwrite",
                currency="USD",
                item_price_ceiling="1",
                enabled=True,
            ),
            expected_revision=2,
        )


@pytest.mark.parametrize("release", ["1", "21"])
def test_revisions_and_replay_for_active_and_paused_watches(integration, release):
    _, watch, service, _, app, _ = integration
    _, state = request(app)
    payload = action(state["csrf"], release=release)
    assert request(app, "/watches/save", payload)[0] == "200 OK"
    assert request(app, "/watches/save", payload)[0] == "403 Forbidden"
    for kind, revision in [("remove", 1), ("remove", 2), ("restore", 1)]:
        _, state = request(app)
        assert (
            request(
                app,
                "/watches/save",
                action(state["csrf"], kind=kind, release=release, revision=revision),
            )[0]
            == "409 Conflict"
        )
    req = {
        "v": 1,
        "op": "watch_save",
        "request_id": "a" * 32,
        "fields": {"action": "restore", "release": release, "revision": 2},
    }
    assert service.handle(req, 1234)["ok"]
    assert service.handle(req, 1234)["error"] == "replay"
    row = next(w for w in watch.snapshot()["watches"] if str(w["release_id"]) == release)
    assert row["revision"] == 3 and not row["removed"] and not row["enabled"]
    _, state = request(app)
    assert (
        request(app, "/watches/save", action(state["csrf"], release=release))[0] == "409 Conflict"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"action": "delete"},
        {"action": True},
        {"action": ["remove"]},
        {"release": 1},
        {"release": "01"},
        {"release": "https://www.discogs.com/release/1"},
        {"release": "9007199254740992"},
        {"revision": True},
        {"revision": 0},
        {"revision": -1},
        {"revision": 2**63},
        {"revision": 1.0},
        {"enabled": True},
        {"price": "5"},
        {"database": "/tmp/other"},
    ],
)
def test_strict_lifecycle_payload_http_and_ipc(integration, changes):
    path, _, service, _, app, _ = integration
    before = contents(path)
    _, state = request(app)
    payload = {**action(state["csrf"]), **changes}
    assert request(app, "/watches/save", payload)[0] == "400 Bad Request"
    req = {
        "v": 1,
        "op": "watch_save",
        "request_id": "b" * 32,
        "fields": {k: v for k, v in payload.items() if k != "csrf"},
    }
    assert not service.handle(req, 1234)["ok"]
    assert contents(path) == before


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_X_REVIEW_PRINCIPAL": "stranger"},
        {"HTTP_HOST": "evil.example"},
        {"HTTP_ORIGIN": "https://evil.example"},
        {"HTTP_SEC_FETCH_SITE": "cross-site"},
        {"CONTENT_TYPE": "text/plain"},
        {"CONTENT_LENGTH": "4097"},
        {"QUERY_STRING": "action=remove"},
    ],
)
def test_lifecycle_auth_origin_size_and_query_boundaries(integration, headers):
    path, _, _, _, app, streams = integration
    _, state = request(app)
    before, count = contents(path), len(streams)
    assert not request(app, "/watches/save", action(state["csrf"]), **headers)[0].startswith("200")
    assert contents(path) == before and len(streams) == count


@pytest.mark.parametrize("kind,revision", [("remove", 1), ("restore", 2)])
def test_lost_lifecycle_response_never_retries(integration, kind, revision):
    _, watch, service, _, _, _ = integration
    if kind == "restore":
        assert remove(integration)[0] == "200 OK"
    streams = []

    def factory():
        stream = ClientStream(service, fail="response")
        streams.append(stream)
        return stream

    client = WatchBrokerClient("/run/fixture/broker.sock", broker_uid=4321, socket_factory=factory)
    with pytest.raises(RuntimeError):
        client.save(action(kind=kind, revision=revision))
    assert len(streams) == 1
    saved = watch.snapshot()["watches"][0]
    assert saved["revision"] == revision + 1
    assert saved["removed"] == (kind == "remove") and not saved["enabled"]


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE discogs_price_rules SET revision=3 WHERE release_id=1",
        "UPDATE discogs_price_rules SET data=? WHERE release_id=1",
        "INSERT INTO discogs_price_rules VALUES(1,3,?) ON CONFLICT(release_id) "
        "DO UPDATE SET revision=3,data=excluded.data",
        "INSERT OR REPLACE INTO discogs_price_rules VALUES(1,3,?)",
        "DELETE FROM discogs_price_rules WHERE release_id=1",
        "UPDATE OR REPLACE discogs_price_rules SET release_id=1 WHERE release_id=2",
    ],
)
@pytest.mark.parametrize("recursive", [0, 1])
def test_legacy_writers_cannot_edit_delete_replace_or_reenable_removed(integration, sql, recursive):
    path, _, _, _, _, _ = integration
    assert remove(integration)[0] == "200 OK"
    before = contents(path)
    raw = json.loads(before["discogs_price_rules"][0][2])
    raw["enabled"] = True
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA recursive_triggers={recursive}")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql, (json.dumps(raw),) if "?" in sql else ())
    assert contents(path) == before
    # Guards are scoped to the removed release, not other saved watches.
    with sqlite3.connect(path) as db:
        db.execute("UPDATE discogs_price_rules SET revision=revision+1 WHERE release_id=2")
    assert contents(path)["discogs_price_rules"][1][1] == 2


def legacy_schema(path):
    # Only this disposable synthetic DB is stripped to reproduce a pre-upgrade store.
    with sqlite3.connect(path) as db:
        for name in list(SCHEMA)[1:]:
            db.execute(f'DROP TRIGGER "{name}"')
        db.execute("DROP TABLE discogs_price_removed")


def test_explicit_migration_and_readonly_startup_contract(integration):
    path, watch, _, _, _, _ = integration
    legacy_schema(path)
    before = contents(path)
    bytes_before = path.read_bytes()
    with pytest.raises(RuntimeError, match="provisioning"):
        AggregatePriceAlerts.for_existing_store(path)
    with pytest.raises(RuntimeError, match="provisioning"):
        watch.snapshot()
    assert path.read_bytes() == bytes_before
    main(["--database", str(path)])
    after = contents(path)
    assert after == {**before, "discogs_price_removed": []}
    bytes_after = path.read_bytes()
    main(["--database", str(path)])
    assert path.read_bytes() == bytes_after
    attached = AggregatePriceAlerts.for_existing_store(path)
    assert len(WatchBackend(attached).snapshot()["watches"]) == 21
    assert path.read_bytes() == bytes_after


@pytest.mark.parametrize("tamper", ["partial", "definition", "table"])
def test_partial_or_changed_schema_never_repaired_or_served(integration, tamper):
    path, watch, _, _, _, _ = integration
    with sqlite3.connect(path) as db:
        if tamper in ("partial", "definition"):
            db.execute("DROP TRIGGER discogs_price_removed_insert_guard")
            if tamper == "definition":
                db.execute(
                    "CREATE TRIGGER discogs_price_removed_insert_guard "
                    "BEFORE INSERT ON discogs_price_rules BEGIN SELECT 1; END"
                )
        else:
            db.execute("ALTER TABLE discogs_price_removed ADD COLUMN unexpected TEXT")
    before = path.read_bytes()
    with sqlite3.connect(path) as db, pytest.raises(RuntimeError):
        initialize_removal_state(db)
    with pytest.raises(RuntimeError):
        watch.snapshot()
    with pytest.raises(RuntimeError):
        AggregatePriceAlerts.for_existing_store(path)
    assert path.read_bytes() == before


def test_provisioning_wrong_missing_and_incomplete_stores_are_unchanged(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(SystemExit):
        main(["--database", str(missing)])
    assert not missing.exists()
    for name, tagged in [("wrong", False), ("incomplete", True)]:
        path = tmp_path / (name + ".db")
        if tagged:
            RulesStore(path, create=True).close()
        else:
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE synthetic(id INTEGER)")
        before = path.read_bytes()
        with pytest.raises(ValueError):
            main(["--database", str(path)])
        assert path.read_bytes() == before


def test_migration_atomic_when_creation_fails(integration):
    path, _, _, _, _, _ = integration
    legacy_schema(path)
    before = contents(path)
    with closing(sqlite3.connect(path)) as db:

        def authorize(action, name, *_):
            if action == sqlite3.SQLITE_CREATE_TRIGGER and name.endswith("update_guard"):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorize)
        with pytest.raises(sqlite3.DatabaseError):
            initialize_removal_state(db)
    assert contents(path) == before
    with sqlite3.connect(path) as db:
        initialize_removal_state(db)
        require_removal_schema(db)


def test_lifecycle_rolls_back_if_work_invalidation_fails(integration):
    path, _, _, client, _, _ = integration
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TRIGGER synthetic_fail_removal BEFORE UPDATE ON discogs_price_samples "
            "WHEN OLD.release_id=1 BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    before = contents(path)
    with pytest.raises(RuntimeError):
        client.save(action())
    after = contents(path)
    for table in before.keys() - {"discogs_review_receipts"}:
        assert before[table] == after[table]


def test_concurrent_save_remove_only_one_revision_wins(integration):
    path, watch, _, _, _, _ = integration
    barrier = Barrier(2)
    before = contents(path)

    def mutate(kind):
        barrier.wait(timeout=5)
        try:
            return watch.save(action() if kind == "remove" else fields())
        except RuntimeError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(mutate, ["remove", "save"]))
    assert sorted(map(str, result)) == ["2", "conflict"]
    after = contents(path)
    assert after["discogs_price_rules"][1:] == before["discogs_price_rules"][1:]
    assert after["discogs_price_samples"][1:] == before["discogs_price_samples"][1:]


def test_snapshot_reads_removal_marker_and_rule_from_one_transaction(integration, monkeypatch):
    path, watch, _, _, _, _ = integration
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
    started, changed = Event(), Event()
    connect = sqlite3.connect
    before = watch.snapshot()

    def intercept(*args, **kwargs):
        db = connect(*args, **kwargs)
        if "mode=ro" in str(args[0]):

            def trace(sql):
                if sql.startswith("SELECT o.release_id,SUM"):
                    started.set()
                    assert changed.wait(5)

            db.set_trace_callback(trace)
        return db

    monkeypatch.setattr(sqlite3, "connect", intercept)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(watch.snapshot)
        assert started.wait(5)
        assert watch.save(action()) == 2
        changed.set()
        during = future.result(timeout=5)
    assert during == before
    after = watch.snapshot()["watches"][0]
    assert after["removed"] and not after["enabled"] and after["revision"] == 2


def test_removed_watch_stops_poll_and_discards_inflight_observation(integration):
    path, _, _, _, _, _ = integration
    alerts = AggregatePriceAlerts(path, clock=lambda: 1000)
    # Pause unrelated synthetic rows, then start an observation before removing ID 1.
    with sqlite3.connect(path) as db:
        for ident, raw in db.execute(
            "SELECT release_id,data FROM discogs_price_rules WHERE release_id!=1"
        ).fetchall():
            data = json.loads(raw)
            data["enabled"] = False
            db.execute(
                "UPDATE discogs_price_rules SET data=? WHERE release_id=?",
                (json.dumps(data), ident),
            )
    started, done = Event(), Event()

    class Reader:
        calls = 0

        def read(self, release, currency):
            self.calls += 1
            started.set()
            assert done.wait(5)
            return {
                "release_id": release,
                "currency": currency,
                "num_for_sale": 5,
                "lowest_item_price": "5.00",
            }

    reader = Reader()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(alerts.poll_once, reader)
        assert started.wait(5)
        assert alerts.set_removed(1, removed=True, expected_revision=1) == 2
        done.set()
        result = pending.result(timeout=5)
    assert result["status"] == "discarded" and reader.calls == 1
    assert contents(path)["discogs_price_outbox"] == []
    alerts.clock = lambda: 2000
    assert alerts.poll_once(reader) == {"status": "idle", "requests": 0}
    assert reader.calls == 1


def test_queued_send_revoked_and_already_dispatched_result_preserved(integration):
    path, _, _, _, _, _ = integration
    alerts = AggregatePriceAlerts(path, clock=lambda: 1000)
    assert (
        alerts.observe(
            1,
            1,
            {"release_id": 1, "currency": "USD", "num_for_sale": 5, "lowest_item_price": "5.00"},
            observed_at=1000,
        )
        == "low"
    )
    binding = NotificationBinding(provider_id="ntfy", destination_ref="synthetic")
    reservation = alerts.claim(binding)
    assert reservation is not None
    assert alerts.set_removed(1, removed=True, expected_revision=1) == 2
    assert contents(path)["discogs_price_outbox"][0][6] == "uncertain"
    assert alerts.claim(binding) is None
    assert not alerts.delivery_current(reservation)
    assert alerts.finish(reservation, "accepted")["state"] == "uncertain"
    assert alerts.claim(binding) is None
    assert WatchBackend(alerts).snapshot()["watches"][0]["notifications"]["uncertain"] == 1


@pytest.mark.parametrize("removed,enabled", [(1, False), ("false", False), (True, True)])
def test_invalid_projection_state_fails_closed(integration, removed, enabled):
    _, watch, _, _, _, _ = integration
    state = watch.snapshot()
    state["watches"][0].update(removed=removed, enabled=enabled)
    with pytest.raises(ValueError):
        project_snapshot(state)


def test_retained_watch_limit_still_counts_removed(integration):
    path, watch, _, _, _, _ = integration
    assert remove(integration)[0] == "200 OK"
    alerts = AggregatePriceAlerts.for_existing_store(path)
    rule = PriceLeadRule(
        release_id=99, public_title="Synthetic", currency="USD", item_price_ceiling="1"
    )
    with pytest.raises(ValueError, match="limit"):
        alerts.save_rule(rule, expected_revision=0, maximum_rules=21)
    assert len(watch.snapshot()["watches"]) == 21


def test_action_fields_are_copied_and_current_bounded_revision_is_required():
    payload = {"action": "remove", "release": "1", "revision": 1}
    assert mutation_fields(payload) == payload and mutation_fields(payload) is not payload


@pytest.mark.parametrize("kind,release", [("restore", "1"), ("remove", "99"), ("restore", "99")])
def test_missing_and_not_removed_targets_fail_without_changes(integration, kind, release):
    path, watch, _, _, _, _ = integration
    before = contents(path)
    with pytest.raises(RuntimeError):
        watch.save(action(kind=kind, release=release))
    assert contents(path) == before


def test_mutation_missing_csrf_and_get_never_change_watches(integration):
    path, _, _, _, app, _ = integration
    before = contents(path)
    assert request(app, "/watches/save", action(csrf="not-a-token"))[0] == "403 Forbidden"
    # Lifecycle GETs are not operations, even with a valid authenticated principal.
    assert request(app, "/watches/save")[0] == "404 Not Found"
    assert contents(path) == before


def test_same_revision_concurrent_removals_and_restores_have_one_winner(integration):
    _, watch, _, _, _, _ = integration
    for kind, revision in [("remove", 1), ("restore", 2)]:
        barrier = Barrier(2)

        def transition(_, barrier=barrier, kind=kind, revision=revision):
            barrier.wait(timeout=5)
            try:
                return watch.save(action(kind=kind, revision=revision))
            except RuntimeError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(transition, range(2)))
        assert sorted(map(str, outcomes)) == [str(revision + 1), "conflict"]
    saved = watch.snapshot()["watches"][0]
    assert saved["revision"] == 3 and not saved["removed"] and not saved["enabled"]
