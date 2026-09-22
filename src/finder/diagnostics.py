"""Aggregate, non-identifying summaries of normalized listings for live validation output.

Summaries deliberately omit item IDs, titles, sellers, URLs, and prices so they are safe to
print in public CI logs.
"""

from collections import Counter
from collections.abc import Iterable
from typing import Any

from finder.domain import Listing

COVERAGE_FIELDS = (
    "current_price",
    "currency",
    "shipping_cost",
    "total_acquisition_cost",
    "condition",
    "seller_username",
    "seller_feedback_percentage",
    "seller_feedback_score",
    "listing_url",
    "primary_image",
    "item_specifics",
    "categories",
    "buying_formats",
    "listing_ends_at",
    "details_observed_at",
)


def _present(value: Any) -> bool:
    return value is not None and value != [] and value != {}


def summarize_listings(listings: Iterable[Listing], *, top_specifics: int = 15) -> dict[str, Any]:
    items = list(listings)
    price_kinds: Counter[str] = Counter()
    currencies: set[str] = set()
    flags: Counter[str] = Counter()
    specifics: Counter[str] = Counter()
    environments: Counter[str] = Counter()
    coverage = dict.fromkeys(COVERAGE_FIELDS, 0)
    for listing in items:
        price_kinds[listing.price_kind] += 1
        if listing.currency:
            currencies.add(listing.currency)
        flags.update(listing.quality_flags)
        specifics.update(listing.item_specifics.keys())
        environments[str(listing.source_metadata.get("environment"))] += 1
        for name in COVERAGE_FIELDS:
            coverage[name] += _present(getattr(listing, name))
    return {
        "listings": len(items),
        "environments": dict(environments),
        "price_kinds": dict(price_kinds),
        "currencies": sorted(currencies),
        "field_coverage": coverage,
        "quality_flags": dict(sorted(flags.items())),
        "item_specific_names": [name for name, _ in specifics.most_common(top_specifics)],
    }


def changed_fields(expected: Listing, actual: Listing) -> list[str]:
    """Names of fields whose values differ; never the values themselves."""
    left, right = expected.model_dump(), actual.model_dump()
    return sorted(key for key in left.keys() | right.keys() if left.get(key) != right.get(key))
