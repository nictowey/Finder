"""Identity judgments and purchase outcomes migrate without invented ground truth."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, insert, select

from finder.adapters.ebay.normalize import normalize_listing
from finder.watch_store import (
    SavedWatch,
    WatchStore,
    decisions,
    migrate,
    migrations,
    verdicts,
)


@pytest.mark.parametrize("legacy", ["mine", "other", "unsure", "bought"])
def test_legacy_feedback_upgrade_preserves_provenance(repository, search_payload, legacy):
    now = datetime(2026, 9, 30, tzinfo=UTC)
    migrate(repository.engine)
    listing = normalize_listing(search_payload["itemSummaries"][0], now)
    repository.upsert(listing)
    store = WatchStore(repository.engine)
    store.add(SavedWatch(release_id=123), now=now)
    claim = store.claim(now=now)
    store.finish(claim, [(listing, {"status": "family_review", "notify": False})], now=now)
    original = dict(
        watch_id=claim["id"],
        marketplace=listing.marketplace,
        marketplace_item_id=listing.marketplace_item_id,
        verdict=legacy,
        tier="family_review",
        decided_at=now.isoformat(),
    )
    with repository.engine.begin() as conn:
        decisions.drop(conn)
        conn.execute(delete(migrations).where(migrations.c.version == 5))
        conn.execute(insert(verdicts).values(**original))
    migrate(repository.engine)
    migrate(repository.engine)
    with repository.engine.connect() as conn:
        row = conn.execute(select(decisions)).mappings().one()
        assert row["legacy"] == original
        assert row["purchased"] == (legacy == "bought")
        assert row["verdict"] == (None if legacy == "bought" else legacy)
        assert row["tier"] == (None if legacy == "bought" else "family_review")
        assert row["decided_at"] == (None if legacy == "bought" else now.isoformat())
        assert dict(conn.execute(select(verdicts)).mappings().one()) == original
    # The same seller-deletion cascade must erase judgments and purchase provenance.
    with repository.engine.begin() as conn:
        from finder.watch_store import watches

        conn.execute(delete(watches).where(watches.c.id == claim["id"]))
    with repository.engine.connect() as conn:
        assert not conn.execute(select(decisions)).first()
