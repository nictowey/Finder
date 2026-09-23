import pytest

from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.matching import decide_match, score_variant


def test_strong_identifiers_create_strong_candidate(search_payload, discogs_release, observed_at):
    raw = search_payload["itemSummaries"][0]
    raw["localizedAspects"] = [
        {"name": "Artist", "value": "Example Artist"},
        {"name": "Release Year", "value": "2018"},
        {"name": "UPC", "value": "0 12345 67890 12"},
        {"name": "Catalog Number", "value": "EX101"},
        {"name": "Color", "value": "Blue"},
    ]
    listing = normalize_listing(raw, observed_at)
    candidate = score_variant(listing, normalize_release(discogs_release, observed_at), observed_at)
    assert candidate.score == 100
    assert candidate.status == "strong_candidate"
    assert {item.field for item in candidate.evidence if item.matched} >= {
        "barcode",
        "catalog_number",
        "artist",
        "release_year",
    }


def test_conflicting_barcode_forces_rejection(search_payload, discogs_release, observed_at):
    raw = search_payload["itemSummaries"][0]
    raw["localizedAspects"] = [
        {"name": "Artist", "value": "Example Artist"},
        {"name": "UPC", "value": "9999999999999"},
    ]
    candidate = score_variant(
        normalize_listing(raw, observed_at),
        normalize_release(discogs_release, observed_at),
        observed_at,
    )
    assert candidate.score <= 20
    assert candidate.status == "rejected"


def test_title_only_never_claims_strong_match(search_payload, discogs_release, observed_at):
    raw = search_payload["itemSummaries"][0]
    raw["title"] = "Example Artist Example Album vinyl LP"
    raw.pop("seller", None)
    raw.pop("shippingOptions", None)
    candidate = score_variant(
        normalize_listing(raw, observed_at),
        normalize_release(discogs_release, observed_at),
        observed_at,
    )
    assert candidate.score <= 25
    assert candidate.status == "rejected"


@pytest.mark.parametrize(
    ("seller_color", "catalog_colors", "matched"),
    [
        ("Pink & Green", ["Pink", "Green"], True),
        ("Green / Pink", ["Pink / Green"], True),
        ("Pink", ["Pink", "Green"], False),
        ("Pink & Blue", ["Pink", "Green"], False),
        ("Pink & Green", ["Pink"], False),
    ],
)
def test_pair_color_compares_whole_palette_without_claiming_exact_pressing(
    search_payload, discogs_release, observed_at, seller_color, catalog_colors, matched
):
    raw = search_payload["itemSummaries"][0]
    raw["title"] = "Example Artist Example Album vinyl LP"
    raw["localizedAspects"] = [
        {"name": "Artist", "value": "Example Artist"},
        {"name": "Color", "value": seller_color},
    ]
    listing = normalize_listing(raw, observed_at)
    variant = normalize_release(
        {
            **discogs_release,
            "formats": [
                {"name": "Vinyl", "qty": "1", "descriptions": ["LP"], "text": color}
                for color in catalog_colors
            ],
        },
        observed_at,
    )
    candidate = score_variant(listing, variant, observed_at)
    color = next(item for item in candidate.evidence if item.field == "color")
    assert color.matched is matched
    decision = decide_match(listing, [variant])
    assert decision.outcome == "family_only"
    assert ("color" in decision.conflicts) is not matched
