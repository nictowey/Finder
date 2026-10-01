"""Conservative, deterministic candidate scoring with inspectable evidence."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher

from finder.categories.vinyl import (
    catalog_number_claims,
    from_listing,
    from_variant,
    has_numbered_claim,
)
from finder.domain import (
    EvidenceRecord,
    Listing,
    ListingVariantCandidate,
    MatchDecision,
    MatchEvidence,
    Variant,
)

MATCH_POLICY_VERSION = "vinyl-decision-v13"

# Compare named colors across the whole record set. Discogs may describe the two
# discs separately while a seller puts both colors in a single item specific.
_COLOR_WORDS = frozenset(
    "black blue brown clear cream gold gray green maroon mint orange pink purple "
    "red silver teal white yellow".split()
)
_COLOR_ALIASES = {"grey": "gray", "transparent": "clear"}


def _plain_text(value: str) -> str:
    # Sellers omit apostrophes and commonly spell A$AP as ASAP. Preserve word
    # boundaries while normalizing these two frequent catalog/title differences.
    value = value.replace("$", "s").replace("’", "'")
    # Catalog/seller text can split an explicit negative contraction: "Do n't".
    # Join only recognizable auxiliaries with n't, not unrelated title words.
    value = re.sub(
        r"\b(do|does|did|is|are|was|were|have|has|had|could|would|should|must|ca|wo|sha)\s+n't\b",
        r"\1n't",
        value,
        flags=re.IGNORECASE,
    ).replace("'", "")
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", _plain_text(value)))


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _normalized(value))


def _album_title_in_listing(listing_title: str, catalog_title: str) -> bool:
    """A whole catalog title may appear inside a seller's much longer title."""
    album = _normalized(catalog_title)
    return bool(album and f" {album} " in f" {_normalized(listing_title)} ")


def _artist_matches_catalog(listing_artist: str, catalog_artists: list[str]) -> bool:
    """Accept exact names, an inversion, or an exact joined collaborator credit.

    A joined credit only agrees when every component is an exact catalog artist.
    Do not treat an approximate spelling or an unlisted collaborator as agreement.
    """
    names = {_normalized(re.sub(r"\s+\(\d+\)$", "", name)) for name in catalog_artists}
    if _normalized(listing_artist) in names:
        return True
    parts = listing_artist.split(",")
    if (
        len(parts) == 2
        and bool(parts[0].strip() and parts[1].strip())
        and _normalized(f"{parts[1]} {parts[0]}") in names
    ):
        return True
    if len(names) < 2:
        return False
    # Only explicit separators represent a collaboration. A catalog artist with
    # internal punctuation was already accepted by the full-name check above.
    components = re.split(r"\s*(?:,|&|/|\band\b)\s*", listing_artist, flags=re.I)
    return len({_normalized(part) for part in components}) >= 2 and all(
        _normalized(part) in names for part in components
    )


def _artist_evidence(listing_values: list[str], catalog_values: list[str]) -> MatchEvidence | None:
    # Discogs' parenthesized numeric suffix disambiguates artists within its database;
    # it is not part of the name sellers generally use.
    cleaned = [re.sub(r"\s+\(\d+\)$", "", value) for value in catalog_values]
    measured = _similarity_evidence("artist", listing_values, cleaned, 15, 0.88)
    if measured is not None and any(
        _artist_matches_catalog(value, catalog_values) for value in listing_values
    ):
        measured = measured.model_copy(update={"matched": True})
    return (
        measured.model_copy(update={"variant_values": catalog_values})
        if measured is not None
        else None
    )


def _non_vinyl_listing(listing: Listing) -> bool:
    """Treat a seller's explicit other-medium claim as a category conflict."""
    structured = any(
        re.search(r"\b(?:cd|compact disc|cassette|dvd|digital)\b", _normalized(value))
        for value in from_listing(listing).format_descriptions
    )
    title = bool(
        re.search(
            r"(?<![/a-z])cd\b|\b(?:compact disc|cassette|dvd|digital download)\b",
            listing.title.lower(),
        )
    )
    return structured or title


def _vinyl_release(variant: Variant) -> bool:
    return any(_normalized(str(item.get("name", ""))) == "vinyl" for item in variant.formats)


def _catalog_numbered(variant: Variant) -> bool:
    return has_numbered_claim(from_variant(variant).editions)


