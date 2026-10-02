"""Gate durable owner refreshes using the endpoint and real PostgreSQL row locks."""

import json
import os
import select as io_select
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text, update

from finder.adapters.ebay.target_search import EbaySearchTarget
from finder.discovery_store import DiscoveryStore, fence, progress, work
from finder.watch_store import SavedWatch, inbox, migrate, migrations, outbox, watches


class BoundTransaction:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def begin(self):
        with self.connection.begin():
            yield self.connection


class RefreshEndpoint:
    """Use a line protocol so the parent can inspect endpoint lock waits."""

    def __init__(self, engine):
        self.buffer = b""
        self.process = subprocess.Popen(
            ["node", "--import", "tsx", "scripts/check_manual_refresh.ts"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env={
                **os.environ,
                "FINDER_DATABASE_URL": engine.url.set(drivername="postgresql").render_as_string(
                    hide_password=False
                ),
            },
        )

    def send(self, **value):
        self.process.stdin.write(json.dumps(value).encode() + b"\n")
        self.process.stdin.flush()

    def receive(self, phase):
        deadline = monotonic() + 15
        while b"\n" not in self.buffer:
            remaining = deadline - monotonic()
            assert remaining > 0, "Refresh endpoint protocol timed out"
            ready, _, _ = io_select.select([self.process.stdout], [], [], remaining)
            assert ready, "Refresh endpoint did not respond"
            chunk = os.read(self.process.stdout.fileno(), 4096)
            assert chunk, "Refresh endpoint exited before responding"
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        result = json.loads(line)
        assert result["phase"] == phase, result
        return result

    def start(self, claim, item_id, *, hold=False):
        self.send(action="request", watch_id=claim["id"], item_id=item_id, hold=hold)
        return self.receive("started")["pid"]

    def finish(self, status=200):
        result = self.receive("result")
        assert result["status"] == status, result
        if status == 200:
            assert result["body"]["ok"]
        return result

    def request(self, claim, item_id, *, status=200):
        self.start(claim, item_id)
        return self.finish(status)

    def set_enabled(self, claim, enabled):
        self.send(action="set_enabled", watch_id=claim["id"], enabled=enabled)
        self.receive("started")
        return self.finish()

    def release(self, *, commit):
        self.send(action="commit" if commit else "rollback")
        self.receive("released")

    def close(self):
        try:
            self.send(action="close")
            assert self.process.wait(timeout=5) == 0
        finally:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            self.process.stdin.close()
            self.process.stdout.close()


def _row(engine, claim, item_id):
    with engine.connect() as conn:
        return dict(
            conn.execute(
                select(work).where(work.c.watch_id == claim["id"], work.c.item_id == item_id)
            )
            .mappings()
            .one()
        )


def _blocked(observer, holder, waiter):
    deadline = monotonic() + 5
    while not observer.execute(
        text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
        {"holder": holder, "waiter": waiter},
    ).scalar_one():
        assert monotonic() < deadline, "Endpoint and disposition must share the watch fence"
        sleep(0.02)


def check_manual_refresh(repo, store, queue, listing, now, *, on_phase):
    on_phase("seed_cached_leads")
    watch_id = store.add(SavedWatch(release_id=791), now=now - timedelta(days=1))
    claim = store.claim(now=now)
    assert claim and claim["id"] == watch_id
    state = queue.load(
        claim,
        EbaySearchTarget(id="refresh-check", catalog_variant_id=791, queries=["Synthetic"]),
        now,
    )
    refs = [
        {
            "itemId": f"v1|920001{index}|0",
            "title": "Synthetic manual refresh",
            "itemOriginDate": now.isoformat(),
        }
        for index in range(3)
    ]
    queue.checkpoint(claim, state, now, items=refs)
    seller = "synthetic-" + uuid4().hex
    copies = {
        ref["itemId"]: listing.model_copy(
            update={
                "marketplace_item_id": ref["itemId"],
                "seller_id": seller,
                "details_observed_at": now,
                "last_observed_at": now,
                "listing_ends_at": None,
            }
        )
        for ref in refs
    }
    for item in queue.due(claim, now, limit=10, pending=True):
        queue.disposition(
            claim,
            item,
            now,
            status="evaluated",
            listing=copies[item["item_id"]],
            repository=repo,
            review={"notify": False, "status": "family_review"},
        )
    ids = [ref["itemId"] for ref in refs]

    on_phase("restore_and_upgrade_legacy_work")
    legacy_columns = [
        column for column in work.c if column.name not in ("refresh_token", "refresh_after")
    ]
    with repo.engine.begin() as conn:
        backup = [dict(row) for row in conn.execute(select(*legacy_columns)).mappings()]
        conn.execute(text("ALTER TABLE finder_discovery_work DROP COLUMN refresh_token"))
        conn.execute(text("ALTER TABLE finder_discovery_work DROP COLUMN refresh_after"))
        conn.execute(delete(migrations).where(migrations.c.version == 6))
    migrate(repo.engine)
    migrate(repo.engine)
    with repo.engine.connect() as conn:
        restored = [dict(row) for row in conn.execute(select(*legacy_columns)).mappings()]
        assert sorted(backup, key=str) == sorted(restored, key=str)
        columns = conn.execute(
            text(
                "SELECT column_name, is_nullable, character_maximum_length "
                "FROM information_schema.columns WHERE table_schema=current_schema() "
                "AND table_name='finder_discovery_work' "
                "AND column_name IN ('refresh_token','refresh_after')"
            )
        ).all()
        assert set(columns) == {("refresh_token", "YES", 36), ("refresh_after", "YES", 40)}
        assert conn.execute(select(migrations).where(migrations.c.version == 6)).one()
        assert all(
            token is None and after is None
            for token, after in conn.execute(select(work.c.refresh_token, work.c.refresh_after))
        )

    def row(item_id):
        return _row(repo.engine, claim, item_id)

    def due(at):
        return {item["item_id"] for item in queue.due(claim, at, limit=10, pending=True)}

    endpoint = RefreshEndpoint(repo.engine)
    try:
        on_phase("unknown_reference_is_404")
        before = row(ids[0])
        with repo.engine.connect() as conn:
            watch_before = dict(
                conn.execute(select(watches).where(watches.c.id == watch_id)).mappings().one()
            )
        endpoint.request(claim, "v1|9299999|0", status=404)
        endpoint.request({"id": str(uuid4())}, ids[0], status=404)
        assert row(ids[0]) == before
        with repo.engine.connect() as conn:
            assert (
                dict(conn.execute(select(watches).where(watches.c.id == watch_id)).mappings().one())
                == watch_before
            )

        on_phase("legacy_completion_preserves_request")
        endpoint.request(claim, ids[0])
        requested = row(ids[0])
        UUID(requested["refresh_token"])
        assert requested["status"] == "pending"
        for legacy_status in ("evaluated", "unavailable"):
            # These are the old worker's columns only: an in-flight v19 write must
            # neither erase the additive marker nor postpone its independent due time.
            with repo.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE finder_discovery_work SET status=:status,reason=NULL,"
                        "checked_at=:stamp,next_check_at=:later,failures=0 "
                        "WHERE watch_id=:watch AND item_id=:item"
                    ),
                    {
                        "status": legacy_status,
                        "stamp": now.isoformat(),
                        "later": (now + timedelta(days=7)).isoformat(),
                        "watch": watch_id,
                        "item": ids[0],
                    },
                )
            after = row(ids[0])
            assert after["refresh_token"] == requested["refresh_token"]
            assert after["refresh_after"] == requested["refresh_after"]
            assert ids[0] in due(now)

        # Even the new worker must not mistake reused cache or an absent detail
        # result for a successfully completed provider read.
        for details in ({"listing": copies[ids[0]], "repository": repo}, {"hydrated": True}):
            queue.disposition(claim, row(ids[0]), now, status="evaluated", **details)
            assert row(ids[0])["refresh_token"] == requested["refresh_token"]
            assert ids[0] in due(now)

        on_phase("first_request_preserves_existing_error_backoff")
        existing_retry = now + timedelta(minutes=2)
        with repo.engine.begin() as conn:
            conn.execute(
                update(work)
                .where(work.c.watch_id == watch_id, work.c.item_id == ids[2])
                .values(status="error", next_check_at=existing_retry.isoformat(), failures=2)
            )
        endpoint.request(claim, ids[2])
        assert datetime.fromisoformat(row(ids[2])["refresh_after"]) == existing_retry
        assert datetime.fromisoformat(row(ids[2])["next_check_at"]) == existing_retry
        assert row(ids[2])["failures"] == 2
        assert ids[2] not in due(now)
        assert ids[2] in due(existing_retry)

        on_phase("request_retry_backoff_and_acknowledgment")
        failure_at = now + timedelta(minutes=1)
        queue.disposition(claim, row(ids[0]), failure_at, status="error", refresh_hours=0.05)
        failed = row(ids[0])
        retry_at = datetime.fromisoformat(failed["refresh_after"])
        assert retry_at >= failure_at + timedelta(minutes=3)
        assert failed["refresh_token"] == requested["refresh_token"]
        for _ in range(2):
            prior = row(ids[0])
            endpoint.request(claim, ids[0])
            after = row(ids[0])
            assert after["refresh_token"] != prior["refresh_token"]
            assert datetime.fromisoformat(after["refresh_after"]) >= retry_at
            assert after["failures"] == failed["failures"]
            assert ids[0] not in due(failure_at + timedelta(seconds=1))
        assert ids[0] in due(retry_at)
        current = row(ids[0])
        queue.disposition(
            claim,
            current,
            retry_at,
            status="evaluated",
            listing=copies[ids[0]].model_copy(
                update={"details_observed_at": retry_at, "last_observed_at": retry_at}
            ),
            repository=repo,
            hydrated=True,
        )
        assert row(ids[0])["refresh_token"] is None
        assert row(ids[0])["refresh_after"] is None
        endpoint.request(claim, ids[0])
        queue.disposition(claim, row(ids[0]), retry_at, status="unavailable", hydrated=True)
        assert row(ids[0])["refresh_token"] is None
        assert row(ids[0])["refresh_after"] is None

        on_phase("endpoint_waits_for_disposition")
        endpoint.request(claim, ids[1])
        old = row(ids[1])
        locked, release_worker = Event(), Event()

        def hold_fence(conn, worker_claim, stamp):
            fence(conn, worker_claim, stamp)
            locked.set()
            assert release_worker.wait(15)

        with repo.engine.connect() as worker, repo.engine.connect() as observer:
            worker_pid = worker.execute(text("SELECT pg_backend_pid()")).scalar_one()
            worker.commit()
            with (
                patch("finder.discovery_store.fence", hold_fence),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                saving = executor.submit(
                    DiscoveryStore(BoundTransaction(worker)).disposition,
                    claim,
                    old,
                    now,
                    status="evaluated",
                    listing=copies[ids[1]],
                    repository=repo,
                    hydrated=True,
                )
                try:
                    assert locked.wait(5)
                    endpoint_pid = endpoint.start(claim, ids[1])
                    _blocked(observer, worker_pid, endpoint_pid)
                finally:
                    release_worker.set()
                saving.result(timeout=10)
                endpoint.finish()
        assert row(ids[1])["refresh_token"] != old["refresh_token"]
        assert row(ids[1])["refresh_token"] and ids[1] in due(now)

        on_phase("disposition_waits_for_endpoint")
        old = row(ids[1])
        endpoint_pid = endpoint.start(claim, ids[1], hold=True)
        assert endpoint.finish()["held"]
        with repo.engine.connect() as worker, repo.engine.connect() as observer:
            worker_pid = worker.execute(text("SELECT pg_backend_pid()")).scalar_one()
            worker.commit()
            with ThreadPoolExecutor(max_workers=1) as executor:
                saving = executor.submit(
                    DiscoveryStore(BoundTransaction(worker)).disposition,
                    claim,
                    old,
                    now,
                    status="evaluated",
                    listing=copies[ids[1]],
                    repository=repo,
                    hydrated=True,
                )
                try:
                    _blocked(observer, endpoint_pid, worker_pid)
                finally:
                    endpoint.release(commit=True)
                saving.result(timeout=10)
        after = row(ids[1])
        assert after["refresh_token"] and after["refresh_token"] != old["refresh_token"]
        assert ids[1] in due(now)
        queue.disposition(claim, old, now, status="error", refresh_hours=48)
        assert row(ids[1])["refresh_token"] == after["refresh_token"]
        assert row(ids[1])["refresh_after"] == after["refresh_after"]
        assert ids[1] in due(now)
        migrate(repo.engine)
        assert row(ids[1])["refresh_token"] == after["refresh_token"]

        on_phase("paused_requests_preserve_work_and_watch")
        accepted = row(ids[1])
        assert accepted["refresh_token"]
        endpoint.set_enabled(claim, False)
        assert row(ids[1]) == accepted
        with repo.engine.connect() as conn:
            paused = dict(
                conn.execute(select(watches).where(watches.c.id == watch_id)).mappings().one()
            )
        assert not paused["enabled"] and not paused["config"]["enabled"]
        for item_id in (ids[0], ids[1]):
            before = row(item_id)
            response = endpoint.request(claim, item_id, status=409)
            assert "paused" in response["body"]["error"].lower()
            assert row(item_id) == before
        endpoint.request(claim, "v1|9299999|0", status=404)
        with repo.engine.connect() as conn:
            assert (
                dict(conn.execute(select(watches).where(watches.c.id == watch_id)).mappings().one())
                == paused
            )

        on_phase("accepted_request_survives_pause_and_resume")
        endpoint.set_enabled(claim, True)
        assert row(ids[1]) == accepted
        with repo.engine.connect() as conn:
            resumed = conn.execute(select(watches).where(watches.c.id == watch_id)).mappings().one()
        assert resumed["enabled"] and resumed["config"]["enabled"]
        assert ids[1] in due(now)

        on_phase("idle_watch_schedule_and_seller_deletion")
        with repo.engine.begin() as conn:
            conn.execute(
                update(watches)
                .where(watches.c.id == watch_id)
                .values(
                    lease_token=None,
                    lease_until=None,
                    next_scan_at=(now + timedelta(days=1)).isoformat(),
                )
            )
        endpoint.request(claim, ids[2])
        with repo.engine.connect() as conn:
            scheduled = conn.execute(
                select(watches.c.next_scan_at).where(watches.c.id == watch_id)
            ).scalar_one()
        assert datetime.fromisoformat(scheduled) <= datetime.now(UTC)
        assert row(ids[2])["refresh_token"]
        repo.delete_ebay_seller(seller)
        with repo.engine.connect() as conn:
            for table in (work, inbox, outbox):
                assert not conn.execute(select(table).where(table.c.watch_id == watch_id)).first()
            assert conn.execute(select(progress).where(progress.c.watch_id == watch_id)).first()
            assert conn.execute(select(watches).where(watches.c.id == watch_id)).first()
        endpoint.request(claim, ids[2], status=404)
    finally:
        endpoint.close()
    print('{"manual_refresh_postgres_endpoint_locking_legacy_upgrade_deletion":"passed"}')
