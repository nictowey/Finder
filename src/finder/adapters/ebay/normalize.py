"""Pure normalization: preserve uncertainty, never infer product identity."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from finder.domain import Listing
from finder.errors import InvalidListingError


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _decimal(value: Any) -> Decimal | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except InvalidOperation:
        return None


def _money(value: Any) -> tuple[Decimal | None, str | None]:
    obj = _object(value)
    amount, currency = _decimal(obj.get("value")), _text(obj.get("currency"))
    if currency is None or len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        return None, None
    return amount, currency.upper()


def normalize_listing(
    raw: Any,
    observed_at: datetime,
    *,
    details_loaded: bool = False,
    quality_flags: list[str] | None = None,
    source_metadata: dict | None = None,
) -> Listing:
    if not isinstance(raw, dict):
        raise InvalidListingError("Listing must be an object")
    item_id, title = _text(raw.get("itemId")), _text(raw.get("title"))
    if not item_id or not title:
        raise InvalidListingError("Missing item ID or title")
    if observed_at.tzinfo is None:
        raise ValueError("Observation timestamp must be timezone-aware")
    observed_at = observed_at.astimezone(UTC)
    flags = list(quality_flags or [])
    formats = [v for v in _list(raw.get("buyingOptions")) if isinstance(v, str)]
    price_kind = "unknown"
    price_data = raw.get("price")
    if "AUCTION" in formats and "FIXED_PRICE" not in formats:
        price_data = raw.get("currentBidPrice") or price_data
        price_kind = "current_bid"
    elif "FIXED_PRICE" in formats:
        price_kind = "fixed_price"
    price, currency = _money(price_data)
    if price is None:
        flags.append("price_unknown")
    shipping = []
    for option in _list(raw.get("shippingOptions")):
        cost, curr = _money(_object(option).get("shippingCost"))
        if cost is not None and curr is not None:
            shipping.append((cost, curr))
    same_currency = [v for v in shipping if v[1] == currency]
    # Keep a foreign-currency quote for inspection, but do not add currencies.
    selected = (
        min(same_currency, key=lambda v: v[0])
        if same_currency
        else (shipping[0] if shipping else (None, None))
    )
    shipping_cost, shipping_currency = selected
    if shipping_cost is None:
        flags.append("shipping_unknown")
    elif currency and shipping_currency != currency:
        flags.append("shipping_currency_mismatch")

    def timestamp(key: str) -> datetime | None:
        value = raw.get(key)
        if value is None:
            return None
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if result.tzinfo is None:
                raise ValueError("missing timezone")
            return result.astimezone(UTC)
        except (ValueError, TypeError, AttributeError):
            flags.append(f"invalid_{key}")
            return None

    seller = _object(raw.get("seller"))
    feedback_percentage = _decimal(seller.get("feedbackPercentage"))
    if feedback_percentage is not None and feedback_percentage > 100:
        feedback_percentage = None
        flags.append("invalid_seller_feedback_percentage")
    # eBay feedback scores can be negative; do not coerce fractional values.
    score = seller.get("feedbackScore")
    if isinstance(score, bool) or not isinstance(score, int):
        score = None
    aspects: dict[str, list[str]] = {}
    for aspect in _list(raw.get("localizedAspects")):
        obj = _object(aspect)
        name, value = _text(obj.get("name")), _text(obj.get("value"))
        if name and value:
            if value not in aspects.setdefault(name, []):
                aspects[name].append(value)
        else:
            flags.append("invalid_item_specific")
    categories = []
    for category in _list(raw.get("categories")):
        obj = _object(category)
        if category_id := _text(obj.get("categoryId")):
            categories.append({"id": category_id, "name": _text(obj.get("categoryName")) or ""})
    if not categories and (category_id := _text(raw.get("categoryId"))):
        categories.append({"id": category_id, "name": _text(raw.get("categoryPath")) or ""})
    metadata = dict(source_metadata or {})
    # Keep API-native identifiers and shipping evidence without large HTML descriptions.
    for key in (
        "legacyItemId",
        "listingMarketplaceId",
        "primaryItemGroup",
        "itemGroupHref",
        "shippingOptions",
        "bidCount",
        "price",
        "currentBidPrice",
    ):
        if key in raw:
            metadata[key] = raw[key]
    return Listing(
        marketplace="ebay",
        marketplace_item_id=item_id,
        title=title,
        current_price=price,
        currency=currency,
        price_kind=price_kind,
        shipping_cost=shipping_cost,
        shipping_currency=shipping_currency,
        condition=_text(raw.get("condition")),
        condition_id=_text(raw.get("conditionId")),
        seller_id=_text(seller.get("userId")),
        seller_username=_text(seller.get("username")),
        seller_feedback_percentage=feedback_percentage,
        seller_feedback_score=score,
        listing_url=_text(raw.get("itemWebUrl")),
        primary_image=_text(_object(raw.get("image")).get("imageUrl")),
        item_specifics=aspects,
        categories=categories,
        buying_formats=formats,
        listing_created_at=timestamp("itemCreationDate"),
        listing_origin_at=timestamp("itemOriginDate"),
        listing_ends_at=timestamp("itemEndDate"),
        first_observed_at=observed_at,
        last_observed_at=observed_at,
        details_observed_at=observed_at if details_loaded else None,
        quality_flags=sorted(set(flags)),
        source_metadata=metadata,
    )
