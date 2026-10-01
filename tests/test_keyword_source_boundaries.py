"""Keyword and label phrases cannot be assembled across seller source values."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from finder.categories.vinyl_clues import Clue, ListingText, apply_cheat_sheet, clue_found
from finder.domain import Listing, Variant
from finder.matching import _compact, _normalized
from finder.watch_store import SavedWatch
from finder.watch_worker import assess_review

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
BASE_TITLE = "Synthetic Artist Synthetic Album"
BARCODE = "00012345678905"
TARGET = Variant(
    catalog_source="synthetic",
    catalog_variant_id="1",
    catalog_product_id="synthetic-family",
    title="Synthetic Album",
    artists=["Synthetic Artist"],
    formats=[{"name": "Vinyl", "descriptions": ["LP"]}],
    identifiers={"Barcode": [BARCODE]},
    observed_at=NOW,
)


def listing(title=BASE_TITLE, specifics=None):
    return Listing(
        marketplace="synthetic",
        marketplace_item_id="source-boundary",
        title=title,
        item_specifics=specifics or {},
        first_observed_at=NOW,
        last_observed_at=NOW,
        details_observed_at=NOW,
        price_kind="fixed_price",
        current_price=Decimal("20"),
        shipping_cost=Decimal("0"),
        currency="USD",
        shipping_currency="USD",
        source_metadata={"delivery_country": "US", "delivery_postal_code": "00000"},
    )


def check(row, kind, value, expected):
    original = row.model_dump(mode="json")
    seen = ListingText(row, TARGET)
    assert clue_found(Clue(kind=kind, value=value), seen) is expected
    neutral = {"status": "family_review", "clues": [], "verify": []}
    required = apply_cheat_sheet(
        neutral, row, TARGET, [Clue(kind=kind, value=value, required=True)], []
    )
    common = apply_cheat_sheet(neutral, row, TARGET, [], [Clue(kind=kind, value=value)])
    assert required["status"] == ("possible_pressing" if expected else "family_review")
    assert common["status"] == ("conflicting" if expected else "family_review")
    assert row.model_dump(mode="json") == original


@pytest.mark.parametrize("kind,value", [("keyword", "test pressing"), ("label", "Synthetic Works")])
@pytest.mark.parametrize("boundary", ["title", "fields", "values"])
def test_independent_source_values_cannot_fabricate_a_phrase(kind, value, boundary):
    first, second = value.split()
    row = {
        "title": listing(f"{BASE_TITLE} {first}", {"Features": [second]}),
        "fields": listing(specifics={"Other": [first], "Features": [second]}),
        "values": listing(specifics={"Features": [first, second]}),
    }[boundary]
    check(row, kind, value, False)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("keyword", "test pressing"),
        ("keyword", "obi"),
        ("keyword", "promo"),
        ("label", "Synthetic Works"),
    ],
)
@pytest.mark.parametrize("boundary", ["title", "fields", "values", "label"])
def test_prefix_negation_cannot_bleed_into_an_independent_positive(kind, value, boundary):
    row = {
        "title": listing(f"{BASE_TITLE} without", {"Features": [value]}),
        "fields": listing(specifics={"Condition Notes": ["no extras"], "Features": [value]}),
        "values": listing(specifics={"Features": ["no extras", value]}),
        "label": listing(specifics={"Record Label": ["No Extras"], "Features": [value]}),
    }[boundary]
    check(row, kind, value, True)


@pytest.mark.parametrize("value", ["test pressing", "obi", "promo"])
@pytest.mark.parametrize("reverse", [False, True])
def test_independent_positive_occurrence_keeps_existing_existential_keyword_policy(value, reverse):
    claims = [f"not {value}", f"{value} included"]
    if reverse:
        claims.reverse()
    check(listing(specifics={"Features": claims}), "keyword", value, True)


@pytest.mark.parametrize("value", ["test pressing", "obi", "promo"])
@pytest.mark.parametrize("claim", ["not a {}", "without {}", "Not: {}", "not\n{}"])
def test_same_source_negation_is_unchanged(value, claim):
    check(listing(specifics={"Features": [claim.format(value)]}), "keyword", value, False)


@pytest.mark.parametrize(
    "value,claim",
    [
        ("promo", "promo not for sale"),
        ("test pressing", "test pressing not numbered"),
        ("obi", "obi not original"),
        ("obi", "obi, missing insert"),
        ("promo", "Promotional"),
        ("reissue", "re-press"),
        ("test pressing", "test-pressing"),
        ("test pressing", "test\npressing"),
        ("None", "None"),
    ],
)
def test_same_source_positive_claims_and_aliases_are_unchanged(value, claim):
    check(listing(specifics={"Features": [claim]}), "keyword", value, True)


@pytest.mark.parametrize("name", ["No Extras", "Not A Promo", "None"])
def test_exact_structured_labels_remain_literal(name):
    check(listing(specifics={"Record Label": [name]}), "label", name, True)


@pytest.mark.parametrize("field", ["Artist", "Release Title"])
def test_existing_literal_owner_keyword_semantics_remain(field):
    check(listing(specifics={field: ["Promo"]}), "keyword", "promo", True)


def test_source_texts_preserve_value_boundaries_without_mutating_joined_or_raw_evidence():
    row = listing(f"{BASE_TITLE} not", {"Features": ["test", "pressing"], "Other": ["a\nb"]})
    seen = ListingText(row, TARGET)
    assert seen.source_texts == (_normalized(row.title), "test", "pressing", "a b")
    assert seen.text == _normalized(" ".join([row.title, "test", "pressing", "a\nb"]))
    assert seen.compact == _compact(row.title)
    assert row.item_specifics == {"Features": ["test", "pressing"], "Other": ["a\nb"]}


@pytest.mark.parametrize("common", [False, True])
def test_source_boundaries_reach_private_alert_policy(common):
    specifics = {"Features": ["pressing"]}
    if common:
        specifics["Barcode"] = [BARCODE]
    row = listing(f"{BASE_TITLE} test", specifics)
    clue = Clue(kind="keyword", value="test pressing", required=not common)
    watch = SavedWatch(
        release_id=1,
        country="US",
        postal_code="00000",
        maximum_subtotal=Decimal("25"),
        tells=[] if common else [clue],
        anti_tells=[clue] if common else [],
    )
    result = assess_review(watch, row, TARGET, now=NOW, alternatives=[], search_incomplete=False)
    assert result["status"] == ("possible_pressing" if common else "family_review")
    assert result["notify"] is common
    assert not result["signs"] and not result["common_signs"]


def test_independent_common_sign_cannot_be_hidden_by_another_fields_negation():
    row = listing(
        specifics={"Condition Notes": ["no extras"], "Features": ["promo"], "Barcode": [BARCODE]}
    )
    watch = SavedWatch(release_id=1, anti_tells=[Clue(kind="keyword", value="promo")])
    result = assess_review(watch, row, TARGET, now=NOW, alternatives=[], search_incomplete=False)
    assert result["status"] == "conflicting" and not result["notify"]
    assert result["common_signs"] == ["keyword: promo"]
