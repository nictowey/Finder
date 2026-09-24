"""Per-watch pressing cheat sheets: signs of the wanted pressing and of common versions.

Suggestions come from comparing the target release with its sibling Discogs releases.
Matching reads seller text only. A found sign is a seller claim, never verification.
"""

import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finder.categories.vinyl import from_listing, from_variant, has_numbered_claim
from finder.domain import Listing, Variant
from finder.matching import _compact, _normalized, _palette

ClueKind = Literal["keyword", "color", "catalog_number", "barcode", "label", "country", "numbered"]


class Clue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    kind: ClueKind
    value: str = Field(min_length=1, max_length=80)
    required: bool = False

    def describe(self) -> str:
        return "numbered" if self.kind == "numbered" else f"{self.kind}: {self.value}"


# Catalog format descriptions that separate pressings, with common seller spellings.
EDITION_TERMS = {
    "reissue": ("reissue", "re issue", "repress", "re press"),
    "remastered": ("remastered", "remaster"),
    "180 gram": ("180 gram", "180g", "180 g", "180gm", "180 grams"),
    "200 gram": ("200 gram", "200g", "200gm"),
    "picture disc": ("picture disc", "picture disk"),
    "unofficial release": ("unofficial", "bootleg"),
    "test pressing": ("test pressing",),
    "promo": ("promo", "promotional"),
    "club edition": ("club edition", "record club"),
    "deluxe edition": ("deluxe",),
    "mono": ("mono",),
}
NEGATION = re.compile(r"\b(?:not|no|non|never|without|isnt)\s+(?:\w+\s+){0,2}$")
COLOR_FIELDS = {"color", "record color", "vinyl color", "colour", "vinyl colour"}


def _phrase_found(text: str, phrase: str) -> bool:
    """Whole-word phrase match that ignores explicitly negated mentions."""
    phrase = _normalized(phrase)
    if not phrase:
        return False
    for match in re.finditer(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text):
        if not NEGATION.search(text[: match.start()]):
            return True
    return False


def _keyword_variants(value: str) -> tuple[str, ...]:
    key = _normalized(value)
    return EDITION_TERMS.get(key, (key,))


class ListingText:
    """Seller claims prepared once per listing."""

    def __init__(self, listing: Listing, target: Variant):
        seller = from_listing(listing)
        self.fingerprint = seller
        values = [value for specific in listing.item_specifics.values() for value in specific]
        self.text = _normalized(" ".join([listing.title, *values]))
        self.compact = _compact(" ".join([listing.title, *seller.catalog_numbers]))
        self.digits = re.sub(r"\D", " ", " ".join([listing.title, *seller.barcodes]))
        self.structured_colors = [
            value
            for name, values in listing.item_specifics.items()
            if _normalized(name) in COLOR_FIELDS
            for value in values
        ]
        # Artist and album words such as "Purple Rain" are not color claims.
        remainder = f" {_normalized(listing.title)} "
        for name in [target.title, *target.artists]:
            name = _normalized(re.sub(r"\s+\(\d+\)$", "", name))
            if name:
                remainder = remainder.replace(f" {name} ", " ")
        self.palette = _palette([*self.structured_colors, remainder])
        self.structured_palette = _palette(self.structured_colors)
        self.numbered = has_numbered_claim([*seller.editions, listing.title])
        self.labels = [_normalized(value) for value in seller.labels]
        self.country = _normalized(seller.country or "")


def clue_found(clue: Clue, seen: ListingText, *, common_version: bool = False) -> bool:
    if clue.kind == "numbered":
        return seen.numbered
    if clue.kind == "color":
        wanted = _palette([clue.value])
        # A common-version color must be a structured seller claim: titles often mention
        # sleeve or label colors that say nothing about the disc.
        palette = seen.structured_palette if common_version else seen.palette
        return bool(wanted) and wanted <= palette
    if clue.kind == "catalog_number":
        value = _compact(clue.value)
        return len(value) >= 4 and value in seen.compact
    if clue.kind == "barcode":
        value = re.sub(r"\D", "", clue.value)
        return len(value) >= 8 and value in seen.digits.replace(" ", "")
    if clue.kind == "label":
        value = _normalized(clue.value)
        return bool(value) and (value in seen.labels or _phrase_found(seen.text, clue.value))
    if clue.kind == "country":
        return bool(seen.country) and seen.country == _normalized(clue.value)
    return any(_phrase_found(seen.text, term) for term in _keyword_variants(clue.value))


