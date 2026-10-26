"""Bind no-submit quote assertions to an isolated current cart candidate."""

from uuid import uuid4

import pytest
from test_discogs_hosted_cartprep import InterceptedAdapter, run, setup
from test_discogs_payment_preflight import quote

from finder.discogs_hosted_checkout import CheckoutStopped, HostedCheckoutPreflight
from finder.discogs_hosted_checkout_coordinator import HostedNoOrderCoordinator
from finder.discogs_order_reconciliation import DiscogsOrderLedger, ExpectedOrder
from finder.discogs_order_safety import OrderSafetyStore
from finder.discogs_payment_preflight import PaymentQuoteBindings
from finder.discogs_rules import RulesStore


def ready(tmp_path):
    now, claims, candidates, snapshot, detail = setup(tmp_path)
    key = str(uuid4())
    assert run(now, claims, candidates, snapshot, detail, InterceptedAdapter(), key)["code"] == (
        "cart_observed_no_order"
    )
    bundle = candidates.cart_review_bundle(123, "1002", key)
    bindings = PaymentQuoteBindings(
        release_id=123,
        listing_id="1002",
        rules_revision=1,
        candidate_sequence=bundle["candidate_sequence"],
        observation_fingerprint=bundle["listing"]["observation_fingerprint"],
        claim_ref=key,
        destination_ref=str(uuid4()),
        payment_ref=str(uuid4()),
        currency="USD",
        max_all_in="50.00",
    )
    coordinator = HostedNoOrderCoordinator(candidates, HostedCheckoutPreflight(candidates))
    return now, candidates, key, bindings, coordinator


def evidence(now, bindings, **changes):
    return quote(
        bindings,
        item={"amount": "5.00", "currency": "USD"},
        shipping={"amount": "4.95", "currency": "USD"},
        tax={"amount": "0.00", "currency": "USD"},
        total={"amount": "9.95", "currency": "USD"},
        observed_at=now[0] - 1,
        expires_at=now[0] + 60,
        **changes,
    )


def test_current_cart_contract_remains_unverified_even_with_complete_assertions(tmp_path):
    now, _, key, bindings, coordinator = ready(tmp_path)
    result = coordinator.inspect_payment_contract(
        123, "1002", key, bindings, evidence(now, bindings)
    )
    assert result["contract_satisfied"] is True
    assert result["code"] == "quote_contract_complete_live_source_unavailable"
    assert result["final_payable_quote_verified"] is result["purchase_allowed"] is False
    assert result["order_submission"] == result["payment_execution"] == "unavailable"
    assert coordinator.readiness()["payment_quote_contract"] == "assessment_only"
    with pytest.raises(CheckoutStopped, match="live_order_submission_unavailable"):
        coordinator.submit()


@pytest.mark.parametrize(
    "changes",
    [
        {"claim_ref": str(uuid4())},
        {"candidate_sequence": 999},
        {"rules_revision": 2},
        {"observation_fingerprint": "0" * 64},
        {"currency": "EUR"},
        {"max_all_in": "51.00"},
    ],
)
def test_quote_cannot_substitute_a_different_current_context(tmp_path, changes):
    _, _, key, bindings, coordinator = ready(tmp_path)
    changed = PaymentQuoteBindings.model_validate({**bindings.model_dump(), **changes})
    with pytest.raises(CheckoutStopped, match="context_changed"):
        coordinator.inspect_payment_contract(123, "1002", key, changed)


def test_changed_or_unobserved_quote_amount_cannot_become_bound_evidence(tmp_path):
    now, _, key, bindings, coordinator = ready(tmp_path)
    changed = quote(bindings, observed_at=now[0] - 1, expires_at=now[0] + 60)
    result = coordinator.inspect_payment_contract(123, "1002", key, bindings, changed)
    assert result["code"] == "quote_item_changed_from_candidate"
    assert result["contract_satisfied"] is False and result["evidence_fingerprint"] is None
    unavailable = coordinator.inspect_payment_contract(123, "1002", key, bindings)
    assert unavailable["code"] == "final_quote_evidence_unavailable"


def test_rule_revocation_during_assessment_cannot_return_a_current_contract(tmp_path, monkeypatch):
    now, candidates, key, bindings, coordinator = ready(tmp_path)
    from finder import discogs_hosted_checkout_coordinator as module

    original = module.assess_payment_quote

    def concurrent_change(*args, **kwargs):
        result = original(*args, **kwargs)
        store = RulesStore(candidates.database)
        saved = store.snapshot()
        store.save(saved["targets"][0], saved["revision"])
        store.close()
        return result

    monkeypatch.setattr(module, "assess_payment_quote", concurrent_change)
    with pytest.raises(CheckoutStopped, match="current_state_unverified"):
        coordinator.inspect_payment_contract(123, "1002", key, bindings, evidence(now, bindings))