def _seller_structured_numbered(listing: Listing) -> bool:
    # A title or a bare serial number is a weaker, unverified seller claim. Only explicit
    # item specifics make a numbered catalog variant plausible; neither proves the copy.
    return has_numbered_claim(from_listing(listing).editions)


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


def _palette(values: list[str]) -> set[str]:
    words = {word for value in values for word in _normalized(value).split()}
    return {color for word in words if (color := _COLOR_ALIASES.get(word, word)) in _COLOR_WORDS}


_COLOR_TERM = "(?:" + "|".join(sorted(_COLOR_WORDS | _COLOR_ALIASES.keys())) + ")"
_COLOR_CLAIM = rf"(?:(?:light|dark|opaque|translucent|solid|neon)\s+){{0,2}}{_COLOR_TERM}"
_COLOR_COORDINATOR = r"(?:and\s+or|and|or|nor|ou)"
_COLOR_SEQUENCE = rf"{_COLOR_CLAIM}(?:\s+(?:{_COLOR_COORDINATOR}\s+)?{_COLOR_CLAIM})*"
_COLOR_DENIAL = (
    r"\b(?:not|no|non|never|without|isnt|arent|neither)\s+"
    r"(?:(?:a|an|the|any|in|on|pressed|vinyl|record|disc)\s+){0,3}"
)
_NEGATED_COLORS = re.compile(rf"{_COLOR_DENIAL}{_COLOR_SEQUENCE}\b")
_NEGATED_COLOR_LIST = re.compile(
    rf"{_COLOR_DENIAL}{_COLOR_CLAIM}(?:\s*,\s*{_COLOR_CLAIM})*"
    rf"\s*,?\s*{_COLOR_COORDINATOR}\s+{_COLOR_CLAIM}\b"
)
_NON_DISC_OBJECTS = (
    r"pochettes?|couvertures?|jaquettes?|etiquettes?|sleeves?|covers?|labels?|jackets?|artwork"
)
_OBJECT_COLOR_SEQUENCE = rf"{_COLOR_CLAIM}(?:\s+{_COLOR_COORDINATOR}\s+{_COLOR_CLAIM})*"
# Inspect modified packaging on seller text before punctuation is discarded.
# Horizontal whitespace cannot join an independent "not clear" clause to a
# subsequent "PVC sleeve" line. Keep object words for French context checks.
_PACKAGING_MODIFIERS = r"(?:(?:inner|outer|paper|plastic|pvc|gatefold)[ \t]+){1,3}"
_MODIFIED_OBJECT_COLORS = re.compile(
    rf"(?P<colors>\b{_OBJECT_COLOR_SEQUENCE}\s+)(?P<modifiers>{_PACKAGING_MODIFIERS})"
    rf"(?P<object>(?:{_NON_DISC_OBJECTS})\b)".replace(r"\s", "[ \t]")
)
_NON_DISC_COLORS = re.compile(
    rf"\b{_OBJECT_COLOR_SEQUENCE}\s+(?:{_NON_DISC_OBJECTS})\b"
    rf"|\b(?:{_NON_DISC_OBJECTS})\s+"
    rf"(?:(?:is|are|in|colored|coloured)\s+)?{_OBJECT_COLOR_SEQUENCE}\b"
)


def _english_color_parts(text: str) -> list[str]:
    """Keep coordinated negative lists intact while separating independent claims."""
    text = " ".join(re.findall(r"[a-z0-9]+|,", text))
    text = _NON_DISC_COLORS.sub(",", text)
    text = _NEGATED_COLOR_LIST.sub(
        lambda match: re.sub(rf",\s*(?={_COLOR_COORDINATOR}\b)", " ", match.group()).replace(
            ",", " or "
        ),
        text,
    )
    return [_normalized(part) for part in text.split(",")]


# Commas and bare slash/AND pairs are definite unless the coordinated group
# contains an explicit choice. Keep the whole run together: "blue, white or red"
# cannot leak Blue as a separate positive claim.
_COLOR_BRANCH = rf"{_COLOR_CLAIM}(?:\s+(?:vinyl|records?|discs?|lps?|pressings?|editions?)){{0,2}}"
_COLOR_JOIN = r"(?:\s+(?:and\s+or|and|or|nor|vs|versus)\s+|\s*,\s*(?:(?:and|or|nor)\s+)?|\s+)"
_COLOR_GROUP = re.compile(
    rf"(?<![a-z0-9])(?:either\s+)?{_COLOR_BRANCH}(?:{_COLOR_JOIN}{_COLOR_BRANCH})+(?![a-z0-9])"
)
_COLOR_CHOICE = re.compile(r"\b(?:or|vs|versus)\b")
# Keep incomplete branches after 'either' uncertain, including an unknown first
# shade or disc index. Positional 'either side/way' is not a color choice.
_EITHER_CHOICE = r"\beither\s+(?!(?:side|way)\b)(?:[a-z0-9]+\s+){0,5}"


