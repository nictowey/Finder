"""Conservative seller-reported playable-vinyl completeness, never verification.

No transport and no interpretation of seller text as instructions. Only explicitly supplied
exact-release catalog formats establish quantity; listing titles/barcodes/embedded release
summaries and absence of a missing-media keyword cannot establish a complete set.
"""

import html
import re
import unicodedata

MAX_COMMENTS = 4096
MAX_MEDIA_COUNT = 100
MAX_FORMATS = 32
MAX_RELEASE_ID = 9007199254740991

_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_NUMBER = r"(?:[1-9]\d{0,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
_MEDIA = r"(?:dis[ck]s?|records?|lps?|vinyl(?:\s+records?)?)"
_NUMBERED = rf"(?:dis[ck]|record|lp)\s*#?\s*{_NUMBER}"
_PACKAGING = (
    r"(?:original\s+)?(?:inserts?|posters?|sleeves?|covers?|booklets?|inners?|obi(?:\s+strip)?)"
)


def normalize_comments(value):
    """Return bounded display data and whether all text is supported for a positive claim.

    Mark truncation, markup, controls and non-ASCII prose unsupported. Missing-media
    evidence in the bounded text can still reject, but sanitizing must never create a pass.
    No seller text is interpolated into explanation strings or executed/rendered as HTML.
    """
    if not isinstance(value, str):
        return {"text": None, "supported": False}
    supported = 0 < len(value) <= MAX_COMMENTS
    text = value[:MAX_COMMENTS]
    decoded = html.unescape(text)
    if decoded != text or re.search(r"[<>]", decoded):
        supported = False
    text = re.sub(r"<[^>]{0,4096}>", " ", decoded)
    if any(unicodedata.category(char).startswith("C") for char in text):
        supported = False
    text = "".join(" " if unicodedata.category(char).startswith("C") else char for char in text)
    if not text.isascii():
        supported = False
    text = re.sub(r"\s+", " ", text).strip()[:MAX_COMMENTS]
    return {"text": text or None, "supported": supported and bool(text)}


def project_release_media(release_metadata, release_id):
    """Project only authoritative /releases/{id} identity and Vinyl format quantities.

    The caller must supply catalog metadata, not a seller-provided release summary.
    Mixed formats (including extra non-playable format records) are intentionally unsupported.
    Unknown, excessive or malformed quantities do not produce a partial inferred count.
    """
    if (
        type(release_id) is not int
        or not 0 < release_id <= MAX_RELEASE_ID
        or not isinstance(release_metadata, dict)
        or type(release_metadata.get("id")) is not int
        or release_metadata["id"] != release_id
    ):
        return None
    formats = release_metadata.get("formats")
    if not isinstance(formats, list) or not 1 <= len(formats) <= MAX_FORMATS:
        return None
    projected, total = [], 0
    for entry in formats:
        if not isinstance(entry, dict) or entry.get("name") != "Vinyl":
            return None
        quantity = entry.get("qty")
        if type(quantity) is int:
            number = quantity
        elif isinstance(quantity, str) and re.fullmatch(r"[1-9]\d{0,2}", quantity):
            number = int(quantity)
        else:
            return None
        if not 1 <= number <= MAX_MEDIA_COUNT:
            return None
        total += number
        if total > MAX_MEDIA_COUNT:
            return None
        projected.append({"name": "Vinyl", "qty": str(number)})
    return {"id": release_id, "formats": projected}


def _number(text):
    return _NUMBERS.get(text, int(text) if text.isdigit() else None)


def _missing_media(text, expected):
    # A denial of missing media is not evidence of missing media. It still cannot
    # qualify without a separately recognized, unambiguous completeness statement.
    checked = re.sub(
        rf"\b(?:no|not)\s+missing\s+{_MEDIA}(?:\s*{_NUMBER})?\b"
        rf"|\bno\s+{_MEDIA}(?:\s+are)?\s+missing\b",
        " ",
        text,
    )
    patterns = (
        rf"\b(?:missing|without|lacks?|absent)\s*(?:the\s+)?{_MEDIA}\s*#?\s*{_NUMBER}\b",
        rf"\b(?:missing|without|lacks?)\s+(?:a|an|one|the)\s+{_MEDIA}\b",
        rf"\bmissing\s+{_MEDIA}\b",
        rf"\bmissing\s+{_NUMBER}\s+{_MEDIA}\b",
        rf"\bnot\s+all\s+{_MEDIA}\b",
        r"\bincomplete\s+(?:vinyl|record|disc|lp)\s+set\b",
        rf"\bno\s*{_NUMBERED}\b",
        rf"\b{_NUMBERED}\s+(?:(?:is|are)\s+)?(?:missing|absent|not\s+(?:included|present|supplied))\b",
        rf"\b{_MEDIA}\s+(?:(?:is|are)\s+)?(?:missing|absent|not\s+(?:included|present|supplied))\b",
        r"\b(?:sleeve|cover)[\s-]*only\b",
        rf"\bno\s+{_MEDIA}(?=\s*(?:$|[.,;!]|included\b|present\b|supplied\b|at\s+all\b))",
        rf"\b(?:without|does\s+not\s+include)\s+(?:the\s+)?{_MEDIA}\b",
    )
    if any(re.search(pattern, checked) for pattern in patterns):
        return True
    for match in re.finditer(rf"\b({_NUMBER})\s*of\s*({_NUMBER})\b", checked):
        if _number(match[1]) < _number(match[2]):
            return True
    for match in re.finditer(rf"\b(?:only\s*({_NUMBERED})|({_NUMBERED})\s*only)\b", checked):
        number = re.search(rf"({_NUMBER})$", match[1] or match[2])
        if expected != 1 or number is None or _number(number[1]) != 1:
            return True
    if (
        expected is not None
        and expected > 1
        and re.search(
            r"\b(?:(?:a\s+|one\s+|single\s+)?(?:record|disc|disk|lp)\s*only"
            r"|only\s+(?:a\s+|one\s+|single\s+)?(?:record|disc|disk|lp))\b",
            checked,
        )
    ):
        return True
    # Explicit numeric shortages reject even alongside a contradictory 'complete' claim.
    for match in re.finditer(
        rf"\b(?:includes?|contains?|only)\s+({_NUMBER})\s+{_MEDIA}\b"
        rf"|\b({_NUMBER})\s+{_MEDIA}\s+only\b",
        checked,
    ):
        if expected is not None and _number(match[1] or match[2]) < expected:
            return True
    return False


