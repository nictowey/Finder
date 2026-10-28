"""Bounded public listing and release-statistics reads. No enumeration, cart or orders."""

import json
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from finder.discogs_completeness import (
    assess_media_completeness,
    current_release_media,
    normalize_comments,
    project_release_media,
)
from finder.discogs_rules import CURRENCIES, Observation, release_id

GRADE_NAMES = {
    "Poor (P)": "P",
    "Fair (F)": "F",
    "Good (G)": "G",
    "Good Plus (G+)": "G+",
    "Very Good (VG)": "VG",
    "Very Good Plus (VG+)": "VG+",
    "Near Mint (NM or M-)": "NM",
    "Mint (M)": "M",
    "Generic": "Generic",
}


class ListingReadError(ValueError):
    """Sanitized failure; never contains provider payloads or credentials."""

    def __init__(self, message, *, retry_after=60, rate_limited=False, access_restricted=False):
        super().__init__(message)
        self.retry_after = retry_after
        self.rate_limited = rate_limited
        self.access_restricted = access_restricted


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


def normalize(payload, requested_id, *, release_metadata=None, release_provenance=None, now=None):
    """Use only top-level API comments; catalog metadata must be supplied separately.

    Catalog metadata must have fresh public API provenance. An embedded listing.release
    summary, title, barcode, seller fields or precomputed verdict is not authoritative.
    """
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
    comments = normalize_comments(payload.get("comments"))
    metadata = current_release_media(
        release_metadata, release_provenance, release["id"], now=now or datetime.now(UTC)
    )
    provenance = (
        {key: release_provenance[key] for key in ("source", "release_id", "fetched_at")}
        if metadata
        else None
    )
    return dict(
        listing_id=str(requested_id),
        seller_comments=comments["text"],
        seller_comments_supported=comments["supported"],
        release_media_metadata=metadata,
        release_media_provenance=provenance,
        media_completeness=assess_media_completeness(
            release["id"], comments["text"], metadata, comments_supported=comments["supported"]
        ),
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


def observation(listing, *, now=None):
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
        observed_at=(
            datetime.now(UTC) if now is None else datetime.fromtimestamp(now, UTC)
        ).isoformat(),
        available=listing["available"],
        pressing_evidence="unknown",
        seller_comments=listing.get("seller_comments"),
        seller_comments_supported=listing.get("seller_comments_supported", False),
        release_media_metadata=listing.get("release_media_metadata"),
        release_media_provenance=listing.get("release_media_provenance"),
    )


class PublicReadBudget:
    """One shared request slot per three seconds; at most one bounded explicit wait.

    Default state is local to this instance/process. Cloud callbacks reserve the existing
    durable budget instead; no SQLite transaction or mutex is held while sleeping.
    """

    def __init__(self, *, clock=time.monotonic, sleep=time.sleep, claim=None, defer=None):
        self.clock, self.sleep = clock, sleep
        self.claim_callback, self.defer_callback = claim, defer
        self.next_read = 0.0
        self.lock = threading.Lock()

    def _claim(self):
        if self.claim_callback is not None:
            return self.claim_callback()
        with self.lock:
            now = self.clock()
            if now < self.next_read:
                return self.next_read - now
            self.next_read = now + 3.0
            return 0.0

    def reserve(self, *, wait=False):
        delay = self._claim()
        if delay and wait and 0 < delay <= 3.0:
            self.sleep(delay)
            delay = self._claim()
        if delay:
            raise ListingReadError("Read cooldown active. Wait before explicitly trying again.")

    def defer(self, seconds):
        seconds = max(60, min(seconds, 86400))
        if self.defer_callback is not None:
            self.defer_callback(seconds)
        else:
            with self.lock:
                self.next_read = max(self.next_read, self.clock() + seconds)