@dataclass(frozen=True)
class SellerColorClaims:
    """Derived seller evidence only; source listing/catalog values stay unchanged."""

    positive: list[str]
    denied: set[str]
    ambiguous: bool = False


def _has_color_choice(text: str) -> bool:
    return bool(
        _COLOR_CHOICE.search(text)
        or re.search(rf"{_EITHER_CHOICE}{_COLOR_CLAIM}\b", _normalized(text))
    )


# These observed French seller words are deliberately not catalog/color aliases.
# Only an explicit vinyle(s) phrase supplies disc context; bare words, other
# languages, packaging, alternatives and unknown shade names remain unclaimed.
_FRENCH_DISC_COLORS = {"bleu": "blue", "blanc": "white", "gris": "gray", "jaune": "yellow"}
_FRENCH_COLOR = "(?:" + "|".join(_FRENCH_DISC_COLORS) + ")"
_FRENCH_SEQUENCE = rf"{_FRENCH_COLOR}(?:\s*(?:/|&|\b(?:et|ou|ni|and|or|nor)\b)\s*{_FRENCH_COLOR})*"
_FRENCH_DENIAL = (
    r"(?:(?:nest|ne sont|isnt)\s+)?(?:pas|non|sans|ni|aucun|not|no|without)"
    r"(?:\s+(?:de|du|des|un|le|a|the))?"
)
_FRENCH_DISC_PREFIX = (
    rf"\b(?P<before>{_FRENCH_DENIAL}\s+)?vinyles?\s+"
    rf"(?:(?:est|sont|de couleur)\s+)?(?P<after>{_FRENCH_DENIAL}\s+)?"
)
_FRENCH_DISC_CLAIM = re.compile(rf"{_FRENCH_DISC_PREFIX}(?P<colors>{_FRENCH_SEQUENCE})\b")
_FRENCH_DISC_LIST = re.compile(
    rf"{_FRENCH_DISC_PREFIX}(?P<colors>{_FRENCH_COLOR}(?:\s*,\s*{_FRENCH_COLOR})*"
    rf"\s*,?\s*(?:et|ou|ni|and|or|nor)\s+{_FRENCH_COLOR})\b"
)
_FRENCH_DENIED_TAIL = re.compile(rf"\s*{_FRENCH_DENIAL}\s+(?P<colors>{_FRENCH_SEQUENCE})\s*")
_FRENCH_NON_DISC = _NON_DISC_OBJECTS

_VINYLE_COLOR_CONTEXT = re.compile(
    rf"\bvinyles?\s+(?:(?:est|sont|de couleur)\s+)?(?:{_COLOR_CLAIM}|{_FRENCH_COLOR})\b"
)


def _without_french_negative_lists(text: str) -> tuple[str, set[str]]:
    """Retain explicit known-color denials without swallowing a new vinyle clause."""
    denied = set()

    def exclude(match):
        prefix = re.split(r"[.!;,\n]|\b(?:mais|but)\b", text[: match.start()])[-1]
        if (
            not (match["before"] or match["after"])
            or re.search(rf"\b(?:{_FRENCH_NON_DISC})\b", prefix)
            or re.match(rf"\s+(?:{_FRENCH_NON_DISC})\b", text[match.end() :])
        ):
            return match.group()
        denied.update(
            _FRENCH_DISC_COLORS[word]
            for word in re.findall(rf"\b{_FRENCH_COLOR}\b", match["colors"])
        )
        return " " * len(match.group())

    return _FRENCH_DISC_LIST.sub(exclude, text), denied


def _has_french_color_choice(text: str) -> bool:
    return bool(
        re.search(r"\b(?:ou|or|vs|versus)\b", text)
        or re.search(rf"{_EITHER_CHOICE}{_FRENCH_COLOR}\b", _normalized(text))
    )


