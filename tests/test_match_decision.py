"""Policy outcomes must abstain when catalog coverage or pressing evidence is weak."""

from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.categories.vinyl import CollectibleAttribute, VinylFingerprint, from_listing
from finder.domain import MatchDecision, Variant
from finder.matching import MATCH_POLICY_VERSION, decide_match, score_variant


def _numbered_case(search_payload, observed_at, *, features=None, title="Future DS2 purple vinyl"):
    """Synthetic DS2-style releases; never store a real seller listing in public tests."""
    raw = search_payload["itemSummaries"][0]
    raw["title"] = title
    raw["localizedAspects"] = [
        {"name": "Artist", "value": "Future"},
        {"name": "Barcode", "value": "0000000000000"},
        {"name": "Color", "value": "Purple"},
    ]
    if features:
        raw["localizedAspects"].append({"name": "Features", "value": features})
    listing = normalize_listing(raw, observed_at)
    base = {
        "catalog_source": "discogs",
        "catalog_product_id": "ds2-family",
        "title": "DS2",
        "artists": ["Future"],
        "release_year": 2015,
        "country": "US",
        "identifiers": {"Barcode": ["0000000000000"]},
        "observed_at": observed_at,
    }
    numbered = Variant(
        **base,
        catalog_variant_id="numbered-purple",
        formats=[
            {"name": "Vinyl", "descriptions": ["LP", "Club Edition", "Numbered"], "text": "Purple"}
        ],
    )
    ordinary = Variant(
        **base,
        catalog_variant_id="ordinary-purple",
        formats=[{"name": "Vinyl", "descriptions": ["LP"], "text": "Purple"}],
    )
    return listing, numbered, ordinary


def test_numbered_release_needs_explicit_seller_claim(search_payload, observed_at):
    listing, numbered, _ = _numbered_case(search_payload, observed_at)
    decision = decide_match(listing, [numbered])
    assert decision.outcome == "family_only"
    assert "numbered_structured_claim_missing" in decision.missing_evidence


def test_numbered_seller_claim_is_not_exact_proof(search_payload, observed_at):
    listing, numbered, _ = _numbered_case(search_payload, observed_at, features="Numbered")
    decision = decide_match(listing, [numbered])
    assert decision.outcome == "probable_variant"
    assert "numbered_structured_claim_missing" not in decision.missing_evidence
    assert decision.outcome != "exact_variant"


def test_numbered_claim_without_pressing_identifier_stays_family_only(search_payload, observed_at):
    listing, numbered, _ = _numbered_case(search_payload, observed_at, features="Numbered")
    listing = listing.model_copy(
        update={
            "item_specifics": {
                key: values for key, values in listing.item_specifics.items() if key != "Barcode"
            }
        }
    )
    decision = decide_match(listing, [numbered])
    assert decision.outcome == "family_only"
    assert "pressing_identifier" in decision.missing_evidence


def test_long_seller_title_and_catalog_artist_suffix_allow_family_review_without_identifiers(
    search_payload, observed_at
):
    listing, numbered, _ = _numbered_case(search_payload, observed_at, features="Numbered")
    listing = listing.model_copy(
        update={
            "title": "Future DS2 original numbered purple double vinyl, sealed copy",
            "item_specifics": {
                key: values for key, values in listing.item_specifics.items() if key != "Barcode"
            },
        }
    )
    numbered = numbered.model_copy(update={"artists": ["Future (4)"]})
    candidate = score_variant(listing, numbered)
    assert {item.field for item in candidate.evidence if item.matched} >= {"artist", "title"}
    decision = decide_match(listing, [numbered])
    assert decision.outcome == "family_only"
    assert decision.candidate_ids == [numbered.catalog_variant_id]
    assert "pressing_identifier" in decision.missing_evidence
    assert decision.outcome != "exact_variant"


def test_other_album_with_same_artist_and_color_is_not_a_family_match(search_payload, observed_at):
    listing, numbered, _ = _numbered_case(search_payload, observed_at)
    listing = listing.model_copy(update={"title": "Future DS3 purple vinyl"})
    assert decide_match(listing, [numbered]).outcome == "insufficient_data"


def test_numbered_claim_with_shared_identifiers_stays_ambiguous(search_payload, observed_at):
    listing, numbered, ordinary = _numbered_case(search_payload, observed_at, features="Numbered")
    decision = decide_match(listing, [numbered, ordinary])
    assert decision.outcome == "ambiguous"
    assert set(decision.candidate_ids) == {"numbered-purple", "ordinary-purple"}


def test_title_serial_claim_alone_cannot_prove_numbered_release(search_payload, observed_at):
    listing, numbered, _ = _numbered_case(
        search_payload, observed_at, title="Future DS2 hand-numbered #123 purple vinyl"
    )
    decision = decide_match(listing, [numbered])
    assert decision.outcome == "family_only"
    assert "numbered_structured_claim_missing" in decision.missing_evidence


def _listing(search_payload, observed_at, **specifics):
    raw = search_payload["itemSummaries"][0]
    raw["title"] = "Example Artist Example Album vinyl LP"
    raw["localizedAspects"] = [{"name": name, "value": value} for name, value in specifics.items()]
    return normalize_listing(raw, observed_at)


def _variant(discogs_release, observed_at, **changes):
    return normalize_release({**discogs_release, **changes}, observed_at)


