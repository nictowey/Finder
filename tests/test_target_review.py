"""Sparse seller wording should produce leads while contradictions stay visible."""

import pytest

from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.categories.target_review import review_target_listing


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
