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
    _artist_matches_catalog,
    _color_evidence,
    _listing_color_claims,
    _non_vinyl_listing,
    _normalized,
    _palette,
    _selected_color_claims,
    score_variant,
)


class TargetReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["possible_pressing", "family_review", "conflicting", "unrelated"]
    clues: list[str]
    verify: list[str]
    alternatives_checked: int | None = None
    alternatives_not_ruled_out: int | None = None


# These words are commonly seller adjectives as well as record titles. A bare
# occurrence in a long listing title cannot establish the album identity.
WEAK_ALBUM_TITLES = {"rare"}
RELEASE_TITLE_FIELDS = {
    "release title",
    "album title",
    "titre de la version",
    "musiktitel",
    "titolo della pubblicazione",
}
RELEASE_TITLE_SUFFIXES = {
    "album",
    "vinyl",
    "record",
    "lp",
    "single",
    "edition",
    "limited",
    "remastered",
    "reissue",
    "pressing",
    "colored",
    "colour",
    "color",
}
ROMAN_TITLE_NUMBERS = {
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
}


def catalog_title_aliases(target: Variant, family: list[Variant]) -> tuple[str, ...]:
    """Use a sibling's recorded spelling, never a guessed seller-title conversion."""

    def parts(title):
        stem, _, suffix = _normalized(title).rpartition(" ")
        number = ROMAN_TITLE_NUMBERS.get(suffix, suffix)
        return (stem, number) if stem and number in ROMAN_TITLE_NUMBERS.values() else None

    def artists(variant):
        return {_normalized(re.sub(r"\s+\(\d+\)$", "", name)) for name in variant.artists}

    def vinyl(variant):
        return any(str(fmt.get("name", "")).casefold() == "vinyl" for fmt in variant.formats)

    identity = parts(target.title)
    names = artists(target)
    master = target.catalog_product_id
    if (
        not identity
        or not names
        or not master
        or master == target.catalog_variant_id
        or not vinyl(target)
    ):
        return ()
    title = _normalized(target.title)
    return tuple(
        sorted(
            {
                _normalized(row.title)
                for row in family
                if row.catalog_source == target.catalog_source
                and row.catalog_variant_id != target.catalog_variant_id
                and row.catalog_product_id == master
                and row.catalog_product_id != row.catalog_variant_id
                and artists(row) == names
                and vinyl(row)
                and _normalized(row.title) != title
                and parts(row.title) == identity
            }
        )
    )


def _release_title_matches(value: str, album: str, artists: list[str]) -> bool:
    title = _normalized(value)
    for artist in artists:
        if artist and title.startswith(f"{artist} "):
            title = title[len(artist) + 1 :]
            break
    if title == album:
        return True
    if not title.startswith(f"{album} "):
        return False
    remaining = title[len(album) + 1 :].split()
    return bool(remaining) and (
        remaining[0] in RELEASE_TITLE_SUFFIXES
        or re.fullmatch(r"(?:19|20)\d{2}", remaining[0]) is not None
    )


def _explicit_album_claim(listing: Listing, album: str, artists: list[str]) -> bool:
    """A separator marks a title claim; a bare 'rare' often describes another LP."""
    for artist in artists:
        if artist and re.search(
            rf"\b{re.escape(artist)}\s*[-:|–—]\s*{re.escape(album)}\b",
            listing.title,
            flags=re.IGNORECASE,
        ):
            return True
    return False


def _more_specific_album_claim(
    listing: Listing, target: Variant, other: Variant, album_aliases: tuple[str, ...] = ()
) -> bool:
    """A longer, competing catalog title can disambiguate a seller's album claim.

    A plain substring match would mistake a sequel or deluxe album for the shorter
    target title. Require a same-artist catalog competitor, not a guessed suffix.
    """
    target_titles = (_normalized(target.title), *album_aliases)
    other_title = _normalized(other.title)
    if not any(title and other_title.startswith(f"{title} ") for title in target_titles):
        return False
    if not any(_artist_matches_catalog(artist, target.artists) for artist in other.artists):
        return False
    structured = (
        value
        for key, values in listing.item_specifics.items()
        if _normalized(key) in RELEASE_TITLE_FIELDS
        for value in values
    )
    if any(_normalized(value) == other_title for value in structured):
        return True
    title = f" {_normalized(listing.title)} "
    claim = f" {other_title} "
    return (
        claim in title
        and f" not {other_title} " not in title
        and not any(f" {album} " in title.replace(claim, " ") for album in target_titles)
    )


