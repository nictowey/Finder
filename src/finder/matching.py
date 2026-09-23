"""Conservative, deterministic candidate scoring with inspectable evidence."""

import re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher

from finder.categories.vinyl import from_listing, from_variant
from finder.domain import (
    EvidenceRecord,
    Listing,
    ListingVariantCandidate,
    MatchDecision,
    MatchEvidence,
    Variant,
)

MATCH_POLICY_VERSION = "vinyl-decision-v4"


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _normalized(value))


def _album_title_in_listing(listing_title: str, catalog_title: str) -> bool:
    """A whole catalog title may appear inside a seller's much longer title."""
    album = _normalized(catalog_title)
    return bool(album and f" {album} " in f" {_normalized(listing_title)} ")


def _artist_evidence(listing_values: list[str], catalog_values: list[str]) -> MatchEvidence | None:
    # Discogs' parenthesized numeric suffix disambiguates artists within its database;
    # it is not part of the name sellers generally use.
    cleaned = [re.sub(r"\s+\(\d+\)$", "", value) for value in catalog_values]
    measured = _similarity_evidence("artist", listing_values, cleaned, 15, 0.88)
    return (
        measured.model_copy(update={"variant_values": catalog_values})
        if measured is not None
        else None
    )


def _non_vinyl_listing(listing: Listing) -> bool:
    """Treat a seller's explicit other-medium claim as a category conflict."""
    structured = any(
        re.search(r"\b(?:cd|compact disc|cassette|dvd|digital)\b", _normalized(value))
        for value in from_listing(listing).format_descriptions
    )
    title = bool(
        re.search(
            r"(?<![/a-z])cd\b|\b(?:compact disc|cassette|dvd|digital download)\b",
            listing.title.lower(),
        )
    )
    return structured or title


def _vinyl_release(variant: Variant) -> bool:
    return any(_normalized(str(item.get("name", ""))) == "vinyl" for item in variant.formats)


def _catalog_numbered(variant: Variant) -> bool:
    return any(
        re.search(r"\bnumbered\b", value.lower()) for value in from_variant(variant).editions
    )


def _seller_structured_numbered(listing: Listing) -> bool:
    # A title or a bare serial number is a weaker, unverified seller claim. Only explicit
    # item specifics make a numbered catalog variant plausible; neither proves the copy.
    return any(
        re.search(r"\bnumbered\b", value.lower()) for value in from_listing(listing).editions
    )


def _exact_evidence(
    field: str, listing_values: list[str], variant_values: list[str], weight: int
) -> MatchEvidence | None:
    if not listing_values or not variant_values:
        return None
    left, right = (
        {_compact(value) for value in listing_values},
        {_compact(value) for value in variant_values},
    )
    left.discard("")
    right.discard("")
    return MatchEvidence(
        field=field,
        listing_values=listing_values,
        variant_values=variant_values,
        matched=bool(left & right),
        weight=weight,
    )


def _similarity_evidence(
    field: str, listing_values: list[str], variant_values: list[str], weight: int, threshold: float
) -> MatchEvidence | None:
    if not listing_values or not variant_values:
        return None
    ratio = max(
        SequenceMatcher(None, _normalized(left), _normalized(right)).ratio()
        for left in listing_values
        for right in variant_values
    )
    return MatchEvidence(
        field=field,
        listing_values=listing_values,
        variant_values=variant_values,
        matched=ratio >= threshold,
        weight=weight,
    )


