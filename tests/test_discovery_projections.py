"""Narrow worker reads retain cadence, queue eligibility and checkpoint semantics."""

import math
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import case, delete, event, insert, select, update

from finder.adapters.ebay.target_search import EbaySearchTarget
from finder.discovery_store import DiscoveryStore, progress, work
from finder.discovery_worker import POLL_BUDGET, detail_batch, poll_minutes
from finder.persistence import listings
from finder.watch_store import SavedWatch, WatchStore, inbox, migrate, watches

NOW = datetime(2026, 10, 2, tzinfo=UTC)
MISSING = object()


@contextmanager
def selected(engine):
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


@pytest.fixture
def discovery(repository):
    migrate(repository.engine)
    store = WatchStore(repository.engine)
    store.add(SavedWatch(release_id=123), now=NOW)
    claim = store.claim(now=NOW)
    queue = DiscoveryStore(repository.engine)
    state = queue.load(
        claim, EbaySearchTarget(id="test", catalog_variant_id=123, queries=["Album"]), NOW
    )
    return repository, claim, queue, state


def legacy_due(engine, claim, now, *, limit, pending):
    """The pre-projection query is an independent oracle for order and eligibility."""
    requested = work.c.refresh_token.is_not(None)
    ordinary_due = (work.c.next_check_at <= now.isoformat()) | (
        inbox.c.data["evidence_invalidated_at"].as_string().is_not(None) if not pending else False
    )
    ordinary = (
        ~requested
        & ordinary_due
        & (work.c.status.in_(("pending", "error")) if pending else work.c.status == "evaluated")
    )
    eligible = (
        ordinary | (requested & (work.c.refresh_after <= now.isoformat())) if pending else ordinary
    )
    with engine.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                select(work, inbox.c.data.label("review_data"))
                .outerjoin(
                    inbox,
                    (inbox.c.watch_id == work.c.watch_id)
                    & (inbox.c.marketplace == "ebay")
                    & (inbox.c.marketplace_item_id == work.c.item_id),
                )
                .where(work.c.watch_id == claim["id"], eligible)
                .order_by(
                    case((requested, work.c.refresh_after), else_=work.c.next_check_at),
                    work.c.first_seen_at,
                    work.c.item_id,
                )
                .limit(limit)
            ).mappings()
        ]


def seed_work(repo, claim, *, count=1000):
    """Include ties, refresh requests, backoff, missing reviews and unusual markers."""
    markers = [MISSING, None, "", NOW.isoformat(), False, 0, [], {}, ["changed"], "not-a-date"]
    rows, reviews = [], []
    for index in range(count):
        item_id = f"synthetic-{index:04}"
        stamp = (NOW + timedelta(minutes=(-1 if index % 3 else 1))).isoformat()
        requested = index % 7 == 0
        rows.append(
            dict(
                watch_id=claim["id"],
                item_id=item_id,
                fingerprint="fingerprint",
                first_seen_at=(NOW - timedelta(days=index % 5)).isoformat(),
                last_search_at=NOW.isoformat(),
                next_check_at=stamp,
                status=("pending", "error", "evaluated", "unavailable", "suppressed")[index % 5],
                kind="new_to_finder",
                failures=index % 2,
                refresh_token=f"request-{index}" if requested else None,
                refresh_after=stamp if requested else None,
            )
        )
        if index % 11:
            marker = markers[index % len(markers)]
            data = {"unrelated": "synthetic " * 100}
            if marker is not MISSING:
                data["evidence_invalidated_at"] = marker
            reviews.append(
                dict(
                    watch_id=claim["id"],
                    marketplace="ebay",
                    marketplace_item_id=item_id,
                    first_seen_at=NOW.isoformat(),
                    last_seen_at=NOW.isoformat(),
                    dismissed=False,
                    alerted=False,
                    data=data,
                )
            )
    with repo.engine.begin() as conn:
        conn.execute(
            insert(listings),
            [
                dict(
                    marketplace="ebay",
                    marketplace_item_id=row["item_id"],
                    data={},
                    first_observed_at=NOW.isoformat(),
                    last_observed_at=NOW.isoformat(),
                )
                for row in rows
            ],
        )
        conn.execute(insert(work), rows)
        conn.execute(insert(inbox), reviews)


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("limit", [0, 1, 12, 16, 300])
def test_due_projects_marker_with_identical_eligible_rows_and_order(discovery, pending, limit):
    repo, claim, queue, _ = discovery
    seed_work(repo, claim)
    before = legacy_due(repo.engine, claim, NOW, limit=limit, pending=pending)
    expected = []
    for row in before:
        review = row.pop("review_data")
        marker = (review or {}).get("evidence_invalidated_at")
        expected.append(
            {
                **row,
                "review_data": {"evidence_invalidated_at": marker} if marker is not None else None,
            }
        )
    with selected(repo.engine) as statements:
        actual = queue.due(claim, NOW, limit=limit, pending=pending)
    assert actual == expected
    assert all(
        row["review_data"] is None or set(row["review_data"]) == {"evidence_invalidated_at"}
        for row in actual
    )
    assert "finder_inbox.data AS review_data" not in statements[0]


