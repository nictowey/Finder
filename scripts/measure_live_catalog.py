"""Bounded cross-genre Discogs title-retrieval probe with anonymous output.

Select one release from each public catalog query, then simulate a seller title from
its catalog artist/title without identifiers. The selected target is NOT pinned for
retrieval. This measures a favorable synthetic title query, not real seller recall.
"""

import json
from datetime import UTC, datetime

from finder.adapters.discogs.adapter import DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.categories.vinyl_target import target_from_release
from finder.config import load_discogs_settings
from finder.domain import Listing

QUERIES = (
    ("jazz", "Miles Davis Kind of Blue", "Jazz"),
    ("rock", "Fleetwood Mac Rumours", "Rock"),
    ("classical", "Beethoven Symphony No. 9", "Classical"),
    ("electronic", "Daft Punk Discovery", "Electronic"),
    ("folk", "Joni Mitchell Blue", "Folk, World, & Country"),
)


def measure() -> dict:
    settings = load_discogs_settings()
    rows = []
    with DiscogsClient(settings) as client:
        provider = DiscogsCatalogProvider(client)
        for genre, query, catalog_genre in QUERIES:
            initial = provider.search_releases(query, limit=3)
            eligible = [
                variant
                for variant in initial
                if catalog_genre in variant.genres
                and variant.artists
                and any(str(fmt.get("name", "")).casefold() == "vinyl" for fmt in variant.formats)
            ]
            if not eligible:
                rows.append({"genre": genre, "eligible_in_first_three": False})
                continue
            selected = eligible[0]
            target_from_release(selected)
            now = datetime.now(UTC)
            listing = Listing(
                marketplace="ebay",
                marketplace_item_id=f"synthetic-{genre}",
                title=f"{selected.artists[0]} {selected.title} vinyl",
                first_observed_at=now,
                last_observed_at=now,
            )
            retrieval = provider.search_for_listing(listing, limit=4)
            rows.append(
                {
                    "genre": genre,
                    "eligible_in_first_three": True,
                    "selected_release_in_title_results": any(
                        variant.catalog_variant_id == selected.catalog_variant_id
                        for variant in retrieval.variants
                    ),
                    "candidates_hydrated": len(retrieval.variants),
                    "first_page_full_or_truncated": retrieval.search_truncated,
                    "candidate_limit_reached": retrieval.candidate_limit_reached,
                    "query_kinds": retrieval.query_kinds,
                }
            )
    return {
        "scope": "catalog_derived_synthetic_titles",
        "max_catalog_requests_without_retries": len(QUERIES) * (1 + 3 + 1 + 4),
        "panel": rows,
        "limitations": (
            "The title is copied from Discogs metadata; no actual marketplace seller supplied it. "
            "Only the first three initial results and first four candidates were considered. "
            "These counts do not measure real listing search recall or pressing precision."
        ),
        "attribution": "Data provided by Discogs",
        "attribution_url": "https://www.discogs.com",
    }


if __name__ == "__main__":
    print(json.dumps(measure(), indent=2))
