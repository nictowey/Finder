import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from pydantic import ValidationError

from finder.execution_guard import (
    ExecutionGuard,
    ExecutionRequest,
    GuardBlocked,
    MockCheckoutAdapter,
    SyntheticOutcome,
)


def request(**changes):
    value = {
        "release_id": 123,
        "idempotency_key": str(uuid4()),
        "target_active": True,
        "eligible_for_review": True,
    }
    value.update(changes)
    return value


@pytest.fixture
def guard(tmp_path):
    result = ExecutionGuard(tmp_path / "execution.sqlite3", create=True)
    result.set_kill_switch(False)
    return result


def test_new_ledger_starts_killed_and_never_authorizes_purchase(tmp_path):
    guard = ExecutionGuard(tmp_path / "execution.sqlite3", create=True)
    snapshot = guard.snapshot()
    assert snapshot["kill_switch_on"] is True
    assert snapshot["mode"] == "synthetic_only"
    assert snapshot["purchase_allowed"] is False
    assert snapshot["attempts"] == []
    assert [row["event"] for row in snapshot["audit"]] == ["initialized_killed"]
    with pytest.raises(GuardBlocked, match="kill_switch_on"):
        guard.execute(request(), MockCheckoutAdapter())
    assert guard.snapshot() == snapshot


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"target_active": False}, "target_inactive"),
        ({"already_owned": True}, "already_owned"),
        ({"eligible_for_review": False}, "not_eligible_for_review"),
        ({"live_requested": True}, "live_capability_unsupported"),
    ],
)
def test_policy_fails_closed_without_reservation_or_adapter_call(
    guard, monkeypatch, changes, reason
):
    def forbidden(*args):
        pytest.fail("Adapter must not be called.")

    monkeypatch.setattr(MockCheckoutAdapter, "simulate", forbidden)
    with pytest.raises(GuardBlocked, match=reason):
        guard.execute(request(**changes), MockCheckoutAdapter())
    assert not guard.snapshot()["attempts"]


def test_eligibility_defaults_to_false(guard):
    value = request()
    del value["eligible_for_review"]
    with pytest.raises(GuardBlocked, match="not_eligible_for_review"):
        guard.reserve(value)


def test_live_config_toggle_is_not_an_enable_switch(tmp_path):
    path = tmp_path / "must-not-exist.sqlite3"
    with pytest.raises(GuardBlocked, match="live_capability_unsupported"):
        ExecutionGuard(path, create=True, live_enabled=True)
    assert not path.exists()


def test_arbitrary_adapter_and_mock_subclasses_are_not_execution_extensions(guard):
    class PretendSynthetic:
        synthetic_only = True

        def simulate(self, attempt):
            pytest.fail("Unknown adapter must never run, even if it advertises synthetic-only.")

    class MockSubclass(MockCheckoutAdapter):
        def simulate(self, attempt):
            pytest.fail("Subclass must never run.")

    for adapter in [PretendSynthetic(), MockSubclass(), None, object()]:
        with pytest.raises(GuardBlocked, match="adapter_capability_unsupported"):
            guard.execute(request(), adapter)
    assert guard.snapshot()["attempts"] == []


def test_duplicate_click_and_restart_return_same_attempt_without_repeat(guard, monkeypatch):
    calls = []

    def simulate(self, attempt):
        calls.append(attempt)
        return SyntheticOutcome.CONFIRMED

    monkeypatch.setattr(MockCheckoutAdapter, "simulate", simulate)
    value = request()
    reserved = guard.reserve(value)
    assert guard.reserve(value) == reserved
    assert reserved.state == "reserved"
    result = guard.execute(value, MockCheckoutAdapter())
    assert result.state == "synthetic_confirmed"
    assert result.purchase_allowed is False
    assert guard.execute(value, MockCheckoutAdapter()) == result
    reopened = ExecutionGuard(guard.path)
    assert reopened.execute(value, MockCheckoutAdapter()) == result
    assert reopened.get_attempt(value["idempotency_key"]) == result
    assert len(calls) == 1
    assert [row["event"] for row in reopened.snapshot()["audit"]] == [
        "initialized_killed",
        "kill_disabled",
        "reserved",
        "mock_started",
        "synthetic_confirmed",
    ]


def test_key_cannot_be_reused_for_a_different_release(guard):
    value = request()
    guard.reserve(value)
    with pytest.raises(GuardBlocked, match="idempotency_conflict"):
        guard.reserve({**value, "release_id": 456})


