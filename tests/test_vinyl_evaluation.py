import json
from datetime import UTC, datetime
from pathlib import Path

from finder.domain import Listing, Variant
from finder.matching import decide_match, rank_variants

CASES = json.loads((Path(__file__).parents[1] / "evaluations" / "vinyl_matching.json").read_text())


def _variant(case, suffix, *, barcode, color, edition):
    return Variant(
        catalog_source="discogs",
        catalog_variant_id=f"eval-{case['id']}-{suffix}",
        catalog_product_id=f"eval-{case['id']}",
        title=case["title"],
        artists=[case["artist"]],
        release_year=case["year"],
        country="US",
        formats=[
            {
                "name": "Vinyl",
                "qty": "1",
                "descriptions": ["LP", edition],
                "text": color,
            }
        ],
        labels=[{"name": "Evaluation Label", "catno": f"EVAL-{case['id']}"}],
        identifiers={"Barcode": [barcode]},
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _evaluation(case):
    target_barcode = f"EVAL{case['id']}TARGET"
    distractor_barcode = f"EVAL{case['id']}OTHER"
    target = _variant(
        case,
        "target",
        barcode=target_barcode,
        color=case["color"],
        edition=case["edition"],
    )
    distractor = _variant(
        case,
        "distractor",
        barcode=(
            target_barcode if case["scenario"] == "ambiguous_shared_barcode" else distractor_barcode
        ),
        color="Black" if case["color"] != "Black" else "White",
        edition="Reissue" if case["edition"] != "Reissue" else "First Pressing",
    )
    specifics = {"Artist": [case["artist"]], "Release Year": [str(case["year"])]}
    scenario = case["scenario"]
    if scenario == "exact_barcode":
        specifics.update({"UPC": [target_barcode], "Color": [case["color"]]})
    elif scenario == "catalog_color":
        specifics.update({"Catalog Number": [f"EVAL-{case['id']}"], "Color": [case["color"]]})
    elif scenario == "edition":
        specifics.update({"Catalog Number": [f"EVAL-{case['id']}"], "Edition": [case["edition"]]})
    elif scenario == "ambiguous_shared_barcode":
        specifics = {
            "UPC": [target_barcode],
            "Artist": [case["artist"]],
            "Release Year": [str(case["year"])],
        }
    elif scenario == "conflicting_barcode":
        specifics = {"UPC": [f"EVAL{case['id']}CONFLICT"]}
    elif scenario == "title_only":
        specifics = {}
    listing = Listing(
        marketplace="ebay",
        marketplace_item_id=f"eval-{case['id']}",
        title=f"{case['artist']} {case['title']} vinyl LP",
        item_specifics=specifics,
        first_observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return (
        target,
        rank_variants(listing, [target, distractor]),
        decide_match(listing, [target, distractor]),
    )


def test_curated_evaluation_set_has_twenty_real_album_scenarios():
    assert len(CASES) == 20
    assert len({case["id"] for case in CASES}) == 20
    assert all(2010 <= case["year"] <= 2026 for case in CASES)


def test_curated_vinyl_matching_expectations():
    for case in CASES:
        target, ranked, decision = _evaluation(case)
        strong = [candidate for candidate in ranked if candidate.status == "strong_candidate"]
        if case["scenario"] in {"exact_barcode", "catalog_color", "edition"}:
            assert ranked[0].catalog_variant_id == target.catalog_variant_id, case["id"]
            assert ranked[0].status == "strong_candidate", case["id"]
            assert len(strong) == 1, case["id"]
            assert decision.outcome == "probable_variant", case["id"]
        elif case["scenario"] == "ambiguous_shared_barcode":
            assert len(strong) == 2, case["id"]
            assert strong[0].score == strong[1].score, case["id"]
            assert decision.outcome == "ambiguous", case["id"]
        else:
            assert not strong, case["id"]
            assert decision.outcome in {"insufficient_data", "rejected"}, case["id"]