def _positive_choice_text(text: str) -> str:
    """Remove recognized denials/objects before inspecting localized choice scope."""
    text, _ = _without_french_negative_lists(text)
    text = _FRENCH_DISC_CLAIM.sub(
        lambda match: (
            " "
            if match["before"]
            or match["after"]
            or re.match(rf"\s+(?:{_FRENCH_NON_DISC})\b", text[match.end() :])
            else match.group()
        ),
        text,
    )
    text = _FRENCH_DENIED_TAIL.sub(" ", text)
    return ",".join(
        re.split(rf"\b(?:{_FRENCH_NON_DISC})\b", _NEGATED_COLORS.sub(" ", part))[0]
        for part in _english_color_parts(text)
    )


def _self_contained_color_choice(text: str, *, disc_context: bool = False) -> bool:
    """A new choice clause is independent; a bare 'ou blanc' continues an earlier one."""
    text = _positive_choice_text(text)
    if re.match(r"^[,\s]*(?:or|ou|vs|versus|and or|et ou)\b", text):
        return False
    return bool(
        (_palette([text]) and _has_color_choice(text))
        or (
            (disc_context or re.search(r"\bvinyles?\b", text))
            and (_palette([text]) or re.search(rf"\b{_FRENCH_COLOR}\b", text))
            and _has_french_color_choice(text)
        )
    )


def _withhold_color_continuations(text: str, *, disc_context: bool) -> tuple[str, bool]:
    """Withhold only a continuation and its nearest linked positive color clause.

    A typed color field supplies disc context for 'blue; ou white'. Elsewhere,
    'ou' needs an explicit vinyle phrase. English choice markers retain their
    existing implicit color context. Prefer a comma-linked context over another
    semicolon/sentence group, then the nearest preceding context (or next when
    none precedes it). Masked text keeps its offsets and original values intact.
    """
    # Preserve offsets while removing full negative lists before comma splitting.
    # Otherwise a middle denied color can masquerade as a new positive context.
    scope = re.sub(r"[^a-z0-9,.;!?\n]", " ", text)
    scope, _ = _without_french_negative_lists(scope)
    for pattern in (_NON_DISC_COLORS, _NEGATED_COLOR_LIST, _NEGATED_COLORS):
        scope = pattern.sub(lambda match: " " * len(match.group()), scope)
    clauses = list(re.finditer(r"[^.;!?,\n]+", text))
    contexts, localized_contexts = set(), set()
    continuations = []
    groups = []
    group, previous_end = 0, 0
    for index, clause in enumerate(clauses):
        group += len(re.findall(r"[.;!?\n]", text[previous_end : clause.start()]))
        groups.append(group)
        previous_end = clause.end()
        positive = _positive_choice_text(scope[clause.start() : clause.end()])
        english_color = bool(_palette([positive]))
        known_color = english_color or re.search(rf"\b{_FRENCH_COLOR}\b", positive)
        marker = re.match(r"^[,\s]*(ou|or|vs|versus)\b", positive)
        if known_color and marker:
            continuations.append((index, marker[1]))
            continue
        explicit_disc = any(
            not re.search(rf"\b{_FRENCH_DENIAL}\s*$", positive[: context.start()])
            for context in _VINYLE_COLOR_CONTEXT.finditer(positive)
        )
        if english_color or explicit_disc or (disc_context and known_color):
            contexts.add(index)
        if explicit_disc or (disc_context and known_color):
            localized_contexts.add(index)
    withheld = set()
    for index, marker in continuations:
        eligible = localized_contexts if marker == "ou" else contexts
        same_group = {other for other in eligible if groups[other] == groups[index]}
        eligible = same_group or eligible
        preceding = [other for other in eligible if other < index]
        following = [other for other in eligible if other > index]
        if preceding or following:
            linked = max(preceding) if preceding else min(following)
            withheld.update((index, linked))
    spans = {(clauses[index].start(), clauses[index].end()) for index in withheld}
    return (
        re.sub(
            r"[^.;!?,\n]+",
            lambda match: (
                " " * len(match.group()) if (match.start(), match.end()) in spans else match.group()
            ),
            text,
        ),
        bool(withheld),
    )


