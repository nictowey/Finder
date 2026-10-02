"""Verify migration, leases and seller deletion in an isolated ephemeral PG schema."""

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, delete, event, insert, select, text
from sqlalchemy.engine import make_url

from finder.adapters.ebay.normalize import normalize_listing
from finder.persistence import SqlAlchemyRepository
from finder.watch_store import (
    MAX_WATCHES,
    NEW_TABLES,
    SavedWatch,
    WatchStore,
    decisions,
    dispatch_attempts,
    inbox,
    migrate,
    migrations,
    outbox,
    profiles,
    rollback_pilot_schema,
    scan_attempts,
    verdicts,
)


def main():
    # Read-only synthetic CTEs exercise the same SQL used by the inbox, including
    # decimal boundaries, unknown totals, watch edits and filtering before paging.
    subprocess.run(["node", "--import", "tsx", "scripts/check_inbox_prices.ts"], check=True)
    url = make_url(os.environ["FINDER_DATABASE_URL"]).set(drivername="postgresql+psycopg")
    # A transaction pool cannot preserve an isolated search_path between transactions.
    # Neon's direct endpoint has the same hostname without the -pooler suffix.
    if url.host and "-pooler." in url.host:
        url = url.set(host=url.host.replace("-pooler.", ".", 1))
    admin = create_engine(url, hide_parameters=True)
    namespace = "finder_check_" + uuid4().hex
    repo = None
    phase = "create_schema"
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{namespace}"'))
        isolated = url.update_query_dict({"options": f"-csearch_path={namespace}"})
        phase = "connect_isolated_schema"
        repo = SqlAlchemyRepository.from_url(isolated.render_as_string(hide_password=False))
        phase = "migrate"
        migrate(repo.engine)
        migrate(repo.engine)
        now = datetime.now(UTC)
        payload = json.loads(Path("tests/fixtures/ebay_search.json").read_text())
        listing = normalize_listing(payload["itemSummaries"][0], now).model_copy(
            update={"seller_id": "synthetic-" + uuid4().hex}
        )
        repo.upsert(listing)
        phase = "claim_and_finish"
        store = WatchStore(repo.engine)
        store.add(SavedWatch(release_id=123), now=now)
        claim = store.claim(now=now)
        assert store.claim(now=now) is None
        store.finish(claim, [(listing, {"notify": True})], now=now)
        later = now + timedelta(minutes=31)
        store.finish(store.claim(now=later), [(listing, {"notify": True})], now=later)
        with repo.engine.begin() as conn:
            conn.execute(
                insert(profiles).values(
                    watch_id=claim["id"], release_id=123, observed_at=now.isoformat(), data={}
                )
            )
            conn.execute(
                insert(verdicts).values(
                    watch_id=claim["id"],
                    marketplace=listing.marketplace,
                    marketplace_item_id=listing.marketplace_item_id,
                    verdict="bought",
                    tier="possible_pressing",
                    decided_at=now.isoformat(),
                )
            )
        # Rehearse the additive upgrade from historical purchase-only feedback.
        with repo.engine.begin() as conn:
            conn.exec_driver_sql(
                "DROP FUNCTION IF EXISTS finder_save_decision"
                "(text,text,text,text,text,boolean,text,text,text)"
            )
            decisions.drop(conn)
            conn.execute(delete(migrations).where(migrations.c.version == 5))
        migrate(repo.engine)
        migrate(repo.engine)
        with repo.engine.connect() as conn:
            decision = conn.execute(select(decisions)).mappings().one()
            assert decision["purchased"] and decision["verdict"] is None
            assert decision["tier"] is None and decision["decided_at"] is None
            assert decision["legacy"]["verdict"] == "bought"
        with repo.engine.connect() as conn:
            phase = "assert_dedup"
            assert len(conn.execute(select(inbox)).all()) == 1
            assert len(conn.execute(select(outbox)).all()) == 1
            assert len(conn.execute(select(scan_attempts)).all()) == 2
            backup = {
                table.name: [dict(row) for row in conn.execute(select(table)).mappings()]
                for table in NEW_TABLES
            }
        # Rehearse a logical pilot backup/restore with synthetic data in this disposable
        # schema only. This is not a claim that a Production recovery has been performed.
        phase = "restore_synthetic_backup"
        rollback_pilot_schema(repo.engine)
        migrate(repo.engine)
        with repo.engine.begin() as conn:
            for table in reversed(NEW_TABLES):
                conn.execute(delete(table))
            for table in NEW_TABLES:
                if backup[table.name]:
                    conn.execute(insert(table), backup[table.name])
            for table in NEW_TABLES:
                assert sorted(
                    json.dumps(dict(row), sort_keys=True)
                    for row in conn.execute(select(table)).mappings()
                ) == sorted(json.dumps(row, sort_keys=True) for row in backup[table.name])
        # Exercise upgrade of an existing v1 pilot, including live inbox/outbox rows.
        phase = "upgrade_existing_pilot"
        with repo.engine.begin() as conn:
            scan_attempts.drop(conn)
            dispatch_attempts.drop(conn)
            conn.execute(delete(migrations).where(migrations.c.version == 2))
        migrate(repo.engine)
        with repo.engine.connect() as conn:
            assert len(conn.execute(select(inbox)).all()) == 1
            assert len(conn.execute(select(outbox)).all()) == 1
            assert not conn.execute(select(scan_attempts)).all()
        phase = "discovery_checkpoints_and_shared_budget"
        from concurrent.futures import ThreadPoolExecutor

        from finder.adapters.ebay.budget import BrowseBudget, budget
        from finder.adapters.ebay.target_search import EbaySearchTarget
        from finder.discovery_store import DiscoveryStore, progress, work

        discovery_time = later + timedelta(minutes=31)
        discovery_claim = store.claim(now=discovery_time)
        queue = DiscoveryStore(repo.engine)
        state = queue.load(
            discovery_claim,
            EbaySearchTarget(id="synthetic", catalog_variant_id=123, queries=["Example Album"]),
            discovery_time,
        )
        queue.checkpoint(
            discovery_claim,
            state,
            discovery_time,
            items=[
                {
                    "itemId": listing.marketplace_item_id,
                    "title": "Synthetic reference",
                    "itemOriginDate": now.isoformat(),
                }
            ],
        )
        item = queue.due(discovery_claim, discovery_time, limit=1, pending=True)[0]
        history_before = repo.get_observations(listing.marketplace, listing.marketplace_item_id)
        statements = []

        def trace_disposition(conn, cursor, statement, parameters, context, executemany):
            # SQL placeholders only, never parameters; this remains in the disposable schema.
            statements.append(statement)

        event.listen(repo.engine, "before_cursor_execute", trace_disposition)
        try:
            queue.disposition(
                discovery_claim,
                item,
                discovery_time,
                status="evaluated",
                listing=listing,
                repository=repo,
                review={"notify": True},
                state=state,
            )
        finally:
            event.remove(repo.engine, "before_cursor_execute", trace_disposition)
        assert not any("listing_observations" in statement for statement in statements)
        assert sum("pg_advisory_xact_lock" in statement for statement in statements) == 1
        assert any(
            statement.startswith("SELECT listings.marketplace,") and "FOR UPDATE" in statement
            for statement in statements
        )
        assert (
            repo.get_observations(listing.marketplace, listing.marketplace_item_id)
            == history_before
        )
        # Real row-level serialization: three contenders cannot spend the two calls
        # above the reserve. No network or actual quota is involved in this rehearsal.
        budget.create(repo.engine, checkfirst=True)
        future_reset = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        with repo.engine.begin() as conn:
            conn.execute(
                insert(budget).values(
                    key="browse",
                    data={
                        "observed_at": datetime.now(UTC).isoformat(),
                        "rates": [
                            {
                                "remaining": 202,
                                "limit": 5000,
                                "window": 86400,
                                "reset": future_reset,
                            }
                        ],
                    },
                )
            )

        def debit():
            guard = BrowseBudget.__new__(BrowseBudget)
            guard.engine, guard.telemetry, guard.last = repo.engine, lambda: {}, {}
            try:
                guard.debit()
                return True
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=3) as executor:
            assert sum(executor.map(lambda _: debit(), range(3))) == 2
        with repo.engine.connect() as conn:
            assert conn.execute(select(budget.c.data)).scalar()["rates"][0]["remaining"] == 200
        phase = "feedback_identity_purchase_and_locking"
        subprocess.run(
            ["node", "--import", "tsx", "scripts/check_feedback.ts"],
            check=True,
            env={
                **os.environ,
                "FINDER_DATABASE_URL": isolated.set(drivername="postgresql").render_as_string(
                    hide_password=False
                ),
            },
        )
        # Bind the canonical purchase API to the runtime digest accounting using
        # actual PostgreSQL triggers and the Python worker, not a hand-made mirror.
        phase = "purchase_only_cannot_consume_initial_digest"
        store.add(SavedWatch(release_id=456), now=discovery_time)
        purchase_claim = store.claim(now=discovery_time)
        assert purchase_claim and purchase_claim["id"] != discovery_claim["id"]
        purchase_state = queue.load(
            purchase_claim,
            EbaySearchTarget(id="purchase-check", catalog_variant_id=456, queries=["Example"]),
            discovery_time,
        )
        refs = [
            {
                "itemId": f"v1|{900001 + offset}|0",
                "title": "Synthetic purchase reference",
                "itemOriginDate": now.isoformat(),
            }
            for offset in range(2)
        ]
        queue.checkpoint(purchase_claim, purchase_state, discovery_time, items=refs)
        first_item, second_item = queue.due(purchase_claim, discovery_time, limit=2, pending=True)

        def evaluate(item, notify):
            queue.disposition(
                purchase_claim,
                item,
                discovery_time,
                status="evaluated",
                listing=listing.model_copy(update={"marketplace_item_id": item["item_id"]}),
                repository=repo,
                review={"notify": notify, "status": "possible_pressing"},
                state=purchase_state,
            )

        evaluate(first_item, False)
        with repo.engine.begin() as conn:
            saved = (
                conn.execute(
                    text(
                        "SELECT * FROM finder_save_decision"
                        "(:watch,'ebay',:item,'purchase',NULL,true,:stamp,NULL,NULL)"
                    ),
                    {
                        "watch": purchase_claim["id"],
                        "item": first_item["item_id"],
                        "stamp": discovery_time.isoformat(),
                    },
                )
                .mappings()
                .one()
            )
            assert saved["purchased"] and saved["verdict"] is None
            assert saved["prediction"] is None
        evaluate(first_item, True)
        assert not purchase_state["initial_digest_sent"]
        with repo.engine.connect() as conn:
            assert not conn.execute(
                select(outbox).where(outbox.c.watch_id == purchase_claim["id"])
            ).first()
        evaluate(second_item, True)
        assert purchase_state["initial_digest_sent"]
        with repo.engine.connect() as conn:
            assert conn.execute(
                select(outbox.c.marketplace_item_id).where(
                    outbox.c.watch_id == purchase_claim["id"], outbox.c.status == "pending"
                )
            ).scalars().all() == [second_item["item_id"]]
        phase = "shared_evidence_invalidation_and_same_event_recovery"
        from check_evidence_invalidation import check_evidence_invalidation

        def evidence_phase(value):
            nonlocal phase
            phase = "evidence_" + value

        check_evidence_invalidation(
            repo, store, queue, listing, discovery_time, on_phase=evidence_phase
        )
        from check_manual_refresh import check_manual_refresh

        def refresh_phase(value):
            nonlocal phase
            phase = "manual_refresh_" + value

        check_manual_refresh(repo, store, queue, listing, discovery_time, on_phase=refresh_phase)
        repo.delete_ebay_seller(listing.seller_id)
        phase = "assert_deletion"
        with repo.engine.connect() as conn:
            assert not conn.execute(select(inbox)).all()
            assert not conn.execute(select(outbox)).all()
            assert not conn.execute(select(verdicts)).all()
            assert not conn.execute(select(decisions)).all()
            assert not conn.execute(select(work)).all()
            assert conn.execute(select(progress)).first()
        rollback_pilot_schema(repo.engine)
        phase = "remigrate"
        migrate(repo.engine)
        # Production tables predate the wider watch limit; its old check must be replaced.
        phase = "widen_watch_slots"
        with repo.engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE finder_watches DROP CONSTRAINT finder_watches_slot_range")
            )
            conn.execute(
                text(
                    "ALTER TABLE finder_watches ADD CONSTRAINT finder_watches_slot_check "
                    "CHECK (slot >= 1 AND slot <= 3)"
                )
            )
        migrate(repo.engine)
        with repo.engine.connect() as conn:
            checks = (
                conn.execute(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = 'finder_watches'::regclass AND contype = 'c'"
                    )
                )
                .scalars()
                .all()
            )
        assert len(checks) == 1 and f"slot <= {MAX_WATCHES}" in checks[0]
        print(
            '{"postgres_migration_lease_dedup_deletion":"passed","synthetic_backup_restore_upgrade":"passed","discovery_shared_budget":"passed"}'
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"postgres_check": "failed", "phase": phase, "error_type": type(exc).__name__}
            )
        )
        return 1
    finally:
        if repo:
            repo.close()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
