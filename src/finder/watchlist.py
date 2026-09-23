"""Internal, deterministic watchlist triage; no live availability or value claims."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from finder.domain import Listing, MatchDecision


class WatchTarget(BaseModel):
    """One buyer's ceiling for a catalog family or exact release, versioned for later storage."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    marketplace: str = Field(min_length=1)
    catalog_source: str = Field(min_length=1)
    family_id: str | None = Field(default=None, min_length=1)
    variant_id: str | None = Field(default=None, min_length=1)
    maximum_delivered_subtotal: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # An explicit allow-list of provider condition IDs avoids guessing vinyl media grades.
    acceptable_condition_ids: frozenset[str] = Field(default_factory=frozenset)
    destination_country: str = Field(pattern=r"^[A-Z]{2}$")
    destination_postal_code: str = Field(min_length=1)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def one_identity_level(self) -> "WatchTarget":
        if (self.family_id is None) == (self.variant_id is None):
            raise ValueError("Specify exactly one family_id or variant_id")
        return self

    @field_validator("maximum_delivered_subtotal")
    @classmethod
    def finite_money(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Maximum delivered subtotal must be finite")
        return value

    @field_validator("acceptable_condition_ids")
    @classmethod
    def nonblank_conditions(cls, values: frozenset[str]) -> frozenset[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Condition IDs cannot be blank")
        return values


class WatchAssessment(BaseModel):
    """Internal triage only; a candidate is not a deal or an availability assertion."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    target_id: str
    marketplace_item_id: str
    status: Literal["candidate", "review", "excluded"]
    reasons: list[str]
    delivered_subtotal: Decimal | None
    currency: str | None
    listing_observed_at: AwareDatetime


def assess_watch_target(
    target: WatchTarget,
    listing: Listing,
    decision: MatchDecision,
    *,
    as_of: datetime,
    maximum_age: timedelta,
    destination_quote_verified: bool,
) -> WatchAssessment:
    """Apply a buyer's ceiling conservatively, without market value or grade inference.

    The caller must establish that the shipping quote is for the target destination; a
    same-currency number in the listing alone does not establish that fact.
    """
    if as_of.tzinfo is None or maximum_age <= timedelta(0):
        raise ValueError("as_of must be timezone-aware and maximum_age positive")
    if (listing.marketplace, listing.marketplace_item_id) != (
        decision.marketplace,
        decision.marketplace_item_id,
    ):
        raise ValueError("Match decision belongs to a different listing")

    excluded: list[str] = []
    review: list[str] = []
    if listing.marketplace != target.marketplace:
        excluded.append("marketplace_mismatch")
    if decision.catalog_source != target.catalog_source:
        review.append("catalog_source_unconfirmed")
    if "catalog_search_incomplete" in decision.missing_evidence:
        review.append("catalog_search_incomplete")
    if decision.outcome == "rejected":
        excluded.append("identity_rejected")
    elif target.family_id:
        if target.family_id not in decision.family_ids:
            review.append("target_family_unconfirmed")
        elif len(decision.family_ids) != 1 or decision.outcome in (
            "ambiguous",
            "insufficient_data",
        ):
            review.append("identity_ambiguous")
    elif target.variant_id not in decision.candidate_ids:
        review.append("target_variant_unconfirmed")
    elif decision.outcome != "exact_variant" or decision.candidate_ids != [target.variant_id]:
        review.append("exact_pressing_unconfirmed")

    if listing.currency != target.currency:
        excluded.append("currency_mismatch")
    if listing.price_kind != "fixed_price":
        review.append("final_price_unknown")
    if target.acceptable_condition_ids:
        if not listing.condition_id:
            review.append("condition_unknown")
        elif listing.condition_id not in target.acceptable_condition_ids:
            excluded.append("condition_excluded")
    if listing.last_observed_at > as_of or as_of - listing.last_observed_at > maximum_age:
        review.append("listing_stale")
    if listing.listing_ends_at and listing.listing_ends_at <= as_of:
        excluded.append("listing_ended")

    subtotal = listing.total_acquisition_cost
    if subtotal is None:
        review.append("delivered_subtotal_unknown")
    elif listing.currency == target.currency and subtotal > target.maximum_delivered_subtotal:
        excluded.append("above_buyer_ceiling")
    if not destination_quote_verified:
        review.append("destination_quote_unverified")
    if "details_unavailable" in listing.quality_flags:
        review.append("listing_details_unavailable")

    reasons = excluded + review
    return WatchAssessment(
        target_id=target.id,
        marketplace_item_id=listing.marketplace_item_id,
        status="excluded" if excluded else "review" if review else "candidate",
        reasons=reasons,
        delivered_subtotal=subtotal,
        currency=listing.currency,
        listing_observed_at=listing.last_observed_at,
    )