def _positive_count(clause, expected):
    if re.fullmatch(
        r"(?:includes?\s+both\s+(?:vinyl\s+)?(?:discs|disks|records|lps)"
        r"|both\s+(?:vinyl\s+)?(?:discs|disks|records|lps)\s+(?:are\s+)?(?:included|present))",
        clause,
    ):
        return 2
    match = (
        re.fullmatch(rf"(?:includes?|contains?)\s+(?:all\s+)?({_NUMBER})\s+{_MEDIA}", clause)
        or re.fullmatch(
            rf"(?:all\s+)?({_NUMBER})\s+{_MEDIA}\s+(?:are\s+)?(?:included|present)", clause
        )
        or re.fullmatch(rf"(?:complete|full)\s+set\s+of\s+({_NUMBER})\s+{_MEDIA}", clause)
    )
    if match:
        return _number(match[1])
    if re.fullmatch(
        rf"(?:includes?\s+all\s+{_MEDIA}"
        rf"|all\s+{_MEDIA}\s+(?:are\s+)?(?:included|present)"
        r"|(?:includes?\s+(?:the\s+)?)?(?:complete|full)\s+(?:vinyl|record|disc|lp)\s+set)",
        clause,
    ):
        return expected
    if expected == 1 and re.fullmatch(r"(?:record|disc|disk|lp)\s+only", clause):
        return 1
    return None


def assess_media_completeness(
    release_id, comments, release_metadata=None, *, comments_supported=True
):
    """Classify bounded English claims conservatively; absence of a warning never passes.

    A reported_complete result means only the seller reports the expected playable vinyl
    media. It does not verify reality, condition, packaging or the physical pressing.
    Unknown clauses, quantities, languages and contradictory claims fail closed.
    """
    metadata = project_release_media(release_metadata, release_id)
    expected = sum(int(entry["qty"]) for entry in metadata["formats"]) if metadata else None
    normalized = normalize_comments(comments)
    text = (normalized["text"] or "").lower()
    packaging = bool(
        re.search(
            rf"\b(?:(?:missing|no|without)\s+{_PACKAGING}"
            rf"|{_PACKAGING}\s+(?:is\s+)?(?:missing|not\s+included)"
            r"|(?:records?|discs?|disks?|lps?|vinyl)\s+only)\b",
            text,
        )
    )
    result = {
        "status": "unknown",
        "release_id": release_id if type(release_id) is int else None,
        "expected_media_count": expected,
        "verified": False,
        "reasons": [],
        "packaging_uncertain": packaging,
    }
    if _missing_media(text, expected):
        result["status"] = "incomplete"
        result["reasons"].append(
            "Seller comments explicitly report missing or partial playable media."
        )
    elif expected is None:
        result["reasons"].append(
            "Authoritative exact-release Vinyl quantity is unavailable or unsupported."
        )
    elif not normalized["supported"] or comments_supported is not True:
        result["reasons"].append(
            "Seller comments are unavailable, truncated or unsupported for completeness."
        )
    else:
        clauses = [part.strip() for part in re.split(r"[.;,!]+", text) if part.strip()]
        positive, unknown = False, not clauses
        for clause in clauses:
            count = _positive_count(clause, expected)
            if count is not None:
                positive = True
                unknown = unknown or count != expected
                continue
            # Only small, explicit ancillary phrases may accompany a complete-set claim.
            # Arbitrary prose is not language-detected or assumed harmless.
            ancillary = re.fullmatch(
                rf"(?:(?:missing|no|without)\s+{_PACKAGING}"
                rf"|{_PACKAGING}\s+(?:is\s+)?(?:missing|not\s+included)"
                r"|(?:vg\+?|nm|m|mint|near mint)(?:\s+(?:vinyl|records?|media))?"
                rf"|no\s+skips(?:\s+on\s+(?:{_NUMBERED}|either\s+disc|both\s+discs))?)",
                clause,
            )
            if not ancillary:
                unknown = True
        if positive and not unknown:
            result["status"] = "reported_complete"
            result["reasons"].append(
                "Seller reports all expected playable vinyl media; not independently verified."
            )
        else:
            result["reasons"].append(
                "No unambiguous supported seller report of the full expected vinyl set."
            )
    if packaging:
        result["reasons"].append(
            "Optional packaging may be missing or uncertain; assessed separately from media."
        )
    return result
