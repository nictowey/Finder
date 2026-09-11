"""Conservative, deterministic candidate scoring with inspectable evidence."""

import re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher

from finder.domain import Listing, ListingVariantCandidate, MatchEvidence, Variant


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _normalized(value))


def _listing_values(listing: Listing, *names: str) -> list[str]:
    wanted = {_normalized(name) for name in names}
    values = []
    for name, candidates in listing.item_specifics.items():
        if _normalized(name) in wanted:
            values.extend(candidate for candidate in candidates if candidate)
    return values


def _variant_catalog_numbers(variant: Variant) -> list[str]:
    values = [str(label["catno"]) for label in variant.labels if label.get("catno")]
    for name, identifiers in variant.identifiers.items():
        if "catalog" in _normalized(name):
            values.extend(identifiers)
    return values


def _variant_barcodes(variant: Variant) -> list[str]:
    return [
        value
        for name, values in variant.identifiers.items()
        if "barcode" in _normalized(name)
        for value in values
    ]


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
    barcode = _exact_evidence(
        "barcode", _listing_values(listing, "UPC", "Barcode"), _variant_barcodes(variant), 60
    )
    catno = _exact_evidence(
        "catalog_number",
        _listing_values(listing, "Catalog Number", "Catalogue Number"),
        _variant_catalog_numbers(variant),
        45,
    )
    artist = _similarity_evidence(
        "artist", _listing_values(listing, "Artist"), variant.artists, 20, 0.88
    )
    title = _similarity_evidence("title", [listing.title], [variant.title], 25, 0.52)
    listing_year = _listing_values(listing, "Release Year", "Year")
    year = _exact_evidence(
        "release_year",
        listing_year,
        [str(variant.release_year)] if variant.release_year else [],
        10,
    )
    listing_format = _listing_values(listing, "Format", "Record Size", "Edition", "Color")
    variant_format = []
    for value in variant.formats:
        variant_format.extend(str(item) for item in value.values() if item)
    format_evidence = _similarity_evidence("format", listing_format, variant_format, 5, 0.75)
    for item in (barcode, catno, artist, title, year, format_evidence):
        if item is not None:
            evidence.append(item)
    score = min(100, sum(item.weight for item in evidence if item.matched))
    # A conflicting strong identifier overrides fuzzy title/artist similarity.
    if barcode is not None and not barcode.matched:
        score = min(score, 20)
    elif catno is not None and not catno.matched:
        score = min(score, 50)
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
