from datetime import timedelta

from sqlalchemy import insert, select, update

from finder.operations import scan_metrics
from finder.watch_store import (
    SavedWatch,
    WatchStore,
    dispatch_attempts,
    migrate,
    scan_attempts,
    watches,
)


def setup_watch(repository, observed_at):
    migrate(repository.engine)
    store = WatchStore(repository.engine)
    store.add(SavedWatch(release_id=123), now=observed_at)
    return store


def test_history_records_due_delay_and_preserves_failures(repository, observed_at):
    store = setup_watch(repository, observed_at)
    late = observed_at + timedelta(minutes=21)
    claim = store.claim(now=late, context={"source": "github_schedule", "run_id": "1234"})
    store.finish(
        claim,
        [],
        success=False,
        now=late + timedelta(seconds=30),
        summary={"error": "discovery_failed", "discovery": {"browse_requests": 4}},
    )
    later = late + timedelta(minutes=31)
    store.finish(store.claim(now=later), [], now=later)
    with repository.engine.connect() as conn:
        rows = (
            conn.execute(select(scan_attempts).order_by(scan_attempts.c.started_at))
            .mappings()
            .all()
        )
    assert [r["status"] for r in rows] == ["failed", "completed"]
    assert rows[0]["due_at"] == observed_at.isoformat()
    assert rows[0]["started_at"] == late.isoformat()
    assert rows[0]["run_id"] == "1234"
    assert rows[0]["metrics"]["browse_requests"] == 4


def test_killed_worker_stays_abandoned_after_recovery(repository, observed_at):
    store = setup_watch(repository, observed_at)
    old = store.claim(now=observed_at)
    later = observed_at + timedelta(minutes=13)
    recovered = store.claim(now=later)
    assert store.finish(old, [], now=later) is None
    store.finish(recovered, [], now=later)
    with repository.engine.connect() as conn:
        rows = conn.execute(select(scan_attempts)).mappings().all()
    assert {r["status"] for r in rows} == {"abandoned", "completed"}
    assert next(r for r in rows if r["status"] == "abandoned")["finished_at"] is None


def test_edit_and_expiry_cannot_record_false_success(repository, observed_at):
    store = setup_watch(repository, observed_at)
    claim = store.claim(now=observed_at)
    assert store.finish(claim, [], now=observed_at + timedelta(minutes=13)) is None
    with repository.engine.begin() as conn:
        assert conn.execute(select(watches.c.last_success_at)).scalar() is None
        conn.execute(update(watches).values(revision=2, lease_token=None))
    second = store.claim(now=observed_at + timedelta(minutes=14))
    with repository.engine.begin() as conn:
        conn.execute(update(watches).values(revision=3, lease_token=None))
    assert store.finish(second, [], now=observed_at + timedelta(minutes=15)) is None


def test_quota_pause_is_history_not_success_and_does_not_move_due_time(repository, observed_at):
    store = setup_watch(repository, observed_at)
    claim = store.claim(now=observed_at)
    store.pause_for_quota(
        claim,
        reason="insufficient_budget",
        remaining=100,
        required=276,
        now=observed_at + timedelta(seconds=5),
    )
    with repository.engine.connect() as conn:
        record = conn.execute(select(scan_attempts)).mappings().one()
        watch = conn.execute(select(watches)).mappings().one()
    assert record["status"] == "quota_paused"
    assert record["metrics"]["quota_remaining"] == 100
    assert watch["next_scan_at"] == observed_at.isoformat()
    assert watch["last_success_at"] is None


def test_catchup_source_requires_a_recorded_dispatch(repository, observed_at):
    store = setup_watch(repository, observed_at)
    context = {"source": "neon_catchup", "dispatch_id": "fake", "run_id": "123"}
    claim = store.claim(now=observed_at, context=context)
    store.finish(claim, [], now=observed_at)
    with repository.engine.begin() as conn:
        assert conn.execute(select(scan_attempts.c.source)).scalar() == "manual"
        conn.execute(
            insert(dispatch_attempts).values(
                id="real",
                status="accepted",
                scheduled_at=observed_at.isoformat(),
                started_at=observed_at.isoformat(),
            )
        )
    later = observed_at + timedelta(minutes=31)
    store.claim(now=later, context={**context, "dispatch_id": "real"})
    with repository.engine.connect() as conn:
        row = (
            conn.execute(select(scan_attempts).where(scan_attempts.c.dispatch_id == "real"))
            .mappings()
            .one()
        )
    assert row["source"] == "neon_catchup"


def test_operational_metrics_drop_provider_evidence():
    metrics = scan_metrics(
        {
            "title": "Private seller title",
            "error": "Private exception",
            "discovery": {
                "browse_requests": 3,
                "query": "private query",
                "newest": [{"cap_reached": True, "partial_details": 2, "seller": "private seller"}],
            },
            "catalog_check_failed": True,
        },
        4,
    )
    assert metrics["capped_pages"] == 1
    assert metrics["browse_requests"] == 3
    assert metrics["new_inbox_rows"] == 4
    assert "Private" not in str(metrics) and "private" not in str(metrics)
