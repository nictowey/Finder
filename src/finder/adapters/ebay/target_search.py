"""Versioned, bounded eBay discovery plans for a collector's saved catalog target."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finder.adapters.base import AdapterStats
from finder.domain import Monitor
from finder.persistence import ListingRepository
from finder.service import ScanSummary, run_scan

if TYPE_CHECKING:
    from finder.adapters.ebay.adapter import EbayAdapter


# Inventory pages and one known-lead recheck alternate. At three watches and 48 scans/day,
# the non-retry Browse ceiling is 3 * 48 * (3 * (1 + 8) + ((1 + 6) + 1) / 2) = 4,464.
REFRESH_PAGE_SIZE = 8
INVENTORY_PAGE_SIZE = 6
INVENTORY_MAX_OFFSET = 30  # Six pages per query; never interpret this as complete inventory.


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
    target: EbaySearchTarget, *, mode: Literal["initial", "refresh"], page_size: int = 10
) -> list[Monitor]:
    """At most three newest/relevance searches with a bounded result page."""
    if not 1 <= page_size <= 10:
        raise ValueError("Target search page size must be between 1 and 10")
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
                "page_size": page_size,
                "max_pages": 1,
                "fetch_details": True,
            },
        )
        for index, query in enumerate(target.queries, 1)
    ]


@dataclass(frozen=True)
class InventoryPass:
    query_index: int
    offset: int


@dataclass(frozen=True)
class InventoryCursor:
    """A private, resumable offset sample. Offset pages are not a stable API snapshot."""

    signature: str
    revision: int
    next_index: int
    offsets: tuple[int, ...]
    due: bool = True

    @staticmethod
    def load(target: EbaySearchTarget, revision: int, summary: dict | None) -> InventoryCursor:
        signature = hashlib.sha256(
            repr((target.plan_version, target.catalog_variant_id, target.queries)).encode()
        ).hexdigest()[:16]
        default = InventoryCursor(signature, revision, 0, (0,) * len(target.queries))
        saved = summary.get("inventory") if isinstance(summary, dict) else None
        if (
            not isinstance(saved, dict)
            or saved.get("version") != 1
            or saved.get("signature") != signature
        ):
            return default
        offsets, index, due = (
            saved.get("offsets"),
            saved.get("next_index"),
            saved.get("due"),
        )
        if (
            type(saved.get("revision")) is not int
            or saved["revision"] != revision
            or type(index) is not int
            or not 0 <= index < len(target.queries)
            or type(due) is not bool
            or not isinstance(offsets, list)
            or len(offsets) != len(target.queries)
            or any(
                type(offset) is not int
                or offset < 0
                or offset > INVENTORY_MAX_OFFSET
                or offset % INVENTORY_PAGE_SIZE
                for offset in offsets
            )
        ):
            return default
        return InventoryCursor(signature, revision, index, tuple(offsets), due)

    def pass_for_refresh(self) -> InventoryPass | None:
        return InventoryPass(self.next_index, self.offsets[self.next_index]) if self.due else None

    def after_success(self, inventory: ScanSummary | None) -> InventoryCursor:
        if inventory is None:
            return InventoryCursor(self.signature, self.revision, self.next_index, self.offsets)
        offsets = list(self.offsets)
        current = offsets[self.next_index]
        offsets[self.next_index] = (
            current + INVENTORY_PAGE_SIZE
            if inventory.limit_reached and current < INVENTORY_MAX_OFFSET
            else 0
        )
        return InventoryCursor(
            self.signature,
            self.revision,
            (self.next_index + 1) % len(offsets),
            tuple(offsets),
            False,
        )

    def as_summary(self) -> dict:
        return {
            "version": 1,
            "signature": self.signature,
            "revision": self.revision,
            "next_index": self.next_index,
            "offsets": list(self.offsets),
            "due": self.due,
        }


class _SharedSearch:
    def __init__(self, adapter: EbayAdapter):
        self.adapter = adapter
        self.seen: set[str] = set()
        self.observed: set[str] = set()

    @property
    def stats(self) -> AdapterStats:
        return self.adapter.stats

    def search(self, monitor: Monitor):
        for observation in self.adapter.search(monitor, seen_item_ids=self.seen):
            if observation.listing is not None:
                self.observed.add(observation.listing.marketplace_item_id)
            yield observation


@dataclass
class TargetScanRun:
    summaries: list[ScanSummary] = field(default_factory=list)
    expected_queries: int = 0
    inventory_summary: ScanSummary | None = None
    # Ephemeral only: never serialize item identities to public workflow output.
    discovered_item_ids: set[str] = field(default_factory=set)

    def found_legacy_item(self, legacy_item_id: str) -> bool:
        """Match the numeric part of a Browse ID, or a plain legacy ID."""
        return any(
            item_id == legacy_item_id
            or (len(parts := item_id.split("|")) == 3 and parts[1] == legacy_item_id)
            for item_id in self.discovered_item_ids
        )


def run_target_scan(
    target: EbaySearchTarget,
    adapter: EbayAdapter,
    repository: ListingRepository,
    *,
    mode: Literal["initial", "refresh"],
    refresh_page_size: int = 10,
    inventory: InventoryPass | None = None,
) -> TargetScanRun:
    """Stop on failure; share identity/detail deduplication across newest and inventory."""
    if inventory is not None and (
        mode != "refresh"
        or not 0 <= inventory.query_index < len(target.queries)
        or inventory.offset % INVENTORY_PAGE_SIZE
        or not 0 <= inventory.offset <= INVENTORY_MAX_OFFSET
    ):
        raise ValueError("Invalid inventory page for this target")
    monitors = plan_target_search(
        target, mode=mode, page_size=refresh_page_size if mode == "refresh" else 10
    )
    if inventory is not None:
        template = monitors[inventory.query_index]
        monitors.append(
            template.model_copy(
                update={
                    "id": f"{template.id}-inventory-{inventory.offset}",
                    "description": "Bounded, offset-based inventory reconciliation",
                    "source_options": {
                        **template.source_options,
                        "sort": "bestMatch",
                        "page_size": INVENTORY_PAGE_SIZE,
                        "offset": inventory.offset,
                    },
                }
            )
        )
    shared = _SharedSearch(adapter)
    results = TargetScanRun(discovered_item_ids=shared.observed, expected_queries=len(monitors))
    for index, monitor in enumerate(monitors):
        result = run_scan(monitor, shared, repository)
        results.summaries.append(result)
        if inventory is not None and index == len(monitors) - 1:
            results.inventory_summary = result
        if result.status != "completed":
            break
    return results
