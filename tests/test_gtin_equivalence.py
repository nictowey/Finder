"""Typed, checksum-valid GTIN representations can identify the same trade item."""

from datetime import UTC, datetime
from itertools import permutations

import pytest

from finder.categories.target_review import review_target_with_alternatives
from finder.categories.vinyl_clues import Clue, ListingText, clue_found, suggest_clues
from finder.categories.vinyl_covers import conflicting_catalog_cover
from finder.domain import Listing, Variant
from finder.matching import _gtin_key, score_variant

NOW = datetime(2026, 10, 1, tzinfo=UTC)
FORMS = ["036000291452", "0036000291452", "00036000291452"]


def variant(barcodes, identity="target", cover="Northern Lights"):
    return Variant(
        catalog_source="synthetic",
        catalog_product_id="example-family",
        catalog_variant_id=identity,
        title="Example Album",
        artists=["Example Artist"],
        formats=[
            {"name": "Vinyl", "descriptions": ["LP"]},
            {"name": "All Media", "text": f"{cover} Alternative Cover"},
        ],
        identifiers={"Barcode": barcodes},
        observed_at=NOW,
    )


def listing(specifics, suffix="vinyl LP"):
    return Listing(
        marketplace="synthetic",
        marketplace_item_id="gtin-example",
        title=f"Example Artist Example Album {suffix}",
        item_specifics=specifics,
        first_observed_at=NOW,
        last_observed_at=NOW,
    )


@pytest.mark.parametrize("seller,catalog", list(permutations(FORMS, 2)))
def test_valid_gtin_padding_recognizes_same_item_and_preserves_original_claims(seller, catalog):
    row, target = listing({"UPC": [seller]}), variant([catalog])
    before = row.model_dump(), target.model_dump()
    scored = score_variant(row, target, NOW)
    barcode = next(item for item in scored.evidence if item.field == "barcode")
    assert barcode.matched
    assert barcode.listing_values == [seller] and barcode.variant_values == [catalog]
    review = review_target_with_alternatives(
        row, target, [variant(["4006381333931"], "other")], search_incomplete=False
    )
    assert review.status == "possible_pressing"
    assert review.alternatives_not_ruled_out == 0
    assert (row.model_dump(), target.model_dump()) == before


@pytest.mark.parametrize("other", [FORMS[0], FORMS[2], None])
def test_shared_equivalent_or_missing_sibling_barcode_remains_unclear(other):
    review = review_target_with_alternatives(
        listing({"UPC": [FORMS[1]]}),
        variant([FORMS[0]]),
        [variant([other] if other else [], "other")],
        search_incomplete=False,
    )
    assert review.status == "family_review"
    assert review.alternatives_not_ruled_out == 1


@pytest.mark.parametrize(
    "claim",
    [
        "4006381333931",  # A different valid item.
        "0036000291453",  # Bad check digit.
        "0036000291452 12",  # A separate supplement is not a GTIN-14.
        "0 036000291452",  # New equivalence does not assemble digit groups.
        "UPC 0036000291452",
        "0036000291452/4006381333931",
        "０036000291452",  # Do not normalize Unicode into a new barcode claim.
        "03600029",  # No UPC-E/GTIN-8 expansion.
        "10036000291459",  # A nonzero GTIN-14 indicator is a different item.
    ],
)
def test_padded_match_cannot_hide_additional_conflicting_or_unsupported_claim(claim):
    for specifics in [{"UPC": [FORMS[1]], "EAN": [claim]}, {"UPC": [FORMS[1], claim]}]:
        review = review_target_with_alternatives(
            listing(specifics), variant([FORMS[0]]), [], search_incomplete=False
        )
        assert review.status == "conflicting" and "barcode" in review.verify


def test_two_consistent_barcode_fields_can_use_different_valid_representations():
    review = review_target_with_alternatives(
        listing({"UPC": [FORMS[1]], "EAN": [FORMS[2]]}),
        variant([FORMS[0]]),
        [variant(["4006381333931"], "other")],
        search_incomplete=False,
    )
    assert review.status == "possible_pressing"


@pytest.mark.parametrize("field", ["Catalog Number", "MPN", "Release Title"])
def test_numeric_other_fields_do_not_gain_barcode_evidence(field):
    scored = score_variant(listing({field: [FORMS[1]]}), variant([FORMS[0]]), NOW)
    assert not any(item.field == "barcode" for item in scored.evidence)


def test_catalog_number_padding_is_not_a_barcode_equivalence():
    target = variant([]).model_copy(update={"labels": [{"catno": FORMS[0]}]})
    review = review_target_with_alternatives(
        listing({"Catalog Number": [FORMS[1]]}), target, [], search_incomplete=False
    )
    assert review.status == "conflicting" and "catalog_number" in review.verify


def test_equivalent_target_barcode_keeps_conflicting_cover_wording_in_review():
    assert (
        conflicting_catalog_cover(
            listing({"UPC": [FORMS[1]]}, "River Scene vinyl LP"),
            variant([FORMS[0]]),
            [variant(["4006381333931"], "other", "River Scene")],
        )
        is None
    )


@pytest.mark.parametrize("seller,catalog", list(permutations(FORMS, 2)))
def test_typed_barcode_clue_recognizes_valid_equivalent_representation(seller, catalog):
    assert clue_found(
        Clue(kind="barcode", value=catalog),
        ListingText(listing({"UPC": [seller]}), variant([catalog])),
    )


def test_equivalent_sibling_barcode_is_not_suggested_as_unique():
    proposed = suggest_clues(variant([FORMS[0]]), [variant([FORMS[1]], "other")], partial=False)
    assert not any(row["kind"] == "barcode" for row in proposed["tells"])
    assert not any(row["kind"] == "barcode" for row in proposed["anti_tells"])


@pytest.mark.parametrize(
    "value",
    [
        "000000000000",
        "0000000000000",
        "00000000000000",
        "03600029",
        "036000291453",
        "0036000291453",
        "00036000291453",
        "000036000291452",
        "0 036000291452",
        "0036000291452 12",
        "UPC 0036000291452",
        "3.6000291452E10",
        "０036000291452",
        "0036000291452/4006381333931",
    ],
)
def test_unsupported_original_barcode_strings_gain_no_gtin_key(value):
    assert _gtin_key(value) is None


def test_valid_gtin14_packaging_indicator_is_preserved():
    assert _gtin_key("10036000291459") == "10036000291459"
    assert _gtin_key("10036000291459") != _gtin_key(FORMS[0])


def test_outer_whitespace_is_not_internal_digit_assembly():
    assert _gtin_key("  " + FORMS[1] + "\t") == _gtin_key(FORMS[0])


def test_catalog_suggestion_equivalence_uses_original_strings():
    proposed = suggest_clues(
        variant([FORMS[0]]), [variant(["0 036000291452"], "other")], partial=False
    )
    assert any(row["kind"] == "barcode" for row in proposed["tells"])
    assert any(row["value"] == "0 036000291452" for row in proposed["anti_tells"])


def test_short_or_text_barcode_clue_does_not_gain_exact_match():
    for value in ["ABC", "123", "DUMMY"]:
        assert not clue_found(
            Clue(kind="barcode", value=value),
            ListingText(listing({"UPC": [value]}), variant([value])),
        )