def test_expiry_during_final_candidate_check_does_not_report_a_current_contract(
    tmp_path, monkeypatch
):
    now, candidates, key, bindings, coordinator = ready(tmp_path)
    current = evidence(now, bindings)
    original, calls = candidates.cart_review_bundle, []

    def delayed_check(*args):
        calls.append(True)
        if len(calls) == 2:
            now[0] = current.expires_at
        return original(*args)

    monkeypatch.setattr(candidates, "cart_review_bundle", delayed_check)
    result = coordinator.inspect_payment_contract(123, "1002", key, bindings, current)
    assert result["code"] == "quote_freshness_unverified"
    assert result["contract_satisfied"] is result["purchase_allowed"] is False


def test_bypass_constructed_nested_evidence_is_normalized_before_field_access(tmp_path):
    now, _, key, bindings, coordinator = ready(tmp_path)
    current = evidence(now, bindings).model_copy(
        update={"item": {"amount": "5.00", "currency": "USD"}}
    )
    result = coordinator.inspect_payment_contract(123, "1002", key, bindings, current)
    assert result["contract_satisfied"] is True and result["purchase_allowed"] is False
    invalid = current.model_copy(update={"item": {"amount": "bad", "currency": "USD"}})
    with pytest.raises(CheckoutStopped, match="evidence_unverified"):
        coordinator.inspect_payment_contract(123, "1002", key, bindings, invalid)


@pytest.mark.parametrize("appears_during_read", [False, True])
def test_existing_order_intent_fences_the_contract_assessment(
    tmp_path, monkeypatch, appears_during_read
):
    now, _, key, bindings, coordinator = ready(tmp_path)
    orders = OrderSafetyStore(tmp_path / "orders.db", create=True)
    coordinator.orders = orders
    calls = []

    def intent(_release):
        calls.append(True)
        return {"state": "uncertain"} if not appears_during_read or len(calls) > 1 else None

    monkeypatch.setattr(orders, "status_for_release", intent)
    with pytest.raises(CheckoutStopped, match="existing_order_intent_blocks"):
        coordinator.inspect_payment_contract(123, "1002", key, bindings, evidence(now, bindings))


@pytest.mark.parametrize("appears_during_read", [False, True])
def test_real_order_ledger_fences_contract_without_using_a_synthetic_order_port(
    tmp_path, monkeypatch, appears_during_read
):
    now, candidates, key, bindings, coordinator = ready(tmp_path)
    ledger = DiscogsOrderLedger(tmp_path / "real-order-evidence.db", create=True)
    coordinator = HostedNoOrderCoordinator(candidates, coordinator.preflight, real_orders=ledger)
    expected = ExpectedOrder(
        intent_id=str(uuid4()),
        approval_ref=str(uuid4()),
        release_id=123,
        listing_id="1002",
        seller="ExampleSeller",
        quoted_total="9.95",
        currency="USD",
        quote_fingerprint="a" * 64,
        rules_revision=1,
        destination_ref=bindings.destination_ref,
        payment_ref=bindings.payment_ref,
        quote_expires_at=now[0] + 60,
    )
    if not appears_during_read:
        ledger.prepare(expected, now=now[0])
    else:
        original = candidates.cart_review_bundle
        calls = []

        def concurrent_intent(*args):
            current = original(*args)
            calls.append(True)
            if len(calls) == 2:
                ledger.prepare(expected, now=now[0])
            return current

        monkeypatch.setattr(candidates, "cart_review_bundle", concurrent_intent)
    assert coordinator.readiness()["real_order_ledger_guard"] == "read_only"
    assert coordinator.readiness()["order_ledger"] == "unavailable"
    with pytest.raises(CheckoutStopped, match="existing_order_intent_blocks"):
        coordinator.inspect_payment_contract(123, "1002", key, bindings, evidence(now, bindings))
    assert ledger.status(expected.intent_id)["state"] == "prepared"


def test_real_order_guard_does_not_replace_synthetic_order_interface(tmp_path):
    _, candidates, _, _, coordinator = ready(tmp_path)
    ledger = DiscogsOrderLedger(tmp_path / "real-order-evidence.db", create=True)
    with pytest.raises(ValueError, match="fixed no-order checkout ports"):
        HostedNoOrderCoordinator(candidates, coordinator.preflight, orders=ledger)