def test_detail_batch_keeps_old_reservation_and_pending_budget(discovery):
    repo, claim, queue, _ = discovery
    seed_work(repo, claim)
    old = legacy_due(repo.engine, claim, NOW, limit=16, pending=False)
    reserved = old[:4]
    pending = legacy_due(repo.engine, claim, NOW, limit=16 - len(reserved), pending=True)
    expected = reserved + pending + old[4 : 4 + 16 - len(reserved) - len(pending)]
    assert [row["item_id"] for row in detail_batch(queue, claim, NOW)] == [
        row["item_id"] for row in expected
    ]


@pytest.mark.parametrize("pending", [False, True])
def test_has_due_matches_old_limit_one_without_returning_work_payload(discovery, pending):
    repo, claim, queue, _ = discovery
    for populated in (False, True):
        if populated:
            seed_work(repo, claim)
        expected = bool(legacy_due(repo.engine, claim, NOW, limit=1, pending=pending))
        with selected(repo.engine) as statements:
            assert queue.has_due(claim, NOW, pending=pending) is expected
        assert len(statements) == 1 and "EXISTS" in statements[0]
        assert "SELECT finder_discovery_work.watch_id" not in statements[0]


@pytest.mark.parametrize("prior", [MISSING, None, "same", "other", False, 0, {}, []])
def test_checkpoint_compares_only_signature_without_losing_json_types(discovery, prior):
    repo, claim, queue, state = discovery
    queue.checkpoint(claim, state, NOW, items=[{"itemId": "synthetic", "title": "Album"}])
    previous = {**state, "unused": "synthetic " * 1000}
    if prior is not MISSING:
        previous["evaluation_signature"] = prior
    with repo.engine.begin() as conn:
        conn.execute(update(progress).values(data=previous))
        conn.execute(update(work).values(status="evaluated"))
    state["evaluation_signature"] = "same"
    with selected(repo.engine) as statements:
        queue.checkpoint(claim, state, NOW)
    with repo.engine.connect() as conn:
        row = conn.execute(select(work)).mappings().one()
        assert row["status"] == ("evaluated" if prior == "same" else "pending")
        assert conn.execute(select(progress.c.data)).scalar_one() == state
    assert not any(
        statement.startswith("SELECT finder_discovery.data \n") for statement in statements
    )


def test_poll_counts_arrays_without_returning_progress_and_preserves_legacy_fallbacks(repository):
    migrate(repository.engine)
    cases = [
        MISSING,
        None,
        {},
        [],
        {"queries": None},
        {"queries": []},
        {"queries": [1, 2]},
        {"queries": "album"},
        {"queries": {"first": 1, "second": 2}},
        {"queries": False},
        {"queries": [None] * 6},
        {"queries": [None] * 6},
        {"queries": [None] * 6},
    ]
    with repository.engine.begin() as conn:
        for i, value in enumerate(cases):
            watch_id = f"watch-{i}"
            conn.execute(
                insert(watches).values(
                    id=watch_id,
                    slot=i + 1,
                    config={},
                    revision=1,
                    enabled=True,
                    next_scan_at=NOW.isoformat(),
                    status="new",
                    summary={},
                )
            )
            if value is not MISSING:
                conn.execute(insert(progress).values(watch_id=watch_id, data=value))
        conn.execute(
            insert(watches).values(
                id="disabled",
                slot=20,
                config={},
                revision=1,
                enabled=False,
                next_scan_at=NOW.isoformat(),
                status="new",
                summary={},
            )
        )
        conn.execute(insert(progress).values(watch_id="disabled", data={"queries": [None] * 1000}))
        rows = (
            conn.execute(
                select(progress.c.data)
                .select_from(watches.outerjoin(progress, progress.c.watch_id == watches.c.id))
                .where(watches.c.enabled.is_(True))
            )
            .scalars()
            .all()
        )
    queries = sum(
        len(data["queries"]) if isinstance(data, dict) and data.get("queries") else 3
        for data in rows
    )
    with selected(repository.engine) as statements:
        assert poll_minutes(repository.engine) == max(10, math.ceil(1440 * queries / POLL_BUDGET))
    assert "json_array_length" in statements[0]
    assert not statements[0].startswith("SELECT finder_discovery.data \n")
    # No persisted progress gives SQL NULL via the outer join and keeps the default.
    with repository.engine.begin() as conn:
        conn.execute(delete(progress))
    assert poll_minutes(repository.engine) == max(
        10, math.ceil(1440 * len(cases) * 3 / POLL_BUDGET)
    )