def score_variant(
    listing: Listing, variant: Variant, observed_at: datetime | None = None
) -> ListingVariantCandidate:
    evidence = []
    listing_vinyl = from_listing(listing)
    variant_vinyl = from_variant(variant)
    barcode = _exact_evidence("barcode", listing_vinyl.barcodes, variant_vinyl.barcodes, 55)
    catno = _exact_evidence(
        "catalog_number",
        listing_vinyl.catalog_numbers,
        variant_vinyl.catalog_numbers,
        40,
    )
    artist = _artist_evidence(listing_vinyl.artists, variant_vinyl.artists)
    title = _similarity_evidence("title", [listing.title], [variant.title], 20, 0.52)
    if title is not None and _album_title_in_listing(listing.title, variant.title):
        title = title.model_copy(update={"matched": True})
    year = _exact_evidence(
        "release_year",
        [str(value) for value in listing_vinyl.release_years],
        [str(value) for value in variant_vinyl.release_years],
        10,
    )
    format_evidence = _similarity_evidence(
        "format",
        [*listing_vinyl.format_descriptions, *listing_vinyl.record_sizes, *listing_vinyl.speeds],
        variant_vinyl.format_descriptions,
        5,
        0.75,
    )
    color = _similarity_evidence("color", listing_vinyl.colors, variant_vinyl.colors, 15, 0.72)
    edition = _similarity_evidence(
        "edition", listing_vinyl.editions, variant_vinyl.editions, 10, 0.72
    )
    country = _exact_evidence(
        "country",
        [listing_vinyl.country] if listing_vinyl.country else [],
        [variant_vinyl.country] if variant_vinyl.country else [],
        5,
    )
    for item in (barcode, catno, artist, title, year, format_evidence, color, edition, country):
        if item is not None:
            evidence.append(item)
    score = min(100, sum(item.weight for item in evidence if item.matched))
    # A conflicting strong identifier overrides fuzzy title/artist similarity.
    if barcode is not None and not barcode.matched:
        score = min(score, 20)
    elif catno is not None and not catno.matched:
        score = min(score, 50)
    if color is not None and not color.matched:
        score = min(score, 65)
    if edition is not None and not edition.matched:
        score = min(score, 65)
    status = "strong_candidate" if score >= 70 else "candidate" if score >= 30 else "rejected"
    return ListingVariantCandidate(
        marketplace=listing.marketplace,
        marketplace_item_id=listing.marketplace_item_id,
        catalog_source=variant.catalog_source,
        catalog_variant_id=variant.catalog_variant_id,
        score=score,
        status=status,
        evidence=evidence,
        observed_at=(observed_at or datetime.now(UTC)),
    )


def rank_variants(
    listing: Listing, variants: list[Variant], observed_at: datetime | None = None
) -> list[ListingVariantCandidate]:
    """Rank candidates deterministically without converting candidates into asserted matches."""
    return sorted(
        (score_variant(listing, variant, observed_at) for variant in variants),
        key=lambda candidate: (-candidate.score, candidate.catalog_variant_id),
    )