def apply_cheat_sheet(review: dict, listing: Listing, target: Variant, tells, anti_tells) -> dict:
    """Refine a family review with the owner's signs. Returns a new review dict."""
    tells, anti_tells = list(tells or []), list(anti_tells or [])
    if not tells and not anti_tells:
        return review
    seen = ListingText(listing, target)
    found = [clue for clue in tells if clue_found(clue, seen)]
    missing = [clue for clue in tells if clue.required and clue not in found]
    common = [clue for clue in anti_tells if clue_found(clue, seen, common_version=True)]
    status = review["status"]
    clues, verify = list(review["clues"]), list(review["verify"])
    if status in ("possible_pressing", "family_review"):
        if common:
            status = "conflicting"
            verify.append("common_version_sign")
        elif any(clue.required for clue in tells):
            status = "family_review" if missing else "possible_pressing"
    if found:
        clues.append("cheat_sheet_sign")
    if missing:
        verify.append("required_sign_not_claimed")
    return {
        **review,
        "status": status,
        "clues": clues,
        "verify": verify,
        "signs": [clue.describe() for clue in found],
        "missing_signs": [clue.describe() for clue in missing],
        "common_signs": [clue.describe() for clue in common],
    }


def _edition_terms(variant: Variant) -> set[str]:
    words = " ".join(_normalized(value) for value in from_variant(variant).format_descriptions)
    return {
        term
        for term, spellings in EDITION_TERMS.items()
        if any(re.search(rf"\b{re.escape(s)}\b", words) for s in spellings)
    }


def _facts(variant: Variant) -> dict[str, set[str]]:
    fingerprint = from_variant(variant)
    colors = {value for value in fingerprint.colors if _palette([value])}
    return {
        "color": colors,
        "catalog_number": {
            value for value in fingerprint.catalog_numbers if len(_compact(value)) >= 4
        },
        "barcode": {
            digits for value in fingerprint.barcodes if len(digits := re.sub(r"\D", "", value)) >= 8
        },
        "label": set(fingerprint.labels),
        "country": {fingerprint.country} if fingerprint.country else set(),
        "keyword": _edition_terms(variant),
        "numbered": {"numbered"} if has_numbered_claim(fingerprint.editions) else set(),
    }


def _shared(kind: str, value: str, facts: dict[str, set[str]]) -> bool:
    if kind == "color":
        wanted = _palette([value])
        return any(wanted <= _palette([other]) for other in facts["color"])
    if kind in ("catalog_number", "label", "country"):
        return _compact(value) in {_compact(other) for other in facts[kind]}
    return value in facts[kind]


def suggest_clues(target: Variant, siblings: list[Variant], *, partial: bool) -> dict:
    """Propose signs that separate the target from the sibling releases retrieved.

    A sign absent from every retrieved sibling is only distinctive within that sample;
    a partial version list is reported so the owner can judge it.
    """
    mine = _facts(target)
    others = [_facts(sibling) for sibling in siblings]
    tells = []
    for kind in ("color", "numbered", "catalog_number", "barcode", "label", "keyword", "country"):
        for value in sorted(mine[kind]):
            if any(_shared(kind, value, facts) for facts in others):
                continue
            tells.append(
                {
                    "kind": kind,
                    "value": value,
                    "required": kind in ("color", "numbered"),
                    "siblings_sharing": 0,
                }
            )
    counts: Counter[tuple[str, str]] = Counter()
    for facts in others:
        for kind in ("keyword", "label", "catalog_number", "color", "barcode", "numbered"):
            for value in facts[kind]:
                if not _shared(kind, value, mine):
                    counts[(kind, value)] += 1
    anti = [
        {"kind": kind, "value": value, "required": False, "siblings_sharing": count}
        for (kind, value), count in sorted(counts.items(), key=lambda row: (-row[1], row[0]))
    ]
    return {
        "tells": tells[:8],
        "anti_tells": anti[:10],
        "siblings_compared": len(siblings),
        "partial": partial,
    }
