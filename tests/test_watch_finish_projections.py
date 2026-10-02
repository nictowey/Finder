"""Finish must narrow locked reads without weakening freshness or lease fences."""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import JSON, delete, event, select, update
from sqlalchemy.dialects import postgresql

from finder.adapters.ebay.normalize import normalize_listing
from finder.evidence import INVALIDATED_AT
from finder.persistence import listings
from finder.watch_store import (
    SavedWatch,
    WatchStore,
    inbox,
    invalidate_review_evidence,
    migrate,
    outbox,
    scan_attempts,
    watches,
)


@pytest.fixture
def finish_case(repository, search_payload, observed_at):
    migrate(repository.engine)
    store = WatchStore(repository.engine)
    listing = normalize_listing(search_payload["itemSummaries"][0], observed_at).model_copy(
        update={"details_observed_at": observed_at}
    )
    repository.upsert(listing)
    watch_id = store.add(SavedWatch(release_id=123), now=observed_at)
    with repository.engine.begin() as conn:
        conn.execute(
            update(watches)
            .where(watches.c.id == watch_id)
            .values(catalog={"large_unused_payload": "x" * 100_000})
        )
    claim = store.claim(now=observed_at)
    return store, claim, listing


@contextmanager
def selected_statements(engine):
    statements = []

    def collect(conn, statement, multiparams, params, execution_options):
        if statement.is_select:
            statements.append(statement)

    event.listen(engine, "before_execute", collect)
    try:
        yield statements
    finally:
        event.remove(engine, "before_execute", collect)


def set_snapshot(repository, listing, metadata, **extra):
    snapshot = {**listing.model_dump(mode="json"), "source_metadata": metadata, **extra}
    with repository.engine.begin() as conn:
        conn.execute(update(listings).values(data=snapshot))


@pytest.mark.parametrize("checkpoint", [False, True])
def test_finish_projects_only_needed_watch_and_listing_fields(
    repository, finish_case, observed_at, checkpoint
):
    store, claim, listing = finish_case
    with selected_statements(repository.engine) as statements:
        assert (
            store.finish(
                claim, [(listing, {"notify": True})], now=observed_at, checkpoint=checkpoint
            )
            == 1
        )
    watch_read, listing_read, inbox_read = statements[:3]
    assert list(watch_read.selected_columns.keys()) == ["id" if checkpoint else "summary"]
    assert list(listing_read.selected_columns.keys()) == [
        "shared_boundary",
        "listing_document",
        "document_type",
        "metadata_type",
    ]
    assert isinstance(listing_read.selected_columns.shared_boundary.type, JSON)
    # PostgreSQL compilation preserves watch-then-listing lock strength/order.
    watch_sql = str(watch_read.compile(dialect=postgresql.dialect()))
    listing_sql = str(listing_read.compile(dialect=postgresql.dialect()))
    assert watch_sql.endswith("FOR NO KEY UPDATE")
    assert listing_sql.endswith("FOR UPDATE")
    assert "finder_watches.lease_token =" in watch_sql
    assert "finder_watches.revision =" in watch_sql
    assert "finder_watches.enabled IS true" in watch_sql
    assert "finder_watches.lease_until >" in watch_sql
    assert ("source_metadata", INVALIDATED_AT) in listing_read.compile().params.values()
    assert set(inbox_read.selected_columns.keys()) == set(inbox.c.keys())


def test_postgres_finish_guards_json_extraction_without_changing_listing_lock(
    finish_case, observed_at
):
    store, claim, listing = finish_case
    connection = Mock(dialect=postgresql.dialect())
    watch_result = Mock()
    watch_result.mappings.return_value.first.return_value = {"id": claim["id"]}
    connection.execute.side_effect = [watch_result, RuntimeError("captured listing read")]
    with pytest.raises(RuntimeError, match="captured listing read"):
        store.finish(
            claim,
            [(listing, {"notify": True})],
            now=observed_at,
            checkpoint=True,
            connection=connection,
        )
    listing_read = connection.execute.call_args_list[1].args[0]
    sql = str(listing_read.compile(dialect=postgresql.dialect()))
    assert "CASE WHEN" in sql and " #> " in sql
    assert "json_typeof(listings.data) AS document_type" in sql
    assert "json_typeof(CASE WHEN" in sql and "AS metadata_type" in sql
    assert "THEN listings.data" in sql
    assert sql.endswith("FOR UPDATE")


