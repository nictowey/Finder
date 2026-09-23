import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import zip_longest

from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.discogs.normalize import normalize_release, product_from_variant
from finder.categories.vinyl import from_listing
from finder.domain import Listing, Product, Variant
from finder.errors import CatalogResponseError, ConfigurationError


@dataclass(frozen=True)
class CandidateRetrieval:
    variants: list[Variant]
    query_kinds: list[str]
    search_truncated: bool
    candidate_limit_reached: bool
    identifiers_omitted: bool
    target_release_id: int | None
    target_not_in_search: bool

    @property
    def incomplete(self) -> bool:
        return (
            self.search_truncated
            or self.candidate_limit_reached
            or self.identifiers_omitted
            or self.target_not_in_search
        )


@dataclass(frozen=True)
class AlternativeRetrieval:
    variants: list[Variant]
    search_incomplete: bool


class DiscogsCatalogProvider:
    name = "discogs"

    def __init__(
        self,
        client: DiscogsClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.client = client
        self.now = now

    def search_releases(self, query: str, *, limit: int = 5) -> list[Variant]:
        if not query.strip():
            raise ConfigurationError("Catalog query cannot be empty.")
        if not 1 <= limit <= 25:
            raise ConfigurationError("Catalog result limit must be between 1 and 25.")
        results, _ = self._search({"q": query.strip()}, limit)
        return self._hydrate(self._ids(results)[:limit])

    def get_release(self, release_id: int) -> Variant:
        """Fetch one collector-selected release through the permitted catalog endpoint."""
        if isinstance(release_id, bool) or not isinstance(release_id, int) or release_id <= 0:
            raise ConfigurationError("Discogs release ID must be a positive integer.")
        return self._hydrate([release_id])[0]

    def search_alternatives(self, target: Variant, *, limit: int = 5) -> AlternativeRetrieval:
        """One catalog query, up to five other details, reusable for an entire watch scan.

        Results are candidates, not a complete master-release inventory. Reuse the already
        hydrated target and never count a pinned target as independent search discovery.
        """
        if not 1 <= limit <= 5:
            raise ConfigurationError("Alternative result limit must be between 1 and 5.")
        if target.catalog_source != "discogs" or not target.title.strip():
            raise ConfigurationError("A Discogs target with a title is required.")
        artist = re.sub(r"\s+\(\d+\)$", "", target.artists[0]) if target.artists else ""
        if artist.casefold() in ("various", "various artists"):
            artist = ""
        results, truncated = self._search({"q": f"{artist} {target.title}".strip()}, limit + 1)
        ids = self._ids(results)
        other_ids = [id for id in ids if str(id) != target.catalog_variant_id]
        incomplete = (
            truncated
            or len(other_ids) > limit
            or target.catalog_variant_id not in {str(id) for id in ids}
            or len(ids) != len(results)
        )
        return AlternativeRetrieval(self._hydrate(other_ids[:limit]), incomplete)

    def search_for_listing(
        self, listing: Listing, *, limit: int = 10, target_release_id: int | None = None
    ) -> CandidateRetrieval:
        """Use at most four searches and `limit` detail requests for provisional candidates.

        All search values are seller claims. Diversifying retrieval is not verification of an
        identifier and does not establish complete coverage of a release family. A user-saved
        exact release is retrieved directly, but never treated as a confirmed listing match.
        A target-derived family search can supply competitors when seller text is too noisy;
        it cannot count as the seller search finding the target.
        """
        if not 1 <= limit <= 25:
            raise ConfigurationError("Catalog result limit must be between 1 and 25.")
        if target_release_id is not None and (
            isinstance(target_release_id, bool)
            or not isinstance(target_release_id, int)
            or target_release_id <= 0
        ):
            raise ConfigurationError("Target Discogs release ID must be a positive integer.")
        fingerprint = from_listing(listing)
        barcodes = list(
            dict.fromkeys(
                digits
                for value in fingerprint.barcodes
                if len(digits := re.sub(r"\D", "", value)) in (8, 12, 13, 14)
            )
        )
        catnos = list(
            dict.fromkeys(value.strip() for value in fingerprint.catalog_numbers if value.strip())
        )
        plans: list[tuple[str, str]] = []
        if barcodes:
            plans.append(("barcode", barcodes[0]))
        if catnos:
            plans.append(("catno", catnos[0]))
        if listing.title.strip():
            plans.append(("q", listing.title.strip()))
        if not plans:
            raise ConfigurationError("Listing has no usable catalog search value.")
        identifiers_omitted = len(barcodes) > 1 or len(catnos) > 1
        per_page = min(limit, 10)
        target = self.get_release(target_release_id) if target_release_id else None
        result_lists = []
        truncated = False
        for name, value in plans:
            results, possibly_more = self._search({name: value}, per_page)
            result_lists.append(self._ids(results))
            truncated |= possibly_more
        seller_ids = list(
            dict.fromkeys(id for row in zip_longest(*result_lists) for id in row if id)
        )
        target_not_in_search = target_release_id is not None and target_release_id not in seller_ids
        query_kinds = [name for name, _ in plans]
        if target is not None and target.artists and target.title:
            artist = re.sub(r"\s+\(\d+\)$", "", target.artists[0])
            family_query = f"{artist} {target.title}"
            if family_query.casefold() != listing.title.strip().casefold():
                results, possibly_more = self._search({"q": family_query}, per_page)
                result_lists.append(self._ids(results))
                truncated |= possibly_more
                query_kinds.append("target_family")
        search_ids = list(
            dict.fromkeys(id for row in zip_longest(*result_lists) for id in row if id)
        )
        # Reserve one bounded detail slot for the user's target. Otherwise broad title search
        # can crowd out the release the collector actually requested.
        unique = (
            [target_release_id, *(id for id in search_ids if id != target_release_id)]
            if target_release_id is not None
            else search_ids
        )
        variants = ([target] if target is not None else []) + self._hydrate(
            [id for id in unique[:limit] if id != target_release_id]
        )
        return CandidateRetrieval(
            variants=variants,
            query_kinds=query_kinds,
            search_truncated=truncated,
            candidate_limit_reached=len(unique) > limit,
            identifiers_omitted=identifiers_omitted,
            target_release_id=target_release_id,
            target_not_in_search=target_not_in_search,
        )

    def _search(self, query: dict[str, str], limit: int) -> tuple[list, bool]:
        payload = self.client.get(
            "/database/search",
            params={
                **query,
                "type": "release",
                "format": "Vinyl",
                "per_page": limit,
                "page": 1,
            },
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise CatalogResponseError("Discogs search response has invalid results.")
        pagination = payload.get("pagination", {})
        pages = pagination.get("pages") if isinstance(pagination, dict) else None
        # A full first page may have more results even if pagination is absent.
        return results[:limit], len(results) >= limit or (isinstance(pages, int) and pages > 1)

    @staticmethod
    def _ids(results: list) -> list[int]:
        ids = []
        seen: set[int] = set()
        for result in results:
            release_id = result.get("id") if isinstance(result, dict) else None
            if (
                isinstance(release_id, bool)
                or not isinstance(release_id, int)
                or release_id <= 0
                or release_id in seen
            ):
                continue
            seen.add(release_id)
            ids.append(release_id)
        return ids

    def _hydrate(self, release_ids: list[int]) -> list[Variant]:
        variants = []
        for release_id in release_ids:
            detail = self.client.get(f"/releases/{release_id}")
            if detail.get("id") != release_id:
                raise CatalogResponseError("Discogs release response has a mismatched ID.")
            variants.append(normalize_release(detail, self.now()))
        return variants

    def product_for(self, variant: Variant) -> Product:
        return product_from_variant(variant)
