"""Verify migration, leases and seller deletion in an isolated ephemeral PG schema."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, delete, insert, select, text
from sqlalchemy.engine import make_url

from finder.adapters.ebay.normalize import normalize_listing
from finder.persistence import SqlAlchemyRepository
from finder.watch_store import (
    NEW_TABLES,
    SavedWatch,
    WatchStore,
    dispatch_attempts,
    inbox,
    migrate,
    migrations,
    outbox,
    rollback_pilot_schema,
    scan_attempts,
)


def main():
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
        repo.delete_ebay_seller(listing.seller_id)
        phase = "assert_deletion"
        with repo.engine.connect() as conn:
            assert not conn.execute(select(inbox)).all()
            assert not conn.execute(select(outbox)).all()
        rollback_pilot_schema(repo.engine)
        phase = "remigrate"
        migrate(repo.engine)
        print(
            '{"postgres_migration_lease_dedup_deletion":"passed","synthetic_backup_restore_upgrade":"passed"}'
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
