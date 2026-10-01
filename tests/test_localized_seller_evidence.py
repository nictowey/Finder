"""Synthetic seller claims; localized wording never verifies a particular pressing."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from finder.categories.target_review import review_target_listing, review_target_with_alternatives
from finder.categories.vinyl import from_listing
from finder.categories.vinyl_clues import Clue, ListingText, clue_found
from finder.domain import Listing, Variant
from finder.matching import _palette, _seller_color_claims, score_variant
from finder.watch_store import SavedWatch
from finder.watch_worker import assess_review

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)


def listing(text="vinyle", specifics=None):
    return Listing(
        marketplace="synthetic",
        marketplace_item_id="localized-evidence",
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


def variant(color="Blue", catno="SYN-123", id="1"):
    return Variant(
        catalog_source="discogs",
        catalog_variant_id=id,
        catalog_product_id="synthetic-family",
        artists=["Example Artist"],
        title="Example Album",
        labels=[{"name": "Example Label", "catno": catno}],
        formats=[{"name": "Vinyl", "descriptions": ["LP"], "text": color}],
        resource_url="https://www.discogs.com/release/" + id,
        observed_at=NOW,
    )


@pytest.mark.parametrize("key", ["Numéro de catalogue", "Katalognummer", "Codice catalogo"])
@pytest.mark.parametrize("catno, matched", [("SYN-123", True), ("SYN-999", False)])
def test_observed_catalog_field_aliases_keep_catalog_semantics(key, catno, matched):
    row = listing(specifics={key: [catno]})
    seller = from_listing(row)
    assert seller.catalog_numbers == [catno]
    assert seller.barcodes == []
    assert row.item_specifics == {key: [catno]}
    evidence = {item.field: item for item in score_variant(row, variant()).evidence}
    assert evidence["catalog_number"].matched is matched
    assert evidence["catalog_number"].listing_values == [catno]
    assert "barcode" not in evidence
    review = review_target_listing(row, variant())
    assert review.status == ("possible_pressing" if matched else "conflicting")
    assert "pressing_identifier_absent" not in review.verify


@pytest.mark.parametrize("key", ["Numéro de catalogue", "Katalognummer", "Codice catalogo"])
def test_numeric_catalog_number_never_becomes_barcode_or_country(key):
    digits = "00012345678905"
    row = listing("vinyle Europe " + digits, {key: [digits]})
    target = variant(catno="SYN-123").model_copy(update={"identifiers": {"Barcode": [digits]}})
    assert from_listing(row).barcodes == []
    assert from_listing(row).country is None
    fields = {item.field: item for item in score_variant(row, target).evidence}
    assert not fields["catalog_number"].matched
    assert "barcode" not in fields and "country" not in fields
    assert review_target_listing(row, target).verify == ["catalog_number"]


@pytest.mark.parametrize(
    "key", ["Numéro", "Catalogue", "Katalog", "Codice", "Catalog Number Notes"]
)
def test_unrecognized_fields_and_title_digits_are_not_structured_identifiers(key):
    row = listing("vinyle 00012345678905", {key: ["00012345678905"]})
    assert from_listing(row).catalog_numbers == from_listing(row).barcodes == []


def test_shared_localized_catalog_number_preserves_competing_pressings():
    row = listing(specifics={"Numéro de catalogue": ["SYN-123"]})
    result = review_target_with_alternatives(row, variant(), [variant(id="2")])
    assert result.status == "family_review" and result.alternatives_not_ruled_out == 1
    assert "shared_pressing_evidence" in result.verify


@pytest.mark.parametrize(
    "artist, expected", [("Example Artist", "family_review"), ("Other Artist", "conflicting")]
)
@pytest.mark.parametrize("key", ["Artiste", "Künstler", "Interpret", "Artista"])
def test_observed_artist_fields_are_identity_evidence(artist, expected, key):
    row = listing(specifics={key: [artist]})
    assert from_listing(row).artists == [artist]
    assert review_target_listing(row, variant()).status == expected


@pytest.mark.parametrize(
    "title, expected", [("Example Album", "family_review"), ("Other Album", "conflicting")]
)
@pytest.mark.parametrize("key", ["Titre de la version", "Musiktitel", "Titolo della pubblicazione"])
def test_observed_release_title_fields_support_or_contradict_family(title, expected, key):
    row = listing(specifics={key: [title]})
    assert review_target_listing(row, variant()).status == expected


def test_french_release_title_can_establish_a_weak_album_title():
    target = variant().model_copy(update={"title": "Rare"})
    row = listing(specifics={"Titre de la version": ["Rare"]}).model_copy(
        update={"title": "Example Artist Rare LP"}
    )
    assert review_target_listing(row, target).status == "family_review"


@pytest.mark.parametrize(
    "text, positive, denied",
    [
        ("ALBUM VINYLE BLEU", {"blue"}, set()),
        ("vinyle blanc", {"white"}, set()),
        ("vinyle gris", {"gray"}, set()),
        ("vinyle jaune", {"yellow"}, set()),
        ("vinyles bleu et blanc", {"blue", "white"}, set()),
        ("vinyle bleu/blanc", {"blue", "white"}, set()),
        ("pas de vinyle bleu", set(), {"blue"}),
        ("vinyle pas bleu", set(), {"blue"}),
        ("vinyle non bleu", set(), {"blue"}),
        ("vinyle n'est pas bleu", set(), {"blue"}),
        ("sans vinyle bleu", set(), {"blue"}),
        ("not vinyle bleu", set(), {"blue"}),
        ("ni vinyle bleu ni vinyle blanc", set(), {"blue", "white"}),
        ("pas de vinyle bleu mais vinyle blanc", {"white"}, {"blue"}),
        ("vinyle bleu, pas blanc ou gris", {"blue"}, {"white", "gray"}),
        ("vinyle bleu avec pochette jaune", {"blue"}, set()),
        ("vinyle bleu, pochette blanche", {"blue"}, set()),
        ("vinyle bleu et vinyle blanc", {"blue", "white"}, set()),
        ("pochette bleue, vinyle blanc", {"white"}, set()),
    ],
)
def test_explicit_french_vinyl_color_claims_have_bounded_scope(text, positive, denied):
    claims, denials = _seller_color_claims([text])
    assert _palette(claims) == positive
    assert denials == denied
    assert _palette([text]) == set()  # Catalog palettes are not translated.


@pytest.mark.parametrize(
    "text",
    [
        "bleu",
        "blanc",
        "gris",
        "jaune",
        "LP bleu",
        "disque bleu",
        "vinyle bleuet",
        "vinyle bleu ou blanc",
        "vinyle bleu ou vinyle blanc",
        "vinyle bleu or blanc",
        "vinyle bleu ou rouge",
        "vinyle bleu et rouge",
        "vinyle bleu blanc",
        "vinyle bleu et 2 vinyles blanc",
        "vinyle bleu-blanc",
        "vinyle bleu, blanc",
        "vinyle bleu?",
        "pochette de vinyle bleu",
        "étiquette vinyle bleu",
        "cover vinyle bleu",
        "vinyle bleu sleeve",
        "pochette bleu",
        "étiquette blanc",
        "couverture gris",
    ],
)
def test_unqualified_ambiguous_and_packaging_french_colors_are_not_disc_claims(text):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == set()
    assert denied == set()


def test_french_artist_album_and_alias_names_are_removed_before_color_parsing():
    claims, denied = _seller_color_claims(
        ["Vinyle Bleu - Pas De Vinyle Blanc - Vinyle Jaune II LP"],
        ignore=("Vinyle Bleu", "Pas De Vinyle Blanc", "Vinyle Jaune II"),
    )
    assert _palette(claims) == denied == set()


@pytest.mark.parametrize(
    "text, color", [("vinyle bleu", "Blue"), ("vinyle blanc", "White"), ("vinyle gris", "Gray")]
)
def test_french_claims_match_or_conflict_only_with_comparable_catalog_palettes(text, color):
    row = listing(text)
    assert review_target_listing(row, variant(color)).status == "possible_pressing"
    assert review_target_listing(row, variant("Clear")).status == "conflicting"
    assert review_target_listing(row, variant("Mustard")).status == "family_review"
    seen = ListingText(row, variant(color))
    assert clue_found(Clue(kind="color", value=color), seen)
    assert not clue_found(Clue(kind="color", value=color), seen, common_version=True)


def test_partial_french_palette_is_incomplete_and_shared_palette_stays_ambiguous():
    row = listing("vinyle bleu")
    partial = review_target_listing(row, variant("Blue/White"))
    assert partial.status == "family_review" and "color_pair_incomplete" in partial.verify
    shared = review_target_with_alternatives(row, variant(), [variant(id="2")])
    assert shared.status == "family_review" and shared.alternatives_not_ruled_out == 1


def test_yellow_translation_never_establishes_named_shade_country_or_unique_pressing():
    for text in ("vinyle jaune", "yellow vinyl"):
        row = listing(text)
        assert review_target_listing(row, variant("Mustard")).status == "family_review"
        target = variant("Mustard Yellow").model_copy(update={"country": "US"})
        alternate = variant("Yellow", id="2").model_copy(update={"country": "Europe"})
        review = review_target_with_alternatives(row, target, [alternate], search_incomplete=False)
        assert review.status == "family_review" and review.alternatives_not_ruled_out == 1
        assert from_listing(row).country is None


@pytest.mark.parametrize("medium", ["CD", "cassette", "DVD"])
def test_french_disc_color_cannot_override_explicit_nonvinyl_medium(medium):
    assert review_target_listing(listing("vinyle bleu " + medium), variant()).verify == [
        "non_vinyl_claim"
    ]


@pytest.mark.parametrize(
    "text", ["pas de vinyle bleu", "vinyle n'est pas bleu", "vinyle blanc, pas bleu"]
)
def test_french_denial_blocks_required_color_and_both_alert_price_tiers(text):
    watch = SavedWatch(
        release_id=1,
        maximum_subtotal=Decimal("100"),
        gamble_max=Decimal("50"),
        country="US",
        postal_code="00000",
        tells=[Clue(kind="color", value="Blue", required=True)],
    )
    review = assess_review(
        watch, listing(text), variant(), now=NOW, alternatives=[], search_incomplete=False
    )
    assert review["status"] == "conflicting"
    assert review["signs"] == [] and review["missing_signs"] == ["color: Blue"]
    assert not review["notify"]


@pytest.mark.parametrize(
    "artist_key,title_key",
    [
        ("Artiste", "Titre de la version"),
        ("Künstler", "Musiktitel"),
        ("Interpret", "Musiktitel"),
        ("Artista", "Titolo della pubblicazione"),
    ],
)
@pytest.mark.parametrize("conflict", ["artist", "title"])
def test_localized_identity_conflicts_override_matching_catalog_number(
    artist_key, title_key, conflict
):
    row = listing(
        specifics={
            "Numéro de catalogue": ["SYN-123"],
            artist_key: ["Other Artist" if conflict == "artist" else "Example Artist"],
            title_key: ["Other Album" if conflict == "title" else "Example Album"],
        }
    )
    assert review_target_listing(row, variant()).status == "conflicting"


def test_recognized_seller_color_is_reported_as_incomparable_not_absent():
    for text in ("vinyle bleu", "blue vinyl"):
        result = review_target_listing(listing(text), variant("Mustard"))
        assert result.status == "family_review"
        assert "color_not_comparable" in result.verify
        assert "color_not_claimed" not in result.verify


@pytest.mark.parametrize("key", ["Titre de la version", "Musiktitel", "Titolo della pubblicazione"])
def test_localized_release_title_can_name_a_more_specific_competing_album(key):
    target = variant()
    other = variant(id="2").model_copy(update={"title": "Example Album Limited"})
    row = listing(specifics={key: ["Example Album Limited"]})
    result = review_target_with_alternatives(row, target, [other])
    assert result.status == "conflicting" and "competing_album_title_claim" in result.verify


@pytest.mark.parametrize("saved_verdict", ["mine", "other", "unsure"])
def test_localized_reevaluation_preserves_owner_label_and_first_prediction(
    repository, saved_verdict
):
    from datetime import timedelta

    from sqlalchemy import insert, select

    from finder.watch_store import WatchStore, decisions, inbox, migrate, outbox, verdicts

    migrate(repository.engine)
    row = listing("vinyle bleu")
    repository.upsert(row)
    watch = SavedWatch(release_id=1)
    store = WatchStore(repository.engine)
    watch_id = store.add(watch, now=NOW)
    previous = {"status": "family_review", "policy": "synthetic-previous-policy", "notify": False}
    store.finish(store.claim(now=NOW), [(row, previous)], now=NOW)
    identity = {
        "watch_id": watch_id,
        "marketplace": row.marketplace,
        "marketplace_item_id": row.marketplace_item_id,
        "verdict": saved_verdict,
        "tier": "family_review",
        "decided_at": NOW.isoformat(),
    }
    original = {
        **identity,
        "purchased": False,
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
    assert review["status"] == "possible_pressing" and review["notify"]
    store.finish(store.claim(now=later), [(row, review)], now=later)
    with repository.engine.connect() as conn:
        assert dict(conn.execute(select(decisions)).mappings().one()) == original
        assert dict(conn.execute(select(verdicts)).mappings().one()) == identity
        current = conn.execute(select(inbox.c.data)).scalar_one()
        assert current["status"] == "possible_pressing" and current["policy"] == review["policy"]
        assert not conn.execute(select(outbox)).all()


@pytest.mark.parametrize("text", ["vinyle bleu, ou blanc", "vinyle bleu, ou vinyle blanc"])
@pytest.mark.parametrize("structured", [False, True])
def test_comma_french_alternatives_cannot_promote_or_satisfy_a_required_color(text, structured):
    row = listing(specifics={"Color": [text]}) if structured else listing(text)
    watch = SavedWatch(
        release_id=1,
        maximum_subtotal=Decimal("100"),
        country="US",
        postal_code="00000",
        tells=[Clue(kind="color", value="Blue", required=True)],
    )
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == denied == set()
    assert review_target_listing(row, variant()).status == "family_review"
    review = assess_review(watch, row, variant(), now=NOW, alternatives=[], search_incomplete=False)
    assert review["status"] == "family_review"
    assert review["signs"] == [] and review["missing_signs"] == ["color: Blue"]
    assert not review["notify"]


@pytest.mark.parametrize("text", ["pas de vinyle bleu et rouge", "pas de vinyle bleu, blanc"])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("required", [False, True])
def test_incomplete_french_lists_preserve_explicit_denials_and_block_gamble_alerts(
    text, structured, required
):
    row = listing(specifics={"Color": [text]}) if structured else listing(text)
    watch = SavedWatch(
        release_id=1,
        maximum_subtotal=Decimal("100"),
        gamble_max=Decimal("50"),
        country="US",
        postal_code="00000",
        tells=[Clue(kind="color", value="Blue", required=True)] if required else [],
    )
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == set() and denied == {"blue"}
    assert review_target_listing(row, variant()).status == "conflicting"
    review = assess_review(watch, row, variant(), now=NOW, alternatives=[], search_incomplete=False)
    assert review["status"] == "conflicting" and not review["notify"]
    assert review.get("signs", []) == []


def test_mixed_language_comma_alternative_cannot_supply_either_branch():
    text = "vinyle bleu, or white"
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == denied == set()
    target = variant("Blue/White")
    watch = SavedWatch(release_id=1, tells=[Clue(kind="color", value="Blue/White", required=True)])
    review = assess_review(
        watch, listing(text), target, now=NOW, alternatives=[], search_incomplete=False
    )
    assert review["status"] == "family_review" and not review["notify"]
    assert review["signs"] == [] and review["missing_signs"] == ["color: Blue/White"]


def test_packaging_clause_ends_inherited_french_disc_context():
    text = "vinyle bleu et blanc, label gris, pas blanc"
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == {"blue", "white"} and denied == set()
    target = variant("Blue/White")
    watch = SavedWatch(release_id=1, tells=[Clue(kind="color", value="Blue/White", required=True)])
    review = assess_review(
        watch, listing(text), target, now=NOW, alternatives=[], search_incomplete=False
    )
    assert review["status"] == "possible_pressing" and review["notify"]
    assert review["signs"] == ["color: Blue/White"] and review["missing_signs"] == []


@pytest.mark.parametrize(
    "localized", ["Titre de la version", "Musiktitel", "Titolo della pubblicazione"]
)
@pytest.mark.parametrize(
    "english, translated", [("Other Album", "Example Album"), ("Example Album", "Other Album")]
)
def test_conflicting_title_field_cannot_be_hidden_by_another_matching_alias(
    localized, english, translated
):
    row = listing(
        specifics={
            "Release Title": [english],
            localized: [translated],
            "Numéro de catalogue": ["SYN-123"],
        }
    )
    result = review_target_listing(row, variant())
    assert result.status == "conflicting" and result.verify == ["release_title_conflict"]
    review = assess_review(
        SavedWatch(release_id=1), row, variant(), now=NOW, alternatives=[], search_incomplete=False
    )
    assert review["status"] == "conflicting" and not review["notify"]


def test_multiple_structured_title_claims_can_use_validated_catalog_spellings():
    target = variant().model_copy(update={"title": "Example Album 2"})
    other = variant(id="2").model_copy(update={"title": "Example Album II"})
    row = listing(
        specifics={
            "Release Title": ["Example Album 2"],
            "Titre de la version": ["Example Album II"],
        }
    ).model_copy(update={"title": "Example Artist Example Album II vinyle"})
    review = review_target_with_alternatives(row, target, [other])
    assert review.status == "family_review" and "release_title_conflict" not in review.verify


@pytest.mark.parametrize("localized", ["Numéro de catalogue", "Katalognummer", "Codice catalogo"])
@pytest.mark.parametrize("english, translated", [("SYN-999", "SYN-123"), ("SYN-123", "SYN-999")])
def test_conflicting_catalog_field_cannot_be_hidden_by_a_matching_localized_field(
    localized, english, translated
):
    row = listing(specifics={"Catalog Number": [english], localized: [translated]})
    evidence = next(
        item for item in score_variant(row, variant()).evidence if item.field == "catalog_number"
    )
    assert not evidence.matched
    assert review_target_listing(row, variant()).verify == ["catalog_number"]
    review = assess_review(
        SavedWatch(release_id=1), row, variant(), now=NOW, alternatives=[], search_incomplete=False
    )
    assert review["status"] == "conflicting" and not review["notify"]


@pytest.mark.parametrize(
    "specifics",
    [
        {"Catalog Number": ["SYN-999", "SYN-123"]},
        {"Catalog Number": ["SYN-999", "SYN-123"], "Numéro de catalogue": ["SYN-888", "SYN-123"]},
        {"Catalog Number": ["SYN-123"], "Numéro de catalogue": [""]},
    ],
)
def test_catalog_field_groups_preserve_multivalue_claim_semantics(specifics):
    row = listing(specifics=specifics)
    assert next(
        item for item in score_variant(row, variant()).evidence if item.field == "catalog_number"
    ).matched
    assert review_target_listing(row, variant()).status == "possible_pressing"


def test_separate_catalog_fields_may_match_distinct_valid_target_numbers():
    target = variant().model_copy(
        update={
            "labels": [
                {"name": "Example Label", "catno": "SYN-123"},
                {"name": "Second Label", "catno": "SYN-999"},
            ]
        }
    )
    row = listing(specifics={"Catalog Number": ["SYN-123"], "Numéro de catalogue": ["SYN-999"]})
    assert next(
        item for item in score_variant(row, target).evidence if item.field == "catalog_number"
    ).matched
    assert review_target_listing(row, target).status == "possible_pressing"


@pytest.mark.parametrize(
    "text", ["not only vinyle bleu but vinyle blanc", "pas seulement vinyle bleu mais vinyle blanc"]
)
def test_not_only_scope_never_becomes_a_single_french_disc_color(text):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == denied == set()
    target = variant("White")
    review = assess_review(
        SavedWatch(release_id=1),
        listing(text),
        target,
        now=NOW,
        alternatives=[],
        search_incomplete=False,
    )
    assert review["status"] == "family_review" and not review["notify"]


def test_packaging_after_disc_phrase_ends_inherited_context_in_the_same_clause():
    text = "vinyle bleu avec pochette blanche, pas blanc"
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == {"blue"} and denied == set()
    review = review_target_listing(listing(text), variant("Blue/White"))
    assert review.status == "family_review" and "color_pair_incomplete" in review.verify


@pytest.mark.parametrize("medium", ["CD", "cassette", "DVD"])
def test_observed_italian_format_field_blocks_nonvinyl_catalog_matches(medium):
    row = listing(specifics={"Codice catalogo": ["SYN-123"], "Formato": [medium]})
    assert from_listing(row).format_descriptions == [medium]
    assert review_target_listing(row, variant()).verify == ["non_vinyl_claim"]
    watch = SavedWatch(
        release_id=1,
        maximum_subtotal=Decimal("100"),
        gamble_max=Decimal("50"),
        country="US",
        postal_code="00000",
    )
    review = assess_review(watch, row, variant(), now=NOW, alternatives=[], search_incomplete=False)
    assert review["status"] == "conflicting" and not review["notify"]


def test_italian_format_alias_preserves_unknown_values_without_translating_them():
    row = listing(specifics={"Formato": ["Disco"]})
    assert from_listing(row).format_descriptions == ["Disco"]
    assert row.item_specifics == {"Formato": ["Disco"]}
    assert review_target_listing(row, variant()).status == "family_review"


@pytest.mark.parametrize(
    "text",
    [
        "vinyle bleu et vinyle rouge",
        "vinyle bleu and vinyle rouge",
        "vinyle bleu et vinyles rouge",
        "vinyle bleu et vinyle noir",
        "vinyle bleu et vinyle moutarde",
        "vinyle bleu et vinyle blanc et vinyle rouge",
        "vinyle bleu et vinyle blanc et rouge",
    ],
)
@pytest.mark.parametrize("structured", [False, True])
def test_repeated_vinyl_noun_with_unsupported_color_cannot_supply_a_partial_positive_palette(
    text, structured
):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == denied == set()
    row = listing(specifics={"Color": [text]}) if structured else listing(text)
    target = variant("Blue")
    assert review_target_listing(row, target).status == "family_review"
    watch = SavedWatch(release_id=1, tells=[Clue(kind="color", value="Blue", required=True)])
    result = assess_review(watch, row, target, now=NOW, alternatives=[], search_incomplete=False)
    assert result["status"] == "family_review" and not result["notify"]
    assert result["signs"] == [] and result["missing_signs"] == ["color: Blue"]


@pytest.mark.parametrize(
    "text", ["pas de vinyle bleu ni vinyle rouge", "not vinyle bleu nor vinyle rouge"]
)
def test_unsupported_repeated_noun_preserves_an_explicit_supported_denial(text):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == set() and denied == {"blue"}
    assert review_target_listing(listing(text), variant()).status == "conflicting"


@pytest.mark.parametrize(
    "separator", [" ", ", ", "; ", ". ", "\n", " mais ", " but ", " plus ", ": ", " (", ") "]
)
@pytest.mark.parametrize("reverse", [False, True])
def test_an_unsupported_disc_phrase_anywhere_prevents_a_partial_french_palette(separator, reverse):
    first, second = ("vinyle rouge", "vinyle bleu") if reverse else ("vinyle bleu", "vinyle rouge")
    text = first + separator + second
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == denied == set()
    review = assess_review(
        SavedWatch(release_id=1),
        listing(text),
        variant(),
        now=NOW,
        alternatives=[],
        search_incomplete=False,
    )
    assert review["status"] == "family_review" and not review["notify"]


def test_unsupported_vinyl_packaging_does_not_invalidate_an_explicit_disc_claim():
    text = "vinyle bleu; pochette de vinyle rouge"
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == {"blue"} and denied == set()
    assert review_target_listing(listing(text), variant()).status == "possible_pressing"


@pytest.mark.parametrize(
    "text", ["vinyle bleu et vinyle rouge, pas bleu", "vinyle bleu and vinyle inconnu, pas bleu"]
)
def test_incomplete_disc_phrase_retains_context_for_an_explicit_supported_denial(text):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == set() and denied == {"blue"}
    watch = SavedWatch(
        release_id=1,
        maximum_subtotal=Decimal("100"),
        gamble_max=Decimal("50"),
        country="US",
        postal_code="00000",
    )
    review = assess_review(
        watch, listing(text), variant(), now=NOW, alternatives=[], search_incomplete=False
    )
    assert review["status"] == "conflicting" and not review["notify"]


@pytest.mark.parametrize(
    "text, expected",
    [("blue vinyl; vinyle rouge", {"blue"}), ("pink vinyl; vinyle bleu et vinyle rouge", {"pink"})],
)
def test_unsupported_french_disc_phrases_preserve_existing_english_evidence(text, expected):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == expected and denied == set()


@pytest.mark.parametrize("separator", ["; ", ". ", "!\n", "\n", ".\n"])
@pytest.mark.parametrize("uncertain", ["ou blanc", "vinyle blanc ou gris", "vinyle blanc et rouge"])
@pytest.mark.parametrize("reverse", [False, True])
def test_independent_french_choice_preserves_definite_claim_but_continuation_abstains(
    separator, uncertain, reverse
):
    first, second = (uncertain, "vinyle bleu") if reverse else ("vinyle bleu", uncertain)
    text = first + separator + second
    claims, denied = _seller_color_claims([text])
    # An explicit new vinyle clause is its own unresolved choice. A bare 'ou blanc'
    # continues the earlier color, and unsupported non-choice grammar still abstains.
    independent_choice = uncertain == "vinyle blanc ou gris"
    assert _palette(claims) == ({"blue"} if independent_choice else set())
    assert denied == set()
    watch = SavedWatch(release_id=1, tells=[Clue(kind="color", value="Blue", required=True)])
    review = assess_review(
        watch, listing(text), variant(), now=NOW, alternatives=[], search_incomplete=False
    )
    assert review["status"] == "family_review" and not review["notify"]
    assert review["signs"] == (["color: Blue"] if independent_choice else [])
    assert review["missing_signs"] == ([] if independent_choice else ["color: Blue"])
    if independent_choice:
        assert "color_claim_ambiguous" in review["verify"]


@pytest.mark.parametrize(
    "text", ["vinyle bleu, pas blanc ou gris", "vinyle bleu; pas de vinyle blanc ou gris"]
)
def test_negative_ou_clauses_do_not_become_positive_alternatives(text):
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == {"blue"} and denied == {"white", "gray"}
    assert review_target_listing(listing(text), variant()).status == "possible_pressing"


def test_value_wide_positive_abstention_preserves_denials_and_english_claims():
    text = "pink vinyl; pas de vinyle bleu. vinyle blanc ou gris"
    claims, denied = _seller_color_claims([text])
    assert _palette(claims) == {"pink"} and denied == {"blue"}
    watch = SavedWatch(
        release_id=1,
        maximum_subtotal=Decimal("100"),
        gamble_max=Decimal("50"),
        country="US",
        postal_code="00000",
    )
    review = assess_review(
        watch, listing(text), variant(), now=NOW, alternatives=[], search_incomplete=False
    )
    assert review["status"] == "conflicting" and not review["notify"]
