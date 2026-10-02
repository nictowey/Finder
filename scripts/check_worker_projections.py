"""Synthetic worker projection checks inside the caller's isolated database only."""

from datetime import timedelta

from sqlalchemy import insert, select, update

from finder.adapters.ebay.target_search import EbaySearchTarget
from finder.discovery_store import DiscoveryStore, progress, work
from finder.discovery_worker import poll_minutes
from finder.evidence import INVALIDATED_AT
from finder.watch_store import SavedWatch, WatchStore, inbox, watches


def check_worker_projections(repository, now, listing):
    """Called after remigration in the disposable PostgreSQL gate; makes no connections."""
    engine = repository.engine
    store, queue = WatchStore(engine), DiscoveryStore(engine)
    store.add(SavedWatch(release_id=424242), now=now)
    claim = store.claim(now=now)
    state = queue.load(
        claim,
        EbaySearchTarget(
            id="synthetic",
            catalog_variant_id=424242,
            queries=[f"Album {index}" for index in range(6)],
        ),
        now,
    )
    for release_id in range(424243, 424262):
        watch_id = store.add(SavedWatch(release_id=release_id), now=now)
        with engine.begin() as conn:
            conn.execute(insert(progress).values(watch_id=watch_id, data=state))
    assert poll_minutes(engine) == 87  # ceil(1440 * 20 * 6 / 2000)
    with engine.begin() as conn:
        conn.execute(update(watches).where(watches.c.id != claim["id"]).values(enabled=False))
    assert poll_minutes(engine) == 10
    raw = {
        "itemId": "synthetic-projection",
        "title": "Synthetic",
        "itemOriginDate": now.isoformat(),
    }
    queue.checkpoint(claim, state, now, items=[raw])
    ordinary = queue.due(claim, now, limit=1, pending=True)[0]
    assert ordinary["item_id"] == raw["itemId"] and ordinary["review_data"] is None
    assert queue.has_due(claim, now, pending=True)
    assert not queue.has_due(claim, now, pending=False)
    listing = listing.model_copy(
        update={
            "marketplace_item_id": raw["itemId"],
            "seller_id": "synthetic-projection-seller",
            "source_metadata": {},
            "details_observed_at": now,
        }
    )
    queue.disposition(
        claim,
        ordinary,
        now,
        status="evaluated",
        listing=listing,
        repository=repository,
        review={"notify": False},
        state=state,
    )
    # Both databases retain Python JSON decoding for problematic Unicode, even in
    # ignored fields, and retain evaluation status when the signature is unchanged.
    for ignored in ("before\0after", "\ud800", "\udc00", "\U0001f600"):
        state.update(evaluation_signature="same", ignored=ignored)
        with engine.begin() as conn:
            conn.execute(
                update(progress).where(progress.c.watch_id == claim["id"]).values(data=state)
            )
            conn.execute(
                update(work).where(work.c.watch_id == claim["id"]).values(status="evaluated")
            )
        queue.checkpoint(claim, state, now)
        with engine.connect() as conn:
            assert (
                conn.execute(
                    select(work.c.status).where(work.c.watch_id == claim["id"])
                ).scalar_one()
                == "evaluated"
            )
        assert poll_minutes(engine) == 10
    # Owner refresh requests remain pending despite future ordinary check times.
    with engine.begin() as conn:
        conn.execute(
            update(work)
            .where(work.c.watch_id == claim["id"])
            .values(
                refresh_token="synthetic-request",
                refresh_after=now.isoformat(),
                next_check_at=(now + timedelta(days=1)).isoformat(),
            )
        )
    assert not queue.has_due(claim, now, pending=False)
    with engine.begin() as conn:
        conn.execute(
            update(inbox)
            .where(inbox.c.watch_id == claim["id"])
            .values(
                data={"evidence_invalidated_at": "known-change", "ignored": "\ud800"},
            )
        )
    requested = queue.due(claim, now, limit=1, pending=True)[0]
    assert requested["review_data"]["evidence_invalidated_at"] == "known-change"
    assert requested["refresh_token"] == "synthetic-request"
    assert queue.has_due(claim, now, pending=True)
    changed_at = now + timedelta(seconds=1)
    stored = listing.model_copy(
        update={
            "source_metadata": {INVALIDATED_AT: changed_at.isoformat(), "ignored": "\ud800"},
            "last_observed_at": changed_at,
        }
    )
    repository.upsert(stored)
    # Old assessment must remain non-notifying despite a marker-only listing read.
    store.finish(claim, [(listing, {"notify": True})], checkpoint=True, now=changed_at)
    with engine.connect() as conn:
        review = conn.execute(
            select(inbox.c.data).where(inbox.c.watch_id == claim["id"])
        ).scalar_one()
        assert review["notify"] is False
        assert review["evidence_invalidated_at"] == changed_at.isoformat()
