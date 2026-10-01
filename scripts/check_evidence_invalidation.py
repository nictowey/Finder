"""Exercise evidence row locks in the caller's disposable PostgreSQL schema."""

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, local
from time import monotonic, sleep
from unittest.mock import patch

from sqlalchemy import event as sql_event
from sqlalchemy import select, text, update

from finder.adapters.ebay.discovery import summary_fingerprint
from finder.adapters.ebay.target_search import EbaySearchTarget
from finder.discovery_store import DiscoveryStore, LostLease, fence, work
from finder.watch_store import SavedWatch, inbox, outbox
from finder.watch_store import watches as watch_rows


class BoundTransaction:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def begin(self):
        with self.connection.begin():
            yield self.connection


def check_evidence_invalidation(repo, store, queue, listing, now, *, on_phase):
    on_phase("seed_shared_reviews")
    refs = [
        {
            "itemId": f"v1|{910001 + i}|0",
            "title": "Synthetic green copy",
            "itemOriginDate": now.isoformat(),
        }
        for i in range(2)
    ]
    watches = []
    for release_id in (789, 790):
        store.add(SavedWatch(release_id=release_id), now=now)
        claim = store.claim(now=now)
        state = queue.load(
            claim,
            EbaySearchTarget(
                id="evidence-check", catalog_variant_id=release_id, queries=["Synthetic"]
            ),
            now,
        )
        queue.checkpoint(claim, state, now, items=refs)
        items = queue.due(claim, now, limit=2, pending=True)
        for item in items:
            copy = listing.model_copy(
                update={
                    "marketplace_item_id": item["item_id"],
                    "details_observed_at": now,
                    "last_observed_at": now,
                    "listing_ends_at": None,
                }
            )
            queue.disposition(
                claim,
                item,
                now,
                status="evaluated",
                listing=copy,
                repository=repo,
                review={"notify": True, "status": "possible_pressing"},
                state=state,
            )
        watches.append((claim, state, items))
    assert len({watch[0]["id"] for watch in watches}) == 2
    changed_at = now + timedelta(minutes=1)
    changed = [{**ref, "title": "Synthetic red copy"} for ref in refs]
    first_locked, second_requested, release_first = Event(), Event(), Event()
    context = local()
    from finder.discovery_store import invalidate_review_evidence

    def controlled_invalidation(conn, marketplace, item_id, stamp, **kwargs):
        index = context.index
        context.index += 1
        if context.role == "second" and index == 0:
            # The reversed provider page must request the same first shared lock.
            assert item_id == refs[0]["itemId"]
            second_requested.set()
        invalidate_review_evidence(conn, marketplace, item_id, stamp, **kwargs)
        if context.role == "first" and index == 0:
            first_locked.set()
            assert release_first.wait(15)

    with (
        repo.engine.connect() as first,
        repo.engine.connect() as second,
        repo.engine.connect() as observer,
    ):
        first_pid = first.execute(text("SELECT pg_backend_pid()")).scalar_one()
        second_pid = second.execute(text("SELECT pg_backend_pid()")).scalar_one()
        assert first_pid != second_pid
        first.commit()
        second.commit()
        second_stage = {"value": "start"}

        @sql_event.listens_for(second, "before_cursor_execute")
        def record_stage(connection, cursor, statement, parameters, execution_context, many):
            # Fixed table names only; never expose SQL or bound listing values.
            for table in ("finder_watches", "finder_discovery_work", "finder_discovery"):
                if table in statement:
                    second_stage["value"] = table
                    break

        def checkpoint(role, connection, watch, items):
            context.role, context.index = role, 0
            claim, state, _ = watch
            DiscoveryStore(BoundTransaction(connection)).checkpoint(
                claim, state, changed_at, items=items
            )

        with patch("finder.discovery_store.invalidate_review_evidence", controlled_invalidation):
            with ThreadPoolExecutor(max_workers=2) as executor:
                a = executor.submit(checkpoint, "first", first, watches[0], changed)
                b = None
                try:
                    on_phase("first_listing_lock")
                    if not first_locked.wait(5):
                        a.result(timeout=1)
                        raise AssertionError("First checkpoint did not reach its shared lock")
                    b = executor.submit(
                        checkpoint, "second", second, watches[1], list(reversed(changed))
                    )
                    on_phase("second_listing_lock")
                    if not second_requested.wait(5):
                        on_phase("second_waiting_" + second_stage["value"])
                        b.result(timeout=1)
                        raise AssertionError("Second checkpoint did not request its shared lock")
                    on_phase("shared_listing_blocking")
                    deadline = monotonic() + 5
                    while not observer.execute(
                        text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                        {"holder": first_pid, "waiter": second_pid},
                    ).scalar_one():
                        assert monotonic() < deadline
                        sleep(0.02)
                finally:
                    release_first.set()
                a.result(timeout=10)
                if b:
                    b.result(timeout=10)
    on_phase("assert_shared_invalidation")
    watch_ids = [watch[0]["id"] for watch in watches]
    with repo.engine.connect() as conn:
        rows = (
            conn.execute(select(inbox.c.data).where(inbox.c.watch_id.in_(watch_ids)))
            .scalars()
            .all()
        )
        assert len(rows) == 4
        assert all(
            row.get("evidence_invalidated_at") == changed_at.isoformat() and not row["notify"]
            for row in rows
        )
        assert not conn.execute(
            select(outbox).where(outbox.c.watch_id.in_(watch_ids), outbox.c.status == "pending")
        ).first()
        original_event = conn.execute(
            select(outbox.c.id).where(outbox.c.watch_id == watch_ids[0])
        ).scalar_one()
    claim, state, items = watches[0]
    on_phase("reject_stale_feedback")
    with repo.engine.begin() as conn:
        # A page opened before invalidation cannot save against the unchanged tier.
        stale_save = conn.execute(
            text(
                "SELECT * FROM finder_save_decision"
                "(:watch,'ebay',:item,'identity','mine',false,:stamp,'possible_pressing',:old)"
            ),
            {
                "watch": claim["id"],
                "item": refs[0]["itemId"],
                "stamp": changed_at.isoformat(),
                "old": now.isoformat(),
            },
        ).all()
        assert stale_save == []
    fresh_at = now + timedelta(minutes=2)
    on_phase("resume_same_unattempted_event")
    fresh = listing.model_copy(
        update={
            "marketplace_item_id": refs[0]["itemId"],
            "details_observed_at": fresh_at,
            "last_observed_at": fresh_at,
            "listing_ends_at": None,
        }
    )
    queue.disposition(
        claim,
        items[0],
        fresh_at,
        status="evaluated",
        listing=fresh,
        repository=repo,
        review={"notify": True, "status": "possible_pressing"},
        state=state,
    )
    with repo.engine.connect() as conn:
        event = (
            conn.execute(select(outbox).where(outbox.c.watch_id == watch_ids[0])).mappings().one()
        )
        assert (
            event["id"] == original_event
            and event["status"] == "pending"
            and event["attempts"] == 0
        )
        other = conn.execute(
            select(inbox.c.data).where(
                inbox.c.watch_id == watch_ids[1], inbox.c.marketplace_item_id == refs[0]["itemId"]
            )
        ).scalar_one()
        assert other.get("evidence_invalidated_at") == changed_at.isoformat()

    on_phase("legacy_pending_event_quarantine_and_recovery")
    legacy_raw = {**refs[0], "itemId": "v1|910003|0"}
    legacy_items = []
    for claim, state, _ in watches:
        queue.checkpoint(claim, state, now, items=[legacy_raw])
        item = next(
            row
            for row in queue.due(claim, now, limit=10, pending=True)
            if row["item_id"] == legacy_raw["itemId"]
        )
        legacy_items.append(item)
        copy = listing.model_copy(
            update={
                "marketplace_item_id": legacy_raw["itemId"],
                "details_observed_at": now,
                "last_observed_at": now,
                "listing_ends_at": None,
            }
        )
        queue.disposition(
            claim,
            item,
            now,
            status="evaluated",
            listing=copy,
            repository=repo,
            review={
                "notify": True,
                "status": "possible_pressing",
                "policy": "private-target-review-v11",
            },
        )
    with repo.engine.begin() as conn:
        legacy_events = {
            row.watch_id: row.id
            for row in conn.execute(
                select(outbox).where(outbox.c.marketplace_item_id == legacy_raw["itemId"])
            )
        }
        conn.execute(
            update(work)
            .where(work.c.watch_id == watch_ids[0], work.c.item_id == legacy_raw["itemId"])
            .values(
                status="pending",
                kind="existing_listing_updated",
                reason=None,
                last_search_at=changed_at.isoformat(),
                fingerprint=summary_fingerprint({**legacy_raw, "title": "Synthetic changed copy"}),
            )
        )
    subprocess.run(
        ["node", "--import", "tsx", "scripts/check_evidence_alerts.ts"],
        check=True,
        env={
            **os.environ,
            "FINDER_DATABASE_URL": repo.engine.url.set(drivername="postgresql").render_as_string(
                hide_password=False
            ),
        },
    )
    with repo.engine.connect() as conn:
        held = (
            conn.execute(select(outbox).where(outbox.c.id.in_(legacy_events.values())))
            .mappings()
            .all()
        )
        assert len(held) == 2 and all(
            row["status"] == "pending" and row["attempts"] == 0 for row in held
        )
    queue.disposition(
        watches[0][0],
        legacy_items[0],
        fresh_at,
        status="evaluated",
        listing=copy.model_copy(
            update={"details_observed_at": fresh_at, "last_observed_at": fresh_at}
        ),
        repository=repo,
        review={
            "notify": True,
            "status": "possible_pressing",
            "policy": "private-target-review-v11",
        },
    )
    with repo.engine.connect() as conn:
        recovered = {
            row.watch_id: row
            for row in conn.execute(select(outbox).where(outbox.c.id.in_(legacy_events.values())))
        }
        assert (
            recovered[watch_ids[0]].status == "pending"
            and recovered[watch_ids[1]].status == "expired"
        )
        assert all(row.attempts == 0 for row in recovered.values())

    # Compatible FK locks must not weaken the owner's ability to supersede a worker.
    for index, (claim, state, _) in enumerate(watches):
        on_phase("owner_edit_waits_for_" + ("fence" if index == 0 else "finish"))
        with (
            repo.engine.connect() as worker,
            repo.engine.connect() as editor,
            repo.engine.connect() as observer,
        ):
            worker_pid = worker.execute(text("SELECT pg_backend_pid()")).scalar_one()
            editor_pid = editor.execute(text("SELECT pg_backend_pid()")).scalar_one()
            worker.commit()
            editor.commit()

            def edit_watch(watch_id=claim["id"]):
                with editor.begin():
                    editor.execute(
                        update(watch_rows)
                        .where(watch_rows.c.id == watch_id)
                        .values(
                            revision=watch_rows.c.revision + 1,
                            lease_token=None,
                            lease_until=None,
                        )
                    )

            transaction = worker.begin()
            if index == 0:
                fence(worker, claim, fresh_at)
            else:
                store.finish(claim, [], now=fresh_at, connection=worker, checkpoint=True)
            with ThreadPoolExecutor(max_workers=1) as executor:
                editing = executor.submit(edit_watch)
                try:
                    deadline = monotonic() + 5
                    while not observer.execute(
                        text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                        {"holder": worker_pid, "waiter": editor_pid},
                    ).scalar_one():
                        assert monotonic() < deadline
                        sleep(0.02)
                finally:
                    transaction.rollback()
                editing.result(timeout=10)
        try:
            queue.checkpoint(claim, state, fresh_at, items=changed)
        except LostLease:
            pass
        else:
            raise AssertionError("Superseded worker passed its lease fence")
        assert store.finish(claim, [], now=fresh_at, checkpoint=True) is None
