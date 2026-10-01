"""Different catalog-defined covers can remove unsupported family-review leads."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from finder.categories.target_review import review_target_with_alternatives
from finder.categories.vinyl_clues import Clue
from finder.categories.vinyl_covers import conflicting_catalog_cover, cover_name
from finder.domain import Listing, Variant
from finder.watch_store import SavedWatch
from finder.watch_worker import assess_review

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)


def target(name="Northern Lights", id="1", **changes):
    return Variant(
        catalog_source="synthetic",
        catalog_variant_id=id,
        catalog_product_id="synthetic-family",
        title="Example Album",
        artists=["Example Artist"],
        formats=[
            {"name": "Vinyl", "descriptions": ["LP"]},
            {"name": "All Media", "text": f"{name} Alternative Artwork"},
        ],
        observed_at=NOW,
    ).model_copy(update=changes)


def row(suffix="River Scene vinyl LP", specifics=None, **changes):
    return Listing(
        marketplace="synthetic",
        marketplace_item_id="cover-review",
        title="Example Artist Example Album " + suffix,
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
    ).model_copy(update=changes)


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("mode", ["review_leads", "strict"])
def test_catalog_other_cover_rejects_family_lead_and_cannot_alert(partial, required, mode):
    listing = row()
    original = listing.model_dump(mode="json")
    variant, other = target(), target("River Scene", "2")
    review = review_target_with_alternatives(listing, variant, [other], search_incomplete=partial)
    assert review.status == "conflicting"
    assert "catalog_cover_conflict" in review.verify
    assert review.alternatives_checked == 1
    watch = SavedWatch(
        release_id=1,
        country="US",
        postal_code="00000",
        maximum_subtotal=Decimal("25"),
        gamble_max=Decimal("25"),
        alert_mode=mode,
        tells=[Clue(kind="keyword", value="vinyl", required=True)] if required else [],
    )
    result = assess_review(
        watch, listing, variant, now=NOW, alternatives=[other], search_incomplete=partial
    )
    assert result["status"] == "conflicting" and not result["notify"]
    assert listing.model_dump(mode="json") == original


@pytest.mark.parametrize("suffix", ["Northern Lights vinyl", "vinyl", "Unknown Design vinyl"])
def test_target_name_or_missing_name_never_promotes_by_itself(suffix):
    review = review_target_with_alternatives(
        row(suffix), target(), [target("River Scene", "2")], search_incomplete=False
    )
    assert review.status == "family_review"
    assert "catalog_cover_conflict" not in review.verify


@pytest.mark.parametrize(
    "suffix",
    [
        "River Scene cover not included vinyl",
        "not River Scene vinyl",
        "River Scene or Northern Lights vinyl",
        "River Scene and Northern Lights 2LP",
        "River Scene + Northern Lights vinyl",
        "Northern Lights vinyl with River Scene insert",
        "River Scene signed insert vinyl LP",
        "River Scene cover insert LP",
        "River Scene vinyl?",
        "River Scene vinyl bundle",
        "River Scene photo vinyl",
        "River Scene cover song LP",
    ],
)
def test_ambiguous_missing_or_accessory_claims_keep_family_review(suffix):
    review = review_target_with_alternatives(
        row(suffix), target(), [target("River Scene", "2")], search_incomplete=False
    )
    assert review.status == "family_review"
    assert "catalog_cover_conflict" not in review.verify


@pytest.mark.parametrize(
    "changes",
    [
        {"catalog_source": "different-source"},
        {"catalog_product_id": "different-family"},
        {"catalog_variant_id": "1"},
        {"artists": ["Different Artist"]},
        {"title": "Different Album"},
        {"formats": [{"name": "CD", "text": "River Scene Alternative Artwork"}]},
        {"formats": [{"name": "Vinyl", "text": "River Scene"}]},
    ],
)
def test_incompatible_or_unsupported_sibling_is_not_cover_evidence(changes):
    other = target("River Scene", "2", **changes)
    assert conflicting_catalog_cover(row(), target(), [other]) is None


@pytest.mark.parametrize("marker", ["Alternative Cover", "Alternative Artwork", "Alternate Cover"])
@pytest.mark.parametrize("format_name", ["Vinyl", "All Media"])
@pytest.mark.parametrize(
    "layout", ["{name} {marker}", "{name}, {marker}", "Signed Insert, {name} {marker}"]
)
def test_structured_catalog_names_are_dynamic_and_source_bound(marker, format_name, layout):
    variant = target(
        formats=[
            {"name": format_name, "text": layout.format(name="The Northern Lights", marker=marker)}
        ]
    )
    assert cover_name(variant) == "northern lights"


@pytest.mark.parametrize(
    "texts",
    [
        ["Alternative Artwork"],
        ["White Vinyl"],
        ["180g"],
        ["Signed Insert, Alternative Cover"],
        ["Northern Lights Alternative Cover", "River Scene Alternative Cover"],
        ["Northern Lights or River Scene Alternative Cover"],
        ["Not Northern Lights Alternative Cover"],
        ["Northern Lights / River Scene Alternative Cover"],
        ["Northern Lights + River Scene Alternative Cover"],
        ["Limited Edition, Alternative Artwork"],
        ["The Alternative Cover"],
        ["Unknown Alternative Cover"],
        ["None Alternative Artwork"],
        ["2026 Alternative Artwork"],
    ],
)
def test_catalog_uncertainty_does_not_invent_a_named_cover(texts):
    variant = target(formats=[{"name": "Vinyl", "text": text} for text in texts])
    assert cover_name(variant) is None
    assert conflicting_catalog_cover(row(), variant, [target("River Scene", "2")]) is None


def test_exact_target_identifier_retains_existing_contradictory_review_behavior():
    variant = target(identifiers={"Barcode": ["00012345678905"]})
    other = target("River Scene", "2", identifiers={"Barcode": ["00012345678912"]})
    review = review_target_with_alternatives(
        row(specifics={"Barcode": ["00012345678905"]}), variant, [other], search_incomplete=False
    )
    assert review.status == "possible_pressing"
    assert "catalog_cover_conflict" not in review.verify


def test_full_structured_target_palette_is_independent_support_and_is_not_discarded():
    variant = target(
        formats=[
            {"name": "Vinyl", "text": "Blue/Green"},
            {"name": "All Media", "text": "Northern Lights Alternative Cover"},
        ]
    )
    other = target("River Scene", "2")
    assert (
        conflicting_catalog_cover(row(specifics={"Color": ["Blue/Green"]}), variant, [other])
        is None
    )


def colored_cover(name, id, color):
    return target(
        name,
        id,
        formats=[
            {"name": "Vinyl", "text": color},
            {"name": "All Media", "text": f"{name} Alternative Cover"},
        ],
    )


@pytest.mark.parametrize("color", ["White", "Blue/Green"])
def test_palette_shared_with_claimed_cover_does_not_cancel_cover_conflict(color):
    variant = colored_cover("Northern Lights", "1", color)
    other = colored_cover("River Scene", "2", color)
    listing = row(specifics={"Color": [color]})
    review = review_target_with_alternatives(listing, variant, [other], search_incomplete=False)
    assert review.status == "conflicting"
    assert "catalog_cover_conflict" in review.verify
    watch = SavedWatch(release_id=1, gamble_max=Decimal("25"))
    result = assess_review(watch, listing, variant, now=NOW, alternatives=[other])
    assert result["status"] == "conflicting" and not result["notify"]


@pytest.mark.parametrize("other_color", ["", "Blue", "White/Blue"])
def test_matching_target_palette_still_abstains_when_claimed_cover_palette_differs_or_missing(
    other_color,
):
    variant = colored_cover("Northern Lights", "1", "White")
    other = colored_cover("River Scene", "2", other_color)
    assert conflicting_catalog_cover(row(specifics={"Color": ["White"]}), variant, [other]) is None


def test_shared_palette_must_belong_to_the_actual_claimed_cover():
    variant = colored_cover("Northern Lights", "1", "White")
    others = [
        colored_cover("River Scene", "2", "Blue"),
        colored_cover("Moon Scene", "3", "White"),
    ]
    assert conflicting_catalog_cover(row(specifics={"Color": ["White"]}), variant, others) is None


@pytest.mark.parametrize("field", ["Barcode", "Catalog Number"])
def test_shared_palette_never_overrides_exact_target_identifier(field):
    variant = colored_cover("Northern Lights", "1", "White").model_copy(
        update=(
            {"identifiers": {"Barcode": ["00012345678905"]}}
            if field == "Barcode"
            else {"labels": [{"name": "Example Label", "catno": "00012345678905"}]}
        )
    )
    other = colored_cover("River Scene", "2", "White")
    listing = row(specifics={"Color": ["White"], field: ["00012345678905"]})
    assert conflicting_catalog_cover(listing, variant, [other]) is None


def test_shared_palette_never_promotes_matching_cover_or_rejects_an_ambiguous_offer():
    variant = colored_cover("Northern Lights", "1", "White")
    other = colored_cover("River Scene", "2", "White")
    for suffix in [
        "Northern Lights vinyl LP",
        "Northern Lights and River Scene vinyl LP",
        "River Scene cover sold separately vinyl LP",
    ]:
        review = review_target_with_alternatives(
            row(suffix, {"Color": ["White"]}), variant, [other], search_incomplete=False
        )
        assert review.status == "family_review"
        assert "catalog_cover_conflict" not in review.verify


def test_same_cover_different_release_is_not_a_cover_conflict():
    assert (
        conflicting_catalog_cover(row("Northern Lights vinyl"), target(), [target(id="2")]) is None
    )


def test_multiple_named_siblings_or_overlapping_names_are_not_tie_broken():
    assert (
        conflicting_catalog_cover(
            row("River Scene Moon Scene vinyl"),
            target(),
            [target("River Scene", "2"), target("Moon Scene", "3")],
        )
        is None
    )
    assert (
        conflicting_catalog_cover(
            row("The Extended River Scene vinyl"),
            target(),
            [target("River Scene", "2"), target("Extended River Scene", "3")],
        )
        is None
    )


def test_a_name_cannot_be_assembled_across_title_and_specifics():
    assert (
        conflicting_catalog_cover(
            row("River vinyl", {"Edition": ["Scene"]}), target(), [target("River Scene", "2")]
        )
        is None
    )


@pytest.mark.parametrize("changes", [{"artists": []}, {"artists": [""]}, {"title": ""}])
def test_missing_catalog_family_identity_cannot_establish_a_cover_conflict(changes):
    assert (
        conflicting_catalog_cover(row(), target(**changes), [target("River Scene", "2", **changes)])
        is None
    )


@pytest.mark.parametrize(
    "suffix",
    [
        "vinyl LP River Scene cover sold separately",
        "River Scene cover sold separately vinyl LP",
        "River Scene cover photograph vinyl LP",
        "River Scene cover photographs vinyl LP",
        "photograph of River Scene cover vinyl LP",
    ],
)
def test_separate_cover_sales_and_photographs_do_not_reject_the_record(suffix):
    review = review_target_with_alternatives(
        row(suffix), target(), [target("River Scene", "2")], search_incomplete=False
    )
    assert review.status == "family_review"
    assert "catalog_cover_conflict" not in review.verify


@pytest.mark.parametrize(
    "suffix",
    [
        "River Scene cover vinyl LP",
        "River Scene cover LP sold separately",
        "River Scene edition LP",
    ],
)
def test_ordinary_named_record_offer_remains_a_catalog_cover_conflict(suffix):
    review = review_target_with_alternatives(
        row(suffix), target(), [target("River Scene", "2")], search_incomplete=False
    )
    assert review.status == "conflicting"
    assert "catalog_cover_conflict" in review.verify


@pytest.mark.parametrize(
    "name,suffix,specifics",
    [
        ("Group", "Universal Music Group vinyl LP", {}),
        ("Group", "Group Records vinyl LP", {}),
        ("Group", "record label Group vinyl LP", {}),
        ("Group", "music by Group vinyl LP", {}),
        ("Group", "Group band vinyl LP", {}),
        (
            "North River",
            "North River Holdings vinyl LP",
            {"Record Label": ["North River Holdings"]},
        ),
        ("North River", "publisher North River vinyl LP", {}),
        ("North River", "North River Publishing vinyl LP", {}),
        ("Group", "Universal Music Group vinyl LP Group cover photograph", {}),
        ("Group", "Universal Music Group vinyl LP Group signed insert", {}),
    ],
)
def test_credit_roles_are_not_claims_of_a_different_cover(name, suffix, specifics):
    review = review_target_with_alternatives(
        row(suffix, specifics), target(), [target(name, "2")], search_incomplete=False
    )
    assert review.status == "family_review"
    assert "catalog_cover_conflict" not in review.verify


@pytest.mark.parametrize(
    "suffix,specifics",
    [
        ("Group cover vinyl LP", {}),
        ("Group artwork vinyl LP", {}),
        ("Group vinyl LP", {}),
        ("Group cover vinyl LP Universal Music Group", {}),
        ("Group cover vinyl LP", {"Record Label": ["Group"]}),
        ("Group cover music vinyl LP", {}),
    ],
)
def test_independent_cover_claim_survives_a_separate_credit(suffix, specifics):
    review = review_target_with_alternatives(
        row(suffix, specifics), target(), [target("Group", "2")], search_incomplete=False
    )
    assert review.status == "conflicting"
    assert "catalog_cover_conflict" in review.verify


def test_credit_guard_does_not_turn_an_existing_multiple_cover_abstention_into_rejection():
    review = review_target_with_alternatives(
        row("River Scene cover vinyl LP Universal Music Group"),
        target(),
        [target("River Scene", "2"), target("Group", "3")],
        search_incomplete=False,
    )
    assert review.status == "family_review"


def test_credit_context_is_preserved_when_the_album_itself_contains_a_role_word():
    review = review_target_with_alternatives(
        row().model_copy(update={"title": "Example Artist Music Universal Music Group vinyl LP"}),
        target(title="Music"),
        [target("Group", "2", title="Music")],
        search_incomplete=False,
    )
    assert review.status == "family_review"
    assert "catalog_cover_conflict" not in review.verify


def test_a_credit_phrase_cannot_be_assembled_across_structured_values():
    review = review_target_with_alternatives(
        row("North River Holdings vinyl LP", {"Record Label": ["North", "River Holdings"]}),
        target(),
        [target("North River", "2")],
        search_incomplete=False,
    )
    assert review.status == "conflicting"
    assert "catalog_cover_conflict" in review.verify
