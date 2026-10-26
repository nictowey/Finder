"""Dormant claim-bound cart preparation coordinator.

No browser, socket, or live adapter is configured here. A caller must supply an
already verified exact-release snapshot and hosted item detail, plus an explicit
cart-write decision and an adapter. Cart preparation never authorizes an order.
"""

import json
import time
from datetime import UTC, datetime

from finder.discogs_candidate_detail import VerifiedHostedDetail
from finder.discogs_candidate_pipeline import (
    CandidatePipeline,
    CandidateStopped,
    observation_fingerprint,
)
from finder.discogs_cart_claims import CartClaimBlocked, CartClaimStore
from finder.discogs_release_snapshots import RenderedReleaseSnapshot, _timestamp
from finder.discogs_rules import Observation, RulesStore, Target, evaluate


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
    # The current hosted item reader reports currency and destination charges
    # as unknown. Cart preparation cannot treat an item price or reported
    # shipping text as an all-in payable quote or verified pressing.
    if (
        detail.observation.currency is None
        or detail.observation.pressing_evidence != "confirmed"
        or any(
            getattr(detail.observation, field) is None
            for field in ("item", "shipping", "tax", "fees")
        )
    ):
        return _stop("hosted_pre_cart_qualification_unavailable")
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
    store = RulesStore(candidates.database)
    try:
        saved = store.snapshot()
        target = next((row for row in saved["targets"] if row["release_id"] == release_id), None)
        if saved["revision"] != rules_revision or target is None:
            return _stop("cart_candidate_review_unverified")
        verdict = evaluate(
            Target.model_validate(target),
            detail.observation,
            now=datetime.fromtimestamp(clock(), UTC),
            already_owned=release_id in saved["owned"],
        )
        if verdict["eligible_for_review"] is not True or verdict["purchase_allowed"] is not False:
            return _stop("cart_candidate_review_unverified")
    except (KeyError, TypeError, ValueError):
        return _stop("cart_candidate_review_unverified")
    finally:
        store.close()

    def reviewed(state):
        try:
            bundle = candidates.review_bundle(release_id, listing_id, idempotency_key)
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
                or bundle["snapshot"]["revision"] != rules_revision
                or bundle["claim"]["state"] != state
                or bundle["claim"]["purchase_allowed"] is not False
                or listing["evaluation"]["eligible_for_review"] is not True
                or listing["evaluation"]["purchase_allowed"] is not False
                or evidence["source"] != "discogs_rendered_listing"
                or evidence["delivered_total_verified"] is not True
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
        reserved = candidates.reserve_rehearsal(release_id, listing_id, idempotency_key)
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
        if check_current() != expected_guard or reviewed("attempting") != review:
            state = "uncertain"
            outcome = None
        else:
            outcome = adapter.prepare(release_id=release_id, listing_id=listing_id)
            state = "cart_observed" if outcome == "cart_observed" else "uncertain"
    except Exception:
        state = "uncertain"
    try:
        claim = claims.transition(idempotency_key, state)
    except CartClaimBlocked:
        return _stop("cart_claim_transition_uncertain")
    return _stop(
        "cart_observed_no_order" if state == "cart_observed" else "cart_outcome_uncertain", claim
    )
