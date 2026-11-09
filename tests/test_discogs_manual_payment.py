"""Offline manual-payment views of durable synthetic order observations only."""

from uuid import uuid4

import pytest

from finder.discogs_manual_payment import (
    ManualPaymentIdentity,
    ManualPaymentStopped,
    manual_payment_handoff,
)
from finder.discogs_order_reconciliation import (
    DiscogsOrderLedger,
    ExpectedOrder,
    OrderPageEvidence,
)


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "orders.db"
    ledger = DiscogsOrderLedger(path, create=True)
    expected = ExpectedOrder(
        intent_id=str(uuid4()),
        approval_ref=str(uuid4()),
        release_id=123,
        listing_id="1002",
        seller="ExampleSeller",
        quoted_total="25.00",
        currency="USD",
        quote_fingerprint="a" * 64,
        rules_revision=7,
        destination_ref=str(uuid4()),
        payment_ref=str(uuid4()),
        quote_expires_at=1300,
    )
    identity = ManualPaymentIdentity(
        intent_id=expected.intent_id, release_id=123, order_id="123456-789012"
    )
    return path, ledger, expected, identity


def observed(status="Invoice Sent", *, at=1100, **changes):
    values = {
        "order_id": "123456-789012",
        "release_id": 123,
        "listing_id": "1002",
        "seller": "exampleseller",
        "total": "25.00",
        "currency": "USD",
        "displayed_status": status,
        "observed_at": at,
        "source": "normal_order_page",
    }
    values.update(changes)
    return OrderPageEvidence(**values)


def record(context, evidence=None):
    _, ledger, expected, _ = context
    ledger.prepare(expected, now=1000)
    ledger.fence_possible_submit(expected.intent_id, now=1001)
    if evidence is not None:
        ledger.record_read_only_lookup(expected.intent_id, evidence, now=evidence.observed_at + 1)


def assert_no_actions_or_links(result):
    assert result["order_url"] is None
    assert result["order_link_status"] == "canonical_route_unverified"
    for key in (
        "live_order_checked",
        "handoff_available",
        "reservation_verified",
        "availability_guaranteed",
        "may_submit_again",
        "purchase_allowed",
    ):
        assert result[key] is False
    assert result["order_submission"] == result["payment_execution"] == "unavailable"


@pytest.mark.parametrize(
    ("status", "payment", "code", "action"),
    [
        (
            "Invoice Sent",
            "unpaid",
            "order_created_unpaid_as_recorded",
            "owner_review_before_payment",
        ),
        (
            "Payment Pending",
            "pending",
            "payment_processing_as_recorded",
            "await_payment_reconciliation",
        ),
        ("Payment Received", "paid", "payment_completed_as_recorded", "none"),
        ("In Progress", "paid", "payment_completed_as_recorded", "none"),
        ("Shipped", "paid", "payment_completed_as_recorded", "none"),
    ],
)
def test_recorded_order_creation_payment_processing_and_completion_are_distinct(
    context, status, payment, code, action
):
    path, ledger, expected, identity = context
    record(context, observed(status))
    before = ledger.status(expected.intent_id)
    result = manual_payment_handoff(DiscogsOrderLedger(path), identity)
    assert result["recorded_payment_state"] == payment
    assert result["code"] == code
    assert result["manual_payment_action"] == action
    assert result["order_id"] == identity.order_id
    assert_no_actions_or_links(result)
    assert ledger.status(expected.intent_id) == before


def test_missing_prepared_and_uncertain_outcomes_never_report_order_creation(context):
    _, ledger, expected, identity = context
    assert manual_payment_handoff(ledger, identity)["code"] == "order_not_recorded"
    ledger.prepare(expected, now=1000)
    prepared = manual_payment_handoff(ledger, identity)
    assert prepared["code"] == "order_creation_not_observed"
    ledger.fence_possible_submit(expected.intent_id, now=1001)
    ledger.record_read_only_lookup(expected.intent_id, None, now=1100)
    uncertain = manual_payment_handoff(ledger, identity)
    assert uncertain["code"] == "order_creation_outcome_unknown"
    for result in (prepared, uncertain):
        assert result["recorded_payment_state"] == "unknown"
        assert result["order_id"] is None
        assert_no_actions_or_links(result)


