"""Offline order-created/payment-state tests. No Discogs requests or purchases."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

from finder.discogs_order_reconciliation import (
    DiscogsOrderLedger,
    ExpectedOrder,
    OrderEvidenceStopped,
    OrderPageEvidence,
)


def expected(**changes):
    values = {
        "intent_id": str(uuid4()),
        "approval_ref": str(uuid4()),
        "release_id": 28720177,
        "listing_id": "3526572746",
        "seller": "SampleSeller",
        "quoted_total": "25.00",
        "currency": "USD",
        "quote_fingerprint": "a" * 64,
        "rules_revision": 3,
        "destination_ref": str(uuid4()),
        "payment_ref": str(uuid4()),
        "quote_expires_at": 1300,
    }
    values.update(changes)
    return ExpectedOrder(**values)


def observed(**changes):
    values = {
        "order_id": "123456-789012",
        "release_id": 28720177,
        "listing_id": "3526572746",
        "seller": "sampleseller",
        "total": "25.00",
        "currency": "USD",
        "displayed_status": "Invoice Sent",
        "observed_at": 1100,
        "source": "normal_order_page",
    }
    values.update(changes)
    return OrderPageEvidence(**values)


def ledger(tmp_path):
    return DiscogsOrderLedger(tmp_path / "orders.db", create=True)


def test_actual_discogs_order_id_shape_and_status_evidence_validation():
    for order_id in ("1-1", "123456-789012", "0001-0099"):
        assert observed(order_id=order_id).order_id == order_id
    for order_id in ("", "123", "123_456", "1-2-3", "https://discogs.com/order/1-2"):
        with pytest.raises(ValueError):
            observed(order_id=order_id)
    with pytest.raises(ValueError):
        observed(total="unknown")
    with pytest.raises(ValueError):
        observed(source="checkout_submit")
    with pytest.raises(ValueError):
        observed(release_id=0)
    with pytest.raises(ValueError):
        expected(destination_ref="real address")


def test_order_created_is_not_paid_and_payment_progression_survives_restart(tmp_path):
    store = ledger(tmp_path)
    proposal = expected()
    assert store.prepare(proposal, now=1000)["state"] == "prepared"
    assert store.prepare(proposal, now=1000)["state"] == "prepared"
    assert (
        store.fence_possible_submit(proposal.intent_id, now=1001)["state"] == "submission_unknown"
    )
    with pytest.raises(OrderEvidenceStopped, match="already_fenced"):
        store.fence_possible_submit(proposal.intent_id, now=1002)
    reopened = DiscogsOrderLedger(tmp_path / "orders.db")
    assert (
        reopened.record_read_only_lookup(proposal.intent_id, None, now=1090)["state"]
        == "submission_unknown"
    )
    created = reopened.record_read_only_lookup(proposal.intent_id, observed(), now=1101)
    assert created["state"] == "order_seen"
    assert created["order_id"] == "123456-789012"
    assert created["payment_state"] == "unpaid"
    assert created["may_submit_again"] is False
    pending = reopened.record_read_only_lookup(
        proposal.intent_id,
        observed(displayed_status="Payment Pending", observed_at=1200),
        now=1201,
    )
    assert pending["payment_state"] == "pending"
    paid = reopened.record_read_only_lookup(
        proposal.intent_id,
        observed(displayed_status="Payment Received", observed_at=1250),
        now=1251,
    )
    assert paid["payment_state"] == "paid"
    assert paid["review_required"] is False
    assert paid["purchase_allowed"] is False
    with pytest.raises(OrderEvidenceStopped, match="one_copy"):
        reopened.prepare(replace(proposal, intent_id=str(uuid4())), now=1000)


def test_unknown_or_mismatched_order_page_retains_fence_and_identity(tmp_path):
    store = ledger(tmp_path)
    proposal = expected()
    store.prepare(proposal, now=1000)
    store.fence_possible_submit(proposal.intent_id, now=1001)
    mismatch = store.record_read_only_lookup(
        proposal.intent_id,
        observed(total="29.00", displayed_status="Payment Received"),
        now=1101,
    )
    assert mismatch["state"] == "identity_conflict"
    assert mismatch["order_id"] == "123456-789012"
    assert mismatch["payment_state"] == "unknown"
    assert mismatch["review_required"] is True
    assert store.record_read_only_lookup(proposal.intent_id, None, now=1102) == mismatch
    conflict = store.record_read_only_lookup(
        proposal.intent_id,
        observed(order_id="555-666", observed_at=1103),
        now=1104,
    )
    assert conflict["state"] == "identity_conflict"
    assert conflict["order_id"] == "123456-789012"
    assert conflict["may_submit_again"] is False


def test_wrong_exact_release_keeps_order_id_and_never_calls_it_paid(tmp_path):
    store = ledger(tmp_path)
    proposal = expected()
    store.prepare(proposal, now=1000)
    store.fence_possible_submit(proposal.intent_id, now=1001)
    result = store.record_read_only_lookup(
        proposal.intent_id,
        observed(release_id=5520534, displayed_status="Payment Received"),
        now=1101,
    )
    assert result["state"] == "identity_conflict"
    assert result["order_id"] == "123456-789012"
    assert result["payment_state"] == "unknown"
    assert result["may_submit_again"] is False


def test_status_unknown_and_stale_or_regressive_evidence_fail_closed(tmp_path):
    store = ledger(tmp_path)
    proposal = expected()
    store.prepare(proposal, now=1000)
    store.fence_possible_submit(proposal.intent_id, now=1001)
    unknown = store.record_read_only_lookup(
        proposal.intent_id, observed(displayed_status="Merged"), now=1101
    )
    assert unknown["payment_state"] == "unknown"
    assert unknown["review_required"] is True
    paid = store.record_read_only_lookup(
        proposal.intent_id,
        observed(displayed_status="Shipped", observed_at=1200),
        now=1201,
    )
    assert paid["payment_state"] == "paid" and paid["review_required"] is True
    with pytest.raises(OrderEvidenceStopped, match="stale"):
        store.record_read_only_lookup(proposal.intent_id, observed(observed_at=1199), now=1202)
    regressive = store.record_read_only_lookup(
        proposal.intent_id, observed(observed_at=1300), now=1301
    )
    assert regressive["payment_state"] == "paid"
    assert regressive["review_required"] is True


def test_stale_different_order_does_not_change_paid_reconciliation(tmp_path):
    store = ledger(tmp_path)
    proposal = expected()
    store.prepare(proposal, now=1000)
    store.fence_possible_submit(proposal.intent_id, now=1001)
    paid = store.record_read_only_lookup(
        proposal.intent_id,
        observed(displayed_status="Payment Received", observed_at=1200),
        now=1201,
    )
    assert paid["state"] == "order_seen" and paid["review_required"] is False
    with pytest.raises(OrderEvidenceStopped, match="stale_order_evidence"):
        store.record_read_only_lookup(
            proposal.intent_id,
            observed(order_id="555-666", observed_at=1100),
            now=1202,
        )
    assert store.status(proposal.intent_id) == paid
    assert (
        store.record_read_only_lookup(
            proposal.intent_id,
            observed(displayed_status="Payment Received", observed_at=1300),
            now=1301,
        )
        == paid
    )


def test_current_different_order_still_conflicts_and_retains_paid_fence(tmp_path):
    store = ledger(tmp_path)
    proposal = expected()
    store.prepare(proposal, now=1000)
    store.fence_possible_submit(proposal.intent_id, now=1001)
    store.record_read_only_lookup(
        proposal.intent_id,
        observed(displayed_status="Payment Received", observed_at=1200),
        now=1201,
    )
    conflict = store.record_read_only_lookup(
        proposal.intent_id,
        observed(order_id="555-666", observed_at=1300),
        now=1301,
    )
    assert conflict["state"] == "identity_conflict"
    assert conflict["order_id"] == "123456-789012"
    assert conflict["payment_state"] == "paid"
    assert conflict["review_required"] is True
    assert conflict["may_submit_again"] is False


def test_fence_is_durable_under_competing_workers_and_no_delete(tmp_path):
    path = tmp_path / "orders.db"
    store = DiscogsOrderLedger(path, create=True)
    proposal = expected()

    def prepare(_):
        return DiscogsOrderLedger(path).prepare(proposal, now=1000)["state"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(prepare, range(2))) == ["prepared", "prepared"]

    def fence(_):
        try:
            return DiscogsOrderLedger(path).fence_possible_submit(proposal.intent_id, now=1001)[
                "state"
            ]
        except OrderEvidenceStopped:
            return "stopped"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(fence, range(2))) == ["stopped", "submission_unknown"]
    assert store.status(proposal.intent_id)["may_submit_again"] is False
    with sqlite3.connect(path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM intents")


def test_expired_quote_cannot_be_prepared_or_fenced(tmp_path):
    store = ledger(tmp_path)
    proposal = expected(quote_expires_at=1050)
    with pytest.raises(OrderEvidenceStopped, match="expired"):
        store.prepare(proposal, now=1060)
    store.prepare(proposal, now=1000)
    with pytest.raises(OrderEvidenceStopped, match="stale"):
        store.fence_possible_submit(proposal.intent_id, now=1060)
