"""Synthetic order lifecycle only: no browser, provider, cart, or payment."""

import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from finder.discogs_order_safety import (
    OrderSafetyStore,
    OrderStopped,
    SimulatedCrash,
    SyntheticAbsence,
    SyntheticFinalQuote,
    SyntheticOrderAdapter,
    SyntheticReceipt,
    _fingerprint,
)
from finder.discogs_rules import Observation, Target

GRANT = "123e4567-e89b-42d3-a456-426614174000"
INTENT = "123e4567-e89b-42d3-a456-426614174001"
OTHER = "123e4567-e89b-42d3-a456-426614174002"
DESTINATION = "123e4567-e89b-42d3-a456-426614174003"
PAYMENT = "123e4567-e89b-42d3-a456-426614174004"
ORDER = "123e4567-e89b-42d3-a456-426614174005"
NOW = datetime(2026, 10, 25, 12, tzinfo=UTC).timestamp()


@pytest.fixture
def context(tmp_path):
    target = Target.model_validate(
        {
            "release_id": 123,
            "min_media": "VG+",
            "min_sleeve": "VG",
            "min_seller_percent": 99.0,
            "min_feedback": 100,
            "max_total": "50",
            "currency": "USD",
            "destination_country": "US",
            "destination_postal": "03106",
        }
    )
    observation = Observation.model_validate(
        {
            "source": "synthetic",
            "listing_id": "222",
            "release_id": 123,
            "media": "NM",
            "sleeve": "VG+",
            "seller_percent": 100.0,
            "feedback": 200,
            "item": "40",
            "shipping": "5",
            "tax": "3",
            "fees": "2",
            "currency": "USD",
            "destination_country": "US",
            "destination_postal": "03106",
            "observed_at": datetime.fromtimestamp(NOW, UTC).isoformat(),
            "available": True,
            "pressing_evidence": "confirmed",
            "seller_comments": "Includes both discs.",
            "release_media_metadata": {"id": 123, "formats": [{"name": "Vinyl", "qty": "2"}]},
        }
    )
    quote = SyntheticFinalQuote.model_validate(
        {
            "mode": "synthetic_final_quote",
            "release_id": 123,
            "listing_id": "222",
            "rules_revision": 7,
            "item": "40",
            "shipping": "5",
            "tax": "3",
            "fees": "2",
            "total": "50",
            "currency": "USD",
            "destination_country": "US",
            "destination_postal": "03106",
            "destination_ref": DESTINATION,
            "payment_ref": PAYMENT,
            "observed_at": NOW,
            "expires_at": NOW + 120,
            "quantity": 1,
            "final_quote_verified": True,
        }
    )
    path = tmp_path / "order-safety.sqlite"
    store = OrderSafetyStore(path, create=True)
    store.issue_simulated_grant(GRANT, target, 7, "50", DESTINATION, PAYMENT, NOW + 3600, now=NOW)
    return path, store, target, observation, quote


def receipt(quote, **changes):
    data = dict(
        intent_id=INTENT,
        provider_order_id=ORDER,
        release_id=123,
        listing_id="222",
        total="50.00",
        currency="USD",
        quote_fingerprint=_fingerprint(quote.model_dump()),
        destination_ref=DESTINATION,
        payment_ref=PAYMENT,
    )
    data.update(changes)
    return SyntheticReceipt(**data)


def absence(quote, **changes):
    data = dict(
        intent_id=INTENT,
        release_id=123,
        listing_id="222",
        quote_fingerprint=_fingerprint(quote.model_dump()),
        destination_ref=DESTINATION,
        payment_ref=PAYMENT,
        checked_at=NOW + 1,
    )
    data.update(changes)
    return SyntheticAbsence(**data)


def prepare(context):
    _, store, target, observation, quote = context
    return store.prepare(GRANT, INTENT, target, 7, observation, quote, now=NOW)


