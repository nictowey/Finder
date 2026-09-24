"""Page-level Browse discovery. No detail requests or category matching here."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, quote, urlsplit

from finder.errors import ResponseError

PAGE_SIZE = 200
RESULT_CEILING = 10_000
SEARCH_PATH = "/buy/browse/v1/item_summary/search"


def iso(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SearchError(ResponseError):
    """A fixed diagnostic code that is safe for public logs."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass
class SearchPage:
    items: list[dict]
    total: int
    more: bool
    fingerprint: str
    # Items whose listing date fell outside the requested window. They are genuine
    # matches for the query, so they are kept; the count is reported for diagnosis.
    window_mismatches: int = 0


def summary_fingerprint(raw):
    # Only change signals, never a persisted provider payload. Shipping estimates and
    # ranking/tracking URLs vary without a seller revision and are deliberately omitted.
    fields = ("title", "price", "currentBidPrice", "conditionId", "itemEndDate", "itemRevision")
    return hashlib.sha256(
        json.dumps({k: raw.get(k) for k in fields}, sort_keys=True).encode()
    ).hexdigest()


def search_page(client, target, query_index, *, lower, upper, offset):
    if offset < 0 or offset % PAGE_SIZE or offset >= RESULT_CEILING:
        raise ValueError("Invalid Browse offset")
    headers = {"X-EBAY-C-MARKETPLACE-ID": target.marketplace_id}
    country, postal = client.settings.delivery_country, client.settings.delivery_postal_code
    filters = ["buyingOptions:{FIXED_PRICE|AUCTION|BEST_OFFER}", "itemLocationRegion:WORLDWIDE"]
    if country:
        filters.append(f"deliveryCountry:{country}")
    if country and postal:
        location = quote(f"country={country},zip={postal}", safe="")
        headers["X-EBAY-C-ENDUSERCTX"] = f"contextualLocation={location}"
    filters.append(f"itemStartDate:[{lower}..{upper}]")
    query = target.queries[query_index]
    # "gtin:<digits>" searches by barcode (UPC/EAN) instead of keywords.
    if query.startswith("gtin:"):
        if not re.fullmatch(r"gtin:\d{8,14}", query):
            raise ValueError("Invalid barcode search")
        keywords = {"gtin": query.removeprefix("gtin:")}
    else:
        keywords = {"q": query}
    payload = client.get(
        SEARCH_PATH,
        headers=headers,
        params={
            **keywords,
            "category_ids": target.category_id,
            "limit": PAGE_SIZE,
            "offset": offset,
            "sort": "newlyListed",
            "filter": ",".join(filters),
        },
    )
    total = payload.get("total")
    if type(total) is not int or total < 0:
        raise SearchError("invalid_total", "Invalid search total")
    # eBay omits itemSummaries on an empty page, including one before its estimated total.
    items = payload.get("itemSummaries", [])
    if not isinstance(items, list) or len(items) > PAGE_SIZE:
        raise SearchError("invalid_page", "Invalid search page")
    # eBay's total is an estimate. An empty page before it is the end of the results,
    # not corruption; the second traversal and daily reconciliation re-check the range.
    estimated_end = not items and total > offset
    next_url = payload.get("next")
    if next_url:
        # Never follow a URL supplied by the provider. Even the continuation hint is
        # checked: arbitrary hosts, credentials, paths and non-forward offsets fail closed.
        link, expected = urlsplit(next_url), urlsplit(client.settings.ebay_base_url)
        try:
            next_offset = int(parse_qs(link.query)["offset"][0])
        except (KeyError, ValueError, TypeError):
            raise SearchError("invalid_next", "Invalid pagination link") from None
        if (
            link.scheme != "https"
            or link.netloc != expected.netloc
            or link.username
            or link.password
            or link.path != SEARCH_PATH
            or link.fragment
            or next_offset != offset + PAGE_SIZE
        ):
            raise SearchError("unsafe_next", "Unsafe pagination link")
    ids = []
    mismatches = 0
    low = datetime.fromisoformat(lower.replace("Z", "+00:00"))
    high = datetime.fromisoformat(upper.replace("Z", "+00:00"))
    for item in items:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("itemId"), str)
            or not item["itemId"]
        ):
            # Replay the entire page; never checkpoint past an unaccounted item.
            raise SearchError("missing_identity", "Search item lacks identity")
        if len(item["itemId"]) > 255:
            raise SearchError("invalid_identity", "Invalid item identity")
        origin = item.get("itemOriginDate")
        # Every item needs a listing date. Creation date is deliberately not substituted.
        try:
            start = datetime.fromisoformat(origin.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise SearchError("missing_start_date", "Search item lacks a start date") from None
        if start.tzinfo is None:
            raise SearchError("missing_start_date", "Search item lacks a start date")
        mismatches += not low <= start <= high
        ids.append(item["itemId"])
    return SearchPage(
        items,
        total,
        not estimated_end and (bool(next_url) or offset + len(items) < total),
        hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
        mismatches,
    )
