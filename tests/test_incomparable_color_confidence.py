"""An identifier cannot make an explicit, uncheckable disc-color claim likely."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from finder.categories.target_review import review_target_listing, review_target_with_alternatives
from finder.categories.vinyl_clues import Clue
from finder.domain import Listing, Variant
from finder.matching import decide_match
from finder.watch_store import SavedWatch
from finder.watch_worker import assess_review

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
BARCODE = "00012345678905"
CATNO = "SYN-123"


def target(color="Cloudburst", id="1"):
    return Variant(
        catalog_source="synthetic",
        catalog_variant_id=id,
        catalog_product_id="synthetic-family",
        title="Example Album",
        artists=["Example Artist"],
        formats=[{"name": "Vinyl", "descriptions": ["LP"], **({"text": color} if color else {})}],
        identifiers={"Barcode": [BARCODE]},
        labels=[{"name": "Example Label", "catno": CATNO}],
        observed_at=NOW,
    )


def listing(color="Black", field="Color", identifier="Barcode"):
    specifics = {
        "Artist": ["Example Artist"],
        identifier: [BARCODE if identifier == "Barcode" else CATNO],
    }
    if field and color:
        specifics[field] = [color]
    return Listing(
        marketplace="synthetic",
        marketplace_item_id="incomparable-color",
        title="Example Artist Example Album "
        + (color + " vinyl" if color and not field else "vinyl"),
        item_specifics=specifics,
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


def assess(row, variant=None, alternatives=None, **settings):
    return assess_review(
        SavedWatch(release_id=1, country="US", postal_code="00000", **settings),
        row,
        variant or target(),
        now=NOW,
        alternatives=alternatives or [],
        search_incomplete=False,
    )


@pytest.mark.parametrize("catalog_color", ["Cloudburst", "180g", None])
@pytest.mark.parametrize("seller_color", ["Black", "Blue"])
@pytest.mark.parametrize("field", [None, "Color", "Record Color"])
@pytest.mark.parametrize("identifier", ["Barcode", "Catalog Number"])
def test_exact_identifier_does_not_resolve_incomparable_positive_color(
    catalog_color, seller_color, field, identifier
):
    row, variant = listing(seller_color, field, identifier), target(catalog_color)
    original = row.model_dump(mode="json")
    review = review_target_listing(row, variant)
    assert review.status == "family_review"
    assert "color_not_comparable" in review.verify and "color_conflict" not in review.verify
    assert "seller_identifier_claim" in review.clues
    result = assess(row, variant, maximum_subtotal=Decimal("25"))
    assert result["status"] == "family_review" and not result["notify"]
    decision = decide_match(row, [variant])
    assert decision.outcome == "family_only"
    assert "color_not_comparable" in decision.missing_evidence
    assert "color" not in decision.conflicts
    assert row.model_dump(mode="json") == original


@pytest.mark.parametrize("mode", ["review_leads", "strict"])
@pytest.mark.parametrize("amount,notify", [("19", False), ("25", True)])
def test_incomparable_color_keeps_owner_unclear_price_and_strict_controls(mode, amount, notify):
    result = assess(listing(), gamble_max=Decimal(amount), alert_mode=mode)
    assert result["status"] == "family_review"
    assert result["notify"] is (notify and mode == "review_leads")


@pytest.mark.parametrize(
    "kind,value",
    [("barcode", BARCODE), ("catalog_number", CATNO), ("keyword", "vinyl")],
)
def test_required_sign_does_not_erase_unchecked_catalog_color(kind, value):
    row = listing().model_copy(
        update={
            "item_specifics": {
                "Artist": ["Example Artist"],
                "Color": ["Black"],
                "Barcode": [BARCODE],
                "Catalog Number": [CATNO],
            }
        }
    )
    result = assess(
        row, tells=[Clue(kind=kind, value=value, required=True)], maximum_subtotal=Decimal("25")
    )
    assert result["status"] == "family_review" and not result["notify"]
    assert result["signs"] and not result["missing_signs"]
    assert "color_not_comparable" in result["verify"]


def test_uncheckable_target_stays_unclear_when_identifiers_rule_out_all_siblings():
    sibling = target("Black", "2").model_copy(
        update={
            "identifiers": {"Barcode": ["00012345678912"]},
            "labels": [{"name": "Example Label", "catno": "SYN-999"}],
        }
    )
    review = review_target_with_alternatives(
        listing(), target(), [sibling], search_incomplete=False
    )
    assert review.status == "family_review" and review.alternatives_not_ruled_out == 0
    assert "seller_identifier_claim" in review.clues and "color_not_comparable" in review.verify


@pytest.mark.parametrize(
    "seller_color,catalog_color,status",
    [
        (None, "Cloudburst", "possible_pressing"),
        ("Black", "Black", "possible_pressing"),
        ("Blue", "Clear", "conflicting"),
        ("Blue", "Blue/White", "possible_pressing"),
        ("Cloudburst", "Cloudburst", "possible_pressing"),
        ("clear PVC sleeve", "Cloudburst", "possible_pressing"),
    ],
)
def test_absence_comparable_conflicts_partial_pairs_and_packaging_keep_existing_policy(
    seller_color, catalog_color, status
):
    result = assess(listing(seller_color), target(catalog_color))
    assert result["status"] == status
    assert result["notify"] is (status == "possible_pressing")


def test_real_identifier_conflict_remains_a_conflict():
    row = listing().model_copy(
        update={"item_specifics": {"Color": ["Black"], "Barcode": ["00012345678912"]}}
    )
    result = assess(row)
    assert result["status"] == "conflicting" and not result["notify"]
    assert "barcode" in result["verify"]


def test_explicit_common_version_sign_still_conflicts():
    result = assess(listing(), anti_tells=[Clue(kind="color", value="Black")])
    assert result["status"] == "conflicting" and not result["notify"]
    assert "common_version_sign" in result["verify"]


def test_unrelated_sparse_catalog_candidate_cannot_downgrade_known_comparable_match():
    unrelated = target("Cloudburst", "2").model_copy(
        update={"title": "Different Album", "artists": ["Different Artist"]}
    )
    result = decide_match(listing("Black"), [target("Black"), unrelated])
    assert result.outcome == "probable_variant"
    assert "color_not_comparable" not in result.missing_evidence


@pytest.mark.parametrize(
    "seller_color,required,status",
    [
        ("Black", "Black", "possible_pressing"),
        ("Black/Blue", "Black", "family_review"),
        ("Black/Blue", "Black/Blue", "possible_pressing"),
        ("Black", "Black/Blue", "family_review"),
        ("Black", "Cloudburst", "family_review"),
    ],
)
def test_owner_required_color_can_supply_missing_catalog_knowledge(seller_color, required, status):
    result = assess(
        listing(seller_color),
        tells=[Clue(kind="color", value=required, required=True)],
        maximum_subtotal=Decimal("25"),
    )
    assert result["status"] == status
    assert result["notify"] is (status == "possible_pressing")


def test_optional_color_hint_alone_does_not_promote_uncheckable_color():
    result = assess(listing(), tells=[Clue(kind="color", value="Black")])
    assert result["signs"] == ["color: Black"]
    assert result["status"] == "family_review" and not result["notify"]


@pytest.mark.parametrize(
    "title,structured,status",
    [
        ("Black vinyl", "Blue", "family_review"),
        ("Red vinyl", "Black", "possible_pressing"),
        ("not Black vinyl", "Black", "conflicting"),
        ("Black or Blue vinyl", "Black", "family_review"),
    ],
)
def test_owner_color_resolution_keeps_source_precedence_denials_and_choices(
    title, structured, status
):
    row = listing(structured).model_copy(update={"title": "Example Artist Example Album " + title})
    result = assess(
        row,
        tells=[Clue(kind="color", value="Black", required=True)],
        maximum_subtotal=Decimal("25"),
    )
    assert result["status"] == status
    assert result["notify"] is (status == "possible_pressing")


def test_owner_required_color_cannot_override_unresolved_sibling_or_missing_required_hint():
    color = Clue(kind="color", value="Black", required=True)
    sibling = target("Cloudburst", "2")
    result = assess(listing(), alternatives=[sibling], tells=[color])
    assert result["status"] == "family_review" and not result["notify"]
    missing = assess(
        listing(), tells=[color, Clue(kind="keyword", value="Numbered", required=True)]
    )
    assert missing["status"] == "family_review" and not missing["notify"]
    unavailable = assess_review(
        SavedWatch(release_id=1, tells=[color]),
        listing(),
        target(),
        now=NOW,
        alternatives=None,
        search_incomplete=True,
    )
    assert unavailable["status"] == "family_review" and not unavailable["notify"]


def test_separate_required_color_tells_jointly_cover_selected_palette():
    tells = [Clue(kind="color", value=color, required=True) for color in ("Black", "Blue")]
    result = assess(listing("Black/Blue"), tells=tells, maximum_subtotal=Decimal("25"))
    assert result["status"] == "possible_pressing" and result["notify"]
    assert result["signs"] == ["color: Black", "color: Blue"] and not result["missing_signs"]


def test_required_color_denial_still_conflicts_with_unknown_catalog_palette():
    result = assess(
        listing("not Black"),
        tells=[Clue(kind="color", value="Black", required=True)],
        gamble_max=Decimal("25"),
    )
    assert result["status"] == "conflicting" and not result["notify"]
    assert "required_sign_conflict" in result["verify"]
