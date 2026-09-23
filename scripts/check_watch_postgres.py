"""Verify migration, leases and seller deletion in an isolated ephemeral PG schema."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from finder.adapters.ebay.normalize import normalize_listing
from finder.persistence import SqlAlchemyRepository
from finder.watch_store import (
    SavedWatch,
    WatchStore,
    inbox,
    migrate,
    outbox,
    rollback_pilot_schema,
)


def main():
    url = make_url(os.environ["FINDER_DATABASE_URL"]).set(drivername="postgresql+psycopg")
    admin = create_engine(url, hide_parameters=True)
    namespace = "finder_check_" + uuid4().hex
    repo = None
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{namespace}"'))
        isolated = url.update_query_dict({"options": f"-csearch_path={namespace}"})
        repo = SqlAlchemyRepository.from_url(isolated.render_as_string(hide_password=False))
        migrate(repo.engine)
        migrate(repo.engine)
        now = datetime.now(UTC)
        payload = json.loads(Path("tests/fixtures/ebay_search.json").read_text())
        listing = normalize_listing(payload["itemSummaries"][0], now).model_copy(
            update={"seller_id": "synthetic-" + uuid4().hex}
        )
        repo.upsert(listing)
        store = WatchStore(repo.engine)
        store.add(SavedWatch(release_id=123), now=now)
        claim = store.claim(now=now)
        assert store.claim(now=now) is None
        store.finish(claim, [(listing, {"notify": True})], now=now)
        later = now + timedelta(minutes=31)
        store.finish(store.claim(now=later), [(listing, {"notify": True})], now=later)
        with repo.engine.connect() as conn:
            assert len(conn.execute(select(inbox)).all()) == 1
            assert len(conn.execute(select(outbox)).all()) == 1
        repo.delete_ebay_seller(listing.seller_id)
        with repo.engine.connect() as conn:
            assert not conn.execute(select(inbox)).all()
            assert not conn.execute(select(outbox)).all()
        rollback_pilot_schema(repo.engine)
        migrate(repo.engine)
        print('{"postgres_migration_lease_dedup_deletion":"passed"}')
        return 0
    except Exception:
        print('{"postgres_check":"failed"}')
        return 1
    finally:
        if repo:
            repo.close()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