@pytest.mark.parametrize(
    "change", [{"intent_id": str(uuid4())}, {"release_id": 124}, {"order_id": "111-222"}]
)
def test_handoff_is_bound_to_exact_durable_intent_release_and_order(context, change):
    _, ledger, _, identity = context
    record(context, observed())
    changed = ManualPaymentIdentity.model_validate({**identity.model_dump(), **change})
    result = manual_payment_handoff(ledger, changed)
    assert result["recorded_payment_state"] == "unknown"
    assert result["order_id"] is None
    assert result["manual_payment_action"] == "review_order"
    assert_no_actions_or_links(result)


@pytest.mark.parametrize(
    "evidence",
    [observed("Merged"), observed(total="26.00"), observed(listing_id="1003")],
)
def test_unknown_status_conflict_or_review_required_suppresses_payment_handoff(context, evidence):
    _, ledger, _, identity = context
    record(context, evidence)
    result = manual_payment_handoff(ledger, identity)
    assert result["code"] == "recorded_order_requires_review"
    assert result["manual_payment_action"] == "review_order"
    assert result["recorded_payment_state"] == "unknown"
    assert_no_actions_or_links(result)


def test_regression_after_paid_does_not_present_another_payment_as_due(context):
    _, ledger, expected, identity = context
    record(context, observed("Payment Received"))
    ledger.record_read_only_lookup(expected.intent_id, observed(at=1200), now=1201)
    result = manual_payment_handoff(ledger, identity)
    assert result["code"] == "recorded_order_requires_review"
    assert result["manual_payment_action"] == "review_order"
    assert result["recorded_payment_state"] == "paid"
    assert_no_actions_or_links(result)


def test_later_reconciliation_updates_projection_without_a_second_state_store(context):
    _, ledger, expected, identity = context
    record(context, observed())
    first = manual_payment_handoff(ledger, identity)
    ledger.record_read_only_lookup(
        expected.intent_id, observed("Payment Pending", at=1200), now=1201
    )
    processing = manual_payment_handoff(ledger, identity)
    ledger.record_read_only_lookup(
        expected.intent_id, observed("Payment Received", at=1250), now=1251
    )
    completed = manual_payment_handoff(ledger, identity)
    assert [row["recorded_payment_state"] for row in (first, processing, completed)] == [
        "unpaid",
        "pending",
        "paid",
    ]


def test_only_exact_ledger_and_identity_are_accepted_and_no_urls_or_secrets(context):
    _, ledger, _, identity = context
    for unsupported in ({}, object()):
        with pytest.raises(
            ManualPaymentStopped, match="durable_ledger_and_exact_identity_required"
        ):
            manual_payment_handoff(unsupported, identity)
    for key in ("order_url", "payment_url", "paypal_account", "access_token"):
        with pytest.raises(ValueError) as stopped:
            ManualPaymentIdentity.model_validate({**identity.model_dump(), key: "private-sentinel"})
        assert "private-sentinel" not in str(stopped.value)
    with pytest.raises(ValueError):
        ManualPaymentIdentity(
            intent_id=identity.intent_id, release_id=123, order_id="https://discogs.com/order/1-2"
        )
    invalid = identity.model_copy(update={"release_id": True})
    with pytest.raises(ManualPaymentStopped, match="identity_unverified"):
        manual_payment_handoff(ledger, invalid)


def test_status_read_failure_is_sanitized_and_does_not_create_an_order(context, monkeypatch):
    _, ledger, expected, identity = context
    record(context, observed())
    before = ledger.status(expected.intent_id)

    def unavailable(_intent):
        raise OSError("private account sentinel")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "status", unavailable)
        with pytest.raises(
            ManualPaymentStopped, match="durable_order_status_unavailable"
        ) as stopped:
            manual_payment_handoff(ledger, identity)
        assert "private account sentinel" not in str(stopped.value)
    assert ledger.status(expected.intent_id) == before
