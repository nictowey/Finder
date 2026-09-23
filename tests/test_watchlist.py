"""Buyer ceiling triage must abstain when identity, price, or destination is uncertain."""

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.matching import decide_match
from finder.watchlist import WatchTarget, assess_watch_target


def _case(search_payload, discogs_release, observed_at):
    raw = search_payload["itemSummaries"][0]
    raw["title"] = "Example Artist Example Album vinyl LP"
    raw["localizedAspects"] = [
        {"name": "Artist", "value": "Example Artist"},
        {"name": "UPC", "value": "0123456789012"},
    ]
    listing = normalize_listing(raw, observed_at)
    variant = normalize_release(discogs_release, observed_at)
    decision = decide_match(listing, [variant])
    target = WatchTarget(
        id="example-album",
        marketplace="ebay",
        catalog_source="discogs",
        family_id=variant.catalog_product_id,
        maximum_delivered_subtotal=Decimal("40"),
        currency="USD",
        destination_country="US",
        destination_postal_code="10001",
        created_at=observed_at,
    )
    return target, listing, decision, variant


def _assess(target, listing, decision, observed_at, **changes):
    return assess_watch_target(
        target,
        listing,
        decision,
        as_of=changes.pop("as_of", observed_at),
        maximum_age=timedelta(hours=1),
        destination_quote_verified=changes.pop("destination_quote_verified", True),
        **changes,
    )


def test_family_ceiling_can_yield_only_an_internal_candidate(
    search_payload, discogs_release, observed_at
):
    target, listing, decision, _ = _case(search_payload, discogs_release, observed_at)
    assert decision.outcome == "probable_variant"
    result = _assess(target, listing, decision, observed_at)
    assert result.status == "candidate"
    assert result.delivered_subtotal == Decimal("34.49")
    assert result.reasons == []
    assert result.model_validate_json(result.model_dump_json()) == result


def test_exact_target_abstains_on_probable_variant(search_payload, discogs_release, observed_at):
    target, listing, decision, variant = _case(search_payload, discogs_release, observed_at)
    target = target.model_copy(update={"family_id": None, "variant_id": variant.catalog_variant_id})
    result = _assess(target, listing, decision, observed_at)
    assert result.status == "review"
    assert "exact_pressing_unconfirmed" in result.reasons


def test_incomplete_catalog_search_needs_review(search_payload, discogs_release, observed_at):
    target, listing, _, variant = _case(search_payload, discogs_release, observed_at)
    decision = decide_match(listing, [variant], retrieval_incomplete=True)
    result = _assess(target, listing, decision, observed_at)
    assert result.status == "review"
    assert "catalog_search_incomplete" in result.reasons


def test_quoted_shipping_requires_destination_confirmation(
    search_payload, discogs_release, observed_at
):
    target, listing, decision, _ = _case(search_payload, discogs_release, observed_at)
    result = _assess(target, listing, decision, observed_at, destination_quote_verified=False)
    assert result.status == "review"
    assert result.reasons == ["destination_quote_unverified"]


def test_unknown_shipping_and_auction_cannot_pass_ceiling(
    search_payload, discogs_release, observed_at
):
    target, listing, decision, _ = _case(search_payload, discogs_release, observed_at)
    listing = listing.model_copy(update={"shipping_cost": None, "price_kind": "current_bid"})
    result = _assess(target, listing, decision, observed_at)
    assert result.status == "review"
    assert result.delivered_subtotal is None
    assert "delivered_subtotal_unknown" in result.reasons
    assert "final_price_unknown" in result.reasons


def test_above_ceiling_or_unacceptable_condition_excluded(
    search_payload, discogs_release, observed_at
):
    target, listing, decision, _ = _case(search_payload, discogs_release, observed_at)
    target = target.model_copy(
        update={
            "maximum_delivered_subtotal": Decimal("30"),
            "acceptable_condition_ids": frozenset({"1000"}),
        }
    )
    result = _assess(target, listing, decision, observed_at)
    assert result.status == "excluded"
    assert "above_buyer_ceiling" in result.reasons
    assert "condition_excluded" in result.reasons


def test_stale_listing_and_missing_condition_need_review(
    search_payload, discogs_release, observed_at
):
    target, listing, decision, _ = _case(search_payload, discogs_release, observed_at)
    target = target.model_copy(update={"acceptable_condition_ids": frozenset({"1000"})})
    listing = listing.model_copy(update={"condition_id": None})
    result = _assess(target, listing, decision, observed_at, as_of=observed_at + timedelta(hours=2))
    assert result.status == "review"
    assert "listing_stale" in result.reasons
    assert "condition_unknown" in result.reasons


def test_wrong_listing_decision_is_rejected(search_payload, discogs_release, observed_at):
    target, listing, decision, _ = _case(search_payload, discogs_release, observed_at)
    with pytest.raises(ValueError, match="different listing"):
        _assess(
            target,
            listing.model_copy(update={"marketplace_item_id": "other"}),
            decision,
            observed_at,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"family_id": None},
        {"variant_id": "111"},
        {"maximum_delivered_subtotal": Decimal("NaN")},
        {"maximum_delivered_subtotal": Decimal("Infinity")},
        {"maximum_delivered_subtotal": Decimal("0")},
        {"currency": "usd"},
    ],
)
def test_target_rejects_ambiguous_or_invalid_configuration(
    search_payload, discogs_release, observed_at, changes
):
    target, _, _, _ = _case(search_payload, discogs_release, observed_at)
    with pytest.raises(ValidationError):
        WatchTarget.model_validate({**target.model_dump(), **changes})
