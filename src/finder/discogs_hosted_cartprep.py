"""Dormant claim-bound cart preparation coordinator.

No browser, socket, or live adapter is configured here. A caller must supply an
already verified exact-release snapshot and hosted item detail, plus an explicit
cart-write decision and an adapter. Cart preparation never authorizes an order.
"""

import time

from finder.discogs_candidate_detail import VerifiedHostedDetail
from finder.discogs_cart_claims import CartClaimBlocked, CartClaimStore
from finder.discogs_release_snapshots import RenderedReleaseSnapshot, _timestamp


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
    if not isinstance(detail, VerifiedHostedDetail):
        return _stop("hosted_detail_unavailable")
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
    try:
        claim = claims.reserve(release_id, listing_id, rules_revision, idempotency_key)
        if claim.state != "reserved":
            return _stop("cart_claim_already_used", claim)
        claim = claims.transition(idempotency_key, "attempting")
    except (CartClaimBlocked, ValueError):
        return _stop("cart_claim_unavailable")
    try:
        # Claim is now durable and cannot be replayed. Recheck the current
        # saved rule/ownership/control state immediately before any adapter call.
        if check_current() != expected_guard:
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
