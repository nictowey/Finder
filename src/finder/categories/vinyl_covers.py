"""Compare catalog-defined cover identities without inventing seller evidence.

Names come only from explicit catalog format metadata for the same album family.
A different named cover is a seller claim, never physical-copy verification.
"""

import re
from difflib import SequenceMatcher

from finder.categories.vinyl import from_listing, from_variant
from finder.domain import Listing, Variant
from finder.matching import _artist_matches_catalog, _compact, _normalized, _palette

MARKER = r"(?:alternative|alternate) (?:cover|artwork)"
UNSAFE = frozenset(
    "not no non never without neither isnt arent dont doesnt cannot cant maybe "
    "possibly perhaps unsure unknown choose choice choices select selection option "
    "options or nor and with including includes included plus bundle bundled lot set "
    "pair both versus vs comparison compare compared unlike except exclude excludes "
    "excluded excluding instead missing absent minus omitted unavailable reference "
    "illustration illustrative sample mockup preview similar featuring song songs "
    "track tracks buy buying purchase purchases order orders payment payments pay "
    "paying accept accepts accepted alongside other another additional bonus feat ft "
    "produced producer production remix remixed remixer tribute inspired guest "
    "appearance aka lyrics poster sticker print photo sleeve replacement only".split()
)
NON_NAME = frozenset(
    "signed insert numbered limited edition stereo mono album vinyl lp record cover "
    "artwork alternative alternate black white clear blue green pink red yellow "
    "purple gray grey gold silver orange brown opaque translucent transparent colored "
    "colour color repress reissue remastered not no without or and the a an none "
    "unknown unspecified unnamed default standard regular generic various".split()
)
ACCESSORIES = frozenset(
    "insert inserts poster posters sticker stickers print prints photo photos photograph "
    "photographs sleeve sleeves jacket jackets only replacement".split()
)
COVER_CONTEXT = frozenset({"cover", "artwork", "edition", "variant", "version"})
CREDIT_BEFORE = frozenset({"music", "label", "publisher", "artist", "band", "by"})
CREDIT_AFTER = frozenset(
    "music records recordings label publisher publishing entertainment distribution "
    "company corporation incorporated artist band".split()
)


def cover_name(variant: Variant) -> str | None:
    names = set()
    for fmt in variant.formats:
        if _normalized(str(fmt.get("name", ""))) not in {"vinyl", "all media"}:
            continue
        text = fmt.get("text")
        if not isinstance(text, str):
            continue
        parts = text.split(",")
        for index, raw in enumerate(parts):
            normal = _normalized(raw)
            match = re.fullmatch(r"(.+?) " + MARKER, normal)
            name = match.group(1) if match else None
            source = raw
            if (
                normal
                in {
                    "alternative cover",
                    "alternate cover",
                    "alternative artwork",
                    "alternate artwork",
                }
                and index
            ):
                source = parts[index - 1]
                name = _normalized(source)
            if not name or re.search(r"[&/+;?]", source):
                continue
            name = name.removeprefix("the ")
            words = name.split()
            if (
                1 <= len(words) <= 5
                and 3 <= len(name) <= 60
                and not name.replace(" ", "").isdigit()
                and not NON_NAME.intersection(words)
            ):
                names.add(name)
    return next(iter(names)) if len(names) == 1 else None


def _vinyl(v: Variant) -> bool:
    return any(_normalized(str(f.get("name", ""))) == "vinyl" for f in v.formats)


def _artists(v: Variant) -> set[str]:
    return {_normalized(re.sub(r"\s+\(\d+\)$", "", a)) for a in v.artists} - {""}


def _compatible(target: Variant, other: Variant) -> bool:
    return bool(
        target.catalog_product_id
        and target.catalog_product_id != target.catalog_variant_id
        and other.catalog_product_id == target.catalog_product_id
        and other.catalog_variant_id != target.catalog_variant_id
        and other.catalog_source == target.catalog_source
        and _artists(target)
        and _artists(other) == _artists(target)
        and _normalized(target.title)
        and _normalized(target.title) == _normalized(other.title)
        and _vinyl(target)
        and _vinyl(other)
    )


def _contains(text: str, phrase: str) -> bool:
    return (
        bool(phrase)
        and re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None
    )


def _near_target(text: str, name: str) -> bool:
    if _contains(text, name):
        return True
    # Fuzzy matching may only cause abstention, never identify a cover or a conflict.
    n = len(name.split())
    words = text.split()
    for i in range(len(words) - n + 1):
        candidate = " ".join(words[i : i + n])
        if len(name) >= 4 and abs(len(candidate) - len(name)) <= 1:
            ratio = SequenceMatcher(None, name, candidate).ratio()
            if ratio >= (len(name) - 1) / len(name):
                return True
    return False


