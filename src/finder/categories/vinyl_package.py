"""Catalog-required signed inserts can be explicitly absent from a seller's offer.

This is a narrow package-completeness guard, not an autograph authenticity or
condition classifier. Positive signing words never promote a listing.
"""

import re

from finder.categories.vinyl_covers import cover_name
from finder.domain import Listing, Variant
from finder.matching import _normalized

INSERT = r"(?:signed|autographed) insert"
DENIAL = re.compile(
    rf"\b(?:(?:no|without|missing) (?:the )?{INSERT}(?: included)?"
    rf"|{INSERT} (?:is )?(?:not included|missing|absent|omitted)"
    r"|no (?:signature|autograph) included)$"
)
# A closed title grammar keeps conditional, historical, quoted and multi-item
# prose out of this high-consequence guard. Unrecognized wording must abstain.
OFFER_WORDS = frozenset(
    "vinyl lp record records disc discs disk disks new sealed damaged mint nm vg "
    "black white clear blue green pink red yellow purple gray grey gold silver "
    "orange brown opaque translucent transparent colored colour color limited "
    "edition stereo mono album inch gram grams g numbered gatefold".split()
)


def _plain_offer(text: str) -> bool:
    return all(
        word in OFFER_WORDS or word.isdigit() or re.fullmatch(r"[2-9]lp", word)
        for word in text.split()
    )


def _requires_signed_insert(target: Variant) -> bool:
    if not any(_normalized(str(fmt.get("name", ""))) == "vinyl" for fmt in target.formats):
        return False
    for fmt in target.formats:
        if _normalized(str(fmt.get("name", ""))) not in {"vinyl", "all media"}:
            continue
        text = fmt.get("text")
        if not isinstance(text, str) or re.search(r"[?\"“”‘’/+&]", text):
            continue
        # Read an explicit catalog format component, never notes, a release title,
        # a signed sleeve, or a phrase such as 'no signed insert'.
        parts = [_normalized(part) for part in text.split(",")]
        if not any(re.fullmatch(INSERT, part) for part in parts):
            continue
        own_cover = cover_name(target)
        cover_parts = (
            {
                f"{own_cover} {kind} {noun}"
                for kind in ("alternative", "alternate")
                for noun in ("cover", "artwork")
            }
            if own_cover
            else set()
        )
        if not all(
            re.fullmatch(INSERT, part) or part in cover_parts or _plain_offer(part)
            for part in parts
        ):
            continue
        return True
    return False


def missing_catalog_signed_insert(listing: Listing, target: Variant) -> bool:
    """Recognize only an unambiguous terminal title claim about the offered package.

    The caller establishes the album family first. Generic item specifics such as
    'Signed: No' can describe the record or sleeve and cannot deny a signed insert.
    Keeping the claim terminal also excludes 'not included in photos/on sleeve'.
    """
    if not _requires_signed_insert(target):
        return False
    title = listing.title
    # Quotes and choice/bundle punctuation retain their meaning before normalizing.
    if re.search(r"[?\"“”`/+&]|(?<!\w)['‘’]|['‘’](?!\w)", title):
        return False
    title = _normalized(title)
    record = re.search(r"\b(?:vinyl|record|lp|[2-9]lp)\b", title)
    if not record:
        return False
    # Only leading catalog identity can be masked. A later occurrence of an album
    # called 'Not' or 'Never' must not erase the seller's denial modifier.
    identity_text = title[: record.start()]
    identities = {
        _normalized(re.sub(r"\s+\(\d+\)$", "", value)) for value in [target.title, *target.artists]
    } - {""}
    for identity in sorted(identities, key=len, reverse=True):
        identity_text = re.sub(r"\b" + re.escape(identity) + r"\b", " ", identity_text, count=1)
    own_cover = cover_name(target)
    if own_cover:
        # A bare natural-language cover name could instead be a modifier such as
        # 'Maybe'. Require an explicit role, except for catalog-recorded codes
        # containing both letters and digits (which are not ordinary modifiers).
        identity_text = re.sub(
            r"\b" + re.escape(own_cover) + r" (?:cover|artwork)\b",
            " ",
            identity_text,
            count=1,
        )
        if (
            re.fullmatch(r"[a-z0-9]+", own_cover)
            and re.search(r"[a-z]", own_cover)
            and re.search(r"[0-9]", own_cover)
        ):
            identity_text = re.sub(
                r"\b" + re.escape(own_cover) + r"\b", " ", identity_text, count=1
            )
    title = " ".join((identity_text + " " + title[record.start() :]).split())
    denial = DENIAL.search(title)
    if not denial or not _plain_offer(title[: denial.start()]):
        return False
    return True