@pytest.mark.parametrize("outcome", list(SyntheticOutcome))
def test_all_outcomes_keep_the_one_copy_slot(guard, outcome):
    value = request()
    attempt = guard.execute(value, MockCheckoutAdapter(outcome))
    assert attempt.state == outcome.value
    assert guard.execute(value, MockCheckoutAdapter()) == attempt
    with pytest.raises(GuardBlocked, match="one_copy_reserved"):
        guard.execute(request(), MockCheckoutAdapter())


def test_exception_is_durable_uncertainty_and_never_leaks_payload(guard, monkeypatch):
    sensitive = "seller_text=SECRET; Authorization: private-credential"
    calls = []

    def broken(self, attempt):
        calls.append(attempt)
        raise RuntimeError(sensitive)

    monkeypatch.setattr(MockCheckoutAdapter, "simulate", broken)
    value = request()
    first = guard.execute(value, MockCheckoutAdapter())
    assert first.state == "uncertain"
    assert ExecutionGuard(guard.path).execute(value, MockCheckoutAdapter()) == first
    assert len(calls) == 1
    assert sensitive not in repr(guard.snapshot())
    assert sensitive.encode() not in guard.path.read_bytes()
    assert value["idempotency_key"].encode() not in guard.path.read_bytes()


def test_invalid_adapter_return_is_uncertain_not_confirmed(guard, monkeypatch):
    monkeypatch.setattr(MockCheckoutAdapter, "simulate", lambda *args: {"order": "secret"})
    result = guard.execute(request(), MockCheckoutAdapter())
    assert result.state == "uncertain"
    assert "secret" not in repr(guard.snapshot())


def test_crash_after_durable_start_cannot_retry_on_restart(guard, monkeypatch):
    calls = []

    def crash(self, attempt):
        calls.append(attempt)
        raise KeyboardInterrupt

    monkeypatch.setattr(MockCheckoutAdapter, "simulate", crash)
    value = request()
    with pytest.raises(KeyboardInterrupt):
        guard.execute(value, MockCheckoutAdapter())
    reopened = ExecutionGuard(guard.path)
    attempt = reopened.get_attempt(value["idempotency_key"])
    assert attempt.state == "attempting"
    assert reopened.execute(value, MockCheckoutAdapter()) == attempt
    assert len(calls) == 1
    with pytest.raises(GuardBlocked, match="one_copy_reserved"):
        reopened.reserve(request())


def test_killing_or_pausing_after_reservation_blocks_execution(guard, monkeypatch):
    def forbidden(*args):
        pytest.fail("Adapter must not be called.")

    monkeypatch.setattr(MockCheckoutAdapter, "simulate", forbidden)
    value = request()
    original = guard.reserve(value)
    guard.set_kill_switch(True)
    with pytest.raises(GuardBlocked, match="kill_switch_on"):
        guard.execute(value, MockCheckoutAdapter())
    assert ExecutionGuard(guard.path).snapshot()["kill_switch_on"] is True
    guard.set_kill_switch(False)
    with pytest.raises(GuardBlocked, match="target_inactive"):
        guard.execute({**value, "target_active": False}, MockCheckoutAdapter())
    with pytest.raises(GuardBlocked, match="already_owned"):
        guard.execute({**value, "already_owned": True}, MockCheckoutAdapter())
    assert guard.get_attempt(value["idempotency_key"]) == original


def test_competing_release_claims_have_only_one_winner(guard):
    barrier = Barrier(8)

    def claim(_):
        instance = ExecutionGuard(guard.path)
        barrier.wait(timeout=5)
        try:
            return instance.reserve(request()).state
        except GuardBlocked as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    assert results.count("reserved") == 1
    assert results.count("one_copy_reserved") == 7
    assert len(guard.snapshot()["attempts"]) == 1
    assert sum(row["event"] == "reserved" for row in guard.snapshot()["audit"]) == 1


def test_concurrent_same_click_runs_mock_only_once(guard, monkeypatch):
    barrier = Barrier(8)
    entered = Event()
    finish = Event()
    calls = []

    def simulate(self, attempt):
        calls.append(attempt)
        entered.set()
        assert finish.wait(timeout=5)
        return SyntheticOutcome.CONFIRMED

    monkeypatch.setattr(MockCheckoutAdapter, "simulate", simulate)
    value = request()

    def execute(_):
        instance = ExecutionGuard(guard.path)
        barrier.wait(timeout=5)
        return instance.execute(value, MockCheckoutAdapter())

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(execute, i) for i in range(8)]
        assert entered.wait(timeout=5)
        # While the first mock is in flight, another process sees a consumed claim.
        duplicate = ExecutionGuard(guard.path).execute(value, MockCheckoutAdapter())
        assert duplicate.state == "attempting"
        finish.set()
        results = [future.result(timeout=5) for future in futures]
    assert len(calls) == 1
    assert len({attempt.attempt_id for attempt in results}) == 1
    assert guard.get_attempt(value["idempotency_key"]).state == "synthetic_confirmed"