def _has_noncredit_claim(title: str, name: str, role_phrases: set[str]) -> bool:
    """Require a cover mention outside an explicit label/company/artist role."""
    for mention in re.finditer(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", title):
        before = title[: mention.start()]
        preceding = before.split()
        following = title[mention.end() :].split()
        if (preceding and preceding[-1] in CREDIT_BEFORE) or (
            following and following[0] in CREDIT_AFTER
        ):
            continue
        explicit_cover = bool(following and following[0] in COVER_CONTEXT)
        if not explicit_cover and any(
            role.start() <= mention.start() and mention.end() <= role.end()
            for phrase in role_phrases
            for role in re.finditer(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", title)
        ):
            continue
        # A second accessory mention cannot rescue a credited name. Read original
        # title context: masking an album called Music must not erase Music Group.
        if re.search(r"\bphotographs? of (?:the )?$", before):
            continue
        if explicit_cover:
            following = following[1:]
        if following[:2] == ["sold", "separately"]:
            continue
        if following and following[0] == "signed":
            following = following[1:]
        if following and following[0] in ACCESSORIES:
            continue
        return True
    return False


def conflicting_catalog_cover(
    listing: Listing, target: Variant, alternatives: list[Variant]
) -> str | None:
    """Return a conflicting catalog cover name only for an explicit scoped claim."""
    own = cover_name(target)
    if not own or not _vinyl(target):
        return None
    catalog_names = {cover_name(v) for v in alternatives if _compatible(target, v)} - {None, own}
    if not catalog_names:
        return None
    title = _normalized(listing.title)
    album = _normalized(target.title)
    seller = from_listing(listing)
    if not _contains(title, album):
        return None
    if not (
        any(_contains(title, a) for a in _artists(target))
        or (
            seller.artists
            and all(_artist_matches_catalog(a, target.artists) for a in seller.artists)
        )
    ):
        return None
    # Catalog artist/title words are identity context, not cover claims or negators.
    masked = " " + title + " "
    for phrase in sorted({album, *_artists(target)}, key=len, reverse=True):
        masked = re.sub(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", " ", masked)
    masked = " ".join(masked.split())
    if UNSAFE.intersection(masked.split()) or re.search(r"[?+&/]", listing.title):
        return None
    if not re.search(r"\b(?:vinyl|record|lp|[2-9]lp)\b", masked):
        return None
    if any(
        _near_target(_normalized(raw), own)
        for raw in [
            listing.title,
            *(x for values in listing.item_specifics.values() for x in values),
        ]
    ):
        return None
    # Exact target identifiers are contradictory support, so retain review rather
    # than rejecting on cover wording. Identifiers never prove physical identity.
    catalog = from_variant(target)
    matching_barcode = {_compact(x) for x in seller.barcodes} & {
        _compact(x) for x in catalog.barcodes
    }
    matching_catalog = {_compact(x) for x in seller.catalog_numbers} & {
        _compact(x) for x in catalog.catalog_numbers
    }
    if matching_barcode or matching_catalog:
        return None
    # A full claimed target palette is another independent reason to abstain.
    wanted = _palette(catalog.colors)
    if wanted and _palette(seller.colors) == wanted:
        return None
    matched = set()
    for name in catalog_names:
        if any(_contains(identity, name) for identity in {album, *_artists(target)}):
            continue
        # Names come from the same album's explicit catalog cover/artwork field,
        # never an owner-label blacklist or arbitrary keyword dictionary.
        for mention in re.finditer(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", masked):
            # A pictured or separately offered cover does not identify the record
            # in the offer. Keep these role checks adjacent to the catalog name;
            # a record itself being sold separately is not a cover-only claim.
            preceding = masked[: mention.start()]
            if re.search(r"\bphotographs? of (?:the )?$", preceding):
                return None
            following = masked[mention.end() :].split()
            if following and following[0] in COVER_CONTEXT:
                following = following[1:]
            if following[:2] == ["sold", "separately"]:
                return None
            if following and following[0] == "signed":
                following = following[1:]
            if following and following[0] in ACCESSORIES:
                continue
            matched.add(name)
    # Preserve the original ambiguity gate before applying the role guard. Removing
    # a credit must never turn multiple named covers into a new rejection.
    if len(matched) != 1:
        return None
    name = next(iter(matched))
    roles = {_normalized(value) for value in [*seller.labels, *seller.artists]} - {""}
    return name if _has_noncredit_claim(title, name, roles) else None
