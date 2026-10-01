"""Synthetic missing identifiers must neither prove nor contradict a pressing."""

import runpy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from finder.adapters.discogs.adapter import DiscogsCatalogProvider
from finder.categories.target_review import review_target_listing, review_target_with_alternatives
from finder.categories.vinyl import catalog_number_claims, from_listing, from_variant
from finder.categories.vinyl_clues import Clue, ListingText, clue_found, suggest_clues
from finder.discovery_worker import watch_queries
from finder.domain import Listing, Variant
from finder.matching import decide_match, score_variant
from finder.watch_store import SavedWatch
from finder.watch_worker import assess_review

NOW = datetime(2026, 10, 1, tzinfo=UTC)
MISSING = ["None", "N/A", "Does not apply", " \t NoNe \n", "N / A", " Does\t NOT  apply "]
REAL = ["NA", "NONE-001", "N/A-001", "N-A-001", "DNA-01", "0", "000", "123"]


def _variant(numbers=(), barcodes=(), *, identity="target"):
    return Variant(
        catalog_source="synthetic",
        catalog_variant_id=identity,
        catalog_product_id="synthetic-family",
        artists=["Synthetic Artist"],
        title="Synthetic Album",
        labels=[
            {"name": f"Synthetic Label {i}", "catno": value} for i, value in enumerate(numbers)
        ],
        identifiers={"Barcode": list(barcodes)},
        formats=[{"name": "Vinyl", "descriptions": ["LP"]}],
        observed_at=NOW,
    )


def _listing(specifics=None, *, suffix=""):
    return Listing(
        marketplace="synthetic",
        marketplace_item_id="synthetic-item",
        title="Synthetic Artist Synthetic Album LP " + suffix,
        item_specifics={"Artist": ["Synthetic Artist"], "Format": ["Vinyl"], **(specifics or {})},
        first_observed_at=NOW,
        last_observed_at=NOW,
        details_observed_at=NOW,
        current_price=Decimal("20"),
        shipping_cost=Decimal("0"),
        price_kind="fixed_price",
        currency="USD",
        shipping_currency="USD",
        source_metadata={"delivery_country": "US", "delivery_postal_code": "00000"},
    )


def _assessments(listing, target, alternatives, **clues):
    watches = {
        "default": SavedWatch(release_id=1, **clues),
        "main": SavedWatch(
            release_id=1, maximum_subtotal=Decimal("30"), country="US", postal_code="00000", **clues
        ),
        "low_gamble": SavedWatch(
            release_id=1,
            maximum_subtotal=Decimal("30"),
            gamble_max=Decimal("10"),
            country="US",
            postal_code="00000",
            **clues,
        ),
        "gamble": SavedWatch(
            release_id=1,
            maximum_subtotal=Decimal("30"),
            gamble_max=Decimal("25"),
            country="US",
            postal_code="00000",
            **clues,
        ),
        "strict": SavedWatch(
            release_id=1,
            maximum_subtotal=Decimal("30"),
            gamble_max=Decimal("25"),
            country="US",
            postal_code="00000",
            alert_mode="strict",
            **clues,
        ),
    }
    result = {}
    for name, watch in watches.items():
        saved = watch.model_dump()
        result[name] = assess_review(
            watch, listing, target, now=NOW, alternatives=alternatives, search_incomplete=False
        )
        assert watch.model_dump() == saved
    return result


def _assert_unclear_alerts(listing, target, alternatives, **clues):
    rows = _assessments(listing, target, alternatives, **clues)
    assert {row["status"] for row in rows.values()} == {"family_review"}
    assert {name: row["notify"] for name, row in rows.items()} == {
        "default": False,
        "main": False,
        "low_gamble": False,
        "gamble": True,
        "strict": False,
    }
    assert rows["gamble"]["alert_budget"] == "within_ceiling"
    assert rows["low_gamble"]["alert_budget"] == "over_ceiling"
    return rows


