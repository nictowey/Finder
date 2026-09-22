"""Generic observed listings. Product identity and valuation are future work."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field, field_validator


class Monitor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    marketplace: str = Field(min_length=1)
    query: str = Field(min_length=1)
    description: str = ""
    source_options: dict[str, Any] = Field(default_factory=dict)


class Listing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    marketplace: str
    marketplace_item_id: str
    title: str
    current_price: Decimal | None = None
    currency: str | None = None
    price_kind: Literal["fixed_price", "current_bid", "unknown"] = "unknown"
    shipping_cost: Decimal | None = None
    shipping_currency: str | None = None
    condition: str | None = None
    condition_id: str | None = None
    seller_id: str | None = None
    seller_username: str | None = None
    seller_feedback_percentage: Decimal | None = None
    seller_feedback_score: int | None = None
    listing_url: str | None = None
    primary_image: str | None = None
    item_specifics: dict[str, list[str]] = Field(default_factory=dict)
    categories: list[dict[str, str]] = Field(default_factory=list)
    buying_formats: list[str] = Field(default_factory=list)
    listing_created_at: AwareDatetime | None = None
    listing_origin_at: AwareDatetime | None = None
    listing_ends_at: AwareDatetime | None = None
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime
    details_observed_at: AwareDatetime | None = None
    quality_flags: list[str] = Field(default_factory=list)
    # Provider metadata stays opaque to the core; no vinyl fields belong here.
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "first_observed_at",
        "last_observed_at",
        "details_observed_at",
        "listing_created_at",
        "listing_origin_at",
        "listing_ends_at",
    )
    @classmethod
    def normalize_timezone(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @computed_field
    @property
    def total_acquisition_cost(self) -> Decimal | None:
        """One unit, price plus quoted shipping; excludes tax/duty/other fees."""
        if (
            self.current_price is None
            or self.shipping_cost is None
            or self.currency is None
            or self.shipping_currency != self.currency
        ):
            return None
        return self.current_price + self.shipping_cost


class Product(BaseModel):
    """A catalog work/release family, independent of a marketplace listing."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    catalog_source: str
    catalog_product_id: str
    title: str
    artists: list[str] = Field(default_factory=list)
    resource_url: str | None = None
    observed_at: AwareDatetime
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def normalize_product_timezone(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class Variant(BaseModel):
    """One exact catalog release/edition that a listing may represent."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    catalog_source: str
    catalog_variant_id: str
    catalog_product_id: str
    title: str
    artists: list[str] = Field(default_factory=list)
    release_year: int | None = None
    country: str | None = None
    formats: list[dict[str, Any]] = Field(default_factory=list)
    labels: list[dict[str, Any]] = Field(default_factory=list)
    identifiers: dict[str, list[str]] = Field(default_factory=dict)
    genres: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
    resource_url: str | None = None
    observed_at: AwareDatetime
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def normalize_variant_timezone(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class MatchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field: str
    listing_values: list[str] = Field(default_factory=list)
    variant_values: list[str] = Field(default_factory=list)
    matched: bool
    weight: int = Field(ge=0)


class ListingVariantCandidate(BaseModel):
    """Auditable deterministic evidence; this does not assert an exact match."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    marketplace: str
    marketplace_item_id: str
    catalog_source: str
    catalog_variant_id: str
    score: int = Field(ge=0, le=100)
    status: Literal["candidate", "strong_candidate", "rejected"]
    evidence: list[MatchEvidence] = Field(default_factory=list)
    observed_at: AwareDatetime

    @field_validator("observed_at")
    @classmethod
    def normalize_candidate_timezone(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class EvidenceRecord(BaseModel):
    """One source value used in a decision, with its provenance intact."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    field: str
    value: str
    source: Literal["marketplace_listing", "catalog_release"]
    method: Literal["seller_structured", "seller_title", "catalog_structured"]
    reliability_class: Literal["seller_claim", "catalog_metadata"]
    observed_at: AwareDatetime


class MatchDecision(BaseModel):
    """A policy result, never an assertion inferred from a raw candidate score."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal[
        "exact_variant",
        "probable_variant",
        "family_only",
        "ambiguous",
        "rejected",
        "insufficient_data",
    ]
    policy_version: str
    catalog_source: str
    family_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
