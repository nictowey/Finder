"""Packaging material/position words cannot turn a sleeve color into a disc claim."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from finder.categories.target_review import review_target_with_alternatives
from finder.categories.vinyl_clues import Clue, ListingText, clue_found
from finder.domain import Listing, Variant
from finder.matching import _palette, _seller_color_details, decide_match
from finder.watch_store import SavedWatch
from finder.watch_worker import assess_review

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
PACKAGING = (
    "clear PVC sleeve",
    "white paper inner sleeve",
    "clear plastic outer sleeve",
    "blue inner sleeve",
    "red outer cover",
    "white gatefold jacket",
)


def listing(text="vinyl", *, field=None):
    return Listing(
        marketplace="synthetic",
        marketplace_item_id="packaging-modifier",
        title="Example Artist Example Album " + ("vinyl" if field else text),
        item_specifics={field: [text]} if field else {},
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


def variant(color="Clear", id="1"):
    return Variant(
        catalog_source="discogs",
        catalog_variant_id=id,
        catalog_product_id="synthetic-family",
        artists=["Example Artist"],
        title="Example Album",
        formats=[{"name": "Vinyl", "descriptions": ["LP"], "text": color}],
        resource_url="https://www.discogs.com/release/" + id,
        observed_at=NOW,
    )


def assess(row, color="Clear", **settings):
    return assess_review(
        SavedWatch(release_id=1, **settings),
        row,
        variant(color),
        now=NOW,
        alternatives=[],
        search_incomplete=False,
    )


@pytest.mark.parametrize("text", PACKAGING)
@pytest.mark.parametrize("field", [None, "Color", "Record Color"])
def test_packaging_modifiers_supply_no_disc_palette_denial_or_choice(text, field):
    row = listing(text, field=field)
    original = row.model_dump(mode="json")
    for prefix in ("", "not ", "without "):
        claims = _seller_color_details([prefix + text], disc_context=bool(field))
        assert _palette(claims.positive) == claims.denied == set()
        assert not claims.ambiguous
    for color in ("Clear", "White", "Blue/White", "Cloudburst"):
        result = assess(row, color)
        assert result["status"] == "family_review" and not result["notify"]
        assert "color_not_claimed" in result["verify"]
        assert "color_conflict" not in result["verify"]
    assert row.model_dump(mode="json") == original


@pytest.mark.parametrize("text", PACKAGING)
@pytest.mark.parametrize("field", [None, "Color"])
def test_packaging_never_satisfies_required_or_common_version_color(text, field):
    row = listing(text, field=field)
    for color in ("Clear", "White", "Blue", "Red"):
        clue = Clue(kind="color", value=color, required=True)
        seen = ListingText(row, variant(color))
        assert not clue_found(clue, seen)
        assert not clue_found(clue, seen, common_version=True)
        result = assess(row, color, tells=[clue])
        assert result["status"] == "family_review" and not result["notify"]
        assert result["signs"] == []
        anti = assess(row, color, anti_tells=[clue])
        assert anti["status"] == "family_review" and not anti["notify"]


@pytest.mark.parametrize("text", PACKAGING)
@pytest.mark.parametrize("field", [None, "Color"])
def test_packaging_does_not_rule_out_siblings_or_supply_generic_color_evidence(text, field):
    row = listing(text, field=field)
    target, sibling = variant(), variant("White", "2")
    review = review_target_with_alternatives(row, target, [sibling], search_incomplete=False)
    assert review.status == "family_review" and review.alternatives_not_ruled_out == 1
    decision = decide_match(row, [target, sibling])
    assert "color" not in decision.conflicts
    assert all(item.field != "color" for item in decision.evidence)


@pytest.mark.parametrize("text", PACKAGING)
def test_packaging_absence_preserves_owner_unclear_budget(text):
    result = assess(listing(text), gamble_max=Decimal("25"), country="US", postal_code="00000")
    assert result["status"] == "family_review" and result["notify"]


@pytest.mark.parametrize(
    "text,palette,denied,ambiguous",
    [
        ("clear vinyl in a PVC sleeve", {"clear"}, set(), False),
        ("blue vinyl in a clear PVC sleeve", {"blue"}, set(), False),
        ("blue vinyl, white paper inner sleeve", {"blue"}, set(), False),
        ("clear vinyl with not white paper inner sleeve", {"clear"}, set(), False),
        ("not clear vinyl in a white paper sleeve", set(), {"clear"}, False),
        ("blue and white vinyl in a clear plastic outer sleeve", {"blue", "white"}, set(), False),
        ("blue or white vinyl in a clear plastic outer sleeve", set(), set(), True),
        ("blue vinyl in a white or red paper sleeve", {"blue"}, set(), False),
        ("blue vinyl with a white, paper inner sleeve", {"blue", "white"}, set(), False),
        ("blue vinyl with a white record in a paper sleeve", {"blue", "white"}, set(), False),
        ("clear vinyl, PVC sleeve", {"clear"}, set(), False),
        ("not clear; white paper sleeve", set(), {"clear"}, False),
    ],
)
def test_definite_disc_claims_denials_choices_and_clause_boundaries_remain(
    text, palette, denied, ambiguous
):
    claims = _seller_color_details([text])
    assert _palette(claims.positive) == palette
    assert claims.denied == denied
    assert claims.ambiguous is ambiguous


@pytest.mark.parametrize(
    "text", ["blue vinyl in a clear PVC sleeve", "blue vinyl in a white or red paper sleeve"]
)
def test_independent_disc_claim_still_supports_likely(text):
    result = assess(listing(text), "Blue", tells=[Clue(kind="color", value="Blue", required=True)])
    assert result["status"] == "possible_pressing" and result["notify"]
    assert "color_claim_ambiguous" not in result["verify"]


def test_modified_packaging_preserves_existing_structured_source_precedence():
    row = listing("white vinyl").model_copy(
        update={"item_specifics": {"Color": ["clear PVC sleeve"]}}
    )
    baseline = row.model_copy(update={"item_specifics": {"Color": ["clear sleeve"]}})
    # Source selection already prefers a nonempty structured field. This focused
    # extraction change does not broaden that separate precedence policy.
    assert assess(row) == assess(baseline)
    assert "seller_color_claim" not in assess(row)["clues"]


@pytest.mark.parametrize("mode", ["strict", "review_leads"])
def test_packaging_missing_color_keeps_price_and_strict_guards(mode):
    row = listing("clear PVC sleeve")
    result = assess(
        row, alert_mode=mode, maximum_subtotal=Decimal("25"), country="US", postal_code="00000"
    )
    assert result["status"] == "family_review" and not result["notify"]
    under = assess(
        row, alert_mode=mode, gamble_max=Decimal("25"), country="US", postal_code="00000"
    )
    assert under["notify"] is (mode == "review_leads")
    over = assess(row, alert_mode=mode, gamble_max=Decimal("19"), country="US", postal_code="00000")
    assert not over["notify"]


@pytest.mark.parametrize(
    "text",
    [
        "clear paper vinyl",
        "clear PVC record",
        "white inner disc",
        "blue gatefold vinyl",
        "clear vinyl paper sleeve",
    ],
)
def test_packaging_modifier_list_does_not_consume_disc_nouns(text):
    claims = _seller_color_details([text])
    assert _palette(claims.positive) == {text.split()[0]}
    assert not claims.denied and not claims.ambiguous


def test_catalog_name_and_validated_alias_exclusion_stays_ahead_of_packaging():
    target = variant("Blue").model_copy(
        update={"title": "Clear PVC Sleeve 2", "artists": ["White Paper"]}
    )
    row = listing("vinyl").model_copy(
        update={"title": "White Paper Clear PVC Sleeve II blue vinyl"}
    )
    from finder.categories.target_review import review_target_listing

    review = review_target_listing(row, target, album_aliases=("clear pvc sleeve ii",))
    assert review.status == "possible_pressing" and "seller_color_claim" in review.clues
