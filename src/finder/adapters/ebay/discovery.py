"""Page-level Browse discovery. No detail requests or category matching here."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, quote, urlsplit

from finder.errors import ResponseError

PAGE_SIZE = 200
RESULT_CEILING = 10_000
SEARCH_PATH = "/buy/browse/v1/item_summary/search"


def iso(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class SearchPage:
    items: list[dict]
    total: int
    more: bool
    fingerprint: str


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
    payload = client.get(
        SEARCH_PATH,
        headers=headers,
        params={
            "q": target.queries[query_index],
            "category_ids": target.category_id,
            "limit": PAGE_SIZE,
            "offset": offset,
            "sort": "newlyListed",
            "filter": ",".join(filters),
        },
    )
    total = payload.get("total")
    if type(total) is not int or total < 0:
        raise ResponseError("Invalid search total")
    items = payload.get("itemSummaries", [] if total == 0 else None)
    if not isinstance(items, list) or len(items) > PAGE_SIZE:
        raise ResponseError("Invalid search page")
    if not items and total > offset:
        raise ResponseError("Empty page before reported end")
    next_url = payload.get("next")
    if next_url:
        # Never follow a URL supplied by the provider. Even the continuation hint is
        # checked: arbitrary hosts, credentials, paths and non-forward offsets fail closed.
        link, expected = urlsplit(next_url), urlsplit(client.settings.ebay_base_url)
        try:
            next_offset = int(parse_qs(link.query)["offset"][0])
        except (KeyError, ValueError, TypeError):
            raise ResponseError("Invalid pagination link") from None
        if (
            link.scheme != "https"
            or link.netloc != expected.netloc
            or link.username
            or link.password
            or link.path != SEARCH_PATH
            or link.fragment
            or next_offset != offset + PAGE_SIZE
        ):
            raise ResponseError("Unsafe pagination link")
    ids = []
    for item in items:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("itemId"), str)
            or not item["itemId"]
        ):
            # Replay the entire page; never checkpoint past an unaccounted item.
            raise ResponseError("Search item lacks identity")
        if len(item["itemId"]) > 255:
            raise ResponseError("Invalid item identity")
        origin = item.get("itemOriginDate")
        # Verify the documented filter on actual responses before advancing a watermark.
        # Creation date is deliberately not substituted for availability/origin date.
        try:
            start = datetime.fromisoformat(origin.replace("Z", "+00:00"))
            valid = (
                datetime.fromisoformat(lower.replace("Z", "+00:00"))
                <= start
                <= datetime.fromisoformat(upper.replace("Z", "+00:00"))
            )
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ResponseError("Start-date window could not be verified")
        ids.append(item["itemId"])
    return SearchPage(
        items,
        total,
        bool(next_url) or offset + len(items) < total,
        hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
    )
