"""Synthetic required-package denials, through review and alert persistence."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from finder.categories.target_review import review_target_listing
from finder.categories.vinyl_clues import Clue
from finder.domain import Listing, Variant
from finder.watch_store import SavedWatch, WatchStore, inbox, migrate, outbox
from finder.watch_worker import assess_review

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)


def target(**changes):
    return Variant(
        catalog_source="synthetic",
        catalog_variant_id="1",
        catalog_product_id="synthetic-family",
        title="Example Album",
        artists=["Example Artist"],
        formats=[
            {"name": "Vinyl", "text": "Blue"},
            {"name": "All Media", "text": "Signed Insert, Mountain Alternative Cover"},
        ],
        identifiers={"Barcode": ["012345678905"]},
        observed_at=NOW,
    ).model_copy(update=changes)


def row(suffix="vinyl LP NO SIGNATURE INCLUDED", **changes):
    return Listing(
        marketplace="synthetic",
        marketplace_item_id="package-review",
        title="Example Artist Example Album " + suffix,
        item_specifics={"UPC": ["0012345678905"]},
        first_observed_at=NOW,
        last_observed_at=NOW,
        details_observed_at=NOW,
        price_kind="fixed_price",
        current_price=Decimal("20"),
        shipping_cost=Decimal("0"),
        currency="USD",
        shipping_currency="USD",
        source_metadata={"delivery_country": "US", "delivery_postal_code": "00000"},
    ).model_copy(update=changes)


def watch(**changes):
    return SavedWatch(
        release_id=1,
        country="US",
        postal_code="00000",
        maximum_subtotal=Decimal("25"),
        gamble_max=Decimal("25"),
        tells=[Clue(kind="keyword", value="vinyl", required=True)],
    ).model_copy(update=changes)


@pytest.mark.parametrize(
    "suffix",
    [
        "vinyl LP NO SIGNATURE INCLUDED",
        "vinyl LP no autograph included",
        "vinyl LP no signed insert",
        "vinyl LP no signed insert included",
        "vinyl LP without the signed insert",
        "vinyl LP missing signed insert",
        "vinyl LP signed insert not included",
        "vinyl LP signed insert is missing",
        "vinyl LP autographed insert not included",
    ],
)
def test_explicit_required_insert_denial_conflicts_even_with_matching_identifier(suffix):
    listing = row(suffix)
    original = listing.model_dump(mode="json")
    review = review_target_listing(listing, target())
    assert review.status == "conflicting"
    assert "catalog_signed_insert_missing" in review.verify
    assert listing.model_dump(mode="json") == original


@pytest.mark.parametrize("mode", ["review_leads", "strict"])
@pytest.mark.parametrize("alternatives", [None, [], [target(catalog_variant_id="2")]])
@pytest.mark.parametrize("tells", [[], watch().tells])
def test_denial_cannot_be_promoted_by_tells_or_alert_at_either_price(mode, alternatives, tells):
    review = assess_review(
        watch(alert_mode=mode, tells=tells),
        row(),
        target(),
        now=NOW,
        alternatives=alternatives,
        search_incomplete=False,
    )
    assert review["status"] == "conflicting"
    assert review["alert_budget"] == "not_alerting"
    assert not review["notify"]


@pytest.mark.parametrize(
    "suffix",
    [
        "vinyl LP",
        "vinyl LP signed insert included",
        "vinyl LP no signature required",
        "vinyl LP shipping no signature included",
        "vinyl LP no delivery signature included",
        "vinyl LP unsigned record with signed insert",
        "vinyl LP unsigned sleeve with signed insert",
        "vinyl LP record not signed",
        "vinyl LP sleeve not signed",
        "vinyl LP no signature on record",
        "vinyl LP no signature on sleeve",
        "vinyl LP no signature included on sleeve",
        "vinyl LP signed insert not included in photos",
        "vinyl LP signed insert not included?",
        'vinyl LP "no signature included"',
        "vinyl LP 'no signed insert'",
        "vinyl LP unlike the other copy no signature included",
        "vinyl LP comparison no signed insert",
        "vinyl LP bundle no signed insert",
        "vinyl LP lot no signed insert",
        "vinyl LP maybe no signed insert",
        "vinyl LP not missing signed insert",
        "vinyl LP signed insert included no signature included",
        "vinyl LP with or without signed insert",
        "vinyl LP signed insert or no signed insert",
        "vinyl LP no signed insert replacement needed",
        "vinyl LP won't ship without the signed insert",
        "vinyl LP won't be missing signed insert",
        "vinyl LP if no signed insert",
        "vinyl LP unless no signed insert",
        "vinyl LP previously no signed insert",
        "vinyl LP zero chance of missing signed insert",
        "vinyl LP reportedly no signature included",
        "vinyl LP advertised as no signature included",
    ],
)
def test_absence_and_ambiguous_or_unrelated_signature_context_abstain(suffix):
    review = assess_review(
        watch(), row(suffix), target(), now=NOW, alternatives=[], search_incomplete=False
    )
    assert review["status"] == "possible_pressing"
    assert "catalog_signed_insert_missing" not in review["verify"]
    assert review["notify"]


@pytest.mark.parametrize(
    "formats",
    [
        [{"name": "Vinyl", "text": "Blue"}],
        [{"name": "Vinyl", "text": "Signed"}],
        [{"name": "Vinyl", "text": "No Signed Insert"}],
        [{"name": "Vinyl", "text": "Signed Insert or Poster"}],
        [{"name": "Vinyl", "text": "Signed Insert, if available"}],
        [{"name": "Vinyl", "text": "Signed Insert, possibly"}],
        [{"name": "Vinyl"}, {"name": "CD", "text": "Signed Insert"}],
        [{"name": "Vinyl", "descriptions": ["Signed Insert"]}],
    ],
)
def test_target_must_explicitly_require_signed_insert_in_format_text(formats):
    review = review_target_listing(row(), target(formats=formats))
    assert review.status == "possible_pressing"
    assert "catalog_signed_insert_missing" not in review.verify


def test_signed_words_do_not_promote_and_generic_signed_specific_does_not_deny_insert():
    review = review_target_listing(
        row("vinyl LP signed insert included", item_specifics={}), target()
    )
    assert review.status == "family_review"
    review = review_target_listing(
        row("vinyl LP", item_specifics={"Signed": ["No"], "UPC": ["012345678905"]}), target()
    )
    assert review.status == "possible_pressing"
    assert "catalog_signed_insert_missing" not in review.verify


def test_required_insert_denial_never_enters_outbox_but_intact_control_does(repository):
    migrate(repository.engine)
    store = WatchStore(repository.engine)
    store.add(watch(), now=NOW)
    denied, intact = row(), row("vinyl LP signed insert included", marketplace_item_id="intact")
    rows = []
    for listing in (denied, intact):
        repository.upsert(listing)
        review = assess_review(
            watch(), listing, target(), now=NOW, alternatives=[], search_incomplete=False
        )
        rows.append((listing, review))
    store.finish(store.claim(now=NOW), rows, now=NOW)
    with repository.engine.connect() as conn:
        assert {r.marketplace_item_id: r.data["status"] for r in conn.execute(select(inbox))} == {
            denied.marketplace_item_id: "conflicting",
            intact.marketplace_item_id: "possible_pressing",
        }
        assert list(conn.execute(select(outbox.c.marketplace_item_id)).scalars()) == [
            intact.marketplace_item_id
        ]


@pytest.mark.parametrize("fmt", ["Vinyl", "All Media"])
@pytest.mark.parametrize("component", ["Signed Insert", "Autographed Insert"])
def test_catalog_component_is_case_insensitive_and_scoped_to_supported_format(fmt, component):
    variant = target(formats=[{"name": "Vinyl"}, {"name": fmt, "text": component.upper()}])
    assert review_target_listing(row(), variant).status == "conflicting"


def test_apostrophes_inside_catalog_identity_are_not_quoted_claims():
    variant = target(title="Don't Wait", artists=["Joan's Band (2)"])
    listing = row(title="Joan's Band Don't Wait vinyl LP no signature included")
    assert review_target_listing(listing, variant).status == "conflicting"


def test_denial_is_not_inferred_from_catalog_title_or_opaque_seller_metadata():
    variant = target(title="No Signature Included")
    listing = row(title="Example Artist No Signature Included vinyl LP")
    assert review_target_listing(listing, variant).status == "possible_pressing"
    listing = row("vinyl LP", source_metadata={"description": "no signed insert"})
    assert review_target_listing(listing, target()).status == "possible_pressing"


@pytest.mark.parametrize(
    "album,suffix",
    [("Not", "not missing signed insert"), ("Never", "never without the signed insert")],
)
def test_catalog_identity_cannot_erase_a_later_denial_modifier(album, suffix):
    variant = target(title=album)
    listing = row(title=f"Example Artist {album} vinyl LP {suffix}")
    review = review_target_listing(listing, variant)
    assert review.status == "possible_pressing"
    assert "catalog_signed_insert_missing" not in review.verify


def test_artist_identity_in_specifics_cannot_erase_a_denial_modifier():
    variant = target(artists=["Not"])
    listing = row(
        title="Example Album vinyl LP not missing signed insert",
        item_specifics={"Artist": ["Not"], "UPC": ["012345678905"]},
    )
    assert review_target_listing(listing, variant).status == "possible_pressing"


@pytest.mark.parametrize("name,prefix", [("Maybe", "maybe"), ("Never", "never")])
def test_bare_natural_language_cover_name_cannot_erase_uncertainty(name, prefix):
    variant = target(
        formats=[
            {"name": "Vinyl"},
            {"name": "All Media", "text": f"Signed Insert, {name} Alternative Cover"},
        ]
    )
    listing = row(f"{prefix} vinyl LP no signed insert")
    assert review_target_listing(listing, variant).status == "possible_pressing"


@pytest.mark.parametrize(
    "cover,title_prefix", [("MTN1", "DAMAGED MTN1 2 DISC"), ("Mountain", "Mountain cover")]
)
def test_explicit_cover_role_or_catalog_code_can_precede_package_denial(cover, title_prefix):
    variant = target(
        formats=[
            {"name": "Vinyl"},
            {"name": "All Media", "text": f"Signed Insert, {cover} Alternative Cover"},
        ]
    )
    listing = row(f"{title_prefix} VINYL LP NO SIGNATURE INCLUDED")
    assert review_target_listing(listing, variant).status == "conflicting"
