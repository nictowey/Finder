"""No-order server coordinator for a reviewed candidate and current-cart evidence.

There is no live final-quote producer or order executor. The optional quote
adapter below is an in-memory fixture; it cannot promote a cart read to a
payable quote or invoke checkout controls.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from finder.discogs_candidate_pipeline import CandidatePipeline, CandidateStopped
from finder.discogs_hosted_checkout import CheckoutStopped, HostedCheckoutPreflight
from finder.discogs_order_safety import OrderSafetyStore, OrderStopped
from finder.discogs_payment_preflight import (
    PaymentPreflightStopped,
    PaymentQuoteBindings,
    PaymentQuoteEvidence,
    assess_payment_quote,
)
from finder.discogs_rules import StrictInput, amount


def _opaque(value):
    try:
        parsed = UUID(value) if type(value) is str else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.int == 0 or str(parsed) != value:
        raise ValueError("Use a canonical opaque reference, not address or payment data.")
    return value


class CheckoutBindings(StrictInput):
    """Owner-selected references; they do not prove the cart uses that destination."""

    rules_revision: int = Field(ge=0)
    destination_ref: str
    payment_ref: str

    _reference = field_validator("destination_ref", "payment_ref")(_opaque)


class FixtureFinalQuoteEvidence(StrictInput):
    """Offline arithmetic fixture, never a live payable quote."""

    source: Literal["offline_checkout_fixture"]
    release_id: int = Field(gt=0)
    listing_id: str = Field(pattern=r"^[1-9][0-9]{0,15}$")
    rules_revision: int = Field(ge=0)
    item: str
    shipping: str
    tax: str
    fees: str
    total: str
    currency: Literal["USD", "EUR", "GBP", "CAD", "AUD"]
    destination_ref: str
    payment_ref: str
    final_quote_verified: Literal[False] = False
    purchase_allowed: Literal[False] = False

    _money = field_validator("item", "shipping", "tax", "fees", "total", mode="before")(amount)
    _reference = field_validator("destination_ref", "payment_ref")(_opaque)

    @model_validator(mode="after")
    def arithmetic(self):
        subtotal = sum(
            (Decimal(getattr(self, key)) for key in ("item", "shipping", "tax", "fees")),
            Decimal(0),
        )
        if subtotal != Decimal(self.total):
            raise ValueError("Fixture total does not match its components.")
        return self


@dataclass(frozen=True)
class FixtureQuoteAdapter:
    """In-memory fixture only. No browser, HTTP, payment or submit methods."""

    quote: FixtureFinalQuoteEvidence

    def read(self):
        return self.quote


class HostedNoOrderCoordinator:
    """Report quote gaps while preserving the durable one-copy/order fences."""

    def __init__(
        self,
        candidates: CandidatePipeline,
        preflight: HostedCheckoutPreflight,
        *,
        orders: OrderSafetyStore | None = None,
        quote_adapter: FixtureQuoteAdapter | None = None,
    ):
        if (
            type(candidates) is not CandidatePipeline
            or type(preflight) is not HostedCheckoutPreflight
            or preflight.candidates is not candidates
            or orders is not None
            and type(orders) is not OrderSafetyStore
            or quote_adapter is not None
            and type(quote_adapter) is not FixtureQuoteAdapter
        ):
            raise ValueError("Only the fixed no-order checkout ports are supported.")
        self.candidates, self.preflight = candidates, preflight
        self.orders, self.quote_adapter = orders, quote_adapter

    def readiness(self):
        return {
            "cart_preflight": "read_only",
            "final_quote_adapter": "offline_fixture" if self.quote_adapter else "unavailable",
            "order_ledger": "synthetic_intent_guard" if self.orders else "unavailable",
            "live_order_submission_enabled": False,
            "payment_quote_contract": "assessment_only",
            "purchase_allowed": False,
        }

    def inspect_payment_contract(
        self, release_id, listing_id, idempotency_key, bindings, evidence=None
    ):
        """Bind untrusted quote assertions to current cart-review state, with no action."""
        if type(bindings) is not PaymentQuoteBindings:
            raise CheckoutStopped("payment_contract_bindings_unverified")
        self._no_existing_order_intent(release_id)
        if type(evidence) is PaymentQuoteEvidence:
            try:
                evidence = PaymentQuoteEvidence.model_validate(evidence.model_dump(warnings=False))
            except (ValueError, TypeError):
                raise CheckoutStopped("payment_contract_evidence_unverified") from None
        try:
            before = self.candidates.cart_review_bundle(release_id, listing_id, idempotency_key)
            target = next(
                row for row in before["snapshot"]["targets"] if row["release_id"] == release_id
            )
            expected = PaymentQuoteBindings(
                release_id=release_id,
                listing_id=listing_id,
                rules_revision=before["snapshot"]["revision"],
                candidate_sequence=before["candidate_sequence"],
                observation_fingerprint=before["listing"]["observation_fingerprint"],
                claim_ref=idempotency_key,
                destination_ref=bindings.destination_ref,
                payment_ref=bindings.payment_ref,
                currency=target["currency"],
                max_all_in=target["max_total"],
            )
            if before["claim"]["state"] != "cart_observed" or bindings != expected:
                raise CheckoutStopped("payment_contract_context_changed")
            assess_payment_quote(expected, evidence, now=self.candidates.clock())
            after = self.candidates.cart_review_bundle(release_id, listing_id, idempotency_key)
            if before != after or after["claim"]["state"] != "cart_observed":
                raise CheckoutStopped("payment_contract_context_changed")
            self._no_existing_order_intent(release_id)
            # A state read can outlive a quote. Assess at the final clock too.
            result = assess_payment_quote(expected, evidence, now=self.candidates.clock())
            if result["contract_satisfied"] and (
                evidence.item.amount != before["listing"]["listing"]["item"]
                or before["listing"]["listing"]["currency"] not in (None, evidence.item.currency)
            ):
                result = {
                    **result,
                    "code": "quote_item_changed_from_candidate",
                    "contract_satisfied": False,
                    "evidence_fingerprint": None,
                }
        except (CandidateStopped, PaymentPreflightStopped, KeyError, ValueError, StopIteration):
            raise CheckoutStopped("payment_contract_current_state_unverified") from None
        return {
            **result,
            "release_id": release_id,
            "listing_id": listing_id,
            "rules_revision": expected.rules_revision,
            "candidate_sequence": expected.candidate_sequence,
            "claim_state": "cart_observed",
        }

    def inspect(self, release_id, listing_id, idempotency_key, *, bindings=None):
        if bindings is not None and type(bindings) is not CheckoutBindings:
            raise CheckoutStopped("checkout_bindings_unverified")
        # Once any intent exists, an uncertain or even confirmed-absent outcome
        # cannot silently recycle this release's one-copy slot.
        self._no_existing_order_intent(release_id)
        result = self.preflight.inspect(release_id, listing_id, idempotency_key)
        if bindings is None:
            return self._blocked(result, "destination_payment_binding_unavailable")
        if bindings.rules_revision != result["rules_revision"]:
            raise CheckoutStopped("checkout_bindings_stale")
        observed = result["evidence"]
        if observed.tax is None or observed.tax_hidden_unknown:
            return self._blocked(result, "cart_tax_unverified")
        if observed.displayed_total is None:
            return self._blocked(result, "cart_total_unverified")
        if self.quote_adapter is None:
            return self._blocked(result, "final_quote_adapter_unavailable")
        quote = self.quote_adapter.read()
        if (
            type(quote) is not FixtureFinalQuoteEvidence
            or quote.release_id != release_id
            or quote.listing_id != listing_id
            or quote.rules_revision != result["rules_revision"]
            or quote.destination_ref != bindings.destination_ref
            or quote.payment_ref != bindings.payment_ref
        ):
            raise CheckoutStopped("fixture_quote_identity_or_cart_unverified")
        if (
            (observed.subtotal and observed.subtotal != (quote.item, quote.currency))
            or (observed.shipping and observed.shipping != (quote.shipping, quote.currency))
            or observed.displayed_total[1] != quote.currency
            or Decimal(observed.displayed_total[0]) > Decimal(quote.total)
        ):
            raise CheckoutStopped("fixture_quote_changed_from_cart")
        try:
            current = self.candidates.review_bundle(release_id, listing_id, idempotency_key)
        except CandidateStopped:
            raise CheckoutStopped("candidate_changed_after_quote_read") from None
        if (
            current["snapshot"]["revision"] != bindings.rules_revision
            or current["candidate_sequence"] != result["candidate_sequence"]
            or current["listing"]["observation_fingerprint"] != result["observation_fingerprint"]
            or current["claim"]["state"] != "cart_observed"
        ):
            raise CheckoutStopped("candidate_changed_after_quote_read")
        return self._blocked(result, "offline_quote_cannot_authorize_purchase")

    def _no_existing_order_intent(self, release_id):
        try:
            existing = self.orders.status_for_release(release_id) if self.orders else None
        except OrderStopped:
            raise CheckoutStopped("invalid_order_intent_lookup") from None
        if existing is not None:
            raise CheckoutStopped("existing_order_intent_blocks_hosted_checkout")

    @staticmethod
    def _blocked(preflight, code):
        return {
            "status": "blocked",
            "code": code,
            "cart_code": preflight["code"],
            "release_id": preflight["release_id"],
            "listing_id": preflight["listing_id"],
            "rules_revision": preflight["rules_revision"],
            "final_payable_quote_verified": False,
            "order_submission": "unavailable",
            "purchase_allowed": False,
        }

    def submit(self, *_args, **_kwargs):
        raise CheckoutStopped("live_order_submission_unavailable")