def test_isolated_worker_projection_gate_runs_on_sqlite(repository, search_payload):
    from finder.adapters.ebay.normalize import normalize_listing
    from scripts.check_worker_projections import check_worker_projections

    migrate(repository.engine)
    listing = normalize_listing(search_payload["itemSummaries"][0], NOW)
    check_worker_projections(repository, NOW, listing)


@pytest.mark.parametrize("data", [True, "bad root", [1], 1, False, 0, [], None])
def test_due_retains_malformed_review_until_original_worker_access(discovery, data):
    repo, claim, queue, _ = discovery
    seed_work(repo, claim, count=2)
    with repo.engine.begin() as conn:
        conn.execute(update(work).values(refresh_token="request", refresh_after=NOW.isoformat()))
        conn.execute(update(inbox).values(data=data))
    original = legacy_due(repo.engine, claim, NOW, limit=2, pending=True)
    actual = queue.due(claim, NOW, limit=2, pending=True)
    assert actual == original
    # The worker skips cached reuse for an owner refresh before reading the review.
    for item in actual:
        assert item.get("refresh_token") or not (item.get("review_data") or {}).get(
            "evidence_invalidated_at"
        )


@pytest.mark.parametrize("data", [True, "bad root", [1], 1])
def test_checkpoint_keeps_malformed_progress_failure_and_transaction(discovery, data):
    repo, claim, queue, state = discovery
    with repo.engine.begin() as conn:
        conn.execute(update(progress).values(data=data))
    with pytest.raises(AttributeError):
        queue.checkpoint(claim, state, NOW)
    with repo.engine.connect() as conn:
        assert conn.execute(select(progress.c.data)).scalar_one() == data


def test_synthetic_due_transfer_at_cached_cap(discovery):
    """Measure returned SQL rows, including fallback columns, rather than Python output."""
    import json

    repo, claim, queue, _ = discovery
    seed_work(repo, claim, count=301)
    with repo.engine.begin() as conn:
        conn.execute(
            update(work).values(
                status="pending",
                refresh_token=None,
                refresh_after=None,
                next_check_at=NOW.isoformat(),
            )
        )
        conn.execute(
            update(inbox).values(
                data={
                    "evidence_invalidated_at": None,
                    "synthetic_padding": "x" * 12000,
                }
            )
        )
    original = legacy_due(repo.engine, claim, NOW, limit=300, pending=True)
    captured = []

    def capture(conn, clauseelement, multiparams, params, execution_options):
        if getattr(clauseelement, "is_select", False):
            captured.append(clauseelement)

    event.listen(repo.engine, "before_execute", capture)
    try:
        projected = queue.due(claim, NOW, limit=300, pending=True)
    finally:
        event.remove(repo.engine, "before_execute", capture)
    with repo.engine.connect() as conn:
        transferred = [dict(row) for row in conn.execute(captured[0]).mappings()]
    assert len(original) == len(projected) == len(transferred) == 300
    assert [row["item_id"] for row in projected] == [row["item_id"] for row in original]
    original_bytes = len(json.dumps(original, separators=(",", ":")).encode())
    projected_bytes = len(json.dumps(transferred, separators=(",", ":")).encode())
    assert projected_bytes < original_bytes * 0.1
    print(
        json.dumps(
            {
                "synthetic_due_rows": 300,
                "original_bytes": original_bytes,
                "projected_bytes": projected_bytes,
            }
        )
    )
