"""Synthetic color choices are uncertainty, never positive or contradictory palettes."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from finder.categories.target_review import review_target_listing, review_target_with_alternatives
from finder.categories.vinyl_clues import Clue, ListingText, clue_found
from finder.domain import Listing, Variant
from finder.matching import _color_evidence, _palette, _seller_color_claims, decide_match
from finder.watch_store import SavedWatch
from finder.watch_worker import assess_review

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
FIELDS = (None, "Color", "Record Color", "Vinyl Color", "Colour", "Vinyl Colour")
CHOICES = (
    "blue or white",
    "either blue or white",
    "blue vs white",
    "blue versus white",
    "blue vs. white",
    "blue vs.white",
    "blue and/or white",
    "either blue/white",
    "either bone/blue",
    "either disc 1 blue / disc 2 white",
    "blue, white or red",
    "blue and white or red",
    "blue/white or red",
    "blue or white and red",
    "blue vinyl or white edition",
    "blue or opaque white vinyl",
    "blue or bone",
    "bone or blue",
    "either disc 1 blue or disc 2 white",
    "blue or, white",
    "vinyle blue ou white",
)


def listing(text="vinyl", specifics=None):
    return Listing(
        marketplace="synthetic",
        marketplace_item_id="color-choice",
        title="Example Artist Example Album " + text,
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


def variant(color="Blue/White", id="1"):
    return Variant(
        catalog_source="discogs",
        catalog_variant_id=id,
        catalog_product_id="synthetic-family",
        artists=["Example Artist"],
        title="Example Album",
        identifiers={"Barcode": ["00012345678905"]},
        labels=[{"name": "Example Label", "catno": "SYN-123"}],
        formats=[{"name": "Vinyl", "descriptions": ["LP"], "text": color}],
        resource_url="https://www.discogs.com/release/" + id,
        observed_at=NOW,
    )


def assess(row, target=None, **settings):
    return assess_review(
        SavedWatch(release_id=1, **settings),
        row,
        target or variant(),
        now=NOW,
        alternatives=[],
        search_incomplete=False,
    )


@pytest.mark.parametrize("text", CHOICES)
@pytest.mark.parametrize("field", FIELDS)
def test_explicit_choices_cannot_supply_a_palette_or_likely_alert(text, field):
    text += " vinyl"
    row = listing(text) if field is None else listing(specifics={field: [text]})
    original = row.model_dump(mode="json")
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == denied == set()
    for color in ("Blue", "Blue/White", "Red", "Cloudburst"):
        assert _color_evidence([text], [color]) is None
        target = variant(color)
        review = review_target_listing(row, target)
        assert review.status == "family_review"
        assert "color_claim_ambiguous" in review.verify
        assert "color_not_claimed" not in review.verify
        for required in ("Blue", "White", "Blue/White"):
            result = assess(row, target, tells=[Clue(kind="color", value=required, required=True)])
            assert result["status"] == "family_review" and not result["notify"]
            assert result["signs"] == [] and result["missing_signs"] == ["color: " + required]
            assert "color_claim_ambiguous" in result["verify"]
    assert row.model_dump(mode="json") == original


@pytest.mark.parametrize("text", CHOICES)
@pytest.mark.parametrize("field", FIELDS)
def test_choices_cannot_be_promoted_by_generic_identifiers(text, field):
    specifics = {"Artist": ["Example Artist"], "Barcode": ["00012345678905"]}
    if field:
        specifics[field] = [text]
    row = listing(text if field is None else "vinyl", specifics)
    target = variant("Blue")
    result = decide_match(row, [target])
    assert result.outcome == "family_only"
    assert result.candidate_ids == [target.catalog_variant_id]
    assert "color_claim_ambiguous" in result.missing_evidence
    assert "color" not in result.conflicts
    assert any(item.field == "barcode" for item in result.evidence)


@pytest.mark.parametrize(
    "text,palette",
    [
        ("blue and white", {"blue", "white"}),
        ("blue & white", {"blue", "white"}),
        ("blue/white", {"blue", "white"}),
        ("blue vinyl / white vinyl", {"blue", "white"}),
        ("disc 1 blue, disc 2 white", {"blue", "white"}),
        ("not only blue but white", {"blue", "white"}),
        ("blue vinyl on either side", {"blue"}),
        ("either side has blue and white vinyl", {"blue", "white"}),
    ],
)
def test_definite_pairs_and_nonchoice_either_keep_their_evidence(text, palette):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == palette and denied == set()
    result = assess(listing(text))
    assert "color_claim_ambiguous" not in result["verify"]
    assert result["status"] == ("possible_pressing" if len(palette) == 2 else "family_review")
    assert result["notify"] == (len(palette) == 2)
    if len(palette) == 1:
        assert "color_pair_incomplete" in result["verify"]


@pytest.mark.parametrize(
    "text,denied",
    [
        ("not blue or white", {"blue", "white"}),
        ("neither blue nor white", {"blue", "white"}),
        ("not blue, white or red", {"blue", "white", "red"}),
        ("no blue or white", {"blue", "white"}),
        ("without blue or white", {"blue", "white"}),
        ("not blue but white", {"blue"}),
        ("blue or white; not blue", {"blue"}),
    ],
)
def test_denials_still_conflict_and_block_both_price_tiers(text, denied):
    assert _seller_color_claims([text])[1] == denied
    result = assess(
        listing(text),
        maximum_subtotal=Decimal("100"),
        gamble_max=Decimal("50"),
        country="US",
        postal_code="00000",
    )
    assert result["status"] == "conflicting" and not result["notify"]
    assert "color_conflict" in result["verify"]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(
    "text", ["blue vinyl; white or red vinyl", "disc 1 blue, disc 2 white or red"]
)
def test_independent_definite_claim_survives_but_required_tell_cannot_erase_choice(text, field):
    row = listing(text) if field is None else listing(specifics={field: [text]})
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == {"blue"} and denied == set()
    result = assess(row, tells=[Clue(kind="color", value="Blue", required=True)])
    assert result["signs"] == ["color: Blue"] and result["missing_signs"] == []
    assert result["status"] == "family_review" and not result["notify"]
    assert "color_claim_ambiguous" in result["verify"]


@pytest.mark.parametrize("field", FIELDS[1:])
@pytest.mark.parametrize(
    "title,structured", [("blue or white", "blue/white"), ("blue/white", "blue or white")]
)
def test_definite_other_source_cannot_erase_a_choice(field, title, structured):
    result = assess(
        listing(title, {field: [structured], "Barcode": ["00012345678905"]}),
        tells=[Clue(kind="color", value="Blue/White", required=True)],
    )
    assert result["status"] == "family_review" and not result["notify"]
    assert "color_claim_ambiguous" in result["verify"]


@pytest.mark.parametrize("field", FIELDS[1:])
def test_alternative_branch_cannot_supply_an_anti_tell(field):
    row = listing(specifics={field: ["blue or white"]})
    result = assess(row, anti_tells=[Clue(kind="color", value="Blue")])
    assert result["status"] == "family_review" and result["common_signs"] == []
    assert not clue_found(Clue(kind="color", value="Blue"), ListingText(row, variant()))


@pytest.mark.parametrize("field", FIELDS)
def test_definite_incompatible_color_still_conflicts_beside_a_choice(field):
    text = "blue or white; red vinyl"
    row = listing(text) if field is None else listing(specifics={field: [text]})
    evidence = _color_evidence([text], ["Blue/White"])
    assert evidence is not None and not evidence.matched
    result = assess(row)
    assert result["status"] == "conflicting" and not result["notify"]


def test_identifier_conflict_and_required_color_denial_still_override_uncertainty():
    row = listing("blue or white", {"Catalog Number": ["SYN-999"]})
    assert assess(row)["status"] == "conflicting"
    result = assess(
        listing("blue or white; not red"),
        variant("Cloudburst"),
        tells=[Clue(kind="color", value="Red", required=True)],
    )
    assert result["status"] == "conflicting" and "required_sign_conflict" in result["verify"]


def test_alternatives_do_not_fabricate_sibling_color_conflicts():
    result = review_target_with_alternatives(
        listing("blue or white"),
        variant(),
        [variant("Blue", "2")],
        search_incomplete=False,
    )
    assert result.status == "family_review" and result.alternatives_not_ruled_out == 1
    row = listing("blue or white", {"Artist": ["Example Artist"], "Barcode": ["00012345678905"]})
    decision = decide_match(row, [variant(), variant("Blue", "2")])
    assert decision.outcome == "ambiguous" and set(decision.candidate_ids) == {"1", "2"}
    assert "color" not in decision.conflicts


@pytest.mark.parametrize("object_name", ["sleeve", "cover", "label", "jacket", "artwork"])
def test_packaging_choice_does_not_invalidate_independent_disc_claim(object_name):
    row = listing("blue vinyl with white or red " + object_name)
    result = assess(row, variant("Blue"))
    assert result["status"] == "possible_pressing" and result["notify"]
    assert "color_claim_ambiguous" not in result["verify"]


@pytest.mark.parametrize(
    "name_field,name", [("title", "Blue Or White"), ("artists", "Blue Versus White")]
)
def test_catalog_names_and_unrelated_candidates_cannot_invent_a_choice(name_field, name):
    changes = {name_field: [name] if name_field == "artists" else name}
    target = variant("Blue").model_copy(update=changes)
    row = listing(specifics={"Artist": target.artists, "Barcode": ["00012345678905"]}).model_copy(
        update={"title": " ".join([*target.artists, target.title, "blue vinyl"])}
    )
    unrelated = variant("White", "2").model_copy(update={"title": "Other Album"})
    assert assess(row, target)["status"] == "possible_pressing"
    decision = decide_match(row, [unrelated, target])
    assert decision.outcome == "probable_variant"
    assert "color_claim_ambiguous" not in decision.missing_evidence


def test_validated_alias_and_arbitrary_specifics_are_excluded_from_color_choices():
    target = variant("Blue").model_copy(update={"title": "Blue Or White 2"})
    alias = "blue or white ii"
    row = listing(specifics={"Notes": ["blue or white"], "Artist": ["Example Artist"]}).model_copy(
        update={"title": "Example Artist " + alias + " blue vinyl"}
    )
    result = review_target_listing(row, target, album_aliases=(alias,))
    assert result.status == "possible_pressing" and "color_claim_ambiguous" not in result.verify
    assert clue_found(
        Clue(kind="color", value="Blue"), ListingText(row, target, album_aliases=(alias,))
    )
    assert (
        assess(listing("blue/white", {"Notes": ["blue or white"]}))["status"] == "possible_pressing"
    )


@pytest.mark.parametrize(
    "text",
    [
        "vinyle bleu ou blanc",
        "vinyle bleu, ou blanc",
        "vinyle bleu or blanc",
        "vinyle bleu, or white",
        "blue or vinyle blanc",
        "vinyle bleu ou white",
    ],
)
@pytest.mark.parametrize("field", FIELDS)
def test_french_and_mixed_choices_propagate_without_leaking_either_branch(text, field):
    row = listing(text) if field is None else listing(specifics={field: [text]})
    row = row.model_copy(
        update={"item_specifics": {**row.item_specifics, "Barcode": ["00012345678905"]}}
    )
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == denied == set()
    result = assess(row, tells=[Clue(kind="color", value="Blue", required=True)])
    assert result["status"] == "family_review" and not result["notify"]
    assert "color_claim_ambiguous" in result["verify"]


@pytest.mark.parametrize("text", ["vinyle", "vinyle rouge", "vinyle bleu et rouge"])
def test_french_abstention_without_an_explicit_choice_does_not_block_an_identifier(text):
    row = listing(text, {"Barcode": ["00012345678905"]})
    result = assess(row, variant("Blue"))
    assert result["status"] == "possible_pressing" and result["notify"]
    assert "color_claim_ambiguous" not in result["verify"]


@pytest.mark.parametrize(
    "title,structured",
    [
        ("vinyle bleu ou blanc", "blue/white"),
        ("blue or white", "vinyle bleu et blanc"),
        ("vinyle bleu et blanc", "blue or white"),
    ],
)
def test_cross_language_definite_source_cannot_erase_choice(title, structured):
    result = assess(listing(title, {"Color": [structured]}))
    assert result["status"] == "family_review" and not result["notify"]
    assert "color_claim_ambiguous" in result["verify"]


def test_gamble_alert_remains_owner_authorized_and_strict_freshness_rules_are_unchanged():
    settings = dict(gamble_max=Decimal("50"), country="US", postal_code="00000")
    row = listing("blue or white")
    result = assess(row, **settings)
    assert result["status"] == "family_review" and result["notify"]
    assert result["alert_budget"] == "within_ceiling"
    assert not assess(row, alert_mode="strict", **settings)["notify"]
    assert not assess(
        row.model_copy(update={"details_observed_at": NOW - timedelta(hours=2)}), **settings
    )["notify"]
    assert not assess(row.model_copy(update={"shipping_cost": None}), **settings)["notify"]
    assert not assess(row.model_copy(update={"source_metadata": {}}), **settings)["notify"]


def test_catalog_palette_is_not_changed_to_seller_choice_semantics():
    assert _palette(["Blue or White"]) == {"blue", "white"}
    evidence = _color_evidence(["blue/white"], ["Blue or White"])
    assert evidence is not None and evidence.matched


@pytest.mark.parametrize(
    "text",
    [
        "vinyle bleu; ou blanc",
        "ou blanc; vinyle bleu",
        "either vinyle bleu/blanc",
        "either vinyle rouge/bleu",
        "vinyle rouge ou bleu",
        "vinyle bleu ou blanc?",
    ],
)
def test_localized_explicit_choice_cannot_be_erased_by_an_identifier_or_definite_field(text):
    row = listing(text, {"Color": ["Blue"], "Barcode": ["00012345678905"]})
    result = assess(row, variant("Blue"))
    assert result["status"] == "family_review" and not result["notify"]
    assert "color_claim_ambiguous" in result["verify"]


@pytest.mark.parametrize(
    "text",
    [
        "vinyle bleu avec pochette jaune ou blanc",
        "vinyle bleu ou blanc sleeve",
        "pochette de vinyle bleu ou blanc",
        "pas de vinyle blanc ou gris neuf",
        "vinyle bleu avec pochette jaune, pas blanc ou gris",
        "pas blanc ou gris; vinyle bleu",
        "vinyle bleu, pas blanc ou gris",
        "vinyle bleu; pas de vinyle blanc ou gris",
    ],
)
def test_localized_packaging_and_denied_choices_are_not_positive_uncertainty(text):
    row = listing(text, {"Barcode": ["00012345678905"]})
    result = assess(row, variant("Blue"))
    assert result["status"] == "possible_pressing" and result["notify"]
    assert "color_claim_ambiguous" not in result["verify"]


def test_definite_structured_color_keeps_its_existing_comparison_priority():
    result = assess(listing("red vinyl", {"Color": ["Blue"]}), variant("Blue"))
    assert result["status"] == "possible_pressing" and result["notify"]
    result = assess(listing("not blue", {"Color": ["Blue"]}), variant("Blue"))
    assert result["status"] == "conflicting" and not result["notify"]


def test_independent_definite_palette_and_identifier_remain_in_private_clues():
    row = listing("blue vinyl; white or red", {"Barcode": ["00012345678905"]})
    result = assess(row, variant("Blue"))
    assert result["status"] == "family_review" and not result["notify"]
    assert {"seller_color_claim", "seller_identifier_claim"} <= set(result["clues"])


@pytest.mark.parametrize("saved_verdict", ["mine", "other", "unsure"])
def test_choice_reassessment_preserves_owner_decision_and_first_prediction(
    repository, saved_verdict
):
    from sqlalchemy import insert, select

    from finder.watch_store import WatchStore, decisions, inbox, migrate, outbox, verdicts

    migrate(repository.engine)
    row = listing("blue or white")
    repository.upsert(row)
    watch = SavedWatch(release_id=1)
    store = WatchStore(repository.engine)
    watch_id = store.add(watch, now=NOW)
    previous = {
        "status": "possible_pressing",
        "policy": "synthetic-previous-policy",
        "notify": True,
    }
    store.finish(store.claim(now=NOW), [(row, previous)], now=NOW)
    identity = {
        "watch_id": watch_id,
        "marketplace": row.marketplace,
        "marketplace_item_id": row.marketplace_item_id,
        "verdict": saved_verdict,
        "tier": "possible_pressing",
        "decided_at": NOW.isoformat(),
    }
    original = {
        **identity,
        "purchased": True,
        "updated_at": NOW.isoformat(),
        "prediction": {"policy": previous["policy"], "evaluated_at": NOW.isoformat()},
        "legacy": None,
    }
    with repository.engine.begin() as conn:
        conn.execute(insert(verdicts).values(**identity))
        conn.execute(insert(decisions).values(**original))
    later = NOW + timedelta(minutes=31)
    row = row.model_copy(update={"last_observed_at": later, "details_observed_at": later})
    repository.upsert(row)
    review = assess_review(
        watch, row, variant(), now=later, alternatives=[], search_incomplete=False
    )
    assert review["status"] == "family_review" and not review["notify"]
    store.finish(store.claim(now=later), [(row, review)], now=later)
    with repository.engine.connect() as conn:
        assert dict(conn.execute(select(decisions)).mappings().one()) == original
        assert dict(conn.execute(select(verdicts)).mappings().one()) == identity
        current = conn.execute(select(inbox.c.data)).scalar_one()
        assert current["status"] == "family_review" and current["policy"] == review["policy"]
        assert not conn.execute(select(outbox).where(outbox.c.status == "pending")).all()


@pytest.mark.parametrize("field", FIELDS[1:])
@pytest.mark.parametrize(
    "title,structured",
    [
        ("red vinyl", "blue or white"),
        ("blue or white; red vinyl", "blue or white"),
        ("blue or white", "red vinyl"),
        ("blue or white", "blue or white; red vinyl"),
    ],
)
@pytest.mark.parametrize("color", ["Blue", "Blue/White"])
def test_ambiguous_only_source_cannot_hide_an_independent_definite_conflict(
    field, title, structured, color
):
    row = listing(title, {field: [structured]})
    result = assess(
        row,
        variant(color),
        maximum_subtotal=Decimal("100"),
        gamble_max=Decimal("50"),
        country="US",
        postal_code="00000",
    )
    assert result["status"] == "conflicting" and not result["notify"]
    assert "color_conflict" in result["verify"]


@pytest.mark.parametrize("field", FIELDS[1:])
def test_independent_definite_structured_palette_keeps_priority_beside_its_choice(field):
    row = listing("red vinyl", {field: ["blue vinyl; white or red"]})
    result = assess(
        row, variant("Blue"), gamble_max=Decimal("50"), country="US", postal_code="00000"
    )
    assert result["status"] == "family_review" and result["notify"]
    assert "color_claim_ambiguous" in result["verify"] and "color_conflict" not in result["verify"]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(
    "text,color",
    [
        ("vinyle bleu; white or red vinyl", "White/Red"),
        ("vinyle bleu; vinyle blanc ou gris", "White/Gray"),
        ("white or red vinyl; vinyle bleu", "White/Red"),
        ("vinyle blanc ou gris; vinyle bleu", "White/Gray"),
    ],
)
def test_independently_definite_french_color_still_conflicts_beside_a_choice(field, text, color):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == {"blue"} and denied == set()
    row = listing(text) if field is None else listing(specifics={field: [text]})
    result = assess(
        row,
        variant(color),
        maximum_subtotal=Decimal("100"),
        gamble_max=Decimal("50"),
        country="US",
        postal_code="00000",
    )
    assert result["status"] == "conflicting" and not result["notify"]
    assert "color_conflict" in result["verify"]


@pytest.mark.parametrize(
    "color,reason",
    [("Blue/White", "color_pair_incomplete"), ("Cloudburst", "color_not_comparable")],
)
def test_ambiguity_keeps_useful_explanation_for_independent_definite_color(color, reason):
    review = review_target_listing(listing("blue vinyl; white or red"), variant(color))
    assert review.status == "family_review"
    assert {reason, "color_claim_ambiguous"} <= set(review.verify)
    assert "color_not_claimed" not in review.verify


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("separator", [", ", "; ", ". ", "\n"])
@pytest.mark.parametrize("reverse", [False, True])
def test_split_mixed_ou_choice_withholds_both_branches_across_disc_context(
    field, separator, reverse
):
    first, second = ("ou white", "vinyle blue") if reverse else ("vinyle blue", "ou white")
    text = first + separator + second
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == denied == set()
    row = listing(text) if field is None else listing(specifics={field: [text]})
    for color in ("Blue", "White", "Blue/White", "Red", "Cloudburst"):
        result = assess(
            row, variant(color), tells=[Clue(kind="color", value="Blue", required=True)]
        )
        assert result["status"] == "family_review" and not result["notify"]
        assert result["signs"] == [] and "color_claim_ambiguous" in result["verify"]
    row = row.model_copy(
        update={
            "item_specifics": {
                **row.item_specifics,
                "Artist": ["Example Artist"],
                "Barcode": ["00012345678905"],
            }
        }
    )
    decision = decide_match(row, [variant()])
    assert (
        decision.outcome == "family_only" and "color_claim_ambiguous" in decision.missing_evidence
    )
    assert "color" not in decision.conflicts


@pytest.mark.parametrize("text", ["vinyle blue; ou white", "ou white; vinyle blue"])
def test_split_mixed_choice_keeps_independent_english_claim_and_its_conflict(text):
    text = "red vinyl; " + text
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == {"red"} and denied == set()
    result = assess(
        listing(text), variant("Blue"), gamble_max=Decimal("50"), country="US", postal_code="00000"
    )
    assert result["status"] == "conflicting" and not result["notify"]


@pytest.mark.parametrize(
    "text",
    [
        "vinyle blue; not white or red",
        "vinyle blue; pas blanc ou gris",
        "blue vinyl; pochette vinyle blanc; ou gris",
        "blue vinyl; pas de vinyle blanc; ou gris",
        "vinyle blue; white or red sleeve",
    ],
)
def test_mixed_continuation_requires_positive_disc_context_not_packaging_or_denial(text):
    result = assess(listing(text), variant("Blue"))
    assert result["status"] == "possible_pressing" and result["notify"]
    assert "color_claim_ambiguous" not in result["verify"]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(
    "text", ["vinyle blue, not red; ou white", "vinyle blue not red; ou white"]
)
def test_masking_a_split_choice_never_erases_its_independent_explicit_denial(field, text):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == set() and denied == {"red"}
    row = listing(text) if field is None else listing(specifics={field: [text]})
    result = assess(
        row, variant("Red"), gamble_max=Decimal("50"), country="US", postal_code="00000"
    )
    assert result["status"] == "conflicting" and not result["notify"]
    required = assess(
        row, variant("Cloudburst"), tells=[Clue(kind="color", value="Red", required=True)]
    )
    assert required["status"] == "conflicting" and "required_sign_conflict" in required["verify"]


def test_masking_a_split_choice_does_not_create_a_packaging_denial():
    claims, denied = _seller_color_claims(["vinyle blue not red sleeve; ou white"])
    assert _palette(claims) == denied == set()


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(
    "text,definite",
    [
        ("vinyle gris; vinyle blue, ou white", "gray"),
        ("vinyle blue, ou white; vinyle gris", "gray"),
        ("vinyle red; vinyle blue; ou white", "red"),
        ("vinyle blue; ou white; vinyle red", "red"),
    ],
)
def test_linked_continuation_keeps_other_supported_vinyle_claims(field, text, definite):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == {definite} and denied == set()
    row = listing(text) if field is None else listing(specifics={field: [text]})
    result = assess(row, gamble_max=Decimal("50"), country="US", postal_code="00000")
    assert result["status"] == "conflicting" and not result["notify"]
    assert "color_conflict" in result["verify"]
    row = row.model_copy(
        update={
            "item_specifics": {
                **row.item_specifics,
                "Artist": ["Example Artist"],
                "Barcode": ["00012345678905"],
            }
        }
    )
    decision = decide_match(row, [variant()])
    assert decision.outcome == "family_only"
    assert "color_claim_ambiguous" in decision.missing_evidence
    if field:
        assert "color" in decision.conflicts


@pytest.mark.parametrize("field", FIELDS[1:])
@pytest.mark.parametrize(
    "text", ["blue ou white", "blue, ou white", "blue; ou white", "ou white; blue"]
)
def test_typed_color_field_supplies_bounded_context_for_ou_choices(field, text):
    row = listing(specifics={field: [text]})
    original = row.model_dump(mode="json")
    seen = ListingText(row, variant())
    assert seen.palette == set() and seen.structured_palette == set()
    for color in ("Blue", "Blue/White", "Red", "Cloudburst"):
        result = assess(row, variant(color))
        assert result["status"] == "family_review" and not result["notify"]
        assert "color_claim_ambiguous" in result["verify"]
    row_with_id = row.model_copy(
        update={
            "item_specifics": {
                **row.item_specifics,
                "Artist": ["Example Artist"],
                "Barcode": ["00012345678905"],
            }
        }
    )
    decision = decide_match(row_with_id, [variant()])
    assert (
        decision.outcome == "family_only" and "color_claim_ambiguous" in decision.missing_evidence
    )
    assert "color" not in decision.conflicts
    assert row.model_dump(mode="json") == original


@pytest.mark.parametrize("field", FIELDS[1:])
@pytest.mark.parametrize(
    "text",
    [
        "blue or white sleeve",
        "blue ou white sleeve",
        "pochette blue ou white",
        "blue ou white pochette",
    ],
)
def test_typed_ou_choice_does_not_turn_recognized_packaging_into_disc_evidence(field, text):
    row = listing(specifics={field: [text], "Barcode": ["00012345678905"]})
    seen = ListingText(row, variant())
    assert seen.palette == set() and not seen.color_claim_ambiguous
    result = assess(row)
    assert result["status"] == "possible_pressing" and result["notify"]
    assert "seller_color_claim" not in result["clues"]


@pytest.mark.parametrize("field", FIELDS[1:])
def test_typed_context_does_not_translate_bare_french_colors_or_scan_other_fields(field):
    row = listing(
        "vinyl",
        {
            field: ["bleu", "blanc", "gris"],
            "Notes": ["blue ou white"],
            "Barcode": ["00012345678905"],
        },
    )
    seen = ListingText(row, variant())
    assert seen.palette == set() and not seen.color_claim_ambiguous
    result = assess(row)
    assert result["status"] == "possible_pressing" and "seller_color_claim" not in result["clues"]
    row = listing("vinyl", {field: ["not blue or white"], "Barcode": ["00012345678905"]})
    seen = ListingText(row, variant("Red"))
    assert seen.palette == set() and seen.denied_palette == {"blue", "white"}
    assert not seen.color_claim_ambiguous
    assert assess(row, variant("Red"))["status"] == "possible_pressing"


@pytest.mark.parametrize("field", FIELDS[1:])
@pytest.mark.parametrize("values", [["red", "blue", "ou white"], ["ou white", "blue", "red"]])
def test_typed_continuation_across_values_keeps_independent_definite_conflict(field, values):
    row = listing(specifics={field: values})
    original = row.model_dump(mode="json")
    seen = ListingText(row, variant())
    assert seen.palette == {"red"} and seen.color_claim_ambiguous
    result = assess(row, gamble_max=Decimal("50"), country="US", postal_code="00000")
    assert result["status"] == "conflicting" and not result["notify"]
    assert row.model_dump(mode="json") == original


@pytest.mark.parametrize("field", FIELDS[1:])
@pytest.mark.parametrize("choice", ["blue ou white", "bleu ou blanc"])
@pytest.mark.parametrize("reverse", [False, True])
def test_typed_self_contained_choice_does_not_erase_independent_french_color(
    field, choice, reverse
):
    parts = [choice, "vinyle gris"] if reverse else ["vinyle gris", choice]
    row = listing(specifics={field: ["; ".join(parts)]})
    seen = ListingText(row, variant())
    assert seen.palette == {"gray"} and seen.color_claim_ambiguous
    result = assess(row, gamble_max=Decimal("50"), country="US", postal_code="00000")
    assert result["status"] == "conflicting" and not result["notify"]


@pytest.mark.parametrize("field", FIELDS[1:])
@pytest.mark.parametrize("denial", ["not", "no", "without"])
@pytest.mark.parametrize(
    "colors,denied",
    [
        ("blue ou white", {"blue", "white"}),
        ("blue, ou white", {"blue", "white"}),
        ("blue, white ou red", {"blue", "white", "red"}),
        ("blue, white, ou red", {"blue", "white", "red"}),
        ("blue and/or white", {"blue", "white"}),
        ("blue, and/or white", {"blue", "white"}),
        ("blue, white and/or red", {"blue", "white", "red"}),
    ],
)
def test_supported_negative_choice_operators_deny_the_whole_group(field, denial, colors, denied):
    row = listing(specifics={field: [denial + " " + colors]})
    seen = ListingText(row, variant("White"))
    assert seen.palette == set() and seen.denied_palette == denied
    assert not seen.color_claim_ambiguous
    color_evidence = _color_evidence([denial + " " + colors], ["White"])
    assert color_evidence is not None and not color_evidence.matched
    result = assess(
        row, variant("White"), gamble_max=Decimal("50"), country="US", postal_code="00000"
    )
    assert result["status"] == "conflicting" and not result["notify"]
    required = assess(
        row, variant("Cloudburst"), tells=[Clue(kind="color", value="White", required=True)]
    )
    assert required["status"] == "conflicting" and "required_sign_conflict" in required["verify"]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(
    "text,definite",
    [
        ("vinyle gris; ou white, vinyle blue", "gray"),
        ("ou white, vinyle blue; vinyle gris", "gray"),
        ("red vinyl; ou white, vinyle blue", "red"),
        ("ou white, vinyle blue; red vinyl", "red"),
    ],
)
def test_comma_linked_choice_cannot_consume_a_separate_definite_clause(field, text, definite):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == {definite} and denied == set()
    row = listing(text) if field is None else listing(specifics={field: [text]})
    result = assess(row, gamble_max=Decimal("50"), country="US", postal_code="00000")
    assert result["status"] == "conflicting" and not result["notify"]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("color", ["White", "Gray"])
@pytest.mark.parametrize(
    "text",
    [
        "pas de vinyle bleu, blanc ou gris",
        "pas de vinyle bleu, blanc, ou gris",
        "vinyle pas bleu, blanc ou gris",
        "sans vinyle bleu, blanc ou gris",
    ],
)
def test_explicit_french_negative_list_retains_all_known_denials(field, color, text):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == set() and denied == {"blue", "white", "gray"}
    row = listing(text) if field is None else listing(specifics={field: [text]})
    seen = ListingText(row, variant(color))
    assert seen.denied_palette == denied and not seen.color_claim_ambiguous
    result = assess(
        row, variant(color), gamble_max=Decimal("50"), country="US", postal_code="00000"
    )
    assert result["status"] == "conflicting" and not result["notify"]
    assert "color_conflict" in result["verify"]


@pytest.mark.parametrize("field", FIELDS)
def test_separate_french_positive_clause_is_not_swallowed_by_a_negative_list(field):
    text = "pas de vinyle bleu, vinyle blanc"
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == {"white"} and denied == {"blue"}
    row = listing(text) if field is None else listing(specifics={field: [text]})
    result = assess(row, variant("White"))
    assert result["status"] == "possible_pressing" and result["notify"]
    assert "color_claim_ambiguous" not in result["verify"]


def test_french_negative_list_does_not_create_disc_denials_from_packaging():
    claims, denied = _seller_color_claims(["pochette pas de vinyle bleu, blanc ou gris"])
    assert _palette(claims) == denied == set()
