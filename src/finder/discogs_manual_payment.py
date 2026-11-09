"""Read-only manual-payment presentation of the existing durable order ledger.

This adds no lifecycle storage, order creator, payment executor or live reader.
The ledger has already checked listing, seller and quoted amount before recording
``order_seen``. This projection binds that recorded result to the exact intent,
release and order the caller expects. Recorded status is not a fresh website read.

No canonical Discogs buyer-order URL grammar has been verified in the retained
evidence. Consequently no URL is accepted, constructed or returned here. An order
record does not establish a reservation or guarantee continued availability.
"""

import sqlite3
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from finder.discogs_order_reconciliation import ORDER_ID, DiscogsOrderLedger
from finder.discogs_rules import StrictInput


class ManualPaymentStopped(RuntimeError):
    """A fixed local stop; never a database, provider or account payload."""


class ManualPaymentIdentity(StrictInput):
    """Expected recorded identity, not an order or payment authorization."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    intent_id: str
    release_id: int = Field(gt=0, le=9007199254740991)
    order_id: str

    @field_validator("intent_id")
    @classmethod
    def opaque_intent(cls, value):
        try:
            parsed = UUID(value)
        except (ValueError, AttributeError):
            raise ValueError("A canonical opaque intent reference is required.") from None
        if parsed.int == 0 or str(parsed) != value:
            raise ValueError("A canonical opaque intent reference is required.")
        return value

    @field_validator("order_id")
    @classmethod
    def exact_order(cls, value):
        if not ORDER_ID.fullmatch(value):
            raise ValueError("An exact recorded Discogs order ID is required.")
        return value


def _result(identity, code, *, order_id=None, payment="unknown", action="review_order"):
    return {
        "mode": "no_submit_manual_payment_handoff",
        "source": "durable_order_ledger_snapshot",
        "code": code,
        "intent_id": identity.intent_id,
        "release_id": identity.release_id,
        "order_id": order_id,
        "recorded_payment_state": payment,
        "manual_payment_action": action,
        "live_order_checked": False,
        "order_url": None,
        "order_link_status": "canonical_route_unverified",
        "handoff_available": False,
        "reservation_verified": False,
        "availability_guaranteed": False,
        "order_submission": "unavailable",
        "payment_execution": "unavailable",
        "may_submit_again": False,
        "purchase_allowed": False,
    }


def manual_payment_handoff(ledger, identity):
    """Read status as recorded; never invite another payment while it is processing.

    ``owner_review_before_payment`` means the ledger last recorded an unpaid
    order. It cannot prove payment is still due. A future handoff must verify the
    ordinary Discogs order link and current order/payment state separately.
    """
    if type(ledger) is not DiscogsOrderLedger or type(identity) is not ManualPaymentIdentity:
        raise ManualPaymentStopped("durable_ledger_and_exact_identity_required")
    try:
        identity = ManualPaymentIdentity.model_validate(identity.model_dump(warnings=False))
    except (ValueError, TypeError):
        raise ManualPaymentStopped("manual_payment_identity_unverified") from None
    try:
        recorded = ledger.status(identity.intent_id)
    except (OSError, sqlite3.Error, ValueError):
        raise ManualPaymentStopped("durable_order_status_unavailable") from None
    if recorded is None:
        return _result(identity, "order_not_recorded")
    if (
        recorded.get("intent_id") != identity.intent_id
        or recorded.get("release_id") != identity.release_id
        or recorded.get("may_submit_again") is not False
        or recorded.get("purchase_allowed") is not False
    ):
        return _result(identity, "recorded_order_identity_unverified")
    if recorded.get("state") == "prepared":
        return _result(identity, "order_creation_not_observed")
    if recorded.get("state") == "submission_unknown":
        return _result(identity, "order_creation_outcome_unknown")
    if recorded.get("order_id") != identity.order_id:
        return _result(identity, "recorded_order_requires_review")
    payment = recorded.get("payment_state")
    if recorded.get("state") != "order_seen" or recorded.get("review_required") is not False:
        return _result(
            identity,
            "recorded_order_requires_review",
            order_id=identity.order_id,
            payment=payment if payment in {"unpaid", "pending", "paid"} else "unknown",
        )
    states = {
        "unpaid": ("order_created_unpaid_as_recorded", "owner_review_before_payment"),
        "pending": ("payment_processing_as_recorded", "await_payment_reconciliation"),
        "paid": ("payment_completed_as_recorded", "none"),
    }
    if payment not in states:
        return _result(identity, "recorded_payment_outcome_unknown", order_id=identity.order_id)
    code, action = states[payment]
    return _result(identity, code, order_id=identity.order_id, payment=payment, action=action)
