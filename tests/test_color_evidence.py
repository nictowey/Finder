from datetime import UTC, datetime

import pytest

from finder.categories.target_review import review_target_with_alternatives
from finder.domain import Listing, Variant
from finder.matching import _color_evidence, score_variant

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)


def variant(id, text):
    return Variant(
        catalog_source="discogs",
        catalog_variant_id=id,
        catalog_product_id="family",
        artists=["Example Artist"],
        title="Example Album",
        formats=[{"name": "Vinyl", "descriptions": ["LP"], **({"text": text} if text else {})}],
        resource_url="https://www.discogs.com/release/" + id,
        observed_at=NOW,
    )


def listing(text="clear vinyl", color=None):
    return Listing(
        marketplace="synthetic",
        marketplace_item_id="probe",
        title="Example Artist Example Album " + text,
        item_specifics={"Color": [color]} if color else {},
        first_observed_at=NOW,
        last_observed_at=NOW,
        details_observed_at=NOW,
        price_kind="fixed_price",
    )


@pytest.mark.parametrize("text", ["180g", "OBI", "Splatter", "TIE-DYE", "Cloudburst", None])
@pytest.mark.parametrize("structured", [False, True])
def test_noncolor_catalog_description_is_missing_color_not_conflict(text, structured):
    target = variant("1", "Transparent Aurora")
    alternative = variant("2", text)
    row = review_target_with_alternatives(
        listing(color="clear" if structured else None),
        target,
        [alternative],
        search_incomplete=False,
    )
    assert row.status == "family_review"
    assert row.alternatives_not_ruled_out == 1


@pytest.mark.parametrize("text", ["180g", "OBI", "Splatter", "TIE-DYE", "Cloudburst", "Aurora"])
def test_no_known_palette_is_neither_color_support_nor_contradiction(text):
    assert _color_evidence([text], [text]) is None
    assert _color_evidence([text], ["Transparent Aurora"]) is None
    assert _color_evidence(["clear"], [text]) is None
    candidate = score_variant(listing(color=text), variant("1", text))
    assert all(e.field != "color" for e in candidate.evidence)


@pytest.mark.parametrize(
    "claim,catalog,matched",
    [
        ("Blue", "Clear", False),
        ("Clear", "Transparent Aurora", True),
        ("Not clear", "Transparent Aurora", False),
        ("Not blue, clear vinyl", "Transparent Aurora", True),
        ("Pink", "Pink and Green", None),
    ],
)
def test_existing_comparable_palette_and_denial_behavior_remains(claim, catalog, matched):
    evidence = _color_evidence([claim], [catalog])
    assert (evidence.matched if evidence else None) is matched


def test_a_named_target_does_not_eliminate_sparse_unnamed_sibling():
    target = variant("1", "Transparent Aurora")
    sparse = variant("2", None)
    row = review_target_with_alternatives(
        listing("Transparent Aurora vinyl"), target, [sparse], search_incomplete=False
    )
    assert row.status == "family_review" and row.alternatives_not_ruled_out == 1


@pytest.mark.parametrize("required", [False, True])
def test_sparse_sibling_prevents_watch_likely_and_main_price_alert(required):
    from finder.categories.vinyl_clues import Clue
    from finder.watch_store import SavedWatch
    from finder.watch_worker import assess_review

    target = variant("1", "Transparent Aurora")
    alternative = variant("2", "180g")
    watch = SavedWatch(
        release_id=1, tells=[Clue(kind="keyword", value="Transparent Aurora", required=required)]
    )
    row = assess_review(
        watch,
        listing("Transparent Aurora vinyl"),
        target,
        now=NOW,
        alternatives=[alternative],
        search_incomplete=False,
    )
    assert row["status"] == "family_review"
    assert row["alternatives_not_ruled_out"] == 1
    assert not row["notify"]


def test_same_palette_different_names_stays_ambiguous():
    row = review_target_with_alternatives(
        listing("Transparent Aurora vinyl"),
        variant("1", "Transparent Aurora"),
        [variant("2", "Transparent Nebula")],
        search_incomplete=False,
    )
    assert row.status == "family_review" and row.alternatives_not_ruled_out == 1