def test_grant_is_owner_only_nonsecret_and_one_copy(context):
    path, store, target, observation, quote = context
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert b"card" not in path.read_bytes().lower()
    assert prepare(context)["state"] == "prepared"
    assert prepare(context)["state"] == "prepared"
    with pytest.raises(OrderStopped, match="one_copy_or_idempotency_conflict"):
        store.prepare(GRANT, OTHER, target, 7, observation, quote, now=NOW)
    assert OrderSafetyStore(path).status(INTENT)["state"] == "prepared"
    with pytest.raises(OrderStopped, match="synthetic_adapter_only"):
        store.simulate_attempt(INTENT, target, 7, quote, object(), now=NOW)


def test_revocation_stale_rules_and_expiration_block_attempt(context):
    _, store, target, _, quote = context
    prepare(context)
    adapter = SyntheticOrderAdapter(submit_result=receipt(quote))
    changed = target.model_copy(update={"min_media": "M"})
    with pytest.raises(OrderStopped, match="grant_missing_stale_revoked_or_expired"):
        store.simulate_attempt(INTENT, changed, 7, quote, adapter, now=NOW)
    with pytest.raises(OrderStopped, match="attempt_state_or_quote_changed"):
        store.simulate_attempt(
            INTENT, target, 7, quote.model_copy(update={"total": "49.00"}), adapter, now=NOW
        )
    with pytest.raises(OrderStopped, match="attempt_state_or_quote_changed"):
        store.simulate_attempt(INTENT, target, 7, quote, adapter, now=NOW + 120)
    store.revoke(GRANT, now=NOW + 1)
    with pytest.raises(OrderStopped, match="grant_missing_stale_revoked_or_expired"):
        store.simulate_attempt(INTENT, target, 7, quote, adapter, now=NOW + 2)
    assert adapter.submit_calls == 0


@pytest.mark.parametrize(
    "change",
    [
        {"total": "49.00"},
        {"tax": "4.00"},
        {"destination_ref": OTHER},
        {"payment_ref": OTHER},
        {"rules_revision": 8},
        {"expires_at": NOW},
    ],
)
def test_changed_quote_never_prepares(context, change):
    _, store, target, observation, quote = context
    with pytest.raises(OrderStopped):
        store.prepare(
            GRANT, INTENT, target, 7, observation, quote.model_copy(update=change), now=NOW
        )
    assert store.status(INTENT) is None


def test_missing_or_damaged_media_never_prepares(context):
    _, store, target, observation, quote = context
    for comment in ("No disc 1", "Record is warped and skips"):
        with pytest.raises(OrderStopped, match="candidate_not_eligible_for_simulation"):
            store.prepare(
                GRANT,
                INTENT,
                target,
                7,
                observation.model_copy(update={"seller_comments": comment}),
                quote,
                now=NOW,
            )


def test_competing_workers_fence_one_release_and_one_submit(context):
    path, store, target, observation, quote = context

    def reserve(intent):
        try:
            return OrderSafetyStore(path).prepare(
                GRANT, intent, target, 7, observation, quote, now=NOW
            )["state"]
        except OrderStopped as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, [INTENT, OTHER])) == [
            "one_copy_or_idempotency_conflict",
            "prepared",
        ]
    winner = INTENT if store.status(INTENT) else OTHER
    adapter = SyntheticOrderAdapter()

    def submit(_):
        try:
            return OrderSafetyStore(path).simulate_attempt(
                winner, target, 7, quote, adapter, now=NOW + 1
            )["state"]
        except OrderStopped as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, [1, 2])) == [
            "attempt_already_started_or_missing",
            "uncertain",
        ]
    assert adapter.submit_calls == 1


