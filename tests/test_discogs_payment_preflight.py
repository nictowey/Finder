"""Synthetic contract checks only; no live quote producer, cart, order or payment."""

from uuid import uuid4

import pytest

from finder.discogs_hosted_checkout import CartEvidence
from finder.discogs_hosted_checkout_coordinator import FixtureFinalQuoteEvidence
from finder.discogs_payment_preflight import (
    PaymentPreflightStopped,
    PaymentQuoteBindings,
    PaymentQuoteEvidence,
    QuoteMoney,
    assess_payment_quote,
    payment_preflight_readiness,
)

NOW = 1000.0


@pytest.fixture
def bindings():
    return PaymentQuoteBindings(
        release_id=123,
        listing_id="1002",
        rules_revision=7,
        candidate_sequence=11,
        observation_fingerprint="a" * 64,
        claim_ref=str(uuid4()),
        destination_ref=str(uuid4()),
        payment_ref=str(uuid4()),
        currency="USD",
        max_all_in="60.00",
    )


def money(value, currency="USD"):
    return {"amount": value, "currency": currency}


def quote(bindings, **changes):
    data = {
        "source": "unverified_noncommitting_quote_contract",
        "bindings": bindings,
        "surface": "noncommitting_final_quote",
        "quantity": 1,
        "item": money("40.00"),
        "shipping": money("5.00"),
        "tax": money("5.00"),
        "total": money("50.00"),
        "observed_at": NOW - 1,
        "expires_at": NOW + 120,
        "currency_explicit": True,
        "tax_final": True,
        "tax_hidden_or_deferred": False,
        "shipping_final": True,
        "destination_reference_observed": True,
        "payment_reference_observed": True,
        "all_payable_charges_included": True,
    }
    data.update(changes)
    return PaymentQuoteEvidence.model_validate(data)


def assessed(bindings, **changes):
    return assess_payment_quote(bindings, quote(bindings, **changes), now=NOW)


def assert_stopped(result, code):
    assert result["status"] == "blocked"
    assert result["code"] == code
    assert result["contract_satisfied"] is False
    assert result["evidence_fingerprint"] is None
    assert result["purchase_allowed"] is False
    assert result["final_payable_quote_verified"] is False
    assert result["order_submission"] == result["payment_execution"] == "unavailable"


def test_default_unavailable_and_complete_contract_still_cannot_verify_live_quote(bindings):
    assert payment_preflight_readiness()["live_quote_producer"] == "unavailable"
    assert_stopped(assess_payment_quote(bindings, now=NOW), "final_quote_evidence_unavailable")
    evidence = quote(bindings)
    result = assess_payment_quote(bindings, evidence, now=NOW)
    assert result["contract_satisfied"] is True
    assert result["code"] == "quote_contract_complete_live_source_unavailable"
    assert len(result["evidence_fingerprint"]) == 64
    assert result["final_payable_quote_verified"] is result["purchase_allowed"] is False
    assert result["live_quote_producer"] == "unavailable"
    assert evidence.fees is None


@pytest.mark.parametrize(
    "change",
    [
        {"release_id": 124},
        {"listing_id": "1003"},
        {"rules_revision": 8},
        {"candidate_sequence": 12},
        {"observation_fingerprint": "b" * 64},
        {"claim_ref": str(uuid4())},
        {"destination_ref": str(uuid4())},
        {"payment_ref": str(uuid4())},
        {"currency": "EUR"},
        {"max_all_in": "59.00"},
    ],
)
def test_every_current_context_field_is_bound_exactly(bindings, change):
    changed = PaymentQuoteBindings.model_validate({**bindings.model_dump(), **change})
    assert_stopped(assess_payment_quote(bindings, quote(changed), now=NOW), "quote_context_changed")


@pytest.mark.parametrize("surface", ["unknown", "cart_display", "creates_order"])
def test_order_creation_and_cart_display_are_not_final_quote_previews(bindings, surface):
    assert_stopped(
        assessed(bindings, surface=surface), "noncommitting_final_quote_surface_unverified"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"observed_at": NOW + 1},
        {"observed_at": NOW - 301},
        {"expires_at": NOW},
        {"expires_at": NOW - 1},
        {"observed_at": NOW - 1, "expires_at": NOW + 300},
        {"observed_at": NOW, "expires_at": NOW},
    ],
)
def test_future_stale_expired_or_excessive_lifetime_quotes_stop(bindings, change):
    assert_stopped(assessed(bindings, **change), "quote_freshness_unverified")


def test_quote_expiry_boundary_and_explicit_same_currency(bindings):
    assert assessed(bindings, observed_at=NOW, expires_at=NOW + 300)["contract_satisfied"]
    assert_stopped(assessed(bindings, currency_explicit=False), "quote_currency_unverified")
    for field in ("item", "shipping", "tax", "fees", "total"):
        assert_stopped(
            assessed(bindings, **{field: money("5.00", "EUR")}), "quote_currency_unverified"
        )


@pytest.mark.parametrize(
    "change", [{"tax": None}, {"tax_final": False}, {"tax_hidden_or_deferred": True}]
)
def test_tax_must_be_explicit_and_final_even_when_displayed_arithmetic_matches(bindings, change):
    assert_stopped(assessed(bindings, **change), "quote_tax_unverified")


