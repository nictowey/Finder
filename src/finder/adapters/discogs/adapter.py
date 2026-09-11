from collections.abc import Callable
from datetime import UTC, datetime

from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.discogs.normalize import normalize_release, product_from_variant
from finder.domain import Product, Variant
from finder.errors import CatalogResponseError, ConfigurationError


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
        payload = self.client.get(
            "/database/search",
            params={
                "q": query.strip(),
                "type": "release",
                "format": "Vinyl",
                "genre": "Hip Hop",
                "per_page": limit,
                "page": 1,
            },
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise CatalogResponseError("Discogs search response has invalid results.")
        variants = []
        seen: set[int] = set()
        for result in results:
            release_id = result.get("id") if isinstance(result, dict) else None
            if (
                isinstance(release_id, bool)
                or not isinstance(release_id, int)
                or release_id in seen
            ):
                continue
            seen.add(release_id)
            detail = self.client.get(f"/releases/{release_id}")
            if detail.get("id") != release_id:
                raise CatalogResponseError("Discogs release response has a mismatched ID.")
            variants.append(normalize_release(detail, self.now()))
        return variants

    def product_for(self, variant: Variant) -> Product:
        return product_from_variant(variant)