def test_single_identifier_candidate_is_probable_not_exact(
    search_payload, discogs_release, observed_at
):
    listing = _listing(search_payload, observed_at, Artist="Example Artist", UPC="0123456789012")
    variant = _variant(discogs_release, observed_at)
    decision = decide_match(listing, [variant])
    assert decision.outcome == "probable_variant"
    assert decision.policy_version == MATCH_POLICY_VERSION
    assert (decision.marketplace, decision.marketplace_item_id) == (
        listing.marketplace,
        listing.marketplace_item_id,
    )
    assert decision.candidate_ids == [variant.catalog_variant_id]
    assert any(
        item.field == "barcode"
        and item.source == "marketplace_listing"
        and item.source_id == listing.marketplace_item_id
        for item in decision.evidence
    )
    assert MatchDecision.model_validate_json(decision.model_dump_json()) == decision


def test_shared_identifier_stays_ambiguous(search_payload, discogs_release, observed_at):
    listing = _listing(search_payload, observed_at, Artist="Example Artist", UPC="0123456789012")
    first = _variant(discogs_release, observed_at)
    second = _variant(discogs_release, observed_at, id=222)
    decision = decide_match(listing, [second, first])
    assert decision.outcome == "ambiguous"
    assert set(decision.candidate_ids) == {"111", "222"}
    assert decision.family_ids == [first.catalog_product_id]
    assert {item.source_id for item in decision.evidence if item.source == "catalog_release"} == {
        "111",
        "222",
    }


def test_cd_listing_cannot_become_probable_vinyl(search_payload, discogs_release, observed_at):
    listing = _listing(
        search_payload, observed_at, Artist="Example Artist", UPC="0123456789012", Format="CD"
    )
    decision = decide_match(listing, [_variant(discogs_release, observed_at)])
    assert decision.outcome == "rejected"
    assert "non_vinyl_listing" in decision.conflicts


def test_cd_in_seller_title_blocks_pressing_even_without_format_specific(
    search_payload, discogs_release, observed_at
):
    listing = _listing(
        search_payload, observed_at, Artist="Example Artist", UPC="0123456789012"
    ).model_copy(update={"title": "Example Artist Example Album CD"})
    decision = decide_match(listing, [_variant(discogs_release, observed_at)])
    assert decision.outcome == "rejected"
    assert "non_vinyl_listing" in decision.conflicts


def test_non_vinyl_catalog_release_cannot_become_probable(
    search_payload, discogs_release, observed_at
):
    listing = _listing(search_payload, observed_at, Artist="Example Artist", UPC="0123456789012")
    raw = dict(discogs_release)
    raw["formats"] = [{"name": "CD", "qty": "1", "descriptions": ["Album"]}]
    decision = decide_match(listing, [_variant(raw, observed_at)])
    assert decision.outcome == "insufficient_data"
    assert "vinyl_catalog_release" in decision.missing_evidence


def test_color_conflict_blocks_pressing_decision(search_payload, discogs_release, observed_at):
    listing = _listing(
        search_payload, observed_at, Artist="Example Artist", UPC="0123456789012", Color="Orange"
    )
    decision = decide_match(listing, [_variant(discogs_release, observed_at)])
    assert decision.outcome == "family_only"
    assert "color" in decision.conflicts


def test_unofficial_catalog_release_requires_explicit_claim(
    search_payload, discogs_release, observed_at
):
    listing = _listing(search_payload, observed_at, Artist="Example Artist", UPC="0123456789012")
    raw = dict(discogs_release)
    raw["formats"] = [{"name": "Vinyl", "qty": "1", "descriptions": ["LP", "Unofficial Release"]}]
    decision = decide_match(listing, [_variant(raw, observed_at)])
    assert decision.outcome == "family_only"
    assert "unofficial_claim_missing" in decision.conflicts


def test_without_structured_artist_even_high_score_cannot_assert_family(
    search_payload, discogs_release, observed_at
):
    listing = _listing(search_payload, observed_at, UPC="0123456789012")
    decision = decide_match(listing, [_variant(discogs_release, observed_at)])
    assert decision.outcome == "insufficient_data"
    assert "structured_artist" in decision.missing_evidence


def test_conflicting_barcode_and_wrong_family_is_rejected(
    search_payload, discogs_release, observed_at
):
    listing = _listing(search_payload, observed_at, Artist="Another Artist", UPC="9999999999999")
    decision = decide_match(listing, [_variant(discogs_release, observed_at)])
    assert decision.outcome == "rejected"
    assert "barcode" in decision.conflicts


def test_empty_search_is_insufficient(search_payload, observed_at):
    decision = decide_match(_listing(search_payload, observed_at), [])
    assert decision.outcome == "insufficient_data"
    assert decision.family_ids == []


def test_copy_claim_and_pressing_fingerprint_serialize_separately(search_payload, observed_at):
    listing = _listing(search_payload, observed_at, Artist="Example Artist", UPC="0123456789012")
    fingerprint = from_listing(listing)
    attribute = CollectibleAttribute(
        kind="autograph", claimed_value="signed", source_field="seller_title"
    )
    assert VinylFingerprint.model_validate_json(fingerprint.model_dump_json()) == fingerprint
    assert CollectibleAttribute.model_validate_json(attribute.model_dump_json()) == attribute
    assert attribute.verification == "unverified"
