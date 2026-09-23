"""Vinyl identity extraction without coupling the core listing model to records."""

import re
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finder.domain import Listing, Variant


def _key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _unique(values: Iterable[str]) -> list[str]:
    result = []
    for value in values:
        value = value.strip()
        if value and value not in result:
            result.append(value)
    return result


def _specifics(listing: Listing, *names: str) -> list[str]:
    wanted = {_key(name) for name in names}
    return _unique(
        value
        for name, values in listing.item_specifics.items()
        if _key(name) in wanted
        for value in values
    )


def _identifier_values(variant: Variant, text: str) -> list[str]:
    return _unique(
        value
        for name, values in variant.identifiers.items()
        if text in _key(name)
        for value in values
    )


def has_numbered_claim(values: Iterable[str]) -> bool:
    """Detect a positive numbered-edition claim, abstaining on explicit negation."""
    values = list(values)
    negated = re.compile(
        r"\b(?:not|no|non)\W+(?:(?:an?|individually|hand)\W+)?numbered\b"
        r"|\bwithout\W+(?:(?:a|copy)\W+)?number(?:ing)?\b",
        re.IGNORECASE,
    )
    if any(negated.search(value) for value in values):
        return False
    return any(re.search(r"\bnumbered\b", value, re.IGNORECASE) for value in values)


class VinylFingerprint(BaseModel):
    """Evidence about a manufactured pressing, separate from the offered copy."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artists: list[str] = Field(default_factory=list)
    title: str | None = None
    release_years: list[int] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    catalog_numbers: list[str] = Field(default_factory=list)
    barcodes: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    editions: list[str] = Field(default_factory=list)
    country: str | None = None
    format_descriptions: list[str] = Field(default_factory=list)
    record_sizes: list[str] = Field(default_factory=list)
    speeds: list[str] = Field(default_factory=list)
    matrix_runouts: list[str] = Field(default_factory=list)


class CollectibleAttribute(BaseModel):
    """A seller's copy-level claim; a claim is never proof of authenticity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["autograph", "numbered", "sealed", "obi", "insert", "hype_sticker"]
    claimed_value: str
    source_field: str
    verification: Literal["unverified", "verified", "rejected"] = "unverified"


# Keep the original public name for callers that use the existing extractor.
VinylMetadata = VinylFingerprint


def from_listing(listing: Listing) -> VinylFingerprint:
    years = []
    for value in _specifics(listing, "Release Year", "Year"):
        match = re.search(r"\b(?:19|20)\d{2}\b", value)
        if match:
            years.append(int(match.group()))
    return VinylFingerprint(
        artists=_specifics(listing, "Artist"),
        title=listing.title,
        release_years=list(dict.fromkeys(years)),
        labels=_specifics(listing, "Record Label", "Label"),
        catalog_numbers=_specifics(listing, "Catalog Number", "Catalogue Number", "Cat No"),
        barcodes=_specifics(listing, "UPC", "EAN", "Barcode"),
        colors=_specifics(listing, "Color", "Record Color", "Vinyl Color"),
        editions=_specifics(listing, "Edition", "Features"),
        country=next(iter(_specifics(listing, "Country", "Country/Region of Manufacture")), None),
        format_descriptions=_specifics(listing, "Format"),
        record_sizes=_specifics(listing, "Record Size", "Size"),
        speeds=_specifics(listing, "Speed"),
        matrix_runouts=_specifics(listing, "Matrix / Runout", "Matrix Number", "Runout"),
    )


def from_variant(variant: Variant) -> VinylFingerprint:
    descriptions = []
    format_text = []
    for value in variant.formats:
        name = value.get("name")
        if isinstance(name, str):
            descriptions.append(name)
        raw_descriptions = value.get("descriptions")
        if isinstance(raw_descriptions, list):
            descriptions.extend(item for item in raw_descriptions if isinstance(item, str))
        text = value.get("text")
        if isinstance(text, str):
            format_text.append(text)
    edition_terms = (
        "edition",
        "reissue",
        "repress",
        "remaster",
        "promo",
        "unofficial",
        "test pressing",
        "pressing",
        "numbered",
    )
    return VinylFingerprint(
        artists=variant.artists,
        title=variant.title,
        release_years=[variant.release_year] if variant.release_year else [],
        labels=_unique(
            str(label["name"]) for label in variant.labels if isinstance(label.get("name"), str)
        ),
        catalog_numbers=_unique(
            str(label["catno"]) for label in variant.labels if label.get("catno")
        ),
        barcodes=_identifier_values(variant, "barcode"),
        colors=_unique(format_text),
        editions=_unique(
            value
            for value in [*descriptions, *format_text]
            if any(term in value.lower() for term in edition_terms)
        ),
        country=variant.country,
        format_descriptions=_unique([*descriptions, *format_text]),
        record_sizes=_unique(value for value in descriptions if '"' in value),
        speeds=_unique(value for value in descriptions if "rpm" in value.lower()),
        matrix_runouts=_identifier_values(variant, "matrix runout"),
    )