@pytest.mark.parametrize("checkpoint", [False, True])
def test_finish_uses_current_summary_and_preserves_checkpoint_lease(
    repository, finish_case, observed_at, checkpoint
):
    store, claim, _ = finish_case
    inventory = {"complete": True, "count": 8}
    with repository.engine.begin() as conn:
        conn.execute(update(watches).values(summary={"inventory": inventory, "old": "ignored"}))
        before = dict(conn.execute(select(watches)).mappings().one())
    assert (
        store.finish(
            claim, [], success=False, summary={"failed": 1}, now=observed_at, checkpoint=checkpoint
        )
        == 0
    )
    with repository.engine.connect() as conn:
        after = dict(conn.execute(select(watches)).mappings().one())
        attempt = conn.execute(select(scan_attempts)).mappings().one()
    if checkpoint:
        assert after == before
        assert attempt["status"] == "started" and attempt["finished_at"] is None
    else:
        assert after["summary"] == {"failed": 1, "inventory": inventory}
        assert after["lease_token"] is None and after["lease_until"] is None
        assert after["status"] == "failed" and after["last_success_at"] is None
        assert attempt["status"] == "failed" and attempt["finished_at"] == observed_at.isoformat()


@pytest.mark.parametrize("checkpoint", [False, True])
@pytest.mark.parametrize("fence", ["token", "revision", "disabled", "expired"])
def test_finish_rejects_stale_fences_before_listing_reads(
    repository, finish_case, observed_at, checkpoint, fence
):
    store, claim, listing = finish_case
    changes = {
        "token": {"lease_token": "replacement-token"},
        "revision": {"revision": claim["revision"] + 1},
        "disabled": {"enabled": False},
        "expired": {"lease_until": observed_at.isoformat()},
    }
    with repository.engine.begin() as conn:
        conn.execute(update(watches).values(**changes[fence]))
        before = dict(conn.execute(select(watches)).mappings().one())
    with selected_statements(repository.engine) as statements:
        assert (
            store.finish(
                claim, [(listing, {"notify": True})], now=observed_at, checkpoint=checkpoint
            )
            is None
        )
    assert len(statements) == 1
    with repository.engine.connect() as conn:
        assert dict(conn.execute(select(watches)).mappings().one()) == before
        assert conn.execute(select(scan_attempts.c.status)).scalar_one() == "superseded"
        assert conn.execute(select(inbox)).first() is None
        assert conn.execute(select(outbox)).first() is None


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {INVALIDATED_AT: None},
        {INVALIDATED_AT: False},
        {INVALIDATED_AT: 20260911},
        {INVALIDATED_AT: 1.2},
        {INVALIDATED_AT: []},
        {INVALIDATED_AT: {"timestamp": "2026-09-11T12:01:00Z"}},
        {INVALIDATED_AT: "invalid"},
        {INVALIDATED_AT: "2026-09-11T12:01:00"},
        {INVALIDATED_AT: "2026-09-11T12:01:00+00:00\x00ignored"},
        {INVALIDATED_AT: "2026-09-11T12:01:00+00:00\ud800"},
    ],
)
def test_missing_null_or_unparseable_shared_marker_keeps_original_eligibility(
    repository, finish_case, observed_at, metadata
):
    store, claim, listing = finish_case
    set_snapshot(repository, listing, metadata)
    assert store.finish(claim, [(listing, {"notify": True})], now=observed_at) == 1
    with repository.engine.connect() as conn:
        data = conn.execute(select(inbox.c.data)).scalar_one()
        assert data["notify"] and "evidence_invalidated_at" not in data
        assert conn.execute(select(outbox.c.status)).scalar_one() == "pending"