def _french_disc_color_claims(
    text: str, *, disc_context: bool = False
) -> tuple[set[str], set[str], bool]:
    """Parse a bounded seller-only vocabulary after removing catalog names.

    A following negative color clause inherits explicit disc context, while an
    independent positive clause needs its own vinyle phrase. Never turn 'ou/or'
    alternatives into a multi-disc palette. This is not general French parsing.
    """
    positive, denied = set(), set()
    ambiguous = False
    positive_disc_seen = orphan_choice = False
    if "?" in text:
        # Questions still abstain, but a question mark must not erase an explicit choice.
        _, _, ambiguous = _french_disc_color_claims(
            text.replace("?", ";"), disc_context=disc_context
        )
        return positive, denied, ambiguous

    def colors(value):
        return {_FRENCH_DISC_COLORS[word] for word in re.findall(rf"\b{_FRENCH_COLOR}\b", value)}

    text, list_denied = _without_french_negative_lists(text)
    denied.update(list_denied)
    uncertain_scope = bool(re.search(r"\b(?:not only|pas seulement)\b", text))
    # A second disc whose color is outside this vocabulary makes a positive
    # palette incomplete even across punctuation. Vinyl packaging is not a disc.
    for clause in re.split(r"[.!;,:()\n]|\b(?:mais|but|plus)\b", text):
        for noun in re.finditer(r"\bvinyles?\b", clause):
            packaging = re.search(rf"\b(?:{_FRENCH_NON_DISC})\b", clause[: noun.start()])
            if (
                not packaging
                and not _FRENCH_DISC_CLAIM.match(clause, noun.start())
                and not _VINYLE_COLOR_CONTEXT.match(clause, noun.start())
                and not _self_contained_color_choice(clause, disc_context=disc_context)
            ):
                uncertain_scope = True
    field_context = disc_context
    for sentence in re.split(r"[.!;\n]", text):
        disc_context = False
        incomplete = False
        sentence_positive, sentence_denied = set(), set()
        for part in re.split(r",|\b(?:mais|but)\b", sentence):
            if re.search(rf"\b(?:{_FRENCH_NON_DISC})\b", part):
                disc_context = False
            tail = _FRENCH_DENIED_TAIL.fullmatch(part) if disc_context else None
            if tail:
                sentence_denied.update(colors(tail["colors"]))
                continue
            # Commas do not end a choice. Filter non-disc and negative wording
            # before deciding whether it introduces positive uncertainty.
            disc_part = _positive_choice_text(part)
            if _has_french_color_choice(disc_part):
                incomplete = True
                has_localized_color = bool(re.search(rf"\b{_FRENCH_COLOR}\b", disc_part))
                explicit_disc = bool(re.search(r"\bvinyles?\b", disc_part))
                if has_localized_color or (explicit_disc and _palette([disc_part])):
                    orphan_choice = True
                    if disc_context or explicit_disc:
                        ambiguous = True
            if disc_context and re.fullmatch(rf"\s*{_FRENCH_SEQUENCE}\s*", part):
                incomplete = True  # An unsupported comma-separated positive list.
            for match in _FRENCH_DISC_CLAIM.finditer(part):
                prefix, suffix = part[: match.start()], part[match.end() :]
                if re.search(rf"\b(?:{_FRENCH_NON_DISC})\b", prefix) or re.match(
                    rf"\s+(?:{_FRENCH_NON_DISC})\b", suffix
                ):
                    continue
                negative = bool(match["before"] or match["after"])
                positive_disc_seen = positive_disc_seen or not negative
                continuation = re.match(r"\s*(?:et|ni|and|nor)\s+(vinyles?\b.*)", suffix)
                supported_continuation = continuation and _FRENCH_DISC_CLAIM.match(continuation[1])
                # Establish disc context before abstaining on an incomplete
                # positive list, so a following explicit denial remains usable.
                disc_context = not re.search(rf"\b(?:{_FRENCH_NON_DISC})\b", suffix)
                # Incomplete positive lists must abstain, but an already explicit
                # denial remains evidence even when later colors are unsupported.
                if (
                    re.match(r"\s*(?:-|/|&|\b(?:et|ou|ni|and|or|nor)\b)", suffix)
                    and not supported_continuation
                ) or re.match(rf"\s+{_FRENCH_COLOR}\b", suffix):
                    incomplete = True
                    if not negative:
                        continue
                if not negative and re.search(
                    r"\b(?:ou|or|vs|versus|pas|non|sans|ni|not|no|without)\b", part
                ):
                    continue
                (sentence_denied if negative else sentence_positive).update(colors(match["colors"]))
        if incomplete and _self_contained_color_choice(sentence, disc_context=field_context):
            # A self-contained alternative does not erase a definite French claim
            # in another sentence. Its own branch colors remain entirely withheld.
            sentence_positive.clear()
        else:
            uncertain_scope = uncertain_scope or incomplete
        positive.update(sentence_positive)
        denied.update(sentence_denied)
    return (
        set() if uncertain_scope else positive,
        denied,
        ambiguous or (positive_disc_seen and orphan_choice),
    )