def decide_match(
    listing: Listing, variants: list[Variant], *, retrieval_incomplete: bool = False
) -> MatchDecision:
    """Distinguish an album family from a pressing without claiming unmeasured exact accuracy.

    A bounded catalog search cannot establish that every pressing was considered. Even a
    single strong candidate is therefore only probable until labeled live-data gates pass.
    """
    ranked = rank_variants(listing, variants)
    seller_numbered = _seller_structured_numbered(listing)
    by_id = {variant.catalog_variant_id: variant for variant in variants}
    family_candidates = []
    for candidate in ranked:
        variant = by_id[candidate.catalog_variant_id]
        fields = {item.field: item for item in candidate.evidence}
        # The scorer's broad title similarity alone is insufficient for a family decision.
        title_in_listing = _album_title_in_listing(listing.title, variant.title)
        if (
            fields.get("artist")
            and fields["artist"].matched
            and title_in_listing
            and _vinyl_release(variant)
        ):
            family_candidates.append(candidate)

    families = sorted(
        {by_id[item.catalog_variant_id].catalog_product_id for item in family_candidates}
    )
    competing = []
    if len(families) == 1:
        for candidate in family_candidates:
            fields = {item.field: item for item in candidate.evidence}
            variant = by_id[candidate.catalog_variant_id]
            identifier = any(
                fields.get(field) and fields[field].matched
                for field in ("barcode", "catalog_number")
            )
            conflict = any(
                fields.get(field) and not fields[field].matched
                for field in ("barcode", "catalog_number", "color", "edition", "country")
            )
            # A catalog-marked unofficial release needs an explicit seller claim before
            # it can even become a probable pressing. This is not authenticity proof.
            unofficial = any(
                "unofficial" in value.lower() for value in from_variant(variant).editions
            )
            seller_unofficial = any(
                "unofficial" in value.lower() for value in from_listing(listing).editions
            )
            conflict = conflict or (unofficial and not seller_unofficial)
            # Shared barcodes and colors do not establish a limited numbered edition.
            # An explicit seller item-specific is necessary but not authenticity proof.
            conflict = conflict or (_catalog_numbered(variant) and not seller_numbered)
            if candidate.status == "strong_candidate" and identifier and not conflict:
                competing.append(candidate)

    if _non_vinyl_listing(listing):
        outcome = "rejected"
    elif len(families) > 1 or len(competing) > 1:
        outcome = "ambiguous"
    elif len(competing) == 1:
        outcome = "family_only" if retrieval_incomplete else "probable_variant"
    elif len(families) == 1:
        outcome = "family_only"
    elif any(
        candidate.status == "rejected"
        and any(item.field == "barcode" and not item.matched for item in candidate.evidence)
        for candidate in ranked
    ):
        outcome = "rejected"
    else:
        outcome = "insufficient_data"

    representative = (competing or family_candidates or ranked)[:2]
    conflicts = sorted(
        {
            item.field
            for candidate in representative
            for item in candidate.evidence
            # Fuzzy title and format misses are not evidence of a different pressing.
            if not item.matched
            and item.field in ("artist", "barcode", "catalog_number", "color", "edition", "country")
        }
    )
    if _non_vinyl_listing(listing):
        conflicts.append("non_vinyl_listing")
    if any(
        "unofficial" in value.lower()
        for candidate in representative
        for value in from_variant(by_id[candidate.catalog_variant_id]).editions
    ) and not any("unofficial" in value.lower() for value in from_listing(listing).editions):
        conflicts.append("unofficial_claim_missing")
    evidence = []
    for candidate in representative:
        variant = by_id[candidate.catalog_variant_id]
        for item in candidate.evidence:
            for value in item.listing_values:
                record = EvidenceRecord(
                    field=item.field,
                    value=value,
                    source="marketplace_listing",
                    source_id=listing.marketplace_item_id,
                    method="seller_title" if item.field == "title" else "seller_structured",
                    reliability_class="seller_claim",
                    observed_at=listing.last_observed_at,
                )
                if record not in evidence:
                    evidence.append(record)
            for value in item.variant_values:
                record = EvidenceRecord(
                    field=item.field,
                    value=value,
                    source="catalog_release",
                    source_id=variant.catalog_variant_id,
                    method="catalog_structured",
                    reliability_class="catalog_metadata",
                    observed_at=variant.observed_at,
                )
                if record not in evidence:
                    evidence.append(record)

    missing = []
    if not from_listing(listing).artists:
        missing.append("structured_artist")
    if not any(
        item.field in ("barcode", "catalog_number") and item.listing_values
        for candidate in ranked
        for item in candidate.evidence
    ):
        missing.append("pressing_identifier")
    if outcome != "probable_variant":
        missing.append("unambiguous_pressing_evidence")
    if retrieval_incomplete:
        missing.append("catalog_search_incomplete")
    if not seller_numbered and any(
        _catalog_numbered(by_id[candidate.catalog_variant_id]) for candidate in representative
    ):
        missing.append("numbered_structured_claim_missing")
    if ranked and not any(_vinyl_release(variant) for variant in variants):
        missing.append("vinyl_catalog_release")
    return MatchDecision(
        outcome=outcome,
        policy_version=MATCH_POLICY_VERSION,
        marketplace=listing.marketplace,
        marketplace_item_id=listing.marketplace_item_id,
        catalog_source=variants[0].catalog_source if variants else "unknown",
        family_ids=families,
        candidate_ids=[item.catalog_variant_id for item in (competing or family_candidates)],
        conflicts=conflicts,
        missing_evidence=missing,
        evidence=evidence,
    )
