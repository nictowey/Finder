"""Build bounded collector searches from a selected Discogs vinyl release."""

import re
from urllib.parse import urlsplit

from finder.adapters.ebay.target_search import EbaySearchTarget
from finder.domain import Variant
from finder.errors import ConfigurationError


def parse_discogs_release_id(value: str) -> int:
    """Accept a release number or a canonical Discogs release URL, never a master URL."""
    if re.fullmatch(r"[1-9][0-9]{0,19}", value):
        return int(value)
    try:
        url = urlsplit(value)
        valid_host = (
            url.hostname in ("discogs.com", "www.discogs.com")
            and url.port is None
            and url.username is None
            and url.password is None
        )
    except ValueError:
        valid_host = False
    if not valid_host or url.scheme != "https" or url.query or url.fragment:
        raise ConfigurationError("Use a positive Discogs release ID or its HTTPS release URL.")
    match = re.fullmatch(r"/release/([1-9][0-9]{0,19})(?:-[A-Za-z0-9-]+)?/?", url.path)
    if match is None:
        raise ConfigurationError("Use a positive Discogs release ID or its HTTPS release URL.")
    return int(match[1])


def target_from_release(variant: Variant, *, queries: list[str] | None = None) -> EbaySearchTarget:
    """Derive a broad album search; optional collector queries replace it entirely."""
    if variant.catalog_source != "discogs" or not any(
        str(value.get("name", "")).casefold() == "vinyl" for value in variant.formats
    ):
        raise ConfigurationError("Selected Discogs release is not cataloged as vinyl.")
    release_id = parse_discogs_release_id(variant.catalog_variant_id)
    artist = re.sub(r"\s+\(\d+\)$", "", variant.artists[0]) if variant.artists else ""
    default_query = f"{artist} {variant.title}".strip()
    if artist.casefold() in ("various", "various artists"):
        default_query = variant.title
    if queries is None:
        # Sellers often write ASAP for A$AP and omit title apostrophes. Keep the
        # original broad search too; neither query requires pressing details.
        alias = default_query.replace("$", "S").replace("’", "").replace("'", "")
        chosen = list(dict.fromkeys([default_query, alias]))
    else:
        chosen = queries
    if not 1 <= len(chosen) <= 3 or any(not q.strip() or len(q.strip()) > 100 for q in chosen):
        raise ConfigurationError("Supply one to three nonblank searches of at most 100 characters.")
    normalized = [q.strip() for q in chosen]
    if len({q.casefold() for q in normalized}) != len(normalized):
        raise ConfigurationError("Target search queries must be distinct.")
    return EbaySearchTarget(
        id=f"discogs-vinyl-{release_id}", catalog_variant_id=release_id, queries=normalized
    )
