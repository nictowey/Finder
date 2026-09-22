"""Policy outcomes must abstain when catalog coverage or pressing evidence is weak."""

from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.categories.vinyl import CollectibleAttribute, VinylFingerprint, from_listing
from finder.domain import MatchDecision
from finder.matching import MATCH_POLICY_VERSION, decide_match


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