def _seller_color_details(
    values: list[str], *, ignore: tuple[str, ...] = (), disc_context: bool = False
) -> SellerColorClaims:
    """Separate definite claims, denials and positive choices from catalog semantics.

    Names and non-disc objects are excluded before linking explicit alternatives.
    Preserve original denials, then parse positive clauses after withholding only
    linked choice branches. A structured color field supplies bounded disc context;
    it does not translate bare French words into new positive color claims.
    """
    texts = []
    for value in values:
        # Check the raw source: _plain_text itself drops some Unicode punctuation.
        # Leave complex or multi-value inputs unchanged so modifier masking
        # cannot join clauses or erase a cross-field choice.
        modifier_safe = (
            len(values) == 1 and re.fullmatch(r"[a-zA-Z0-9 \t,.;!?]*", value) is not None
        )
        text = _plain_text(value)
        for name in ignore:
            words = _normalized(name).split()
            if words:
                phrase = r"[^a-z0-9]+".join(map(re.escape, words))
                text = re.sub(rf"(?<![a-z0-9]){phrase}(?![a-z0-9])", " ", text)
        # Mask only the validated modifier words. The original non-disc parser
        # then consumes the color/object pair exactly once, without letting the
        # reverse-object branch consume a subsequent independent disc color.
        if modifier_safe:
            text = _MODIFIED_OBJECT_COLORS.sub(
                lambda match: match["colors"] + " " * len(match["modifiers"]) + match["object"],
                text,
            )
        # The abbreviation's period does not make two independent color claims.
        texts.append(re.sub(r"\bvs\.(?=\s|[a-z])", "vs ", text))
    masked, ambiguous = _withhold_color_continuations("\n".join(texts), disc_context=disc_context)
    claims, denied = [], set()
    offset = 0
    for text in texts:
        positive_text = masked[offset : offset + len(text)]
        offset += len(text) + 1
        french_colors, french_denied, french_ambiguous = _french_disc_color_claims(
            text, disc_context=disc_context
        )
        denied.update(french_denied)
        # Unsupported wording in a withheld branch cannot erase a genuinely
        # independent French claim. Original denials above always remain available.
        if positive_text != text:
            french_colors, _, _ = _french_disc_color_claims(
                positive_text, disc_context=disc_context
            )
        scoped = _positive_choice_text(text)
        if (
            disc_context
            and (_palette([scoped]) or re.search(rf"\b{_FRENCH_COLOR}\b", scoped))
            and _has_french_color_choice(scoped)
        ):
            french_ambiguous = True
        ambiguous = ambiguous or french_ambiguous
        for clause in re.split(r"[.;!?]|\bbut\b", text):
            for part in _english_color_parts(clause):
                for match in _NEGATED_COLORS.finditer(part):
                    denied.update(_palette([match.group()]))
        positives = []
        for clause in re.split(r"[.;!?]|\bbut\b", positive_text):
            parts = _english_color_parts(clause)
            positive = ",".join(_NEGATED_COLORS.sub(",", part) for part in parts)
            if _palette([positive]) and (
                _has_color_choice(positive) or (french_ambiguous and re.search(r"\bou\b", positive))
            ):
                ambiguous = True
                positive = _COLOR_GROUP.sub(
                    lambda match: "," if _has_color_choice(match.group()) else match.group(),
                    positive,
                )
                # An unknown/mixed-language branch cannot leave its known branch
                # as a definite claim. Avoid interpreting arbitrary choice wording.
                if _has_color_choice(positive) or (
                    french_ambiguous and re.search(r"\bou\b", positive)
                ):
                    positive = ""
            positives.append(positive)
        claims.append(" ".join([*positives, *sorted(french_colors)]).strip())
    return SellerColorClaims(claims, denied, ambiguous)


def _seller_color_claims(
    values: list[str], *, ignore: tuple[str, ...] = ()
) -> tuple[list[str], set[str]]:
    """Compatibility projection for callers needing only definite colors/denials."""
    result = _seller_color_details(values, ignore=ignore)
    return result.positive, result.denied