@pytest.mark.parametrize("missing", MISSING)
def test_identifier_extraction_drops_only_whole_sentinels_and_preserves_sources(missing):
    listing = _listing(
        {
            "Catalog Number": [missing, *REAL],
            "Numéro de catalogue": [missing],
            "UPC": [missing, *REAL],
            "EAN": [missing],
            "Barcode": [missing],
            "Record Label": [missing],
            "Matrix / Runout": [missing],
            "Features": [missing],
        }
    )
    target = _variant([missing, *REAL], [missing, *REAL]).model_copy(
        update={
            "identifiers": {"Barcode": [missing, *REAL], "Matrix / Runout": [missing]},
        }
    )
    original_listing, original_target = listing.model_dump(), target.model_dump()
    seller, catalog = from_listing(listing), from_variant(target)
    assert seller.catalog_numbers == catalog.catalog_numbers == REAL
    assert seller.barcodes == catalog.barcodes == REAL
    assert catalog_number_claims(listing) == [REAL]
    assert seller.labels == seller.editions == seller.matrix_runouts == [missing.strip()]
    assert catalog.matrix_runouts == [missing.strip()]
    assert catalog.labels == [f"Synthetic Label {i}" for i in range(len(REAL) + 1)]
    assert listing.model_dump() == original_listing
    assert target.model_dump() == original_target


@pytest.mark.parametrize("missing", MISSING)
@pytest.mark.parametrize("kind", ["catalog_number", "barcode"])
@pytest.mark.parametrize("side", ["seller", "catalog", "both"])
def test_missing_identifier_has_no_score_conflict_or_pressing_claim(missing, kind, side):
    field, real = (
        ("Catalog Number", "SYN-123") if kind == "catalog_number" else ("UPC", "0123456789012")
    )
    seller_values = [missing] if side != "catalog" else [real]
    target_values = (
        [real, missing] if side == "both" else ([missing] if side == "catalog" else [real])
    )
    target = (
        _variant(target_values) if kind == "catalog_number" else _variant(barcodes=target_values)
    )
    listing = _listing({field: seller_values})
    candidate = score_variant(listing, target, NOW)
    assert candidate.score == 40 and candidate.status == "candidate"
    assert not any(item.field == kind for item in candidate.evidence)
    decision = decide_match(listing, [target])
    assert decision.outcome == "family_only"
    assert not decision.conflicts
    assert "pressing_identifier" in decision.missing_evidence
    assert not any(item.field == kind for item in decision.evidence)
    assert review_target_listing(listing, target).status == "family_review"
    _assert_unclear_alerts(listing, target, [])
    if side == "both":
        sibling = (
            _variant(["SYN-999"], identity="sibling")
            if kind == "catalog_number"
            else _variant(barcodes=["9876543210123"], identity="sibling")
        )
        review = review_target_with_alternatives(
            listing, target, [sibling], search_incomplete=False
        )
        assert review.alternatives_not_ruled_out == 1
        assert decide_match(listing, [target, sibling]).outcome == "family_only"
        _assert_unclear_alerts(listing, target, [sibling])


@pytest.mark.parametrize("missing", MISSING)
@pytest.mark.parametrize("kind", ["catalog_number", "barcode"])
def test_missing_catalog_alternative_stays_unresolved(missing, kind):
    if kind == "catalog_number":
        listing, target, sibling = (
            _listing({"Catalog Number": ["SYN-123"]}),
            _variant(["SYN-123"]),
            _variant([missing], identity="sibling"),
        )
    else:
        listing, target, sibling = (
            _listing({"UPC": ["0123456789012"]}),
            _variant(barcodes=["0123456789012"]),
            _variant(barcodes=[missing], identity="sibling"),
        )
    review = review_target_with_alternatives(listing, target, [sibling], search_incomplete=False)
    assert review.status == "family_review"
    assert review.alternatives_checked == review.alternatives_not_ruled_out == 1
    assert "shared_pressing_evidence" in review.verify
    _assert_unclear_alerts(listing, target, [sibling])


