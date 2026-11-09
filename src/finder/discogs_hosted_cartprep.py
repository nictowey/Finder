"""Dormant claim-bound cart preparation coordinator.

No browser, socket, or live adapter is configured here. A caller must supply an
already verified exact-release snapshot and hosted item detail, plus an explicit
cart-write decision and an adapter. Cart preparation never authorizes an order.
"""

import json
import time
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from finder.discogs_candidate_detail import VerifiedHostedDetail
from finder.discogs_candidate_pipeline import (
    CandidatePipeline,
    CandidateStopped,
    observation_fingerprint,
)
from finder.discogs_cart_claims import CartClaimBlocked, CartClaimStore
from finder.discogs_release_snapshots import RenderedReleaseSnapshot, _timestamp
from finder.discogs_rules import Observation, RulesStore, StrictInput, Target, evaluate_pre_cart


class CartInspectionAuthorization(StrictInput):
    """Explicit, short-lived scope for one reversible cart quote inspection.

    This is an input contract, not a credential or a runtime grant issuer.
    The durable one-copy claim still enforces single use across processes.
    """

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)

    action: Literal["cart_quote_inspection"]
    release_id: int = Field(gt=0, le=9007199254740991)
    listing_id: str = Field(pattern=r"^[1-9][0-9]{0,15}$")
    seller_handle: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    rules_revision: int = Field(ge=0)
    idempotency_key: str
    observation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at: float
    expires_at: float

    @model_validator(mode="after")
    def bounded(self):
        if not 0 < self.expires_at - self.issued_at <= 300:
            raise ValueError("Cart inspection authorization lifetime must be at most five minutes.")
        return self

    @field_validator("idempotency_key")
    @classmethod
    def canonical_key(cls, value):
        try:
            parsed = UUID(value)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("A canonical idempotency UUID is required.") from None
        if parsed.int == 0 or str(parsed) != value:
            raise ValueError("A nonzero canonical idempotency UUID is required.")
        return value


def _stop(code, claim=None):
    return {
        "status": "blocked",
        "code": code,
        "claim_state": claim.state if claim else None,
        "purchase_allowed": False,
    }