class ListingReader:
    def __init__(
        self,
        *,
        transport=None,
        clock=time.monotonic,
        sleep=time.sleep,
        wall_clock=time.time,
        budget=None,
    ):
        self.transport, self.clock, self.wall_clock = transport, clock, wall_clock
        self.budget = budget or PublicReadBudget(clock=clock, sleep=sleep)
        self._media_cache = OrderedDict()
        self._cache_lock = threading.Lock()

    @property
    def next_read(self):
        return self.budget.next_read

    def _now(self):
        return datetime.fromtimestamp(self.wall_clock(), UTC)

    def read(self, value, *, budget=None):
        """One listing GET, including when called by the scheduled monitor."""
        ident = listing_id(value)
        return normalize(self._request(f"/marketplace/listings/{ident}", budget=budget), ident)

    def read_release_media(self, release, *, budget=None):
        """Read bounded, exact-release public catalog quantity or return None.

        Invalid release identities still raise ValueError before any request. A valid
        identity with an unavailable or unsupported catalog response cannot establish
        a playable-media count. Return copies so callers cannot alter the TTL cache.
        """
        ident = release_id(release)
        with self._cache_lock:
            cached = self._media_cache.get(ident)
            if cached and current_release_media(*cached, ident, now=self._now()):
                self._media_cache.move_to_end(ident)
                return project_release_media(cached[0], ident), dict(cached[1])
            self._media_cache.pop(ident, None)
        try:
            raw = self._request(f"/releases/{ident}", budget=budget, wait=True)
            metadata = project_release_media(raw, ident)
            if metadata is None:
                return None
            provenance = {
                "source": "discogs_release_api",
                "release_id": ident,
                "fetched_at": self._now().isoformat(),
            }
            with self._cache_lock:
                self._media_cache[ident] = (metadata, provenance)
                self._media_cache.move_to_end(ident)
                while len(self._media_cache) > 100:
                    self._media_cache.popitem(last=False)
            return project_release_media(metadata, ident), dict(provenance)
        except ListingReadError:
            return None

    def read_for_release(self, value, configured_release_id, *, budget=None):
        """Explicit known-listing review only: at most one additional catalog GET.

        Never discover a release from seller text or fetch an unexpected listing release.
        Catalog failure preserves the listing with unknown completeness. No stale cache reuse.
        """
        ident, release = listing_id(value), release_id(configured_release_id)
        payload = self._request(f"/marketplace/listings/{ident}", budget=budget)
        listing = normalize(payload, ident)
        if listing["release_id"] != release:
            return listing
        cached = self.read_release_media(release, budget=budget)
        if cached is None:
            return listing
        return normalize(
            payload,
            ident,
            release_metadata=cached[0],
            release_provenance=cached[1],
            now=self._now(),
        )

    def _request(self, path, *, params=None, budget=None, wait=False):
        """Only callers construct fixed public GET routes; never accept an input URL."""
        active_budget = budget or self.budget
        active_budget.reserve(wait=wait)
        if active_budget is not self.budget:
            # Cloud's durable slot coordinates the stats worker; retain the reader's
            # local cooldown too, including when a caller also uses it directly.
            self.budget.reserve()
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
                    "GET", f"https://api.discogs.com{path}", params=params
                ) as response:
                    if response.status_code == 429:
                        seconds = 60
                        retry = response.headers.get("Retry-After", "")
                        if retry.isdigit() and len(retry) <= 6:
                            seconds = max(60, min(int(retry), 86400))
                        active_budget.defer(seconds)
                        if active_budget is not self.budget:
                            self.budget.defer(seconds)
                        raise ListingReadError(
                            "Discogs rate limit reached. No automatic retry; "
                            "wait before trying again.",
                            retry_after=seconds,
                            rate_limited=True,
                        )
                    if response.status_code != 200:
                        raise ListingReadError(
                            f"Discogs returned HTTP {response.status_code}. No listing verified.",
                            access_restricted=response.status_code in {401, 403},
                        )
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 256 * 1024:
                            raise ListingReadError("Discogs response exceeded the safe size limit.")
            return json.loads(data, parse_float=Decimal)
        except (httpx.HTTPError, ImportError):
            raise ListingReadError(
                "Discogs request failed or timed out; availability is unknown."
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            raise ListingReadError("Discogs returned an unreadable listing response.") from None


class ReleaseStatsReader(ListingReader):
    """Aggregate release availability only. This endpoint does not discover listing IDs."""

    def read(self, value, currency):
        ident = release_id(value)
        if currency not in CURRENCIES:
            raise ListingReadError("Unsupported currency.")
        data = self._request(f"/marketplace/stats/{ident}", params={"curr_abbr": currency})
        if not isinstance(data, dict):
            raise ListingReadError("Discogs returned unreadable release availability.")
        count = data.get("num_for_sale")
        if type(count) is not int or not 0 <= count <= 10000000:
            raise ListingReadError("Discogs returned an invalid listing count.")
        price, reported_currency = money(data.get("lowest_price"))
        if reported_currency != currency:
            price = None
        return {
            "release_id": ident,
            "num_for_sale": count,
            "lowest_item_price": price,
            "currency": currency,
        }