@pytest.mark.parametrize(
    "specifics,kind",
    [
        ({"Catalog Number": ["SYN-999", "None"]}, "catalog_number"),
        ({"Catalog Number": ["None"], "Numéro de catalogue": ["SYN-999"]}, "catalog_number"),
        (
            {"Catalog Number": ["SYN-123"], "Numéro de catalogue": ["SYN-999", "None"]},
            "catalog_number",
        ),
        ({"UPC": ["9876543210123", "None"]}, "barcode"),
        ({"UPC": ["None"], "EAN": ["9876543210123"]}, "barcode"),
    ],
)
def test_placeholder_cannot_hide_real_wrong_identifier(specifics, kind):
    target = _variant(["SYN-123", "none"], ["0123456789012", "none"])
    listing = _listing(specifics)
    candidate = score_variant(listing, target, NOW)
    evidence = next(item for item in candidate.evidence if item.field == kind)
    assert not evidence.matched
    assert "none" not in [
        value.casefold() for value in [*evidence.listing_values, *evidence.variant_values]
    ]
    if kind == "barcode":
        assert candidate.status == "rejected" and candidate.score <= 20
    decision = decide_match(listing, [target])
    assert kind in decision.conflicts and decision.outcome != "probable_variant"
    rows = _assessments(listing, target, [])
    assert all(row["status"] == "conflicting" and not row["notify"] for row in rows.values())


@pytest.mark.parametrize(
    "specifics,numbers",
    [
        ({"Catalog Number": ["SYN-123"], "Numéro de catalogue": ["None"]}, ["SYN-123", "none"]),
        (
            {"Catalog Number": ["None", "SYN-123"], "Numéro de catalogue": ["SYN-456", "N/A"]},
            ["SYN-123", "SYN-456", "none"],
        ),
        ({"Catalog Number": ["SYN-999", "SYN-123", "None"]}, ["SYN-123", "none"]),
    ],
)
def test_valid_multivalue_and_multilabel_claims_keep_matching(specifics, numbers):
    listing, target = _listing(specifics), _variant(numbers)
    assert score_variant(listing, target, NOW).score == 80
    assert decide_match(listing, [target]).outcome == "probable_variant"
    assert all(
        row["status"] == "possible_pressing" and row["notify"]
        for row in _assessments(listing, target, []).values()
    )
    sibling = _variant(numbers, identity="sibling")
    _assert_unclear_alerts(listing, target, [sibling])


@pytest.mark.parametrize(
    "specifics",
    [
        {"UPC": ["None", "9876543210123", "0123456789012"]},
        {"UPC": ["None"], "EAN": ["0123456789012"]},
    ],
)
def test_real_multivalue_barcode_match_survives_placeholder_removal(specifics):
    listing, target = _listing(specifics), _variant(barcodes=["None", "0123456789012"])
    candidate = score_variant(listing, target, NOW)
    assert candidate.score == 95 and candidate.status == "strong_candidate"
    assert decide_match(listing, [target]).outcome == "probable_variant"
    assert all(row["notify"] for row in _assessments(listing, target, []).values())


@pytest.mark.parametrize("value", REAL)
def test_usable_catalog_numbers_are_not_expanded_into_sentinels(value):
    listing, target = _listing({"Catalog Number": [value]}), _variant([value])
    candidate = score_variant(listing, target, NOW)
    assert candidate.score == 80 and candidate.status == "strong_candidate"
    assert decide_match(listing, [target]).outcome == "probable_variant"
    assert all(row["notify"] for row in _assessments(listing, target, []).values())


@pytest.mark.parametrize("missing", MISSING)
@pytest.mark.parametrize("kind", ["catalog_number", "barcode"])
@pytest.mark.parametrize("role", ["tells", "anti_tells"])
def test_stale_typed_placeholder_clues_cannot_promote_or_reject(missing, kind, role):
    listing = _listing(suffix="Nonesuch Records catalog number None N/A Does not apply")
    target = _variant(["SYN-123", "none"], ["None"])
    clue = Clue(kind=kind, value=missing, required=role == "tells")
    assert not clue_found(clue, ListingText(listing, target), common_version=role == "anti_tells")
    rows = _assert_unclear_alerts(listing, target, [], **{role: [clue]})
    for row in rows.values():
        assert row["signs"] == row["common_signs"] == []
        assert row["missing_signs"] == ([clue.describe()] if role == "tells" else [])


