import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from urllib.parse import quote

from pydantic import ValidationError

from finder.adapters.base import AdapterStats, ListingObservation
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.normalize import normalize_listing
from finder.adapters.ebay.options import EbayOptions
from finder.domain import Monitor
from finder.errors import (
    ConfigurationError,
    InvalidListingError,
    ItemUnavailableError,
    RequestError,
    ResponseError,
)

log = logging.getLogger("finder.ebay")


class EbayAdapter:
    def __init__(
        self, client: EbayClient, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ):
        self.client = client
        self.now = now
        self.stats = AdapterStats()

    def refresh_known(
        self, item_id: str, *, source_metadata: dict | None = None
    ) -> ListingObservation:
        """Recheck one discovered item without depending on its current search rank."""
        settings = self.client.settings
        headers = {"X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}
        if settings.delivery_country and settings.delivery_postal_code:
            location = quote(
                f"country={settings.delivery_country},zip={settings.delivery_postal_code}", safe=""
            )
            headers["X-EBAY-C-ENDUSERCTX"] = f"contextualLocation={location}"
        try:
            raw = self.client.get(
                f"/buy/browse/v1/item/{quote(item_id, safe='')}",
                headers=headers,
                params={"fieldgroups": "ADDITIONAL_SELLER_DETAILS"}
                if settings.ebay_environment == "production"
                else None,
            )
        except ItemUnavailableError:
            return ListingObservation(skip_reason="item_unavailable")
        if raw.get("itemId") != item_id:
            raise ResponseError("eBay detail response has mismatched or missing ID.")
        try:
            listing = normalize_listing(
                raw,
                self.now(),
                details_loaded=True,
                source_metadata={
                    **{
                        key: value
                        for key, value in (source_metadata or {}).items()
                        if key in ("monitor_id", "query", "search_marketplace_id")
                    },
                    "environment": settings.ebay_environment,
                    "delivery_country": settings.delivery_country,
                    "delivery_postal_code": settings.delivery_postal_code,
                },
            )
        except InvalidListingError:
            return ListingObservation(skip_reason="invalid_listing")
        if listing.listing_ends_at and listing.listing_ends_at <= self.now():
            return ListingObservation(skip_reason="listing_ended")
        if settings.ebay_environment == "production" and not listing.seller_id:
            return ListingObservation(skip_reason="missing_seller_id")
        return ListingObservation(listing=listing)

    def search(
        self, monitor: Monitor, *, seen_item_ids: set[str] | None = None
    ) -> Iterator[ListingObservation]:
        self.stats = AdapterStats()
        if monitor.marketplace != "ebay":
            raise ConfigurationError("This adapter only supports eBay monitors.")
        try:
            options = EbayOptions.model_validate(monitor.source_options)
        except ValidationError:
            raise ConfigurationError(
                "Invalid eBay source_options in monitor configuration."
            ) from None
        headers = {"X-EBAY-C-MARKETPLACE-ID": options.marketplace_id}
        settings = self.client.settings
        country, postal = settings.delivery_country, settings.delivery_postal_code
        if country and postal:
            location = quote(f"country={country},zip={postal}", safe="")
            headers["X-EBAY-C-ENDUSERCTX"] = f"contextualLocation={location}"
        filters = ["buyingOptions:{" + "|".join(options.buying_options) + "}"]
        if country:
            filters.append(f"deliveryCountry:{country}")
        params = {"q": monitor.query, "limit": options.page_size, "filter": ",".join(filters)}
        if options.sort != "bestMatch":
            params["sort"] = options.sort
        if options.category_ids:
            params["category_ids"] = ",".join(options.category_ids)
        if options.aspect_filter:
            params["aspect_filter"] = options.aspect_filter
        seen = seen_item_ids if seen_item_ids is not None else set()
        for page in range(options.max_pages):
            offset = options.offset + page * options.page_size
            payload = self.client.get(
                "/buy/browse/v1/item_summary/search",
                headers=headers,
                params={**params, "offset": offset},
            )
            total = payload.get("total")
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise ResponseError("eBay search response has a missing or invalid total.")
            items = payload.get("itemSummaries", [] if total == 0 else None)
            if not isinstance(items, list):
                raise ResponseError("eBay search response has invalid itemSummaries.")
            if not items and total > offset:
                raise ResponseError("eBay returned an empty page before the reported end.")
            self.stats.fetched += len(items)
            self.stats.pages_fetched += 1
            self.stats.reported_total = total
            log.info("ebay_page_fetched", extra={"fields": {"page": page + 1, "items": len(items)}})
            for raw in items:
                if not isinstance(raw, dict) or not isinstance(raw.get("itemId"), str):
                    yield ListingObservation(skip_reason="missing_item_id")
                    continue
                item_id = raw["itemId"].strip()
                if not item_id:
                    yield ListingObservation(skip_reason="missing_item_id")
                    continue
                if item_id in seen:
                    yield ListingObservation(skip_reason="duplicate_in_scan")
                    continue
                seen.add(item_id)
                flags = []
                details_loaded = False
                if options.fetch_details:
                    try:
                        detail = self.client.get(
                            f"/buy/browse/v1/item/{quote(item_id, safe='')}",
                            headers=headers,
                            params=(
                                {"fieldgroups": "ADDITIONAL_SELLER_DETAILS"}
                                if settings.ebay_environment == "production"
                                else None
                            ),
                        )
                        if detail.get("itemId") != item_id:
                            raise ResponseError(
                                "eBay detail response has mismatched or missing ID."
                            )
                        # Merge only after checking the identity, retaining summary-only fields.
                        raw = {**raw, **detail}
                        details_loaded = True
                    except ItemUnavailableError:
                        yield ListingObservation(skip_reason="item_unavailable")
                        continue
                    except (RequestError, ResponseError) as exc:
                        flags.append("details_unavailable")
                        log.warning(
                            "ebay_details_unavailable",
                            extra={
                                "fields": {
                                    "error_type": type(exc).__name__,
                                }
                            },
                        )
                    # Auth and rate-limit errors are scan-wide failures, never swallowed.
                else:
                    flags.append("details_not_requested")
                try:
                    now = self.now()
                    listing = normalize_listing(
                        raw,
                        now,
                        details_loaded=details_loaded,
                        quality_flags=flags,
                        source_metadata={
                            "monitor_id": monitor.id,
                            "query": monitor.query,
                            "environment": settings.ebay_environment,
                            "search_marketplace_id": options.marketplace_id,
                            "delivery_country": country,
                            "delivery_postal_code": postal,
                        },
                    )
                    if listing.listing_ends_at and listing.listing_ends_at <= now:
                        yield ListingObservation(skip_reason="listing_ended")
                        continue
                    if settings.ebay_environment == "production" and not listing.seller_id:
                        yield ListingObservation(skip_reason="missing_seller_id")
                        continue
                except InvalidListingError:
                    yield ListingObservation(skip_reason="invalid_listing")
                    continue
                yield ListingObservation(listing=listing)
            has_more = bool(payload.get("next")) or offset + len(items) < total
            if not has_more or not items:
                return
        self.stats.limit_reached = True
        log.warning("scan_limit_reached", extra={"fields": {"max_pages": options.max_pages}})
