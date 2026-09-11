"""Normalize only Discogs catalog data classified as CC0 by its API terms."""

from datetime import UTC, datetime
from typing import Any

from finder.domain import Product, Variant
from finder.errors import CatalogResponseError


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _names(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        name = _text(value.get("name")) if isinstance(value, dict) else _text(value)
        if name and name not in result:
            result.append(name)
    return result


def normalize_release(raw: Any, observed_at: datetime) -> Variant:
    if not isinstance(raw, dict):
        raise CatalogResponseError("Discogs release must be an object.")
    release_id = raw.get("id")
    title = _text(raw.get("title"))
    if isinstance(release_id, bool) or not isinstance(release_id, int) or not title:
        raise CatalogResponseError("Discogs release is missing an ID or title.")
    master_id = raw.get("master_id")
    if isinstance(master_id, bool) or not isinstance(master_id, int) or master_id <= 0:
        master_id = release_id
    year = raw.get("year")
    if isinstance(year, bool) or not isinstance(year, int) or year <= 0:
        year = None
    identifiers: dict[str, list[str]] = {}
    for identifier in (
        raw.get("identifiers", []) if isinstance(raw.get("identifiers"), list) else []
    ):
        if not isinstance(identifier, dict):
            continue
        kind, value = _text(identifier.get("type")), _text(identifier.get("value"))
        if kind and value and value not in identifiers.setdefault(kind, []):
            identifiers[kind].append(value)
    formats = (
        [value for value in raw.get("formats", []) if isinstance(value, dict)]
        if isinstance(raw.get("formats"), list)
        else []
    )
    labels = []
    for value in raw.get("labels", []) if isinstance(raw.get("labels"), list) else []:
        if not isinstance(value, dict):
            continue
        labels.append(
            {
                key: value[key]
                for key in ("id", "name", "catno")
                if isinstance(value.get(key), (str, int)) and not isinstance(value.get(key), bool)
            }
        )
    resource_url = _text(raw.get("uri"))
    if resource_url and resource_url.startswith("/"):
        resource_url = f"https://www.discogs.com{resource_url}"
    return Variant(
        catalog_source="discogs",
        catalog_variant_id=str(release_id),
        catalog_product_id=str(master_id),
        title=title,
        artists=_names(raw.get("artists")),
        release_year=year,
        country=_text(raw.get("country")),
        formats=formats,
        labels=labels,
        identifiers=identifiers,
        genres=_names(raw.get("genres")),
        styles=_names(raw.get("styles")),
        resource_url=resource_url,
        observed_at=observed_at.astimezone(UTC),
        source_metadata={
            "data_quality": raw.get("data_quality"),
            "status": raw.get("status"),
        },
    )


def product_from_variant(variant: Variant) -> Product:
    return Product(
        catalog_source=variant.catalog_source,
        catalog_product_id=variant.catalog_product_id,
        title=variant.title,
        artists=variant.artists,
        resource_url=(
            f"https://www.discogs.com/master/{variant.catalog_product_id}"
            if variant.catalog_product_id != variant.catalog_variant_id
            else variant.resource_url
        ),
        observed_at=variant.observed_at,
        source_metadata={"derived_from_variant": variant.catalog_variant_id},
    )