def _listing_color_claims(
    listing: Listing, target: Variant, *, album_aliases: tuple[str, ...] = ()
) -> tuple[SellerColorClaims, SellerColorClaims]:
    """Return title and structured claims with shared source/name boundaries."""
    names = tuple(
        re.sub(r"\s+\(\d+\)$", "", name) for name in (target.title, *album_aliases, *target.artists)
    )
    return (
        _seller_color_details([listing.title], ignore=names),
        _seller_color_details(from_listing(listing).colors, disc_context=True),
    )


def _color_evidence(listing_values: list[str], variant_values: list[str]) -> MatchEvidence | None:
    if not listing_values or not variant_values:
        return None

    claims = _seller_color_details(listing_values, disc_context=True)
    left, right = _palette(claims.positive), _palette(variant_values)
    if claims.denied & right:
        return MatchEvidence(
            field="color",
            listing_values=listing_values,
            variant_values=variant_values,
            matched=False,
            weight=15,
        )
    if claims.ambiguous and not (right and left - right):
        return None  # A choice is missing evidence, never agreement or contradiction.
    if _palette(listing_values) and not left:
        return None  # Only denied colors were named; absence is not a positive claim.
    if left and right:
        # A seller may describe just one disc of a pair. Its partial claim is
        # missing evidence, whereas an additional different color is a conflict.
        if left < right:
            return None
        return MatchEvidence(
            field="color",
            listing_values=listing_values,
            variant_values=variant_values,
            matched=left == right,
            weight=15,
        )
    # Discogs format text also contains weights, packaging and unnamed patterns.
    # Without comparable palettes, neither a similar phrase nor different wording
    # establishes a color match or conflict. Keep those pressings unresolved.
    return None


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
    if catno is not None and catno.matched:
        # A matching localized field must not hide a contradictory English (or
        # other localized) field. Multiple values within each field remain valid
        # when any belongs to the target's potentially multi-label release.
        target_numbers = {_compact(value) for value in variant_vinyl.catalog_numbers} - {""}
        catno = catno.model_copy(
            update={
                "matched": all(
                    {_compact(value) for value in claim} & target_numbers
                    for claim in catalog_number_claims(listing)
                )
            }
        )
    artist = _artist_evidence(listing_vinyl.artists, variant_vinyl.artists)
    title = _similarity_evidence("title", [listing.title], [variant.title], 20, 0.52)
    if title is not None and _album_title_in_listing(listing.title, variant.title):
        title = title.model_copy(update={"matched": True})
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
    color = _color_evidence(listing_vinyl.colors, variant_vinyl.colors)
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


