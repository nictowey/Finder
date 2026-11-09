"""Assess the missing final-quote contract without providing a live quote source.

Every input here is an untrusted normalized assertion. Matching these fields is
useful for reviewing a future read-only producer, but cannot verify that a live
page showed them. There is no reader, adapter injection, authorization issuer or
action API. In particular, an order-creating control is never a quote preview.

Existing synthetic quotes and cart observations are deliberately incompatible.
Post-attempt order/payment state belongs to ``discogs_order_reconciliation``;
passing this assessment neither creates an order nor reports one as paid.
"""

import hashlib
import json
import re
from decimal import Decimal
from math import isfinite
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, ValidationError, field_validator

from finder.discogs_rules import StrictInput

MAX_ID = 9007199254740991
MAX_QUOTE_AGE = 300
Currency = Literal["USD", "EUR", "GBP", "CAD", "AUD"]


class PaymentPreflightStopped(RuntimeError):
    """A fixed local contract stop, never a provider response or account payload."""


class _EvidenceInput(StrictInput):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)


def _opaque(value):
    try:
        parsed = UUID(value) if type(value) is str else None
    except (ValueError, AttributeError):
        parsed = None
    if parsed is None or parsed.int == 0 or str(parsed) != value:
        raise ValueError("A canonical opaque reference is required.")
    return value


def _exact_money(value):
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,6})\.[0-9]{2}", value, re.ASCII):
        raise ValueError("An exact nonnegative money amount is required.")
    return value


def _valid_time(value):
    try:
        return type(value) in (int, float) and value >= 0 and isfinite(value)
    except OverflowError:
        return False


class QuoteMoney(_EvidenceInput):
    """An exact observed amount and currency; no conversion or missing-value default."""

    amount: str
    currency: Currency

    _amount = field_validator("amount")(_exact_money)


class PaymentQuoteBindings(_EvidenceInput):
    """Expected current context, not proof of a claim or purchase authorization.

    A future caller must obtain this from current guarded candidate/claim state
    and recheck it around a read. This pure module cannot read those stores.
    Destination and payment references contain no address or payment details.
    """

    release_id: int = Field(gt=0, le=MAX_ID)
    listing_id: str
    rules_revision: int = Field(ge=0, le=MAX_ID)
    candidate_sequence: int = Field(gt=0, le=MAX_ID)
    observation_fingerprint: str
    claim_ref: str
    destination_ref: str
    payment_ref: str
    currency: Currency
    max_all_in: str

    _references = field_validator("claim_ref", "destination_ref", "payment_ref")(_opaque)

    @field_validator("listing_id")
    @classmethod
    def listing(cls, value):
        if not re.fullmatch(r"[1-9][0-9]{0,15}", value, re.ASCII) or int(value) > MAX_ID:
            raise ValueError("An exact listing ID is required.")
        return value

    @field_validator("observation_fingerprint")
    @classmethod
    def fingerprint(cls, value):
        if not re.fullmatch(r"[0-9a-f]{64}", value, re.ASCII):
            raise ValueError("An observation fingerprint is required.")
        return value

    @field_validator("max_all_in")
    @classmethod
    def positive_ceiling(cls, value):
        value = _exact_money(value)
        if Decimal(value) <= 0:
            raise ValueError("A positive all-in ceiling is required.")
        return value