def test_crash_requires_lookup_and_never_resubmits(context):
    path, store, target, _, quote = context
    prepare(context)
    crashed = SyntheticOrderAdapter(submit_result=SimulatedCrash())
    with pytest.raises(SimulatedCrash):
        store.simulate_attempt(INTENT, target, 7, quote, crashed, now=NOW + 1)
    reopened = OrderSafetyStore(path)
    assert reopened.status(INTENT)["state"] == "submitting"
    with pytest.raises(OrderStopped, match="attempt_already_started_or_missing"):
        reopened.simulate_attempt(INTENT, target, 7, quote, crashed, now=NOW + 2)
    lookup = SyntheticOrderAdapter(lookup_result=receipt(quote))
    assert reopened.reconcile(INTENT, lookup, now=NOW + 2)["state"] == "confirmed"
    assert crashed.submit_calls == 1 and lookup.submit_calls == 0 and lookup.lookup_calls == 1


def test_unknown_mismatch_stays_fenced_until_read_only_reconciliation(context):
    _, store, target, _, quote = context
    prepare(context)
    adapter = SyntheticOrderAdapter(submit_result=receipt(quote, total="51.00"))
    result = store.simulate_attempt(INTENT, target, 7, quote, adapter, now=NOW)
    assert result["state"] == "uncertain"
    assert store.reconcile(INTENT, SyntheticOrderAdapter(), now=NOW + 1)["state"] == "uncertain"
    assert store.status(INTENT)["requires_reconciliation"]
    assert (
        store.reconcile(INTENT, SyntheticOrderAdapter(lookup_result=receipt(quote)), now=NOW + 1)[
            "state"
        ]
        == "confirmed"
    )
    assert adapter.submit_calls == 1


def test_verified_absent_still_retains_one_copy_slot(context):
    _, store, target, observation, quote = context
    prepare(context)
    assert (
        store.simulate_attempt(INTENT, target, 7, quote, SyntheticOrderAdapter(), now=NOW + 1)[
            "state"
        ]
        == "uncertain"
    )
    assert (
        store.reconcile(
            INTENT,
            SyntheticOrderAdapter(lookup_result=absence(quote, checked_at=NOW + 1.5)),
            now=NOW + 2,
        )["state"]
        == "absent"
    )
    with pytest.raises(OrderStopped, match="one_copy_or_idempotency_conflict"):
        store.prepare(GRANT, OTHER, target, 7, observation, quote, now=NOW + 1)


@pytest.mark.parametrize("lookup_kind", ["string", "listing", "fingerprint", "destination"])
def test_unsupported_or_mismatched_absence_stays_uncertain(context, lookup_kind):
    _, store, target, _, quote = context
    lookup = {
        "string": "confirmed_absent",
        "listing": absence(quote, listing_id="333"),
        "fingerprint": absence(quote, quote_fingerprint="0" * 64),
        "destination": absence(quote, destination_ref=OTHER),
    }[lookup_kind]
    prepare(context)
    store.simulate_attempt(INTENT, target, 7, quote, SyntheticOrderAdapter(), now=NOW)
    assert (
        store.reconcile(INTENT, SyntheticOrderAdapter(lookup_result=lookup), now=NOW + 2)["state"]
        == "uncertain"
    )


@pytest.mark.parametrize("checked_at", [NOW - 1, NOW + 0.5])
def test_absence_before_attempt_cannot_clear_uncertainty(context, checked_at):
    _, store, target, _, quote = context
    prepare(context)
    store.simulate_attempt(INTENT, target, 7, quote, SyntheticOrderAdapter(), now=NOW + 1)
    result = store.reconcile(
        INTENT,
        SyntheticOrderAdapter(lookup_result=absence(quote, checked_at=checked_at)),
        now=NOW + 2,
    )
    assert result["state"] == "uncertain" and result["requires_reconciliation"] is True