def prepare_hosted_cart(
    *,
    release_id,
    listing_id,
    rules_revision,
    idempotency_key,
    snapshot,
    detail,
    claims,
    candidates=None,
    adapter=None,
    check_current=None,
    authorization=None,
    allow_cart_write=False,
    clock=time.time,
):
    """Bind seller and exact item before one durable, intercepted cart attempt.

    The adapter contract is a single ``prepare(release_id, listing_id)`` call
    returning ``cart_observed`` only after cart reconciliation. Any other result
    or exception is uncertain and permanently blocks automatic replay.
    """
    if not isinstance(claims, CartClaimStore) or not isinstance(snapshot, RenderedReleaseSnapshot):
        return _stop("cart_evidence_unavailable")
    if not isinstance(detail, VerifiedHostedDetail) or not isinstance(
        detail.observation, Observation
    ):
        return _stop("hosted_detail_unavailable")
    # A dataclass wrapper is not proof of hosted provenance. In particular, an
    # eligible synthetic Observation must never become a hosted cart decision.
    if detail.observation.source != "discogs_rendered_listing":
        return _stop("hosted_detail_provenance_unverified")
    if (
        type(release_id) is not int
        or type(listing_id) is not str
        or type(rules_revision) is not int
    ):
        return _stop("cart_identity_invalid")
    if (
        snapshot.status != "observed"
        or snapshot.complete is not True
        or snapshot.has_next is not False
        or snapshot.release_id != release_id
        or not 0 <= clock() - _timestamp(snapshot.observed_at) <= 300
        or detail.requested_release != release_id
        or detail.requested_listing != listing_id
        or detail.route != f"/sell/item/{listing_id}"
        or detail.observation.release_id != release_id
        or detail.observation.listing_id != listing_id
        or detail.cart_state != "addable"
        or not 0 <= clock() - detail.completed_at <= 300
    ):
        return _stop("cart_evidence_stale_or_mismatched")
    rows = [row for row in snapshot.rows if row.listing_id == listing_id]
    if len(rows) != 1 or rows[0].url != f"https://www.discogs.com/sell/item/{listing_id}":
        return _stop("cart_listing_not_unique")
    market_seller = rows[0].fields.seller_handle
    if not market_seller or not detail.seller_handle or market_seller != detail.seller_handle:
        return _stop("cart_seller_unverified_or_changed")
    if allow_cart_write is not True or adapter is None or check_current is None:
        return _stop("cart_write_disabled")
    if (
        not isinstance(authorization, CartInspectionAuthorization)
        or authorization.action != "cart_quote_inspection"
        or authorization.release_id != release_id
        or authorization.listing_id != listing_id
        or authorization.seller_handle != market_seller
        or authorization.rules_revision != rules_revision
        or authorization.idempotency_key != idempotency_key
        or authorization.observation_fingerprint != observation_fingerprint(detail.observation)
        or not authorization.issued_at <= clock() < authorization.expires_at
    ):
        return _stop("cart_inspection_authorization_unverified")
    if (
        type(candidates) is not CandidatePipeline
        or candidates.claims is None
        or candidates.claims.path != claims.path
    ):
        return _stop("cart_candidate_review_unavailable")
    expected_guard = {
        "release_id": release_id,
        "listing_id": listing_id,
        "rules_revision": rules_revision,
        "seller_handle": market_seller,
        "allowed": True,
    }
    try:
        if check_current() != expected_guard:
            return _stop("cart_current_state_changed")
    except Exception:
        return _stop("cart_current_state_unavailable")

    def assessment_current(saved):
        target = next((row for row in saved["targets"] if row["release_id"] == release_id), None)
        if saved["revision"] != rules_revision or target is None:
            return False
        verdict = evaluate_pre_cart(
            Target.model_validate(target),
            detail.observation,
            now=datetime.fromtimestamp(clock(), UTC),
            already_owned=release_id in saved["owned"],
        )
        return verdict["eligible_for_cart_inspection"] is True

    store = RulesStore(candidates.database)
    try:
        if not assessment_current(store.snapshot()):
            return _stop("cart_current_assessment_blocked")
    except (KeyError, TypeError, ValueError):
        return _stop("cart_current_assessment_blocked")
    finally:
        store.close()

    def reviewed(state):
        try:
            bundle = candidates.cart_review_bundle(release_id, listing_id, idempotency_key)
            listing = bundle["listing"]
            evidence = listing["detail_evidence"]
            with_store = RulesStore(candidates.database)
            try:
                baseline = with_store.db.execute(
                    "SELECT baseline FROM discogs_release_baselines WHERE release_id=?",
                    (release_id,),
                ).fetchone()
            finally:
                with_store.close()
            if (
                not baseline
                or json.loads(baseline[0]) != snapshot.model_dump()
                or not assessment_current(bundle["snapshot"])
                or bundle["snapshot"]["revision"] != rules_revision
                or bundle["claim"]["state"] != state
                or bundle["claim"]["purchase_allowed"] is not False
                or listing["cart_inspection_evaluation"]["eligible_for_cart_inspection"] is not True
                or listing["cart_inspection_evaluation"]["purchase_allowed"] is not False
                or listing["cart_inspection_evaluation"]["final_quote_verified"] is not False
                or evidence["source"] != "discogs_rendered_listing"
                or evidence["delivered_total_verified"] is not False
                or evidence["seller_handle"] != market_seller
                or listing["observation_fingerprint"] != observation_fingerprint(detail.observation)
            ):
                return None
            return (
                bundle["candidate_sequence"],
                listing["observation_fingerprint"],
                bundle["snapshot"]["revision"],
            )
        except (CandidateStopped, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    try:
        reserved = candidates.reserve_cart_inspection(
            release_id,
            listing_id,
            idempotency_key,
            seller_handle=market_seller,
            fingerprint=observation_fingerprint(detail.observation),
        )
        claim = claims.get(idempotency_key)
        if reserved["rules_revision"] != rules_revision or claim is None:
            return _stop("cart_candidate_review_unverified", claim)
        if claim.state != "reserved":
            return _stop("cart_claim_already_used", claim)
        review = reviewed("reserved")
        if review is None:
            return _stop("cart_candidate_review_unverified", claim)
        claim = claims.transition(idempotency_key, "attempting")
    except (CandidateStopped, CartClaimBlocked, KeyError, TypeError, ValueError):
        return _stop("cart_claim_unavailable")
    try:
        # Claim is now durable and cannot be replayed. Recheck the current
        # saved rule/ownership/control state immediately before any adapter call.
        if (
            check_current() != expected_guard
            or reviewed("attempting") != review
            or not authorization.issued_at <= clock() < authorization.expires_at
        ):
            state = "uncertain"
            outcome = None
        else:
            outcome = adapter.prepare(release_id=release_id, listing_id=listing_id)
            state = (
                "cart_observed"
                if outcome == "cart_observed"
                and check_current() == expected_guard
                and reviewed("attempting") == review
                and authorization.issued_at <= clock() < authorization.expires_at
                else "uncertain"
            )
    except Exception:
        state = "uncertain"
    try:
        claim = claims.transition(idempotency_key, state)
    except CartClaimBlocked:
        return _stop("cart_claim_transition_uncertain")
    return _stop(
        "cart_observed_no_order" if state == "cart_observed" else "cart_outcome_uncertain", claim
    )
