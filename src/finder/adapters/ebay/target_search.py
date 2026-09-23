"""Versioned, bounded eBay discovery plans for a collector's saved catalog target."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finder.adapters.base import AdapterStats
from finder.domain import Monitor
from finder.persistence import ListingRepository
from finder.service import ScanSummary, run_scan

if TYPE_CHECKING:
    from finder.adapters.ebay.adapter import EbayAdapter


class EbaySearchTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    plan_version: Literal[1] = 1
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    catalog_source: Literal["discogs"] = "discogs"
    catalog_variant_id: int = Field(gt=0)
    queries: list[str] = Field(min_length=1, max_length=3)
    marketplace_id: str = Field(default="EBAY_US", pattern=r"^EBAY_[A-Z]{2,10}$")
    category_id: str = Field(default="176985", pattern=r"^\d+$")

    @model_validator(mode="after")
    def distinct_queries(self) -> EbaySearchTarget:
        if any(not query.strip() or len(query) > 100 for query in self.queries):
            raise ValueError("Queries must be nonblank and at most 100 characters")
        if len({query.casefold() for query in self.queries}) != len(self.queries):
            raise ValueError("Queries must be distinct")
        return self


def plan_target_search(
    target: EbaySearchTarget, *, mode: Literal["initial", "refresh"]
) -> list[Monitor]:
    """At most three searches and ten detailed items per search."""
    return [
        Monitor(
            id=f"{target.id}-v{target.plan_version}-{index}",
            name=f"{target.id} search {index}",
            marketplace="ebay",
            query=query,
            description=f"Target search plan v{target.plan_version}; {mode} coverage is bounded.",
            source_options={
                "marketplace_id": target.marketplace_id,
                "category_ids": [target.category_id],
                "buying_options": ["FIXED_PRICE", "AUCTION"],
                "sort": "bestMatch" if mode == "initial" else "newlyListed",
                "page_size": 10,
                "max_pages": 1,
                "fetch_details": True,
            },
        )
        for index, query in enumerate(target.queries, 1)
    ]


class _SharedSearch:
    def __init__(self, adapter: EbayAdapter):
        self.adapter = adapter
        self.seen: set[str] = set()

    @property
    def stats(self) -> AdapterStats:
        return self.adapter.stats

    def search(self, monitor: Monitor):
        return self.adapter.search(monitor, seen_item_ids=self.seen)


def run_target_scan(
    target: EbaySearchTarget,
    adapter: EbayAdapter,
    repository: ListingRepository,
    *,
    mode: Literal["initial", "refresh"],
) -> list[ScanSummary]:
    """Stop after a failed query; dedupe identity and detail requests across queries."""
    shared = _SharedSearch(adapter)
    results: list[ScanSummary] = []
    for monitor in plan_target_search(target, mode=mode):
        result = run_scan(monitor, shared, repository)
        results.append(result)
        if result.status != "completed":
            break
    return results
