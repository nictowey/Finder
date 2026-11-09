"""Entirely synthetic already-rendered inputs; no browser, provider or notification calls."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from test_cloud_app import ORIGIN, verifier
from test_discogs_rules import target

from finder.cloud_app import CloudApp
from finder.discogs_monitor import ReleaseMonitor
from finder.discogs_release_snapshots import (
    FIELD_NAMES,
    MAX_AGE_SECONDS,
    RETENTION_SECONDS,
    ReleaseSnapshots,
)
from finder.discogs_rules import RulesStore


@pytest.fixture
def setup(tmp_path):
    database = tmp_path / "rules.db"
    RulesStore(database, create=True).close()
    app = CloudApp(database, ORIGIN, verifier())
    store = RulesStore(database)
    store.save(target().model_dump(), 0)
    with store.db:
        store.db.execute("UPDATE cloud_control SET killed=0,revision=1")
    store.close()
    clock = [datetime(2026, 10, 24, tzinfo=UTC).timestamp()]
    engine = ReleaseSnapshots(database, clock=lambda: clock[0])
    return engine, clock, app


def listing(ident, **fields):
    return {
        "listing_id": str(ident),
        "url": f"https://www.discogs.com/sell/item/{ident}",
        "fields": {**{name: None for name in FIELD_NAMES}, **fields},
    }


def observed(clock, rows=None, **updates):
    rows = [listing(1001, price="$10.00", media="Very Good Plus (VG+)")] if rows is None else rows
    return {
        "source": "discogs_rendered_release",
        "purchase_allowed": False,
        "release_id": 123,
        "url": "https://www.discogs.com/sell/release/123",
        "observed_at": datetime.fromtimestamp(clock[0], UTC).isoformat(),
        "status": "observed",
        "complete": True,
        "range_start": 1 if rows else 0,
        "range_end": len(rows),
        "total": len(rows),
        "page": 1,
        "has_next": False,
        "rows": rows,
        "assertions": dict(
            release_identity=True, listing_container=True, pagination=True, stable=True
        ),
        **updates,
    }


def ingest(engine, value, baseline=0, rules=1, control=1):
    return engine.ingest(
        value,
        expected_rules_revision=rules,
        expected_control_revision=control,
        expected_baseline_revision=baseline,
    )


def release_state(engine):
    return engine.snapshot()["releases"][0]


def table_counts(engine):
    store = RulesStore(engine.database)
    try:
        return {
            table: store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "discogs_release_baselines",
                "discogs_release_seen",
                "discogs_release_changes",
            )
        }
    finally:
        store.close()


def test_initial_and_unchanged_observations_do_not_create_alerts(setup):
    engine, clock, _ = setup
    first = ingest(engine, observed(clock))
    assert first["status"] == "baseline" and first["change_ids"] == []
    clock[0] += 1
    second = ingest(engine, observed(clock), baseline=1)
    assert second["status"] == "unchanged" and second["baseline_revision"] == 2
    state = engine.snapshot()
    assert state["changes"] == [] and state["pending_change_ids"] == []
    assert state["source_verified"] is False and state["notifications_enabled"] is False
    assert state["purchase_allowed"] is False and state["qualifies_all_in"] is False
    assert release_state(engine)["fresh"] is True


def test_same_count_substitution_is_detected_and_not_claimed_as_sale_or_same_copy(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock, [listing(1001, price="$10")]))
    clock[0] += 1
    result = ingest(engine, observed(clock, [listing(1002, price="$10")]), baseline=1)
    assert result["status"] == "changed" and len(result["change_ids"]) == 2
    changes = engine.snapshot()["changes"]
    assert {(row["kind"], row["listing_id"]) for row in changes} == {
        ("new", "1002"),
        ("disappeared", "1001"),
    }
    assert all(not row["purchase_allowed"] and not row["pressing_verified"] for row in changes)
    assert "not a sale" in engine.snapshot()["warning"]
    # A new ID with identical displayed fields remains distinct, never deduped as the old copy.
    assert release_state(engine)["baseline"]["rows"][0]["listing_id"] == "1002"


def test_reappeared_same_id_is_distinct_from_new_relisting_id(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock, [listing(1001)]))
    clock[0] += 1
    ingest(engine, observed(clock, []), baseline=1)
    clock[0] += 1
    ingest(engine, observed(clock, [listing(1001), listing(1002)]), baseline=2)
    assert {(row["kind"], row["listing_id"]) for row in engine.snapshot()["changes"]} == {
        ("disappeared", "1001"),
        ("reappeared", "1001"),
        ("new", "1002"),
    }


def test_changed_fields_only_compare_known_values_and_normalize_spacing(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock, [listing(1001, price="$10", seller=" Example  Seller ")]))
    clock[0] += 1
    ingest(
        engine,
        observed(clock, [listing(1001, price="$12", seller="Example Seller", shipping="$2")]),
        baseline=1,
    )
    changes = engine.snapshot()["changes"]
    assert len(changes) == 1 and changes[0]["kind"] == "changed"
    assert changes[0]["fields"] == {"price": {"before": "$10", "after": "$12"}}
    clock[0] += 1
    ingest(
        engine,
        observed(clock, [listing(1001, price=None, seller="Example Seller", shipping=None)]),
        baseline=2,
    )
    assert len(engine.snapshot()["changes"]) == 1
    clock[0] += 1
    ingest(
        engine, observed(clock, [listing(1001, price="$14", seller="Example Seller")]), baseline=3
    )
    assert len(engine.snapshot()["changes"]) == 1


@pytest.mark.parametrize(
    "status,code",
    [
        ("incomplete", "pagination"),
        ("incomplete", "loading"),
        ("incomplete", "racing"),
        ("error", "blocked"),
    ],
)
def test_failed_partial_loading_and_racing_preserve_last_success(setup, status, code):
    engine, clock, _ = setup
    ingest(engine, observed(clock))
    baseline = release_state(engine)["baseline"]
    clock[0] += 1
    attempt = observed(
        clock,
        [],
        status=status,
        complete=False,
        range_start=None,
        range_end=None,
        total=None,
        page=None,
        has_next=None,
        error_code=code,
    )
    result = ingest(engine, attempt, baseline=1)
    state = release_state(engine)
    assert result["status"] == "preserved" and result["baseline_revision"] == 1
    assert state["baseline"] == baseline and state["last_status"] == status
    assert state["fresh"] is False and state["error_code"] == code
    assert engine.snapshot()["changes"] == []
    clock[0] += 1
    assert ingest(engine, observed(clock), baseline=1)["status"] == "unchanged"


def test_verified_partial_rows_are_reportable_but_never_replace_a_baseline(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock, [listing(1001, price="$10")]))
    baseline = release_state(engine)["baseline"]
    clock[0] += 1
    partial = observed(
        clock,
        [listing(1002, price="$9")],
        status="incomplete",
        complete=False,
        range_start=1,
        range_end=1,
        total=3,
        has_next=True,
        error_code="additional_page_required",
    )
    result = ingest(engine, partial, baseline=1)
    assert result["status"] == "preserved"
    assert release_state(engine)["baseline"] == baseline
    assert release_state(engine)["fresh"] is False
    assert engine.snapshot()["changes"] == []
    assert engine.snapshot()["purchase_allowed"] is False

    for updates in (
        {"error_code": "loading"},
        {"has_next": False},
        {"range_end": 2},
        {"total": 1},
        {"assertions": {**partial["assertions"], "stable": False}},
        {"status": "error"},
    ):
        invalid = {**partial, **updates}
        with pytest.raises(ValueError):
            ingest(engine, invalid, baseline=1)
    assert release_state(engine)["baseline"] == baseline


def test_failure_without_baseline_does_not_create_empty_success(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock, [], status="error", complete=False, error_code="blocked"))
    assert release_state(engine)["baseline"] is None
    assert release_state(engine)["baseline_revision"] == 0
    clock[0] += 1
    assert ingest(engine, observed(clock), baseline=0)["status"] == "baseline"


@pytest.mark.parametrize(
    "updates",
    [
        {"total": 2},
        {"range_start": 2},
        {"range_end": 2},
        {"has_next": True},
        {"page": 2},
        {"total": None},
        {"has_next": None},
        {"status": "error"},
        {"status": "incomplete"},
        {"complete": False},
        {"error_code": "loading"},
        {"rows": []},
    ],
)
def test_incomplete_or_inconsistent_claim_cannot_replace_baseline(setup, updates):
    engine, clock, _ = setup
    ingest(engine, observed(clock))
    before = release_state(engine)
    clock[0] += 1
    value = observed(clock)
    value.update(updates)
    with pytest.raises(ValueError):
        ingest(engine, value, baseline=1)
    assert release_state(engine)["baseline"] == before["baseline"]
    assert release_state(engine)["last_attempt_observed_at"] == before["last_attempt_observed_at"]


@pytest.mark.parametrize(
    "assertion", ["release_identity", "listing_container", "pagination", "stable"]
)
def test_each_dom_assertion_is_required_for_complete(setup, assertion):
    engine, clock, _ = setup
    value = observed(clock)
    value["assertions"][assertion] = False
    with pytest.raises(ValueError):
        ingest(engine, value)
    assert table_counts(engine)["discogs_release_baselines"] == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.update(source="authenticated_live"),
        lambda s: s.update(purchase_allowed=True),
        lambda s: s.update(purchase_allowed=0),
        lambda s: s.update(release_id=True),
        lambda s: s.update(release_id="123"),
        lambda s: s.update(url="https://www.discogs.com/sell/release/456"),
        lambda s: s.update(url="https://www.discogs.com/sell/release/123?page=1"),
        lambda s: s.update(url="https://discogs.com.evil.test/sell/release/123"),
        lambda s: s.update(observed_at="2026-10-24T00:00:00"),
        lambda s: s.update(observed_at="2026-10-24T01:00:00+01:00"),
        lambda s: s.update(total=True),
        lambda s: s.update(assertions={}),
        lambda s: s.update(extra="ignored"),
        lambda s: s["rows"][0].update(listing_id="001001"),
        lambda s: s["rows"][0].update(listing_id="9007199254740992"),
        lambda s: s["rows"][0].update(url="https://www.discogs.com/sell/item/1002"),
        lambda s: s["rows"][0]["fields"].update(price=10),
        lambda s: s["rows"][0]["fields"].update(seller="x" * 121),
        lambda s: s["rows"][0]["fields"].update(price="a\x00b"),
        lambda s: s.update(error_code="<private HTML>"),
    ],
)
def test_strict_identity_shape_and_bounds_reject_without_mutation(setup, mutate):
    engine, clock, _ = setup
    value = observed(clock)
    mutate(value)
    with pytest.raises(ValueError):
        ingest(engine, value)
    assert set(table_counts(engine).values()) == {0}


def test_duplicate_ids_are_not_silently_deduplicated(setup):
    engine, clock, _ = setup
    with pytest.raises(ValueError):
        ingest(engine, observed(clock, [listing(1001), listing(1001)]))
    assert table_counts(engine)["discogs_release_baselines"] == 0


def test_maximum_rows_and_encoded_bytes_are_bounded(setup, monkeypatch):
    engine, clock, _ = setup
    with pytest.raises(ValueError):
        ingest(engine, observed(clock, [listing(i + 1) for i in range(251)]))
    monkeypatch.setattr("finder.discogs_release_snapshots.MAX_BYTES", 10)
    with pytest.raises(ValueError):
        ingest(engine, observed(clock))
    assert table_counts(engine)["discogs_release_baselines"] == 0


@pytest.mark.parametrize("offset", [1, 86400, -MAX_AGE_SECONDS - 1])
def test_future_and_stale_timestamp_do_not_poison_watermark(setup, offset):
    engine, clock, _ = setup
    value = observed([clock[0] + offset])
    with pytest.raises(ValueError):
        ingest(engine, value)
    assert table_counts(engine)["discogs_release_baselines"] == 0
    assert ingest(engine, observed(clock))["status"] == "baseline"


def test_out_of_order_and_duplicate_observations_are_rejected(setup):
    engine, clock, _ = setup
    initial = observed(clock)
    ingest(engine, initial)
    with pytest.raises(RuntimeError):
        ingest(engine, initial, baseline=1)
    clock[0] += 10
    ingest(engine, observed(clock, [], status="incomplete", complete=False), baseline=1)
    with pytest.raises(RuntimeError):
        ingest(engine, observed([clock[0] - 5]), baseline=1)
    assert release_state(engine)["baseline_revision"] == 1
    assert release_state(engine)["baseline"] == {**initial, "error_code": None, "coverage_pages": 1}


@pytest.mark.parametrize("change", ["stopped", "restarted", "rules", "paused", "owned", "missing"])
def test_saved_target_rules_and_shared_kill_switch_checked_before_writes(setup, change):
    engine, clock, _ = setup
    store = RulesStore(engine.database)
    with store.db:
        if change == "stopped":
            store.db.execute("UPDATE cloud_control SET killed=1,revision=2")
        elif change == "restarted":
            store.db.execute("UPDATE cloud_control SET killed=0,revision=3")
        elif change == "missing":
            store.db.execute("DELETE FROM targets")
    if change in ("rules", "paused"):
        value = target().model_dump()
        value["enabled"] = change != "paused"
        store.save(value, 1)
    elif change == "owned":
        store.mark_owned(123, True, 1)
    store.close()
    with pytest.raises((ValueError, RuntimeError)):
        ingest(engine, observed(clock))
    assert set(table_counts(engine).values()) == {0}


@pytest.mark.parametrize("state", ["paused", "owned"])
def test_even_current_revisions_cannot_import_inactive_target(setup, state):
    engine, clock, _ = setup
    store = RulesStore(engine.database)
    if state == "owned":
        store.mark_owned(123, True, 1)
    else:
        store.save({**target().model_dump(), "enabled": False}, 1)
    store.close()
    with pytest.raises(ValueError):
        ingest(engine, observed(clock), rules=2)
    assert table_counts(engine)["discogs_release_baselines"] == 0


@pytest.mark.parametrize(
    "kwargs", [{"rules": True}, {"control": None}, {"baseline": -1}, {"baseline": "0"}]
)
def test_explicit_revision_types_required(setup, kwargs):
    engine, clock, _ = setup
    with pytest.raises(ValueError):
        ingest(engine, observed(clock), **kwargs)
    assert table_counts(engine)["discogs_release_baselines"] == 0


def test_racing_imports_allow_only_one_expected_baseline_revision(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock))
    clock[0] += 2
    gate = Barrier(2)

    def compete(ident):
        gate.wait()
        try:
            return ingest(engine, observed(clock, [listing(ident)]), baseline=1)["status"]
        except RuntimeError:
            return "discarded"

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(compete, [1002, 1003])) == ["changed", "discarded"]
    assert release_state(engine)["baseline_revision"] == 2
    assert len(engine.snapshot()["changes"]) == 2


def test_restart_keeps_baseline_changes_and_separate_acknowledgment(setup):
    engine, clock, app = setup
    ingest(engine, observed(clock))
    clock[0] += 1
    changed = ingest(engine, observed(clock, [listing(1002)]), baseline=1)
    reopened = ReleaseMonitor(app.database, clock=lambda: clock[0]).release_snapshots
    state = reopened.snapshot()
    assert state["pending_change_ids"] == sorted(changed["change_ids"], reverse=True)
    ids = changed["change_ids"]
    result = reopened.acknowledge(ids, expected_rules_revision=1, expected_control_revision=1)
    assert result["notifications_sent"] == 0
    reopened.acknowledge(ids, expected_rules_revision=1, expected_control_revision=1)
    assert reopened.snapshot()["pending_change_ids"] == []
    assert release_state(reopened)["baseline_revision"] == 2
    clock[0] += 1
    assert ingest(reopened, observed(clock, [listing(1002)]), baseline=2)["status"] == "unchanged"
    clock[0] += 1
    ingest(reopened, observed(clock, [listing(1003)]), baseline=3)
    assert len(reopened.snapshot()["pending_change_ids"]) == 2
    assert len(reopened.snapshot()["changes"]) == 4


def test_unacknowledged_change_survives_later_baseline_update(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock))
    clock[0] += 1
    event = ingest(engine, observed(clock, [listing(1002)]), baseline=1)["change_ids"]
    clock[0] += 1
    ingest(engine, observed(clock, [listing(1002)]), baseline=2)
    assert set(engine.snapshot()["pending_change_ids"]) == set(event)


def test_acknowledgment_is_atomic_and_revision_checked(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock))
    clock[0] += 1
    ids = ingest(engine, observed(clock, [listing(1002)]), baseline=1)["change_ids"]
    with pytest.raises(RuntimeError):
        engine.acknowledge(ids, expected_rules_revision=0, expected_control_revision=1)
    with pytest.raises(ValueError):
        engine.acknowledge([ids[0], 99999], expected_rules_revision=1, expected_control_revision=1)
    assert len(engine.snapshot()["pending_change_ids"]) == 2


def test_retention_bounds_history_and_events_without_expiring_successful_baseline(
    setup, monkeypatch
):
    engine, clock, _ = setup
    monkeypatch.setattr("finder.discogs_release_snapshots.MAX_CHANGES", 2)
    monkeypatch.setattr("finder.discogs_release_snapshots.MAX_SEEN", 2)
    ingest(engine, observed(clock))
    for revision in range(1, 4):
        clock[0] += 1
        ingest(engine, observed(clock, [listing(1001 + revision)]), baseline=revision)
    assert table_counts(engine)["discogs_release_changes"] == 2
    assert table_counts(engine)["discogs_release_seen"] == 2
    before = release_state(engine)["baseline"]
    clock[0] += RETENTION_SECONDS + 1
    state = engine.snapshot()
    assert state["changes"] == []
    assert state["releases"][0]["baseline"] == before
    assert state["releases"][0]["fresh"] is False
    assert table_counts(engine)["discogs_release_seen"] == 1


def test_no_auto_target_or_known_listing_configuration(setup):
    engine, clock, _ = setup
    before = RulesStore(engine.database)
    initial = copy.deepcopy(before.snapshot())
    before.close()
    ingest(engine, observed(clock))
    after = RulesStore(engine.database)
    try:
        assert after.snapshot() == initial
        assert after.db.execute("SELECT count(*) FROM discogs_poll").fetchone()[0] == 0
    finally:
        after.close()


def test_additive_tables_preserve_existing_rules_and_aggregate_state(setup):
    engine, clock, app = setup
    store = RulesStore(engine.database)
    with store.db:
        store.db.execute(
            "INSERT INTO discogs_poll(release_id,kind,reference,payload) "
            "VALUES(123,'release','123',?)",
            (json.dumps({"num_for_sale": 8}),),
        )
    baseline = store.snapshot()
    store.close()
    ingest(engine, observed(clock))
    ReleaseSnapshots(app.database)
    store = RulesStore(engine.database)
    try:
        assert store.snapshot() == baseline
        assert json.loads(store.db.execute("SELECT payload FROM discogs_poll").fetchone()[0]) == {
            "num_for_sale": 8
        }
    finally:
        store.close()


def test_snapshot_target_limit_preserves_existing_data(setup, monkeypatch):
    engine, clock, _ = setup
    monkeypatch.setattr("finder.discogs_release_snapshots.MAX_RELEASES", 1)
    ingest(engine, observed(clock))
    store = RulesStore(engine.database)
    store.save({**target().model_dump(), "release_id": 456}, 1)
    store.close()
    clock[0] += 1
    with pytest.raises(ValueError):
        ingest(
            engine,
            observed(clock, release_id=456, url="https://www.discogs.com/sell/release/456"),
            rules=2,
        )
    assert table_counts(engine)["discogs_release_baselines"] == 1
    assert engine.snapshot()["releases"][1]["baseline"] is None


def test_retired_history_is_not_an_unbounded_permanent_identity_claim(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock))
    clock[0] += 1
    ingest(engine, observed(clock, []), baseline=1)
    clock[0] += RETENTION_SECONDS + 1
    ingest(engine, observed(clock, [listing(1001)]), baseline=2)
    assert [row["kind"] for row in engine.snapshot()["changes"]] == ["new"]


def test_expired_change_id_never_reused_or_acknowledged_as_different_event(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock))
    clock[0] += 1
    expired = ingest(engine, observed(clock, []), baseline=1)["change_ids"]
    engine.acknowledge(expired, expected_rules_revision=1, expected_control_revision=1)
    clock[0] += RETENTION_SECONDS + 1
    current = ingest(engine, observed(clock, [listing(1002)]), baseline=2)["change_ids"]
    assert min(current) > max(expired)
    assert set(engine.snapshot()["pending_change_ids"]) == set(current)
    with pytest.raises(ValueError):
        engine.acknowledge(expired, expected_rules_revision=1, expected_control_revision=1)


def test_invalid_snapshot_after_success_cannot_advance_watermark(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock))
    clock[0] += 1
    with pytest.raises(ValueError):
        ingest(engine, observed([clock[0] + 86400]), baseline=1)
    assert ingest(engine, observed(clock), baseline=1)["status"] == "unchanged"


def test_kill_and_edit_after_observation_context_discard_without_writing(setup):
    engine, clock, _ = setup
    context = engine.snapshot()
    observation = observed(clock)
    store = RulesStore(engine.database)
    store.save({**target().model_dump(), "max_total": "60"}, 1)
    with store.db:
        store.db.execute("UPDATE cloud_control SET killed=1,revision=2")
    store.close()
    with pytest.raises(RuntimeError):
        engine.ingest(
            observation,
            expected_rules_revision=context["rules_revision"],
            expected_control_revision=context["control_revision"],
            expected_baseline_revision=context["releases"][0]["baseline_revision"],
        )
    assert set(table_counts(engine).values()) == {0}


@pytest.mark.parametrize("description", [None, "", " \n\t "])
def test_unavailable_seller_description_is_null_not_an_incomplete_snapshot(setup, description):
    engine, clock, _ = setup
    ingest(engine, observed(clock, [listing(1001, seller_description=description)]))
    baseline = release_state(engine)["baseline"]
    assert baseline["rows"][0]["fields"]["seller_description"] is None
    assert baseline["complete"] is True


def test_seller_description_is_optional_displayed_data_not_record_set_verification(setup):
    engine, clock, _ = setup
    value = observed(clock)
    del value["rows"][0]["fields"]["seller_description"]
    ingest(engine, value)
    assert release_state(engine)["baseline"]["rows"][0]["fields"]["seller_description"] is None
    clock[0] += 1
    result = ingest(
        engine, observed(clock, [listing(1001, seller_description=" No disc 1 ")]), baseline=1
    )
    assert result["purchase_allowed"] is False and result["pressing_verified"] is False
    assert result["status"] == "unchanged"
    assert (
        release_state(engine)["baseline"]["rows"][0]["fields"]["seller_description"] == "No disc 1"
    )
    clock[0] += 1
    ingest(
        engine,
        observed(clock, [listing(1001, seller_description="Both discs present")]),
        baseline=2,
    )
    assert engine.snapshot()["changes"][0]["fields"] == {
        "seller_description": {"before": "No disc 1", "after": "Both discs present"}
    }


@pytest.mark.parametrize("description", ["x" * 2001, 1, True, {"text": "record"}, "a\x00b"])
def test_seller_description_bound_and_type_fail_without_mutation(setup, description):
    engine, clock, _ = setup
    with pytest.raises(ValueError):
        ingest(engine, observed(clock, [listing(1001, seller_description=description)]))
    assert set(table_counts(engine).values()) == {0}


def test_maximum_seller_description_preserved_without_truncation(setup):
    engine, clock, _ = setup
    description = "x" * 2000
    ingest(engine, observed(clock, [listing(1001, seller_description=description)]))
    assert (
        release_state(engine)["baseline"]["rows"][0]["fields"]["seller_description"] == description
    )


def test_pre_description_stored_baselines_upgrade_as_unknown_without_false_change(setup):
    engine, clock, _ = setup
    ingest(engine, observed(clock))
    store = RulesStore(engine.database)
    with store.db:
        encoded = store.db.execute("SELECT baseline FROM discogs_release_baselines").fetchone()[0]
        old = json.loads(encoded)
        del old["rows"][0]["fields"]["seller_description"]
        store.db.execute("UPDATE discogs_release_baselines SET baseline=?", (json.dumps(old),))
    store.close()
    reopened = ReleaseSnapshots(engine.database, clock=lambda: clock[0])
    assert release_state(reopened)["baseline"]["rows"][0]["fields"]["seller_description"] is None
    clock[0] += 1
    value = observed(clock)
    value["rows"][0]["fields"]["seller_description"] = "Both records included"
    assert ingest(reopened, value, baseline=1)["status"] == "unchanged"
    assert reopened.snapshot()["changes"] == []