@pytest.mark.parametrize(
    "changes",
    [
        {"release_id": True},
        {"release_id": 0},
        {"release_id": -1},
        {"release_id": 9007199254740992},
        {"release_id": "123"},
        {"release_id": 1.0},
        {"idempotency_key": ""},
        {"idempotency_key": "secret"},
        {"idempotency_key": "0" * 36},
        {"idempotency_key": "00000000-0000-0000-0000-000000000000"},
        {"idempotency_key": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"},
        {"idempotency_key": 123},
        {"source": "discogs_public_listing"},
        {"source": "live"},
        {"target_active": "true"},
        {"target_active": 1},
        {"eligible_for_review": "true"},
        {"already_owned": "false"},
        {"live_requested": "false"},
        {"credentials": "private-value"},
        {"seller_text": "untrusted text"},
    ],
)
def test_malformed_input_has_no_durable_side_effect(guard, changes):
    before = guard.snapshot()
    with pytest.raises(ValidationError):
        guard.execute(request(**changes), MockCheckoutAdapter())
    assert guard.snapshot() == before


def test_unsafe_constructed_model_is_revalidated(guard):
    value = ExecutionRequest.model_construct(**request(target_active="true"))
    with pytest.raises(ValidationError):
        guard.reserve(value)
    assert not guard.snapshot()["attempts"]


def test_validation_error_text_does_not_echo_rejected_credentials(guard):
    with pytest.raises(ValidationError) as error:
        guard.reserve(request(credentials="private-credential-value"))
    assert "private-credential-value" not in str(error.value)


@pytest.mark.parametrize("value", [None, "off", 0, 1, [], {}])
def test_kill_switch_requires_actual_boolean(guard, value):
    with pytest.raises(ValueError, match="boolean"):
        guard.set_kill_switch(value)


@pytest.mark.parametrize("outcome", [None, "synthetic_confirmed", "real", {}, True])
def test_mock_requires_an_explicit_outcome_enum(outcome):
    with pytest.raises(ValueError, match="synthetic"):
        MockCheckoutAdapter(outcome)


def test_audit_is_append_only_and_slots_cannot_reset(guard):
    attempt = guard.execute(request(), MockCheckoutAdapter())
    before = guard.snapshot()
    with sqlite3.connect(guard.path) as db:
        for statement in (
            "DELETE FROM audit",
            "UPDATE audit SET event='kill_enabled'",
            "INSERT OR REPLACE INTO audit SELECT * FROM audit LIMIT 1",
            "DELETE FROM attempts",
            "UPDATE attempts SET state='reserved'",
            "INSERT OR REPLACE INTO attempts SELECT * FROM attempts LIMIT 1",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement)
    assert guard.snapshot() == before
    for row in before["audit"]:
        assert set(row) == {"sequence", "occurred_at", "attempt_id", "event"}
        assert row["attempt_id"] in (None, attempt.attempt_id)
    assert "idempotency_key" not in json.dumps(before["audit"])


def test_unrelated_database_is_rejected_unchanged(tmp_path):
    path = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE precious(value TEXT)")
        db.execute("INSERT INTO precious VALUES ('keep me')")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Not an execution ledger"):
        ExecutionGuard(path)
    assert path.read_bytes() == before
    with pytest.raises(FileExistsError):
        ExecutionGuard(path, create=True)
    assert path.read_bytes() == before


def test_missing_file_and_symlinks_are_rejected(tmp_path, guard):
    with pytest.raises(ValueError, match="does not exist"):
        ExecutionGuard(tmp_path / "absent.sqlite3")
    link = tmp_path / "link.sqlite3"
    link.symlink_to(guard.path)
    with pytest.raises(ValueError, match="regular"):
        ExecutionGuard(link)
    with pytest.raises(ValueError, match="regular"):
        ExecutionGuard(tmp_path / ".env", create=True)
    assert not (tmp_path / ".env").exists()


def test_unknown_attempt_returns_none(guard):
    assert guard.get_attempt(str(uuid4())) is None


def test_corrupt_missing_kill_state_fails_closed(guard):
    with sqlite3.connect(guard.path) as db:
        db.execute("DELETE FROM control")
    with pytest.raises(GuardBlocked, match="invalid_kill_switch_state"):
        guard.reserve(request())
    with pytest.raises(GuardBlocked, match="invalid_kill_switch_state"):
        ExecutionGuard(guard.path)