def review_target_listing(
    listing: Listing, variant: Variant, *, album_aliases: tuple[str, ...] = ()
) -> TargetReview:
    """Surface incomplete seller claims without converting them to a match assertion."""
    seller = from_listing(listing)
    catalog = from_variant(variant)
    title = _normalized(listing.title)
    album = _normalized(variant.title)
    album_titles = (album, *album_aliases)
    artist_names = [_normalized(re.sub(r"\s+\(\d+\)$", "", name)) for name in variant.artists]
    artist_in_title = any(
        artist not in ("various", "various artists") and f" {artist} " in f" {title} "
        for artist in artist_names
    )
    artist_in_specifics = bool(seller.artists) and all(
        _artist_matches_catalog(value, variant.artists) for value in seller.artists
    )
    artist_conflict = bool(seller.artists and not artist_in_specifics)
    release_titles = [
        value
        for key, values in listing.item_specifics.items()
        if _normalized(key) in RELEASE_TITLE_FIELDS | {"album"}
        for value in values
        if value.strip()
    ]
    structured_album = bool(release_titles) and all(
        any(
            _release_title_matches(value, claimed_album, artist_names)
            for claimed_album in album_titles
        )
        for value in release_titles
    )
    family = bool(
        album
        and any(f" {claimed_album} " in f" {title} " for claimed_album in album_titles)
        and (artist_in_title or artist_in_specifics or artist_names == ["various"])
    )
    if _non_vinyl_listing(listing):
        return TargetReview(status="conflicting", clues=[], verify=["non_vinyl_claim"])
    if artist_conflict:
        return TargetReview(status="conflicting", clues=[], verify=["artist_conflict"])
    if family and release_titles and not structured_album:
        return TargetReview(status="conflicting", clues=[], verify=["release_title_conflict"])
    if (
        family
        and album in WEAK_ALBUM_TITLES
        and not (structured_album or _explicit_album_claim(listing, album, artist_names))
    ):
        return TargetReview(status="unrelated", clues=[], verify=["album_title_not_established"])
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
    title_colors, structured_colors = _listing_color_claims(
        listing, variant, album_aliases=album_aliases
    )
    # Structured definite claims keep their existing precedence. Unresolved
    # choices and explicit denials, however, cannot be erased by another source.
    color_ambiguous = title_colors.ambiguous or structured_colors.ambiguous
    selected_colors = _selected_color_claims(
        title_colors, structured_colors, has_structured=bool(seller.colors)
    )
    if (title_colors.denied | structured_colors.denied) & _palette(catalog.colors):
        return TargetReview(status="conflicting", clues=clues, verify=["color_conflict"])
    seller_palette = _palette(selected_colors.positive)
    color = _color_evidence([" ".join(sorted(seller_palette))], catalog.colors)
    if color is not None and not color.matched:
        return TargetReview(status="conflicting", clues=clues, verify=["color_conflict"])
    color_match = bool(color and color.matched)
    if color_match:
        clues.append("seller_color_claim")
    elif seller_palette < _palette(catalog.colors) and seller_palette:
        verify.append("color_pair_incomplete")
    elif seller_palette:
        verify.append("color_not_comparable")
    elif not color_ambiguous:
        verify.append("color_not_claimed")
    if color_ambiguous:
        verify.append("color_claim_ambiguous")

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
    possible = (
        not color_ambiguous
        and not (seller_palette and not _palette(catalog.colors))
        and (color_match or identifier_match or (catalog_numbered and seller_numbered))
        and (not catalog_numbered or seller_numbered)
    )
    return TargetReview(
        status="possible_pressing" if possible else "family_review",
        clues=clues,
        verify=verify,
    )


def review_target_with_alternatives(
    listing: Listing,
    target: Variant,
    alternatives: list[Variant] | None,
    *,
    search_incomplete: bool = True,
) -> TargetReview:
    """Expose competing explanations without hiding a sparse but useful target lead.

    A competitor with missing evidence remains unresolved. Counting zero competitors in
    this bounded search never proves unique identity or permits an exact pressing claim.
    """
    if alternatives is None:
        return review_target_listing(listing, target)
    unique = {
        row.catalog_variant_id: row
        for row in alternatives
        if row.catalog_source == target.catalog_source
        and row.catalog_variant_id != target.catalog_variant_id
        and any(str(fmt.get("name", "")).casefold() == "vinyl" for fmt in row.formats)
    }
    family = [target, *unique.values()]
    aliases = catalog_title_aliases(target, family)
    review = review_target_listing(listing, target, album_aliases=aliases)
    if review.status in ("possible_pressing", "family_review") and any(
        _more_specific_album_claim(listing, target, row, aliases) for row in unique.values()
    ):
        review = TargetReview(
            status="conflicting", clues=["artist_and_album"], verify=["competing_album_title_claim"]
        )
    # The sparse family check handles normalized artist/title spelling and also keeps
    # plausible candidates whose master grouping is absent or differs in the catalog.
    considered = [
        review_target_listing(listing, row, album_aliases=catalog_title_aliases(row, family))
        for row in unique.values()
    ]
    unresolved = sum(row.status in ("possible_pressing", "family_review") for row in considered)
    verify = [value for value in review.verify if value != "catalog_alternatives_not_checked"]
    if search_incomplete:
        verify.append("catalog_alternative_search_incomplete")
    if not unique:
        verify.append("no_competing_pressings_retrieved")
    if unresolved and review.status in ("possible_pressing", "family_review"):
        verify.append("other_pressings_not_ruled_out")
        if review.status == "possible_pressing":
            verify.append("shared_pressing_evidence")
            review = review.model_copy(update={"status": "family_review"})
    return review.model_copy(
        update={
            "verify": verify,
            "alternatives_checked": len(unique),
            "alternatives_not_ruled_out": unresolved,
        }
    )