def test_observed_cart_amounts_and_hidden_tax_never_become_zero_tax(bindings):
    result = assessed(
        bindings,
        item=money("199.99"),
        shipping=money("5.85"),
        tax=None,
        total=money("205.84"),
        tax_final=False,
        tax_hidden_or_deferred=True,
    )
    assert_stopped(result, "quote_tax_unverified")
    explicit_zero = quote(bindings, tax=money("0.00"), total=money("45.00"))
    assert assess_payment_quote(bindings, explicit_zero, now=NOW)["contract_satisfied"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"shipping_final": False}, "quote_shipping_finality_unverified"),
        ({"destination_reference_observed": False}, "quote_destination_or_payment_unverified"),
        ({"payment_reference_observed": False}, "quote_destination_or_payment_unverified"),
        ({"all_payable_charges_included": False}, "quote_payable_charge_coverage_unverified"),
        ({"total": money("51.00")}, "quote_component_total_mismatch"),
        ({"tax": money("16.00"), "total": money("61.00")}, "quote_above_all_in_ceiling"),
    ],
)
def test_finality_reference_charge_coverage_and_ceiling_gaps_stop(bindings, change, reason):
    assert_stopped(assessed(bindings, **change), reason)


def test_absent_fee_line_is_preserved_and_any_observed_charge_is_included(bindings):
    evidence = quote(bindings)
    assert evidence.fees is None
    assert assess_payment_quote(bindings, evidence, now=NOW)["contract_satisfied"]
    changed = quote(bindings, fees=money("2.00"), total=money("52.00"))
    assert assess_payment_quote(bindings, changed, now=NOW)["contract_satisfied"]
    assert_stopped(assessed(bindings, fees=money("2.00")), "quote_component_total_mismatch")
    assert_stopped(
        assessed(bindings, all_payable_charges_included=False),
        "quote_payable_charge_coverage_unverified",
    )


def test_fixture_and_cart_contracts_cannot_be_promoted(bindings):
    fixture = FixtureFinalQuoteEvidence(
        source="offline_checkout_fixture",
        release_id=123,
        listing_id="1002",
        rules_revision=7,
        item="40.00",
        shipping="5.00",
        tax="5.00",
        fees="0.00",
        total="50.00",
        currency="USD",
        destination_ref=bindings.destination_ref,
        payment_ref=bindings.payment_ref,
    )
    cart = CartEvidence("1002", None, None, None, None, None, None, True)
    for incompatible in (fixture, cart, quote(bindings).model_dump(), object()):
        assert_stopped(
            assess_payment_quote(bindings, incompatible, now=NOW), "unsupported_quote_evidence"
        )
    with pytest.raises(ValueError):
        quote(bindings, source="offline_checkout_fixture")


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1.0, "1000", 10**1000])
def test_invalid_time_values_fail_closed(bindings, value):
    with pytest.raises(PaymentPreflightStopped, match="invalid_assessment_time"):
        assess_payment_quote(bindings, now=value)
    for field in ("observed_at", "expires_at"):
        with pytest.raises(ValueError):
            quote(bindings, **{field: value})


@pytest.mark.parametrize(
    "change",
    [
        {"release_id": True},
        {"listing_id": "1002\n"},
        {"rules_revision": True},
        {"candidate_sequence": 0},
        {"observation_fingerprint": "a" * 64 + "\n"},
        {"claim_ref": "00000000-0000-0000-0000-000000000000"},
        {"destination_ref": "private street address"},
        {"payment_ref": "4111111111111111"},
        {"max_all_in": "0.00"},
        {"max_all_in": "NaN"},
    ],
)
def test_invalid_bindings_and_nonopaque_private_values_are_rejected(bindings, change):
    with pytest.raises(ValueError):
        PaymentQuoteBindings.model_validate({**bindings.model_dump(), **change})


def test_secret_and_action_fields_are_forbidden_and_errors_hide_input(bindings):
    for key in ("card_number", "cvv", "address", "access_token", "submit", "payment_route"):
        with pytest.raises(ValueError) as stopped:
            quote(bindings, **{key: "private-sentinel"})
        assert "private-sentinel" not in str(stopped.value)
    for value in ("5", "5.0", "05.00", "-1.00", "5.000", "5.00\n", "NaN"):
        with pytest.raises(ValueError):
            QuoteMoney(amount=value, currency="USD")


@pytest.mark.parametrize("quantity", [True, 1.0, "1", 0, 2])
def test_exact_single_quantity_is_required(bindings, quantity):
    with pytest.raises(ValueError):
        quote(bindings, quantity=quantity)


def test_frozen_and_bypassed_models_cannot_skip_boundary_validation(bindings):
    evidence = quote(bindings)
    with pytest.raises(ValueError):
        evidence.tax_final = False
    invalid = evidence.model_copy(update={"quantity": 2})
    assert_stopped(assess_payment_quote(bindings, invalid, now=NOW), "quote_contract_invalid")
    invalid_bindings = bindings.model_copy(update={"rules_revision": True})
    with pytest.raises(PaymentPreflightStopped, match="payment_quote_bindings_invalid"):
        assess_payment_quote(invalid_bindings, evidence, now=NOW)


def test_fingerprint_is_stable_and_changes_with_quote_not_payment_status(bindings):
    first = assessed(bindings)
    assert first == assessed(bindings)
    changed = assessed(bindings, tax=money("6.00"), total=money("51.00"))
    assert first["evidence_fingerprint"] != changed["evidence_fingerprint"]
    assert "order_id" not in first and "payment_state" not in first
