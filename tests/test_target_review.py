"""Sparse seller wording should produce leads while contradictions stay visible."""

import pytest

from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.categories.target_review import review_target_listing, review_target_with_alternatives


@pytest.fixture
def release(discogs_release, observed_at):
    return normalize_release(
        {
            **discogs_release,
            "title": "Don't Be Dumb",
            "artists": [{"name": "A$AP Rocky"}],
            "formats": [
                {"name": "Vinyl", "text": "Pink", "descriptions": ["LP"]},
                {"name": "Vinyl", "text": "Green", "descriptions": ["LP"]},
            ],
        },
        observed_at,
    )


def _listing(search_payload, observed_at, title, specifics=()):
    return normalize_listing(
        {
            **search_payload["itemSummaries"][0],
            "title": title,
            "localizedAspects": [{"name": name, "value": value} for name, value in specifics],
        },
        observed_at,
    )


@pytest.mark.parametrize(
    ("title", "specifics", "status"),
    [
        ("ASAP Rocky Dont Be Dumb Pink and Green 2LP", (), "possible_pressing"),
        ("A$AP Rocky - Don't Be Dumb vinyl", (), "family_review"),
        ("ASAP Rocky Dont Be Dumb vinyl", (("Color", "Pink & Green"),), "possible_pressing"),
        ("ASAP Rocky Dont Be Dumb Pink and Blue vinyl", (), "conflicting"),
        ("ASAP Rocky Dont Be Dumb Pink vinyl", (), "family_review"),
        ("ASAP Rocky Dont Be Dumb vinyl", (("Color", "Black"),), "conflicting"),
        ("ASAP Rocky Dont Be Dumb vinyl", (("Barcode", "9999999999999"),), "conflicting"),
        ("ASAP Rocky Dont Be Dumb CD", (), "conflicting"),
        ("ASAP Rocky Another Album Pink and Green vinyl", (), "unrelated"),
        ("ASAP Rocky Dont Be Dumb Pink and Green vinyl", (("Artist", "Other"),), "conflicting"),
    ],
)
def test_target_review_from_sparse_listing(
    search_payload, observed_at, release, title, specifics, status
):
    row = review_target_listing(_listing(search_payload, observed_at, title, specifics), release)
    assert row.status == status
    if status == "possible_pressing":
        assert "catalog_alternatives_not_checked" in row.verify


def test_numbered_variant_without_numbered_seller_claim_needs_review(
    search_payload, observed_at, release
):
    numbered = release.model_copy(
        update={
            "formats": [{"name": "Vinyl", "text": "Pink / Green", "descriptions": ["Numbered"]}]
        }
    )
    without = _listing(search_payload, observed_at, "ASAP Rocky Dont Be Dumb Pink Green vinyl")
    assert review_target_listing(without, numbered).status == "family_review"
    with_claim = without.model_copy(update={"title": without.title + " numbered"})
    row = review_target_listing(with_claim, numbered)
    assert row.status == "possible_pressing"
    assert "seller_numbered_claim" in row.clues


def test_identifier_can_surface_uncolored_vinyl_for_review(
    search_payload, discogs_release, observed_at
):
    variant = normalize_release(discogs_release, observed_at)
    listing = _listing(
        search_payload,
        observed_at,
        "Example Artist Example Album LP",
        (("Barcode", "0123456789012"),),
    )
    row = review_target_listing(listing, variant)
    assert row.status == "possible_pressing"
    assert "seller_identifier_claim" in row.clues
    assert "catalog_alternatives_not_checked" in row.verify


def test_shared_color_stays_visible_with_ambiguous_alternative(
    search_payload, observed_at, release
):
    listing = _listing(search_payload, observed_at, "ASAP Rocky Dont Be Dumb Pink Green vinyl")
    other = release.model_copy(update={"catalog_variant_id": "222"})
    row = review_target_with_alternatives(listing, release, [release, other, other])
    assert row.status == "possible_pressing"
    assert row.alternatives_checked == row.alternatives_not_ruled_out == 1
    assert "other_pressings_not_ruled_out" in row.verify
    assert "catalog_alternative_search_incomplete" in row.verify
    assert "catalog_alternatives_not_checked" not in row.verify


def test_missing_competitor_color_is_unresolved_not_ruled_out(search_payload, observed_at, release):
    listing = _listing(search_payload, observed_at, "ASAP Rocky Dont Be Dumb Pink Green vinyl")
    other = release.model_copy(update={"catalog_variant_id": "222", "formats": [{"name": "Vinyl"}]})
    row = review_target_with_alternatives(listing, release, [other], search_incomplete=False)
    assert row.alternatives_not_ruled_out == 1


def test_contradicted_color_and_other_album_do_not_add_ambiguity(
    search_payload, observed_at, release
):
    listing = _listing(search_payload, observed_at, "ASAP Rocky Dont Be Dumb Pink Green vinyl")
    other_color = release.model_copy(
        update={"catalog_variant_id": "222", "formats": [{"name": "Vinyl", "text": "Blue"}]}
    )
    other_album = release.model_copy(update={"catalog_variant_id": "333", "title": "Other"})
    row = review_target_with_alternatives(
        listing, release, [other_color, other_album], search_incomplete=False
    )
    assert row.status == "possible_pressing"
    assert row.alternatives_checked == 2
    assert row.alternatives_not_ruled_out == 0
    assert "other_pressings_not_ruled_out" not in row.verify


def test_shared_barcode_needs_discriminating_evidence(search_payload, discogs_release, observed_at):
    target = normalize_release(discogs_release, observed_at)
    other = target.model_copy(update={"catalog_variant_id": "222"})
    listing = _listing(
        search_payload,
        observed_at,
        "Example Artist Example Album LP",
        (("Barcode", "0123456789012"),),
    )
    row = review_target_with_alternatives(listing, target, [other])
    assert row.alternatives_not_ruled_out == 1
    assert row.status == "possible_pressing"


def test_missing_or_empty_catalog_comparison_never_proves_uniqueness(
    search_payload, observed_at, release
):
    listing = _listing(search_payload, observed_at, "ASAP Rocky Dont Be Dumb Pink Green vinyl")
    failed = review_target_with_alternatives(listing, release, None)
    assert failed.alternatives_checked is None
    assert "catalog_alternatives_not_checked" in failed.verify
    empty = review_target_with_alternatives(listing, release, [])
    assert empty.status == "possible_pressing"
    assert empty.alternatives_checked == 0
    assert "no_competing_pressings_retrieved" in empty.verify
    assert "catalog_alternative_search_incomplete" in empty.verify
