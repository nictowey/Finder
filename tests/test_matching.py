import pytest

from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.matching import _palette, _seller_color_claims, decide_match, score_variant


@pytest.mark.parametrize(
    ("values", "positive", "denied"),
    [
        (["Neither pink nor green"], set(), {"pink", "green"}),
        (["Not pink or translucent green"], set(), {"pink", "green"}),
        (["Not only pink but green"], {"pink", "green"}, set()),
        (["Not black, pink vinyl"], {"pink"}, {"black"}),
        (["Not pink", "Green"], {"green"}, {"pink"}),
        (["Not a reissue, pink/green vinyl"], {"pink", "green"}, set()),
        (["No grey or transparent vinyl"], set(), {"gray", "clear"}),
        (["Not pink, green or blue"], set(), {"pink", "green", "blue"}),
        (["Neither pink, green, nor blue"], set(), {"pink", "green", "blue"}),
    ],
)
def test_seller_color_denials_have_bounded_scope(values, positive, denied):
    claims, denials = _seller_color_claims(values)
    assert _palette(claims) == positive
    assert denials == denied
    # Catalog palettes still describe raw colors; seller-language rules are separate.
    assert _palette(values) == positive | denied


def test_album_and_artist_names_are_removed_before_color_denials():
    claims, denied = _seller_color_claims(
        ["Black Sabbath - Not Black But White, Pink Vinyl"],
        ignore=("Black Sabbath", "Not Black But White"),
    )
    assert _palette(claims) == {"pink"}
    assert denied == set()


@pytest.mark.parametrize(
    "value",
    [
        "Pink vinyl, not pink sleeve",
        "Pink vinyl with black cover",
        "Pink vinyl, label is black",
        "Pink vinyl with not black and white artwork",
        "Without black sleeve pink vinyl",
        "Not black sleeve pink vinyl",
    ],
)
def test_non_disc_colors_are_not_positive_or_negative_disc_claims(value):
    claims, denied = _seller_color_claims([value])
    assert _palette(claims) == {"pink"}
    assert denied == set()


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
        ("Pink", ["Pink", "Green"], None),
        ("Pink & Blue", ["Pink", "Green"], False),
        ("Pink & Green", ["Pink"], False),
        ("Not pink or green", ["Pink", "Green"], False),
        ("Pink and green, not black", ["Pink", "Green"], True),
        ("Black vinyl, not pink or green", ["Pink", "Green"], False),
        ("Not pink but green", ["Pink", "Green"], False),
        ("Not pink", ["Pink"], False),
        ("Without black sleeve pink vinyl", ["Pink"], True),
        ("Pink vinyl", ["Pink"], True),
        ("Transparent", ["Clear"], True),
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
    color = next((item for item in candidate.evidence if item.field == "color"), None)
    assert (color.matched if color else None) is matched
    decision = decide_match(listing, [variant])
    assert decision.outcome == "family_only"
    assert ("color" in decision.conflicts) is (matched is False)