class PaymentQuoteEvidence(_EvidenceInput):
    """Untrusted contract data, never a live verified quote or payment receipt.

    No noncommitting final-quote surface has been verified. The surface field
    describes a requirement for a future producer, not an available selector.
    A missing fee line stays missing: it is not a new buyer fee or a zero fee.
    """

    source: Literal["unverified_noncommitting_quote_contract"]
    bindings: PaymentQuoteBindings
    surface: Literal["unknown", "cart_display", "noncommitting_final_quote", "creates_order"] = (
        "unknown"
    )
    quantity: Literal[1]
    item: QuoteMoney
    shipping: QuoteMoney
    tax: QuoteMoney | None = None
    fees: QuoteMoney | None = None
    total: QuoteMoney
    observed_at: float
    expires_at: float
    currency_explicit: bool = False
    tax_final: bool = False
    tax_hidden_or_deferred: bool = True
    shipping_final: bool = False
    destination_reference_observed: bool = False
    payment_reference_observed: bool = False
    all_payable_charges_included: bool = False

    @field_validator("quantity", mode="before")
    @classmethod
    def exactly_one(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("An observed quantity of exactly one is required.")
        return value

    @field_validator("observed_at", "expires_at", mode="before")
    @classmethod
    def timestamp(cls, value):
        if not _valid_time(value):
            raise ValueError("A finite nonnegative timestamp is required.")
        return value


def payment_preflight_readiness():
    """No live producer or committing capability can be enabled by configuration."""
    return {
        "mode": "no_submit_contract_assessment",
        "live_quote_producer": "unavailable",
        "final_payable_quote_verified": False,
        "order_submission": "unavailable",
        "payment_execution": "unavailable",
        "purchase_allowed": False,
    }


def _result(code, *, contract_satisfied=False, fingerprint=None):
    return {
        **payment_preflight_readiness(),
        "status": "blocked",
        "code": code,
        "contract_satisfied": contract_satisfied,
        "evidence_fingerprint": fingerprint,
    }


def assess_payment_quote(bindings, evidence=None, *, now):
    """Check supplied assertions only; every outcome still blocks live execution.

    Even complete data cannot establish a live source, destination/payment
    identity, authorization or current durable state. A fingerprint identifies
    only the supplied contract bytes; it is not a verified quote capability.
    """
    if not _valid_time(now):
        raise PaymentPreflightStopped("invalid_assessment_time")
    if type(bindings) is not PaymentQuoteBindings:
        raise PaymentPreflightStopped("payment_quote_bindings_required")
    try:
        # Pydantic's model_copy/model_construct can bypass field validation.
        bindings = PaymentQuoteBindings.model_validate(bindings.model_dump(warnings=False))
    except (ValidationError, ValueError, TypeError):
        raise PaymentPreflightStopped("payment_quote_bindings_invalid") from None
    if evidence is None:
        return _result("final_quote_evidence_unavailable")
    if type(evidence) is not PaymentQuoteEvidence:
        return _result("unsupported_quote_evidence")
    try:
        evidence = PaymentQuoteEvidence.model_validate(evidence.model_dump(warnings=False))
    except (ValidationError, ValueError, TypeError):
        return _result("quote_contract_invalid")
    if evidence.bindings != bindings:
        return _result("quote_context_changed")
    if evidence.surface != "noncommitting_final_quote":
        return _result("noncommitting_final_quote_surface_unverified")
    if (
        not evidence.observed_at <= now < evidence.expires_at
        or now - evidence.observed_at > MAX_QUOTE_AGE
        or not 0 < evidence.expires_at - evidence.observed_at <= MAX_QUOTE_AGE
    ):
        return _result("quote_freshness_unverified")
    components = (evidence.item, evidence.shipping, evidence.tax, evidence.fees)
    if not evidence.currency_explicit or any(
        value.currency != bindings.currency for value in (*components, evidence.total) if value
    ):
        return _result("quote_currency_unverified")
    if evidence.tax is None or not evidence.tax_final or evidence.tax_hidden_or_deferred:
        return _result("quote_tax_unverified")
    if not evidence.shipping_final:
        return _result("quote_shipping_finality_unverified")
    if not evidence.destination_reference_observed or not evidence.payment_reference_observed:
        return _result("quote_destination_or_payment_unverified")
    if not evidence.all_payable_charges_included:
        return _result("quote_payable_charge_coverage_unverified")
    # Absent fees remain None. Reconcile only the observed lines against an
    # independently asserted complete total, without manufacturing a fee line.
    known_total = sum((Decimal(value.amount) for value in components if value), Decimal(0))
    if known_total != Decimal(evidence.total.amount):
        return _result("quote_component_total_mismatch")
    if Decimal(evidence.total.amount) > Decimal(bindings.max_all_in):
        return _result("quote_above_all_in_ceiling")
    fingerprint = hashlib.sha256(
        json.dumps(evidence.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return _result(
        "quote_contract_complete_live_source_unavailable",
        contract_satisfied=True,
        fingerprint=fingerprint,
    )
