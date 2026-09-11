"""Conservative, deterministic candidate scoring with inspectable evidence."""

import re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher

from finder.categories.vinyl import from_listing, from_variant
from finder.domain import Listing, ListingVariantCandidate, MatchEvidence, Variant


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _normalized(value))


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
    artist = _similarity_evidence("artist", listing_vinyl.artists, variant_vinyl.artists, 15, 0.88)
    title = _similarity_evidence("title", [listing.title], [variant.title], 20, 0.52)
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
