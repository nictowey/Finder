"""Explicit public known-listing reads. No discovery, credentials, cart or orders."""

import json
import math
import re
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from finder.discogs_rules import Observation

GRADE_NAMES = {
    "Poor (P)": "P",
    "Fair (F)": "F",
    "Good (G)": "G",
    "Good Plus (G+)": "G+",
    "Very Good (VG)": "VG",
    "Very Good Plus (VG+)": "VG+",
    "Near Mint (NM or M-)": "NM",
    "Mint (M)": "M",
}


class ListingReadError(ValueError):
    """Sanitized failure; never contains provider payloads or credentials."""


def listing_id(value):
    if type(value) is int:
        value = str(value)
    if isinstance(value, str):
        match = re.fullmatch(
            r"(?:https://(?:www\.)?discogs\.com/sell/item/)?([1-9]\d{0,15})", value.strip()
        )
        if match and int(match[1]) <= 9007199254740991:
            return int(match[1])
    raise ListingReadError("Use a listing ID or canonical HTTPS Discogs sell/item link.")


def money(value):
    if not isinstance(value, dict):
        return None, None
    currency = value.get("currency")
    if not isinstance(currency, str) or not re.fullmatch("[A-Z]{3}", currency):
        return None, None
    raw = value.get("value")
    if type(raw) not in (str, int, float, Decimal):
        return None, currency
    try:
        result = Decimal(str(raw))
        if (
            not result.is_finite()
            or result < 0
            or result > Decimal("9999999.99")
            or result != result.quantize(Decimal(".01"))
        ):
            return None, currency
        return format(result, ".2f"), currency
    except (InvalidOperation, ValueError):
        return None, currency


def normalize(payload, requested_id):
    if (
        not isinstance(payload, dict)
        or type(payload.get("id")) is not int
        or payload["id"] != requested_id
    ):
        raise ListingReadError("Listing identity is missing or differs from the requested listing.")
    release = payload.get("release")
    if (
        not isinstance(release, dict)
        or type(release.get("id")) is not int
        or not 0 < release["id"] <= 9007199254740991
    ):
        raise ListingReadError("Listing release identity is missing or invalid.")
    seller = payload.get("seller")
    stats = seller.get("stats", {}) if isinstance(seller, dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    rating = stats.get("rating")
    try:
        rating = float(rating) if type(rating) in (str, int, float) else None
        if rating is not None and (not math.isfinite(rating) or not 0 <= rating <= 100):
            rating = None
    except (ValueError, OverflowError):
        rating = None
    feedback = stats.get("total")
    if type(feedback) is not int or not 0 <= feedback <= 10000000:
        feedback = None
    item, currency = money(payload.get("price"))
    shipping, shipping_currency = money(payload.get("shipping_price"))
    available = payload.get("status") == "For Sale"
    return dict(
        listing_id=str(requested_id),
        release_id=release["id"],
        media=GRADE_NAMES.get(payload.get("condition"))
        if isinstance(payload.get("condition"), str)
        else None,
        sleeve=GRADE_NAMES.get(payload.get("sleeve_condition"))
        if isinstance(payload.get("sleeve_condition"), str)
        else None,
        seller_percent=rating,
        feedback=feedback,
        item=item,
        currency=currency,
        reported_shipping=shipping,
        reported_shipping_currency=shipping_currency,
        shipping_blocked=payload.get("shipping_is_blocked") is not False,
        available=available,
        url=f"https://www.discogs.com/sell/item/{requested_id}",
    )


def observation(listing):
    """Reported shipping never becomes a verified destination quote."""
    return Observation(
        source="discogs_public_listing",
        listing_id=listing["listing_id"],
        release_id=listing["release_id"],
        media=listing["media"],
        sleeve=listing["sleeve"],
        seller_percent=listing["seller_percent"],
        feedback=listing["feedback"],
        item=listing["item"],
        currency=listing["currency"],
        observed_at=datetime.now(UTC).isoformat(),
        available=listing["available"],
        pressing_evidence="unknown",
    )


class ListingReader:
    def __init__(self, *, transport=None, clock=time.monotonic):
        self.transport, self.clock, self.next_read = transport, clock, 0.0

    def read(self, value):
        ident = listing_id(value)
        now = self.clock()
        if now < self.next_read:
            raise ListingReadError("Read cooldown active. Wait before explicitly trying again.")
        self.next_read = now + 3.0  # At most 20 attempts/minute, below public API limit.
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=8,
                follow_redirects=False,
                headers={
                    "User-Agent": "Finder/0.2 +https://github.com/nictowey/Finder",
                    "Accept": "application/json",
                },
            ) as client:
                with client.stream(
                    "GET", f"https://api.discogs.com/marketplace/listings/{ident}"
                ) as response:
                    if response.status_code == 429:
                        self.next_read = now + 60
                        retry = response.headers.get("Retry-After", "")
                        if retry.isdigit() and len(retry) <= 6:
                            self.next_read = now + max(60, min(int(retry), 86400))
                        raise ListingReadError(
                            "Discogs rate limit reached. No automatic retry; "
                            "wait before trying again."
                        )
                    if response.status_code != 200:
                        raise ListingReadError(
                            f"Discogs returned HTTP {response.status_code}. No listing verified."
                        )
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 256 * 1024:
                            raise ListingReadError("Discogs response exceeded the safe size limit.")
            return normalize(json.loads(data, parse_float=Decimal), ident)
        except (httpx.HTTPError, ImportError):
            raise ListingReadError(
                "Discogs request failed or timed out; availability is unknown."
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ListingReadError("Discogs returned an unreadable listing response.") from None