@pytest.mark.parametrize(
    "kind,value",
    [
        ("catalog_number", "NONE-001"),
        ("catalog_number", "N/A-001"),
        ("barcode", "0123456789012"),
        ("keyword", "None"),
        ("label", "None"),
    ],
)
def test_valid_typed_and_non_identifier_clues_keep_existing_semantics(kind, value):
    listing, target = _listing(suffix=value), _variant()
    clue = Clue(kind=kind, value=value, required=True)
    assert clue_found(clue, ListingText(listing, target))
    rows = _assessments(listing, target, [], tells=[clue])
    assert all(row["status"] == "possible_pressing" and row["notify"] for row in rows.values())
    assert all(
        not row["notify"] for row in _assessments(listing, target, [], anti_tells=[clue]).values()
    )


@pytest.mark.parametrize("missing", MISSING)
def test_fresh_suggestions_do_not_propose_placeholder_identifiers(missing):
    target = _variant([missing, "SYN-123"], [missing, "0123456789012"])
    sibling = _variant([missing, "SYN-999"], [missing, "9876543210123"], identity="sibling")
    result = suggest_clues(target, [sibling], partial=False)

    def identifiers(values):
        return {
            (item["kind"], item["value"])
            for item in values
            if item["kind"] in ("catalog_number", "barcode")
        }

    assert identifiers(result["tells"]) == {
        ("catalog_number", "SYN-123"),
        ("barcode", "0123456789012"),
    }
    assert identifiers(result["anti_tells"]) == {
        ("catalog_number", "SYN-999"),
        ("barcode", "9876543210123"),
    }
    # A missing value unique to the target must not become a distinguishing tell.
    assert not identifiers(
        suggest_clues(_variant([missing], [missing]), [_variant()], partial=False)["tells"]
    )


@pytest.mark.parametrize("missing", MISSING)
def test_catalog_queries_skip_missing_identifiers_but_keep_real_values(monkeypatch, missing):
    provider = DiscogsCatalogProvider(None)
    searches = []

    def search(params, limit):
        searches.append(params)
        return [], False

    monkeypatch.setattr(provider, "_search", search)
    monkeypatch.setattr(provider, "_hydrate", lambda ids: [])
    listing = _listing({"Catalog Number": [missing], "UPC": [missing]})
    assert provider.search_for_listing(listing).query_kinds == ["q"]
    assert searches == [{"q": listing.title.strip()}]
    searches.clear()
    listing = _listing({"Catalog Number": [missing, "NA"], "UPC": [missing, "0123456789012"]})
    assert provider.search_for_listing(listing).query_kinds == ["barcode", "catno", "q"]
    assert searches[:2] == [{"barcode": "0123456789012"}, {"catno": "NA"}]
    watch = SavedWatch(release_id=1)
    assert watch_queries(["Synthetic Album"], _variant(barcodes=[missing]), watch) == [
        "Synthetic Album"
    ]
    assert watch_queries(
        ["Synthetic Album"], _variant(barcodes=[missing, "0123456789012"]), watch
    ) == ["Synthetic Album", "gtin:0123456789012"]


def test_live_validation_helpers_use_normalized_identifiers():
    helpers = runpy.run_path(str(Path(__file__).parents[1] / "scripts/validate_live_matching.py"))
    empty = _variant(MISSING, MISSING)
    assert helpers["_barcodes"](empty) == helpers["_catalog_numbers"](empty) == []
    target = _variant([*MISSING, "SYN-123"], [*MISSING, "0123456789012"])
    assert helpers["_barcodes"](target) == ["0123456789012"]
    assert helpers["_catalog_numbers"](target) == ["SYN-123"]
    listing = helpers["_synthetic_listing"](target, NOW)
    assert listing.item_specifics["UPC"] == ["0123456789012"]
    assert listing.item_specifics["Catalog Number"] == ["SYN-123"]