@pytest.mark.parametrize("details_offset", [-1, 0, 1, None])
@pytest.mark.parametrize("boundary_suffix", ["+00:00", "Z", "+01:00"])
def test_shared_boundary_blocks_old_or_missing_details_and_allows_equal_or_newer(
    repository, finish_case, observed_at, details_offset, boundary_suffix
):
    store, claim, listing = finish_case
    boundary = observed_at + timedelta(minutes=1)
    raw_boundary = boundary.isoformat().replace("+00:00", boundary_suffix)
    if boundary_suffix == "+01:00":
        raw_boundary = (boundary + timedelta(hours=1)).isoformat().replace("+00:00", "+01:00")
    set_snapshot(repository, listing, {INVALIDATED_AT: raw_boundary})
    listing = listing.model_copy(
        update={
            "details_observed_at": (
                boundary + timedelta(seconds=details_offset) if details_offset is not None else None
            )
        }
    )
    assert (
        store.finish(
            claim, [(listing, {"notify": True, "subtotal": "25"})], now=boundary, checkpoint=True
        )
        == 1
    )
    eligible = details_offset is not None and details_offset >= 0
    with repository.engine.connect() as conn:
        data = conn.execute(select(inbox.c.data)).scalar_one()
        assert data["notify"] is eligible
        assert bool(conn.execute(select(outbox)).first()) is eligible
    if eligible:
        assert data["subtotal"] == "25" and "evidence_invalidated_at" not in data
    else:
        assert data["subtotal"] is None and data["budget"] == "needs_refresh"
        assert data["evidence_invalidated_reason"] == "seller_changed"
        assert data["verify"] == ["seller_details_changed"]


@pytest.mark.parametrize("missing_listing", [False, True])
def test_absent_or_json_null_listing_does_not_create_review(
    repository, finish_case, observed_at, missing_listing
):
    store, claim, listing = finish_case
    with repository.engine.begin() as conn:
        conn.execute(
            delete(listings) if missing_listing else update(listings).values(data=JSON.NULL)
        )
    assert store.finish(claim, [(listing, {"notify": True})], now=observed_at) == 0
    with repository.engine.connect() as conn:
        assert conn.execute(select(inbox)).first() is None
        assert conn.execute(select(outbox)).first() is None


@pytest.mark.parametrize(
    "snapshot",
    [
        [],
        "scalar",
        3,
        False,
        {"source_metadata": None},
        {"source_metadata": []},
        {"source_metadata": "scalar"},
        {"source_metadata": 3},
        {"source_metadata": False},
    ],
)
def test_nonobject_snapshot_or_metadata_fails_closed(
    repository, finish_case, observed_at, snapshot
):
    store, claim, listing = finish_case
    with repository.engine.begin() as conn:
        conn.execute(update(listings).values(data=snapshot))
    with pytest.raises(AttributeError):
        store.finish(claim, [(listing, {"notify": True})], now=observed_at)
    with repository.engine.connect() as conn:
        assert conn.execute(select(inbox)).first() is None
        assert conn.execute(select(outbox)).first() is None
        assert conn.execute(select(watches.c.lease_token)).scalar_one() == claim["lease_token"]
        assert conn.execute(select(scan_attempts.c.status)).scalar_one() == "started"


def test_missing_metadata_keeps_original_eligibility(repository, finish_case, observed_at):
    store, claim, listing = finish_case
    snapshot = listing.model_dump(mode="json")
    snapshot.pop("source_metadata")
    with repository.engine.begin() as conn:
        conn.execute(update(listings).values(data=snapshot))
    assert store.finish(claim, [(listing, {"notify": True})], now=observed_at) == 1
    with repository.engine.connect() as conn:
        assert conn.execute(select(inbox.c.data)).scalar_one()["notify"]
        assert conn.execute(select(outbox.c.status)).scalar_one() == "pending"


def test_finish_keeps_changes_inside_the_supplied_transaction(repository, finish_case, observed_at):
    store, claim, listing = finish_case
    with repository.engine.connect() as conn:
        transaction = conn.begin()
        assert (
            store.finish(
                claim,
                [(listing, {"notify": True})],
                now=observed_at,
                checkpoint=True,
                connection=conn,
            )
            == 1
        )
        assert conn.execute(select(inbox)).first() is not None
        assert conn.execute(select(outbox)).first() is not None
        transaction.rollback()
    with repository.engine.connect() as conn:
        assert conn.execute(select(inbox)).first() is None
        assert conn.execute(select(outbox)).first() is None
        assert conn.execute(select(watches.c.lease_token)).scalar_one() == claim["lease_token"]
        assert conn.execute(select(scan_attempts.c.status)).scalar_one() == "started"


