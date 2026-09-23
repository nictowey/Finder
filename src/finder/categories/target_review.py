"""Private, target-aware review of sparse marketplace listings.

Only a selected catalog release is checked here. A plausible row is a lead for
human inspection, never a verified pressing or an estimate of market value.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from finder.categories.vinyl import from_listing, from_variant, has_numbered_claim
from finder.domain import Listing, Variant
from finder.matching import (
    _color_evidence,
    _non_vinyl_listing,
    _normalized,
    _palette,
    score_variant,
)


class TargetReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["possible_pressing", "family_review", "conflicting", "unrelated"]
    clues: list[str]
    verify: list[str]


def review_target_listing(listing: Listing, variant: Variant) -> TargetReview:
    """Surface incomplete seller claims without converting them to a match assertion."""
    seller = from_listing(listing)
    catalog = from_variant(variant)
    title = _normalized(listing.title)
    album = _normalized(variant.title)
    artist_names = [_normalized(re.sub(r"\s+\(\d+\)$", "", name)) for name in variant.artists]
    artist_in_title = any(
        artist not in ("various", "various artists") and f" {artist} " in f" {title} "
        for artist in artist_names
    )
    artist_in_specifics = any(_normalized(value) in artist_names for value in seller.artists)
    artist_conflict = bool(seller.artists and not artist_in_specifics)
    family = bool(
        album
        and f" {album} " in f" {title} "
        and (artist_in_title or artist_in_specifics or artist_names == ["various"])
    )
    if _non_vinyl_listing(listing):
        return TargetReview(status="conflicting", clues=[], verify=["non_vinyl_claim"])
    if artist_conflict:
        return TargetReview(status="conflicting", clues=[], verify=["artist_conflict"])
    if not family:
        return TargetReview(status="unrelated", clues=[], verify=["album_family_unconfirmed"])

    candidate = score_variant(listing, variant)
    fields = {evidence.field: evidence for evidence in candidate.evidence}
    conflicts = [
        field
        for field in ("barcode", "catalog_number")
        if field in fields and not fields[field].matched
    ]
    if conflicts:
        return TargetReview(status="conflicting", clues=["artist_and_album"], verify=conflicts)

    clues = ["artist_and_album"]
    verify = ["catalog_alternatives_not_checked"]
    # Remove the album and artist before interpreting color words in a title.
    remainder = title.replace(album, " ", 1)
    for artist in artist_names:
        if artist and artist not in ("various", "various artists"):
            remainder = remainder.replace(artist, " ", 1)
    title_colors = _palette([remainder])
    # Structured color is more useful than title copy. When it is present, a
    # partial pair or an explicit different color remains a conflict.
    color_claims = seller.colors or ([" ".join(sorted(title_colors))] if title_colors else [])
    color = _color_evidence(color_claims, catalog.colors)
    if color is not None and not color.matched:
        return TargetReview(status="conflicting", clues=clues, verify=["color_conflict"])
    color_match = bool(color and color.matched)
    if color_match:
        clues.append("seller_color_claim")
    elif _palette(color_claims) < _palette(catalog.colors) and _palette(color_claims):
        verify.append("color_pair_incomplete")
    else:
        verify.append("color_not_claimed")

    catalog_numbered = has_numbered_claim(catalog.editions)
    seller_numbered = has_numbered_claim([*seller.editions, listing.title])
    if catalog_numbered and seller_numbered:
        clues.append("seller_numbered_claim")
    elif catalog_numbered:
        verify.append("numbered_copy_unconfirmed")
    if "barcode" not in fields and "catalog_number" not in fields:
        verify.append("pressing_identifier_absent")
    if "edition" in fields and not fields["edition"].matched:
        verify.append("edition_needs_review")
    identifier_match = any(
        fields.get(field) and fields[field].matched for field in ("barcode", "catalog_number")
    )
    if identifier_match:
        clues.append("seller_identifier_claim")
    # Numbering is a copy claim. It can bring a listing to review without
    # proving the specific numbered copy is genuine.
    possible = (color_match or identifier_match or (catalog_numbered and seller_numbered)) and (
        not catalog_numbered or seller_numbered
    )
    return TargetReview(
        status="possible_pressing" if possible else "family_review",
        clues=clues,
        verify=verify,
    )
