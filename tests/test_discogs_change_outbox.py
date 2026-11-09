"""Synthetic in-app alerts; no browser, notification transport or live provider."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from test_cloud_app import ORIGIN, verifier
from test_discogs_release_snapshots import ingest, listing, observed
from test_discogs_rules import target

from finder.cloud_app import CloudApp
from finder.discogs_change_outbox import InAppChangeOutbox
from finder.discogs_release_snapshots import ReleaseSnapshots
from finder.discogs_rules import RulesStore


@pytest.fixture
def setup(tmp_path):
    database = tmp_path / "rules.db"
    RulesStore(database, create=True).close()
    CloudApp(database, ORIGIN, verifier())
    store = RulesStore(database)
    store.save(target().model_dump(), 0)
    with store.db:
        store.db.execute("UPDATE cloud_control SET killed=0,revision=1")
    store.close()
    clock = [datetime(2026, 10, 25, tzinfo=UTC).timestamp()]
    engine = ReleaseSnapshots(database, clock=lambda: clock[0])
    return database, engine, clock


def test_change_outbox_restarts_without_duplicates_and_acknowledges_idempotently(setup):
    database, engine, clock = setup
    outbox = InAppChangeOutbox(database, clock=lambda: clock[0])
    assert outbox.materialize(expected_rules_revision=1, expected_control_revision=1) == 0
    ingest(engine, observed(clock))
    assert outbox.materialize(expected_rules_revision=1, expected_control_revision=1) == 0

    clock[0] += 1
    ingest(engine, observed(clock, [listing(1002)]), baseline=1)
    assert outbox.materialize(expected_rules_revision=1, expected_control_revision=1) == 2
    first = outbox.pending()
    assert {(row["kind"], row["listing_id"]) for row in first} == {
        ("new", "1002"),
        ("disappeared", "1001"),
    }
    assert all(row["delivery"] == "in_app_only" for row in first)
    assert all(row["purchase_allowed"] is False for row in first)
    assert all(row["qualifies_all_in"] is False for row in first)
    assert outbox.materialize(expected_rules_revision=1, expected_control_revision=1) == 2

    restarted = InAppChangeOutbox(database, clock=lambda: clock[0])
    assert restarted.pending() == first
    sequence = first[0]["change_sequence"]
    result = restarted.acknowledge(
        [sequence], expected_rules_revision=1, expected_control_revision=1
    )
    assert result == {"acknowledged_change_sequences": [sequence], "messages_sent": 0}
    restarted.acknowledge([sequence], expected_rules_revision=1, expected_control_revision=1)
    assert [row["change_sequence"] for row in restarted.pending()] == [first[1]["change_sequence"]]
    assert sequence not in engine.snapshot()["pending_change_ids"]
    assert restarted.materialize(expected_rules_revision=1, expected_control_revision=1) == 1


def test_outbox_revision_and_missing_sequence_fail_atomically(setup):
    database, engine, clock = setup
    ingest(engine, observed(clock))
    clock[0] += 1
    ingest(engine, observed(clock, [listing(1002)]), baseline=1)
    outbox = InAppChangeOutbox(database, clock=lambda: clock[0])
    with pytest.raises(RuntimeError):
        outbox.materialize(expected_rules_revision=0, expected_control_revision=1)
    assert outbox.pending() == []
    outbox.materialize(expected_rules_revision=1, expected_control_revision=1)
    sequences = [row["change_sequence"] for row in outbox.pending()]
    with pytest.raises(ValueError):
        outbox.acknowledge(
            [sequences[0], 999999],
            expected_rules_revision=1,
            expected_control_revision=1,
        )
    assert {row["change_sequence"] for row in outbox.pending()} == set(sequences)
    with pytest.raises(RuntimeError):
        outbox.acknowledge(sequences, expected_rules_revision=1, expected_control_revision=0)
    assert len(outbox.pending()) == 2


def test_outbox_prunes_expired_change_references_without_delivery_claim(setup):
    database, engine, clock = setup
    ingest(engine, observed(clock))
    clock[0] += 1
    ingest(engine, observed(clock, [listing(1002)]), baseline=1)
    outbox = InAppChangeOutbox(database, clock=lambda: clock[0])
    assert outbox.materialize(expected_rules_revision=1, expected_control_revision=1) == 2
    store = RulesStore(database)
    with store.db:
        store.db.execute("DELETE FROM discogs_release_changes")
    store.close()
    assert outbox.materialize(expected_rules_revision=1, expected_control_revision=1) == 0
    assert outbox.pending() == []


def test_two_workers_materialize_one_change_sequence_each(setup):
    database, engine, clock = setup
    ingest(engine, observed(clock))
    clock[0] += 1
    ingest(engine, observed(clock, [listing(1002)]), baseline=1)
    gate = Barrier(2)

    def enqueue(_):
        gate.wait()
        return InAppChangeOutbox(database, clock=lambda: clock[0]).materialize(
            expected_rules_revision=1, expected_control_revision=1
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(enqueue, range(2))) == [2, 2]
    store = RulesStore(database)
    assert store.db.execute("SELECT count(*) FROM discogs_change_outbox").fetchone()[0] == 2
    store.close()


def test_existing_snapshot_acknowledgment_never_resurrects_in_app_alert(setup):
    database, engine, clock = setup
    ingest(engine, observed(clock))
    clock[0] += 1
    changed = ingest(engine, observed(clock, [listing(1002)]), baseline=1)
    engine.acknowledge(
        changed["change_ids"], expected_rules_revision=1, expected_control_revision=1
    )
    outbox = InAppChangeOutbox(database, clock=lambda: clock[0])
    assert outbox.materialize(expected_rules_revision=1, expected_control_revision=1) == 0
    assert outbox.pending() == []
