"""Sanitize and replay captured eBay Browse responses without network access."""

from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from finder.adapters.base import AdapterStats, ListingObservation
from finder.adapters.ebay.normalize import normalize_listing
from finder.domain import Monitor
from finder.errors import ConfigurationError, InvalidListingError, ResponseError


def _replacement_id(index: int) -> str:
    return f"v1|fixture{index:06d}|0"


def _scrub_urls_and_accounts(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _scrub_urls_and_accounts(item)
        return
    if not isinstance(value, dict):
        return
    for key in list(value):
        normalized = key.lower()
        if normalized in {"legacyitemid", "buyerprotection"}:
            value.pop(key, None)
        elif normalized == "username":
            value[key] = "fixture-seller"
        elif normalized.endswith("url") or normalized.endswith("href"):
            value[key] = "https://example.invalid/redacted"
        else:
            _scrub_urls_and_accounts(value[key])


def sanitize_fixture(bundle: Any) -> dict[str, Any]:
    """Remove account and URL identifiers while preserving matching-relevant fields."""
    if not isinstance(bundle, dict) or bundle.get("schema_version") != 1:
        raise ConfigurationError("eBay replay fixture must use schema_version 1.")
    pages = bundle.get("search_responses")
    details = bundle.get("detail_responses", {})
    if not isinstance(pages, list) or not all(isinstance(page, dict) for page in pages):
        raise ConfigurationError("Fixture search_responses must be a list of objects.")
    if not isinstance(details, dict):
        raise ConfigurationError("Fixture detail_responses must be an object.")

    result = deepcopy(bundle)
    id_map: dict[str, str] = {}
    next_id = 1
    for page in result["search_responses"]:
        page.pop("href", None)
        page.pop("next", None)
        for item in page.get("itemSummaries", []):
            if not isinstance(item, dict) or not isinstance(item.get("itemId"), str):
                continue
            original = item["itemId"]
            if original not in id_map:
                id_map[original] = _replacement_id(next_id)
                next_id += 1
            item["itemId"] = id_map[original]
            _scrub_urls_and_accounts(item)

    clean_details = {}
    for original, detail in result.get("detail_responses", {}).items():
        if original not in id_map or not isinstance(detail, dict):
            continue
        clean = deepcopy(detail)
        clean["itemId"] = id_map[original]
        _scrub_urls_and_accounts(clean)
        clean_details[id_map[original]] = clean
    result["detail_responses"] = clean_details
    result.pop("captured_at", None)
    result["sanitized"] = True
    return result


class EbayReplayAdapter:
    """Offline adapter for deterministic regression tests from sanitized API bundles."""

    def __init__(self, bundle: Any, *, observed_at: datetime | None = None):
        if not isinstance(bundle, dict) or bundle.get("schema_version") != 1:
            raise ConfigurationError("eBay replay fixture must use schema_version 1.")
        if bundle.get("sanitized") is not True:
            raise ConfigurationError("Refusing to replay an unsanitized eBay fixture.")
        self.pages = bundle.get("search_responses")
        self.details = bundle.get("detail_responses", {})
        if not isinstance(self.pages, list) or not isinstance(self.details, dict):
            raise ConfigurationError("Malformed eBay replay fixture.")
        self.observed_at = (observed_at or datetime.now(UTC)).astimezone(UTC)
        self.stats = AdapterStats()

    def search(self, monitor: Monitor) -> Iterator[ListingObservation]:
        if monitor.marketplace != "ebay":
            raise ConfigurationError("eBay fixtures require an eBay monitor.")
        self.stats = AdapterStats()
        seen = set()
        for page in self.pages:
            if not isinstance(page, dict):
                raise ResponseError("Fixture search page must be an object.")
            items = page.get("itemSummaries")
            if not isinstance(items, list):
                raise ResponseError("Fixture search page has invalid itemSummaries.")
            self.stats.fetched += len(items)
            for summary in items:
                if not isinstance(summary, dict) or not isinstance(summary.get("itemId"), str):
                    yield ListingObservation(skip_reason="missing_item_id")
                    continue
                item_id = summary["itemId"]
                if item_id in seen:
                    yield ListingObservation(skip_reason="duplicate_in_scan")
                    continue
                seen.add(item_id)
                detail = self.details.get(item_id)
                raw = {**summary, **detail} if isinstance(detail, dict) else summary
                try:
                    listing = normalize_listing(
                        raw,
                        self.observed_at,
                        details_loaded=isinstance(detail, dict),
                        quality_flags=[] if detail else ["replayed_without_details"],
                        source_metadata={
                            "monitor_id": monitor.id,
                            "environment": "sanitized_fixture",
                        },
                    )
                except InvalidListingError:
                    yield ListingObservation(skip_reason="invalid_listing")
                    continue
                yield ListingObservation(listing=listing)
