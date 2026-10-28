"""Synthetic notification-state transitions; no provider or network sender exists."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from test_cloud_app import ORIGIN, verifier
from test_discogs_release_snapshots import ingest, listing, observed
from test_discogs_rules import target

from finder.cloud_app import CloudApp
from finder.discogs_notification_outbox import (
    BASE_BACKOFF,
    LEASE_SECONDS,
    NotificationBinding,
    NotificationOutbox,
    ProviderCapability,
)
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
    snapshots = ReleaseSnapshots(database, clock=lambda: clock[0])
    ingest(snapshots, observed(clock))
    clock[0] += 1
    ingest(snapshots, observed(clock, [listing(1002)]), baseline=1)
    binding = NotificationBinding("fake_push", "device_opaque_1")
    ready = ProviderCapability("fake_push", True)
    return database, snapshots, clock, binding, ready


def outbox(database, clock):
    return NotificationOutbox(database, clock=lambda: clock[0])


def test_default_provider_unavailable_and_acceptance_is_not_delivery(setup):
    database, snapshots, clock, binding, ready = setup
    queue = outbox(database, clock)
    assert queue.status() == {
        "configured": False,
        "provider_available": False,
        "sender_connected": False,
        "notifications_enabled": False,
        "provider_acceptance_is_delivery": False,
        "states": {},
        "purchase_allowed": False,
    }
    assert queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1) == 2
    assert queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1) == 2
    assert queue.claim(binding) is None
    assert queue.status(binding, ready)["sender_connected"] is False
    first = queue.claim(binding, ready)
    assert first["attempt"] == 1 and first["sent"] is False
    assert first["provider_id"] == binding.provider_id
    assert first["destination_ref"] == binding.destination_ref
    assert set(first["payload"]) == {"title", "body", "event_key", "purchase_allowed"}
    assert first["payload"]["purchase_allowed"] is False
    assert "1002" not in first["payload"]["body"]
    reopened = outbox(database, clock)
    assert reopened.enqueue(binding, expected_rules_revision=1, expected_control_revision=1) == 2
    assert reopened.record_accepted(first) == {
        "state": "accepted",
        "delivered": False,
        "next_attempt": clock[0],
    }
    assert reopened.record_accepted(first) == {"state": "accepted", "delivered": False}
    assert reopened.status(binding, ready)["states"] == {"accepted": 1, "pending": 1}
    assert len(snapshots.snapshot()["pending_change_ids"]) == 2


def test_known_nonacceptance_backs_off_and_eventually_stops(setup):
    database, _, clock, binding, ready = setup
    queue = outbox(database, clock)
    queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1)
    first = queue.claim(binding, ready)
    other = queue.claim(binding, ready)
    queue.record_accepted(other)
    for attempt in range(1, 6):
        claim = first if attempt == 1 else queue.claim(binding, ready)
        assert claim["attempt"] == attempt
        result = queue.record_known_failure(
            claim, "provider_rejected", retry_after_seconds=3600 if attempt == 1 else 0
        )
        assert result["delivered"] is False
        assert result["state"] == ("failed" if attempt == 5 else "retry_wait")
        assert queue.claim(binding, ready) is None
        if attempt == 1:
            assert result["next_attempt"] == clock[0] + 3600
        elif attempt < 5:
            assert result["next_attempt"] >= clock[0] + BASE_BACKOFF
        clock[0] = result["next_attempt"] + 1
    assert queue.status()["states"]["failed"] >= 1


def test_expired_or_uncertain_attempt_never_retries_automatically(setup):
    database, _, clock, binding, ready = setup
    queue = outbox(database, clock)
    queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1)
    first = queue.claim(binding, ready)
    clock[0] += LEASE_SECONDS + 1
    queue.reconcile()
    with pytest.raises(RuntimeError):
        queue.record_accepted(first)
    assert queue.status()["states"]["uncertain"] == 1
    assert queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1) == 2
    other = queue.claim(binding, ready)
    assert other["event_key"] != first["event_key"]
    assert queue.record_uncertain(other)["state"] == "uncertain"
    assert queue.claim(binding, ready) is None


def test_binding_change_and_stale_revision_cannot_reroute_event(setup):
    database, _, clock, binding, ready = setup
    queue = outbox(database, clock)
    with pytest.raises(RuntimeError):
        queue.enqueue(binding, expected_rules_revision=0, expected_control_revision=1)
    queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1)
    with pytest.raises(RuntimeError):
        queue.enqueue(
            NotificationBinding("fake_push", "other_device"),
            expected_rules_revision=1,
            expected_control_revision=1,
        )
    assert queue.claim(NotificationBinding("fake_push", "other_device"), ready) is None
    with pytest.raises(ValueError):
        NotificationBinding("fake_push", "person@example.com")
    with pytest.raises(ValueError):
        NotificationBinding("unavailable", "device_opaque_1")
    with pytest.raises(ValueError):
        ProviderCapability("fake_push", 1)


def test_concurrent_claims_and_revocation_preserve_one_attempt(setup):
    database, _, clock, binding, ready = setup
    queue = outbox(database, clock)
    queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1)
    gate = Barrier(2)

    def compete(_):
        gate.wait()
        return outbox(database, clock).claim(binding, ready)

    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = list(workers.map(compete, range(2)))
    assert all(claim is not None for claim in claims)
    assert claims[0]["event_key"] != claims[1]["event_key"]
    queue.revoke(binding)
    assert queue.status()["states"] == {"uncertain": 2}
    with pytest.raises(RuntimeError):
        queue.record_accepted(claims[0])
    assert queue.claim(binding, ready) is None


def test_kill_switch_blocks_even_synthetic_ready_provider(setup):
    database, _, clock, binding, ready = setup
    queue = outbox(database, clock)
    queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1)
    store = RulesStore(database)
    with store.db:
        store.db.execute("UPDATE cloud_control SET killed=1,revision=2")
    store.close()
    assert queue.claim(binding, ready) is None


def test_private_review_suppresses_unsent_provider_events(setup):
    database, snapshots, clock, binding, ready = setup
    queue = outbox(database, clock)
    queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1)
    reviewed = snapshots.snapshot()["pending_change_ids"]
    snapshots.acknowledge(reviewed, expected_rules_revision=1, expected_control_revision=1)
    queue.reconcile()
    assert queue.status()["states"] == {"revoked": 2}
    assert queue.claim(binding, ready) is None
    assert queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1) == 2


def test_terminal_old_binding_does_not_block_later_new_events(setup):
    database, snapshots, clock, binding, _ = setup
    queue = outbox(database, clock)
    queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1)
    queue.revoke(binding)
    new_binding = NotificationBinding("fake_push", "new_device")
    assert queue.enqueue(new_binding, expected_rules_revision=1, expected_control_revision=1) == 0
    clock[0] += 1
    ingest(snapshots, observed(clock, [listing(1003)]), baseline=2)
    assert queue.enqueue(new_binding, expected_rules_revision=1, expected_control_revision=1) == 2
    assert queue.status()["states"] == {"pending": 2, "revoked": 2}


def test_pausing_release_revokes_unsent_events(setup):
    database, _, clock, binding, ready = setup
    queue = outbox(database, clock)
    queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1)
    store = RulesStore(database)
    store.save({**target().model_dump(), "enabled": False}, 1)
    store.close()
    assert queue.claim(binding, ready) is None
    assert queue.status()["states"] == {"revoked": 2}


def test_pausing_during_claim_makes_late_acceptance_uncertain(setup):
    database, _, clock, binding, ready = setup
    queue = outbox(database, clock)
    queue.enqueue(binding, expected_rules_revision=1, expected_control_revision=1)
    claimed = queue.claim(binding, ready)
    store = RulesStore(database)
    store.save({**target().model_dump(), "enabled": False}, 1)
    store.close()
    with pytest.raises(RuntimeError):
        queue.record_accepted(claimed)
    assert queue.status()["states"] == {"revoked": 1, "uncertain": 1}
