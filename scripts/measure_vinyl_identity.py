"""Repeatable synthetic pressing-policy measurement across genres and eras.

These fabricated listings never call either provider. The target and competing catalog
variants are supplied directly; the numbers do not measure live candidate retrieval,
listing discovery, or precision on real eBay listings.
"""

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime

from finder.domain import Listing, Variant
from finder.matching import MATCH_POLICY_VERSION, decide_match

WHEN = datetime(2026, 1, 1, tzinfo=UTC)
ALBUMS = (
    ("jazz", "The Harbor Quartet", "An Evening in Blue", 1958, "Blue", "First Pressing"),
    ("rock", "The Lanterns", "Echoes on the Road", 1977, "Red", "Limited Edition"),
    ("classical", "Westfield Ensemble", "Winter Suite", 1994, "White", "Reissue"),
    ("electronic", "Static Garden", "Night Circuit", 2008, "Green", "Album"),
    ("folk", "Mara Vale", "A Long Way Home", 2022, "Orange", "Deluxe Edition"),
)
SCENARIOS = (
    "unique_barcode",
    "shared_barcode_with_color",
    "shared_barcode_other_color",
    "shared_barcode_without_color",
    "catalog_number_and_edition",
    "title_only",
    "conflicting_barcode",
)


def _case(album: tuple, scenario: str) -> tuple[Listing, list[Variant], str | None]:
    genre, artist, title, year, color, edition = album
    family_id = f"synthetic-{genre}"
    target_id, other_id = f"{family_id}-target", f"{family_id}-other"
    barcode = f"SYNTHETIC-{genre}-SHARED"
    shared = scenario.startswith("shared_barcode")
    specifics: dict[str, list[str]] = {"Artist": [artist], "Release Year": [str(year)]}
    if scenario == "unique_barcode":
        specifics.update(UPC=[barcode], Color=[color])
    elif shared:
        specifics["UPC"] = [barcode]
        if scenario == "shared_barcode_with_color":
            specifics["Color"] = [color]
        elif scenario == "shared_barcode_other_color":
            specifics["Color"] = ["Black" if color != "Black" else "White"]
    elif scenario == "catalog_number_and_edition":
        specifics.update({"Catalog Number": [f"SYN-{genre}"], "Edition": [edition]})
    elif scenario == "title_only":
        specifics = {}
    else:
        specifics = {"UPC": [f"SYNTHETIC-{genre}-CONFLICT"]}

    def variant(id: str, barcode_value: str, variant_color: str, variant_edition: str) -> Variant:
        return Variant(
            catalog_source="discogs",
            catalog_variant_id=id,
            catalog_product_id=family_id,
            title=title,
            artists=[artist],
            release_year=year,
            country="US",
            formats=[
                {
                    "name": "Vinyl",
                    "qty": "1",
                    "descriptions": ["LP", variant_edition],
                    "text": variant_color,
                }
            ],
            labels=[{"name": "Synthetic Label", "catno": f"SYN-{genre}"}],
            identifiers={"Barcode": [barcode_value]},
            observed_at=WHEN,
        )

    target = variant(target_id, barcode, color, edition)
    other = variant(
        other_id,
        barcode if shared else f"SYNTHETIC-{genre}-OTHER",
        "Black" if color != "Black" else "White",
        "Reissue" if edition != "Reissue" else "First Pressing",
    )
    listing = Listing(
        marketplace="ebay",
        marketplace_item_id=f"synthetic-{genre}-{scenario}",
        title=f"{artist} {title} vinyl LP",
        item_specifics=specifics,
        first_observed_at=WHEN,
        last_observed_at=WHEN,
    )
    true_id = (
        None
        if scenario == "conflicting_barcode"
        else other_id
        if scenario == "shared_barcode_other_color"
        else target_id
    )
    return listing, [target, other], true_id


def measure() -> dict:
    outcomes: Counter[str] = Counter()
    by_genre: dict[str, Counter[str]] = defaultdict(Counter)
    calls = correct_calls = false_calls = 0
    failures: list[str] = []
    for album in ALBUMS:
        for scenario in SCENARIOS:
            listing, variants, true_id = _case(album, scenario)
            decision = decide_match(listing, variants)
            outcomes[decision.outcome] += 1
            by_genre[album[0]][decision.outcome] += 1
            if decision.outcome == "probable_variant":
                calls += 1
                if decision.candidate_ids == [true_id] and true_id is not None:
                    correct_calls += 1
                else:
                    false_calls += 1
                    failures.append(f"{album[0]}/{scenario}")
    total = len(ALBUMS) * len(SCENARIOS)
    assert sum(outcomes.values()) == total
    assert calls == correct_calls + false_calls
    return {
        "scope": "synthetic_policy_only",
        "policy_version": MATCH_POLICY_VERSION,
        "cases": total,
        "genres": len(ALBUMS),
        "scenarios_per_genre": len(SCENARIOS),
        "provisional_variant_calls": calls,
        "provisional_variant_correct": correct_calls,
        "provisional_variant_false": false_calls,
        "provisional_variant_precision": correct_calls / calls if calls else None,
        "exact_variant_calls": outcomes["exact_variant"],
        "pressing_abstentions": total - calls - outcomes["exact_variant"],
        "decision_outcomes": dict(sorted(outcomes.items())),
        "by_genre": {genre: dict(sorted(counts.items())) for genre, counts in by_genre.items()},
        "false_call_cases": failures,
        "limitations": [
            "The seller claims and catalog candidates are fabricated and supplied to the matcher.",
            "Live eBay search recall, Discogs retrieval recall, and real precision are unmeasured.",
            "The five genre panels share templates and are not independent holdout labels.",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(measure(), indent=2))
