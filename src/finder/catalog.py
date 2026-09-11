from typing import Protocol

from finder.domain import Product, Variant


class CatalogProvider(Protocol):
    """Catalog lookup is separate from marketplace listing ingestion."""

    name: str

    def search_releases(self, query: str, *, limit: int = 5) -> list[Variant]: ...

    def product_for(self, variant: Variant) -> Product: ...