@pytest.mark.parametrize(
    "flag", ["details_unavailable", "item_specifics_stale", "details_not_requested"]
)
def test_current_timestamp_with_stale_quality_flag_still_blocks_alert(
    repository, finish_case, observed_at, flag
):
    store, claim, listing = finish_case
    set_snapshot(repository, listing, {INVALIDATED_AT: observed_at.isoformat()})
    stale = listing.model_copy(update={"quality_flags": [flag]})
    assert store.finish(claim, [(stale, {"notify": True})], now=observed_at) == 1
    with repository.engine.connect() as conn:
        data = conn.execute(select(inbox.c.data)).scalar_one()
        assert not data["notify"]
        assert data["evidence_invalidated_at"] == observed_at.isoformat()
        assert conn.execute(select(outbox)).first() is None


def test_fresh_review_resumes_same_unattempted_event_and_older_finish_cannot_overwrite(
    repository, finish_case, observed_at
):
    store, claim, listing = finish_case
    review = {"notify": True, "subtotal": "25", "policy": "private-target-review-v19"}
    assert store.finish(claim, [(listing, review)], now=observed_at, checkpoint=True) == 1
    with repository.engine.connect() as conn:
        event_id = conn.execute(select(outbox.c.id)).scalar_one()
    boundary = observed_at + timedelta(minutes=1)
    with repository.engine.begin() as conn:
        invalidate_review_evidence(
            conn,
            listing.marketplace,
            listing.marketplace_item_id,
            boundary,
            reason="seller_changed",
        )
    assert store.finish(claim, [(listing, review)], now=boundary, checkpoint=True) == 0
    with repository.engine.connect() as conn:
        suspended = conn.execute(select(outbox)).mappings().one()
        data = conn.execute(select(inbox.c.data)).scalar_one()
    assert suspended["id"] == event_id and suspended["status"] == "expired"
    assert data["evidence_pending_event_id"] == event_id and not data["notify"]
    fresh = listing.model_copy(update={"details_observed_at": boundary})
    assert store.finish(claim, [(fresh, review)], now=boundary, checkpoint=True) == 0
    with repository.engine.connect() as conn:
        resumed = conn.execute(select(outbox)).mappings().one()
        fresh_data = conn.execute(select(inbox.c.data)).scalar_one()
    assert resumed["id"] == event_id and resumed["status"] == "pending"
    assert resumed["attempts"] == 0
    assert fresh_data["notify"] and "evidence_pending_event_id" not in fresh_data
    assert "evidence_invalidated_at" not in fresh_data
    assert store.finish(claim, [(listing, {"notify": False})], now=boundary, checkpoint=True) == 0
    with repository.engine.connect() as conn:
        assert conn.execute(select(inbox.c.data)).scalar_one() == fresh_data
        assert dict(conn.execute(select(outbox)).mappings().one()) == dict(resumed)


@pytest.mark.parametrize(
    "unused", ["nul\x00", "lone\ud800", "lone\udfff", "pair\ud800\udc00", "💿"]
)
def test_unrelated_unicode_does_not_hide_a_shared_invalidation_boundary(
    repository, finish_case, observed_at, unused
):
    store, claim, listing = finish_case
    boundary = observed_at + timedelta(minutes=1)
    set_snapshot(repository, listing, {INVALIDATED_AT: boundary.isoformat()}, unused=unused)
    assert store.finish(claim, [(listing, {"notify": True})], now=boundary) == 1
    with repository.engine.connect() as conn:
        data = conn.execute(select(inbox.c.data)).scalar_one()
        assert not data["notify"]
        assert data["evidence_invalidated_at"] == boundary.isoformat()
        assert conn.execute(select(outbox)).first() is None