def test_crash_reopen_preserves_submission_time_and_permanent_fence(context):
    path, store, target, observation, quote = context
    prepare(context)
    crashed = SyntheticOrderAdapter(submit_result=SimulatedCrash())
    with pytest.raises(SimulatedCrash):
        store.simulate_attempt(INTENT, target, 7, quote, crashed, now=NOW + 1)
    reopened = OrderSafetyStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT submission_started_at FROM intents").fetchone()[0] == NOW + 1
    stale = reopened.reconcile(
        INTENT,
        SyntheticOrderAdapter(lookup_result=absence(quote, checked_at=NOW + 0.5)),
        now=NOW + 2,
    )
    assert stale["state"] == "uncertain" and stale["requires_reconciliation"] is True
    fresh = reopened.reconcile(
        INTENT,
        SyntheticOrderAdapter(lookup_result=absence(quote, checked_at=NOW + 1.5)),
        now=NOW + 2,
    )
    assert fresh["state"] == "absent" and fresh["requires_reconciliation"] is False
    with pytest.raises(OrderStopped, match="attempt_already_started_or_missing"):
        reopened.simulate_attempt(INTENT, target, 7, quote, crashed, now=NOW + 2)
    with pytest.raises(OrderStopped, match="one_copy_or_idempotency_conflict"):
        reopened.prepare(GRANT, OTHER, target, 7, observation, quote, now=NOW + 2)
    assert crashed.submit_calls == 1


@pytest.mark.parametrize("legacy_state", ["submitting", "uncertain", "absent"])
def test_legacy_missing_submission_time_cannot_confirm_absence(context, legacy_state):
    path, store, target, _, quote = context
    prepare(context)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE intents SET state=?", (legacy_state,))
        db.execute("ALTER TABLE intents DROP COLUMN submission_started_at")
        db.execute("PRAGMA user_version=1")
    reopened = OrderSafetyStore(path)
    assert OrderSafetyStore(path).status(INTENT)["requires_reconciliation"] is True
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT submission_started_at FROM intents").fetchone()[0] is None
    result = reopened.reconcile(
        INTENT,
        SyntheticOrderAdapter(lookup_result=absence(quote, checked_at=NOW + 2)),
        now=NOW + 3,
    )
    assert result["state"] == "uncertain" and result["requires_reconciliation"] is True
    with pytest.raises(OrderStopped, match="attempt_already_started_or_missing"):
        reopened.simulate_attempt(INTENT, target, 7, quote, SyntheticOrderAdapter(), now=NOW + 3)


@pytest.mark.parametrize("legacy_state", ["prepared", "confirmed"])
def test_legacy_unstarted_or_confirmed_intent_keeps_its_state(context, legacy_state):
    path, store, target, _, quote = context
    prepare(context)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE intents SET state=?", (legacy_state,))
        db.execute("ALTER TABLE intents DROP COLUMN submission_started_at")
        db.execute("PRAGMA user_version=1")
    reopened = OrderSafetyStore(path)
    assert reopened.status(INTENT)["state"] == legacy_state
    if legacy_state == "prepared":
        reopened.simulate_attempt(INTENT, target, 7, quote, SyntheticOrderAdapter(), now=NOW + 1)
        assert (
            reopened.reconcile(
                INTENT,
                SyntheticOrderAdapter(lookup_result=absence(quote, checked_at=NOW + 1.5)),
                now=NOW + 2,
            )["state"]
            == "absent"
        )


def test_null_submission_time_in_current_ledger_keeps_reconciliation_required(context):
    path, store, target, _, quote = context
    prepare(context)
    store.simulate_attempt(INTENT, target, 7, quote, SyntheticOrderAdapter(), now=NOW + 1)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE intents SET submission_started_at=NULL")
    result = OrderSafetyStore(path).reconcile(
        INTENT,
        SyntheticOrderAdapter(lookup_result=absence(quote, checked_at=NOW + 2)),
        now=NOW + 3,
    )
    assert result["state"] == "uncertain" and result["requires_reconciliation"] is True


def test_no_grant_or_ledger_exists_in_private_app_by_default(tmp_path):
    path = tmp_path / "not-created.sqlite"
    with pytest.raises(ValueError, match="explicitly initialize"):
        OrderSafetyStore(path)
    assert not path.exists()


def test_ledger_reopen_rejects_broader_file_permissions(context):
    path, _, _, _, _ = context
    path.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        OrderSafetyStore(path)
