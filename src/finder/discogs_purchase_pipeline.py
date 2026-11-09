"""Fixture-only handoff from a reviewed candidate to the simulated order fence.

No hosted service constructs this coordinator. The only injectable executor is
the in-memory SyntheticOrderAdapter; there is no browser or provider transport.
"""

import time

from finder.discogs_candidate_pipeline import (
    CandidatePipeline,
    CandidateStopped,
    observation_fingerprint,
)
from finder.discogs_order_safety import (
    OrderSafetyStore,
    OrderStopped,
    SyntheticFinalQuote,
    SyntheticOrderAdapter,
)
from finder.discogs_rules import Observation, Target


class OfflinePurchasePipeline:
    """Tie a current synthetic candidate and observed-cart claim to one order intent."""

    def __init__(
        self,
        candidates: CandidatePipeline,
        orders: OrderSafetyStore,
        *,
        executor: SyntheticOrderAdapter | None = None,
        clock=time.time,
    ):
        if (
            type(candidates) is not CandidatePipeline
            or type(orders) is not OrderSafetyStore
            or executor is not None
            and type(executor) is not SyntheticOrderAdapter
        ):
            raise ValueError(
                "Only the isolated candidate and synthetic order stores are supported."
            )
        self.candidates, self.orders, self.executor, self.clock = (
            candidates,
            orders,
            executor,
            clock,
        )

    def readiness(self):
        return {
            "mode": "synthetic_only",
            "candidate_handoff": "explicit_fixture",
            "executor": "synthetic_fixture" if self.executor is not None else "unavailable",
            "live_quote_verified": False,
            "live_order_submission_enabled": False,
            "purchase_allowed": False,
        }

    def _bound_candidate(self, release, listing, intent_id, observation):
        if not isinstance(observation, Observation) or observation.source != "synthetic":
            raise OrderStopped("synthetic_observation_required")
        try:
            bundle = self.candidates.review_bundle(release, listing, intent_id)
            claim, reviewed = bundle["claim"], bundle["listing"]
            if (
                claim["mode"] != "durable_cart_rehearsal"
                or claim["state"] != "cart_observed"
                or claim["idempotency_key"] != intent_id
                or claim["purchase_allowed"] is not False
                or reviewed["detail_evidence"]["source"] != "synthetic"
                or reviewed["detail_evidence"]["delivered_total_verified"] is not True
                or reviewed["evaluation"]["eligible_for_review"] is not True
                or reviewed["evaluation"]["purchase_allowed"] is not False
                or reviewed["observation_fingerprint"] != observation_fingerprint(observation)
            ):
                raise OrderStopped("candidate_or_observed_cart_unverified")
            revision = bundle["snapshot"]["revision"]
            target = Target.model_validate(
                next(
                    value
                    for value in bundle["snapshot"]["targets"]
                    if value["release_id"] == release
                )
            )
            if target.release_id != observation.release_id or listing != observation.listing_id:
                raise OrderStopped("candidate_or_observed_cart_unverified")
            return target, revision
        except (CandidateStopped, KeyError, StopIteration, ValueError, TypeError):
            raise OrderStopped("candidate_or_observed_cart_unverified") from None

    def issue_simulated_grant(
        self,
        grant_id,
        intent_id,
        release,
        listing,
        observation,
        max_all_in,
        destination_ref,
        payment_ref,
        expires_at,
    ):
        target, revision = self._bound_candidate(release, listing, intent_id, observation)
        return self.orders.issue_simulated_grant(
            grant_id,
            target,
            revision,
            max_all_in,
            destination_ref,
            payment_ref,
            expires_at,
            now=self.clock(),
        )

    def prepare(self, grant_id, intent_id, release, listing, observation, quote):
        target, revision = self._bound_candidate(release, listing, intent_id, observation)
        if not isinstance(quote, SyntheticFinalQuote):
            raise OrderStopped("synthetic_final_quote_required")
        result = self.orders.prepare(
            grant_id, intent_id, target, revision, observation, quote, now=self.clock()
        )
        # A racing change may retain a conservative intent, but cannot reach an executor.
        self._bound_candidate(release, listing, intent_id, observation)
        return result

    def simulate_attempt(self, intent_id, release, listing, observation, quote):
        if self.executor is None:
            raise OrderStopped("executor_unavailable")
        target, revision = self._bound_candidate(release, listing, intent_id, observation)
        return self.orders.simulate_attempt(
            intent_id, target, revision, quote, self.executor, now=self.clock()
        )

    def reconcile(self, intent_id):
        """Read-only lookup remains possible when the target has since paused."""
        if self.executor is None:
            raise OrderStopped("executor_unavailable")
        return self.orders.reconcile(intent_id, self.executor, now=self.clock())