def decide_match(
    listing: Listing, variants: list[Variant], *, retrieval_incomplete: bool = False
) -> MatchDecision:
    """Distinguish an album family from a pressing without claiming unmeasured exact accuracy.

    A bounded catalog search cannot establish that every pressing was considered. Even a
    single strong candidate is therefore only probable until labeled live-data gates pass.
    """
    ranked = rank_variants(listing, variants)
    seller_numbered = _seller_structured_numbered(listing)
    by_id = {variant.catalog_variant_id: variant for variant in variants}
    family_candidates = []
    for candidate in ranked:
        variant = by_id[candidate.catalog_variant_id]
        fields = {item.field: item for item in candidate.evidence}
        # The scorer's broad title similarity alone is insufficient for a family decision.
        title_in_listing = _album_title_in_listing(listing.title, variant.title)
        if (
            fields.get("artist")
            and fields["artist"].matched
            and title_in_listing
            and _vinyl_release(variant)
        ):
            family_candidates.append(candidate)

    families = sorted(
        {by_id[item.catalog_variant_id].catalog_product_id for item in family_candidates}
    )
    competing = []
    if len(families) == 1:
        for candidate in family_candidates:
            fields = {item.field: item for item in candidate.evidence}
            variant = by_id[candidate.catalog_variant_id]
            identifier = any(
                fields.get(field) and fields[field].matched
                for field in ("barcode", "catalog_number")
            )
            conflict = any(
                fields.get(field) and not fields[field].matched
                for field in ("barcode", "catalog_number", "color", "edition", "country")
            )
            # A catalog-marked unofficial release needs an explicit seller claim before
            # it can even become a probable pressing. This is not authenticity proof.
            unofficial = any(
                "unofficial" in value.lower() for value in from_variant(variant).editions
            )
            seller_unofficial = any(
                "unofficial" in value.lower() for value in from_listing(listing).editions
            )
            conflict = conflict or (unofficial and not seller_unofficial)
            # Shared barcodes and colors do not establish a limited numbered edition.
            # An explicit seller item-specific is necessary but not authenticity proof.
            conflict = conflict or (_catalog_numbered(variant) and not seller_numbered)
            if candidate.status == "strong_candidate" and identifier and not conflict:
                competing.append(candidate)

    # Scope uncertainty to viable family candidates. Parsing against an unrelated
    # catalog title could mistake the real album/artist name for a seller choice.
    color_ambiguous = any(
        title.ambiguous or structured.ambiguous
        for candidate in (competing or family_candidates)
        for title, structured in [
            _listing_color_claims(listing, by_id[candidate.catalog_variant_id])
        ]
    )
    if _non_vinyl_listing(listing):
        outcome = "rejected"
    elif len(families) > 1 or len(competing) > 1:
        outcome = "ambiguous"
    elif len(competing) == 1:
        outcome = "family_only" if retrieval_incomplete or color_ambiguous else "probable_variant"
    elif len(families) == 1:
        outcome = "family_only"
    elif any(
        candidate.status == "rejected"
        and any(item.field == "barcode" and not item.matched for item in candidate.evidence)
        for candidate in ranked
    ):
        outcome = "rejected"
    else:
        outcome = "insufficient_data"

    representative = (competing or family_candidates or ranked)[:2]
    conflicts = sorted(
        {
            item.field
            for candidate in representative
            for item in candidate.evidence
            # Fuzzy title and format misses are not evidence of a different pressing.
            if not item.matched
            and item.field in ("artist", "barcode", "catalog_number", "color", "edition", "country")
        }
    )
    if _non_vinyl_listing(listing):
        conflicts.append("non_vinyl_listing")
    if any(
        "unofficial" in value.lower()
        for candidate in representative
        for value in from_variant(by_id[candidate.catalog_variant_id]).editions
    ) and not any("unofficial" in value.lower() for value in from_listing(listing).editions):
        conflicts.append("unofficial_claim_missing")
    evidence = []
    for candidate in representative:
        variant = by_id[candidate.catalog_variant_id]
        for item in candidate.evidence:
            for value in item.listing_values:
                record = EvidenceRecord(
                    field=item.field,
                    value=value,
                    source="marketplace_listing",
                    source_id=listing.marketplace_item_id,
                    method="seller_title" if item.field == "title" else "seller_structured",
                    reliability_class="seller_claim",
                    observed_at=listing.last_observed_at,
                )
                if record not in evidence:
                    evidence.append(record)
            for value in item.variant_values:
                record = EvidenceRecord(
                    field=item.field,
                    value=value,
                    source="catalog_release",
                    source_id=variant.catalog_variant_id,
                    method="catalog_structured",
                    reliability_class="catalog_metadata",
                    observed_at=variant.observed_at,
                )
                if record not in evidence:
                    evidence.append(record)

    missing = []
    if color_ambiguous:
        missing.append("color_claim_ambiguous")
    if not from_listing(listing).artists:
        missing.append("structured_artist")
    if not any(
        item.field in ("barcode", "catalog_number") and item.listing_values
        for candidate in ranked
        for item in candidate.evidence
    ):
        missing.append("pressing_identifier")
    if outcome != "probable_variant":
        missing.append("unambiguous_pressing_evidence")
    if retrieval_incomplete:
        missing.append("catalog_search_incomplete")
    if not seller_numbered and any(
        _catalog_numbered(by_id[candidate.catalog_variant_id]) for candidate in representative
    ):
        missing.append("numbered_structured_claim_missing")
    if ranked and not any(_vinyl_release(variant) for variant in variants):
        missing.append("vinyl_catalog_release")
    return MatchDecision(
        outcome=outcome,
        policy_version=MATCH_POLICY_VERSION,
        marketplace=listing.marketplace,
        marketplace_item_id=listing.marketplace_item_id,
        catalog_source=variants[0].catalog_source if variants else "unknown",
        family_ids=families,
        candidate_ids=[item.catalog_variant_id for item in (competing or family_candidates)],
        conflicts=conflicts,
        missing_evidence=missing,
        evidence=evidence,
    )
