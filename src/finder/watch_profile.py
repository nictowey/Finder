"""Cached sibling pressings and cheat-sheet suggestions for each saved watch."""

from datetime import timedelta

from sqlalchemy import insert, select, update

from finder.categories.vinyl_clues import suggest_clues
from finder.domain import Variant
from finder.watch_store import profiles

PROFILE_DAYS = 7
SIBLING_LIMIT = 20


def load_profile(engine, watch_id, provider, target: Variant, now):
    """Return {siblings, partial, proposals, ...}, rebuilding at most weekly.

    Discogs failures keep the previous profile when there is one; otherwise the caller
    falls back to a bounded catalog search. Nothing here decides a listing's pressing.
    """
    release_id = int(target.catalog_variant_id)
    with engine.connect() as conn:
        saved = (
            conn.execute(select(profiles).where(profiles.c.watch_id == watch_id)).mappings().first()
        )
    if (
        saved
        and saved["release_id"] == release_id
        and saved["observed_at"] > (now - timedelta(days=PROFILE_DAYS)).isoformat()
    ):
        return saved["data"]
    try:
        master = int(target.catalog_product_id)
        if master == release_id:
            # Discogs gives a release without a master its own ID; there are no siblings.
            ids, truncated = [], False
        else:
            ids, truncated = provider.vinyl_versions(master)
        others = [value for value in ids if value != release_id]
        siblings = provider.get_releases(others[:SIBLING_LIMIT])
        partial = truncated or len(others) > SIBLING_LIMIT
        data = {
            "master_id": master if master != release_id else None,
            "vinyl_versions": len(others) + 1,
            "siblings": [sibling.model_dump(mode="json") for sibling in siblings],
            "partial": partial,
            "proposals": suggest_clues(target, siblings, partial=partial),
        }
    except Exception:
        return saved["data"] if saved and saved["release_id"] == release_id else None
    with engine.begin() as conn:
        values = dict(release_id=release_id, observed_at=now.isoformat(), data=data)
        if saved:
            conn.execute(update(profiles).where(profiles.c.watch_id == watch_id).values(**values))
        else:
            conn.execute(insert(profiles).values(watch_id=watch_id, **values))
    return data


def siblings_of(profile) -> list[Variant]:
    return [Variant.model_validate(row) for row in profile.get("siblings", [])]
