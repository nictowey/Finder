"""Conservative seller-text media damage check; never a physical inspection."""

import re

from finder.discogs_completeness import _unsupported_language, normalize_comments

_MEDIA = r"(?:record|disc|disk|lp|vinyl|groove|side\s+[a-d1-4])s?"
_PACKAGING = r"(?:record\s+)?(?:sleeve|cover|jacket|case|insert|booklet|poster|obi)"
_DAMAGE = (
    r"(?:warp(?:ed|ing|s)?|edge[ -]?warp(?:ed)?|dish(?:ed|ing)?|"
    r"crack(?:ed|s)?|brok(?:en|e)|chip(?:ped|s)?|"
    r"skip(?:s|ped|ping)?|jump(?:s|ed|ing)?|stuck\s+grooves?|"
    r"scratch(?:ed|es|ing)?|scuff(?:ed|s|ing)?|hairlines?|"
    r"goug(?:e|es|ed|ing)|surface\s+marks?|"
    r"distort(?:ed|ion)|surface\s+noise|crackle|pops?)"
)
_UNCERTAIN = re.compile(
    r"\b(?:may|might|maybe|possibly|perhaps|probably|unsure|uncertain|"
    r"not\s+sure|cannot\s+confirm|can't\s+confirm|un(?:tested|checked))\b"
)
_NEGATIONS = (
    r"\b(?:no|not)\s+(?:scratches?|scuffs?|skips?|warps?|cracks?)\s+or\s+"
    r"(?:scratches?|scuffs?|skips?|warps?|cracks?)\b",
    r"\b(?:no|not|never|without)\s+(?:(?:audible|loud|heavy|surface)\s+)*"
    r"(?:skips?|skipping|jumps?|jumping|warps?|warped|cracks?|cracked|"
    r"warping|scratches?|scratched|scuffs?|scuffed|chips?|chipped|"
    r"noise|distortion|pops?|crackle)\b",
    r"\b(?:does\s+not|doesn't|won't|will\s+not)\s+(?:skip|jump|crackle|pop)\b",
    r"\b(?:scratch|skip|warp|noise|scuff)[ -]?free\b",
)
_CLEAR_DAMAGE = re.compile(
    r"\b(?:warp(?:ed|ing|s)?|edge[ -]?warp(?:ed)?|dish(?:ed|ing)?|"
    r"skip(?:s|ped|ping)?|jump(?:s|ed|ing)?|stuck\s+grooves?)\b"
)
_PHYSICAL = re.compile(
    r"\b(?:crack(?:ed|s)?|brok(?:en|e)|chip(?:ped|s)?|"
    r"scratch(?:ed|es|ing)?|scuff(?:ed|s|ing)?|hairlines?|"
    r"goug(?:e|es|ed|ing)|surface\s+marks?)\b"
)
_PLAYBACK = re.compile(
    r"\b(?:audible|loud|constant|persistent|heavy|severe)\s+"
    r"(?:surface\s+noise|distortion|crackle|pops?)\b"
    r"|\b(?:plays?\s+with|has)\s+(?:surface\s+noise|distortion|crackle|pops?)\b"
    r"|\b(?:distorted\s+playback|distortion\s+on\s+playback)\b"
)


def assess_media_defects(comments, *, comments_supported=True):
    """Classify disclosed playable-media damage separately from missing records.

    An unqualified claim of a scratch/crack may describe the package, so it remains
    unknown unless the clause identifies playable media. Definite playback defects and
    warp claims are media defects except when explicitly limited to packaging.
    """
    normalized = normalize_comments(comments)
    text = (normalized["text"] or "").lower()
    result = {"status": "unknown", "verified": False, "reasons": []}
    if not normalized["supported"] or comments_supported is not True or _unsupported_language(text):
        # Visible definite damage still blocks; unsupported text cannot become a pass.
        unsupported = True
    else:
        unsupported = False
    uncertain = False
    for clause in re.split(r"[.;,!]+|\b(?:but|however)\b", text):
        clause = clause.strip()
        if not clause:
            continue
        if re.search(rf"\b(?:record|vinyl|disc|lp)\s+or\s+{_PACKAGING}\b", clause) and re.search(
            rf"\b{_DAMAGE}\b", clause
        ):
            uncertain = True
            continue
        clause = re.sub(rf"\b{_PACKAGING}\s+{_DAMAGE}\b|\b{_DAMAGE}\s+{_PACKAGING}\b", " ", clause)
        packaging = bool(re.search(rf"\b{_PACKAGING}\b", clause))
        media = bool(re.search(rf"\b{_MEDIA}\b", re.sub(r"\brecord\s+sleeve\b", "sleeve", clause)))
        # A clause solely describing the package does not assert vinyl damage.
        if packaging and not media:
            continue
        for pattern in _NEGATIONS:
            clause = re.sub(pattern, " ", clause)
        if not re.search(rf"\b{_DAMAGE}\b", clause):
            continue
        if _UNCERTAIN.search(clause) or "?" in clause:
            uncertain = True
            continue
        if (
            _CLEAR_DAMAGE.search(clause)
            or _PLAYBACK.search(clause)
            or (media and _PHYSICAL.search(clause))
        ):
            result["status"] = "defective"
            result["reasons"].append("Seller comments disclose playable-media or playback damage.")
            return result
        uncertain = True
    if uncertain:
        result["reasons"].append("Seller damage description is ambiguous or uncertain.")
    elif unsupported:
        result["reasons"].append(
            "Seller comments are unavailable or unsupported for media-damage checks."
        )
    else:
        result["status"] = "no_disclosed_media_defect"
        result["reasons"].append("No playable-media damage disclosed; condition is not verified.")
    return result
