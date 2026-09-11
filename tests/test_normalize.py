from decimal import Decimal

import pytest

from finder.adapters.ebay.normalize import normalize_listing
from finder.errors import InvalidListingError


def test_complete_listing(search_payload, detail_payload, observed_at):
    listing = normalize_listing(
        {**search_payload["itemSummaries"][0], **detail_payload}, observed_at, details_loaded=True
    )
    assert listing.marketplace_item_id == "v1|123456789012|0"
    assert listing.current_price == Decimal("29.99")
    assert listing.shipping_cost == Decimal("4.50")
    assert listing.total_acquisition_cost == Decimal("34.49")
    assert listing.seller_feedback_percentage == Decimal("99.8")
    assert listing.seller_feedback_score == 1234
    assert listing.seller_username == "fixture-records"
    assert listing.condition == "Used"
    assert listing.item_specifics["Color"] == ["Blue"]
    assert listing.categories[0]["id"] == "176985"
    assert listing.listing_created_at.isoformat() == "2026-09-01T14:00:00+00:00"
    assert listing.listing_origin_at.isoformat() == "2026-08-31T14:00:00+00:00"
    assert listing.listing_ends_at.isoformat() == "2026-10-01T14:00:00+00:00"
    assert listing.first_observed_at == listing.last_observed_at == observed_at
    assert listing.details_observed_at == observed_at
    assert listing.primary_image.startswith("https://i.ebayimg.com/")
    assert listing.listing_url.endswith("123456789012")
    assert listing.quality_flags == []


@pytest.mark.parametrize(
    "cost,currency,total",
    [
        ("0.00", "USD", Decimal("29.99")),
        ("0.01", "USD", Decimal("30.00")),
        ("5.00", "EUR", None),
        (None, "USD", None),
        ("-2.00", "USD", None),
        ("NaN", "USD", None),
    ],
)
def test_total_cost(search_payload, observed_at, cost, currency, total):
    raw = search_payload["itemSummaries"][0]
    raw["shippingOptions"] = [{"shippingCost": {"value": cost, "currency": currency}}]
    assert normalize_listing(raw, observed_at).total_acquisition_cost == total


def test_unknown_shipping_is_not_free(search_payload, observed_at):
    raw = search_payload["itemSummaries"][0]
    raw.pop("shippingOptions")
    listing = normalize_listing(raw, observed_at)
    assert listing.shipping_cost is None
    assert listing.total_acquisition_cost is None
    assert "shipping_unknown" in listing.quality_flags


def test_cheapest_quoted_shipping_in_price_currency(search_payload, observed_at):
    raw = search_payload["itemSummaries"][0]
    raw["shippingOptions"] = [
        {"shippingCost": {"value": value, "currency": curr}}
        for value, curr in [("0", "EUR"), ("8", "USD"), ("3.50", "USD")]
    ]
    assert normalize_listing(raw, observed_at).shipping_cost == Decimal("3.50")


def test_auction_and_missing_optional_fields(search_payload, observed_at):
    listing = normalize_listing(search_payload["itemSummaries"][1], observed_at)
    assert listing.current_price == Decimal("10")
    assert listing.price_kind == "current_bid"
    assert listing.condition is None
    assert listing.seller_username is None
    assert listing.total_acquisition_cost is None


@pytest.mark.parametrize("raw", [None, [], {}, {"title": "no ID"}, {"itemId": "abc"}])
def test_invalid_listings(raw, observed_at):
    with pytest.raises(InvalidListingError):
        normalize_listing(raw, observed_at)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "invalid", True, {}, None])
def test_bad_price_preserves_partial_listing(value, observed_at):
    raw = {"itemId": "id", "title": "title", "price": {"value": value, "currency": "USD"}}
    listing = normalize_listing(raw, observed_at)
    assert listing.current_price is None
    assert "price_unknown" in listing.quality_flags


def test_malformed_optional_containers(observed_at):
    listing = normalize_listing(
        {
            "itemId": "id",
            "title": "title",
            "seller": [],
            "image": "bad",
            "shippingOptions": [None, {}],
            "localizedAspects": [None, {"name": "Color", "value": "Blue"}],
            "itemCreationDate": "bad",
            "itemEndDate": "2026-09-11",
        },
        observed_at,
    )
    assert listing.listing_created_at is None
    assert listing.listing_ends_at is None
    assert listing.item_specifics == {"Color": ["Blue"]}
    assert "invalid_itemCreationDate" in listing.quality_flags


def test_multiple_aspect_values(observed_at):
    raw = {
        "itemId": "id",
        "title": "title",
        "localizedAspects": [
            {"name": "Genre", "value": "Rap"},
            {"name": "Genre", "value": "Hip Hop"},
            {"name": "Genre", "value": "Rap"},
        ],
    }
    assert normalize_listing(raw, observed_at).item_specifics["Genre"] == ["Rap", "Hip Hop"]
