"""Synthetic SQL/transaction contracts for snapshot-only cached dispositions."""

from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from finder.adapters.ebay.target_search import EbaySearchTarget
from finder.discovery_store import SETTINGS_CHANGED, DiscoveryStore, LostLease, progress, work
from finder.domain import Listing
from finder.errors import PersistenceError
from finder.evidence import INVALIDATED_AT, SUMMARY_FINGERPRINT
from finder.persistence import listing_observations, listings
from finder.watch_store import SavedWatch, WatchStore, decisions, inbox, migrate, outbox, verdicts

NOW = datetime(2026, 10, 1, 10, tzinfo=UTC)


@pytest.fixture
def source():
    return Listing(
        marketplace="ebay",
        marketplace_item_id="synthetic-item",
        seller_id="synthetic-seller",
        title="Synthetic record",
        current_price=Decimal("20"),
        first_observed_at=NOW,
        last_observed_at=NOW,
        details_observed_at=NOW,
        item_specifics={"Synthetic field": ["Synthetic value"]},
        source_metadata={
            INVALIDATED_AT: (NOW - timedelta(minutes=1)).isoformat(),
            SUMMARY_FINGERPRINT: "synthetic-fingerprint",
        },
    )


@contextmanager
def sql_trace(engine):
    # Compile placeholders only: never capture parameter values or seller payloads.
    statements = []

    def capture(conn, statement, multiparams, params, execution_options):
        statements.append(" ".join(str(statement).split()))

    event.listen(engine, "before_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_execute", capture)


def upsert(repository, listing, mode, caller):
    options = {} if mode is None else {"record_observation": mode}
    with repository.engine.begin() if caller else nullcontext(None) as conn:
        return repository.upsert(listing, connection=conn, **options)


def operations(statements):
    prefixes = {
        "SELECT ebay_deleted_users.seller_id": "tombstone",
        "SELECT listing_observations.observed_at": "history_read",
        "INSERT INTO listing_observations ": "history_insert",
        "SELECT listings.marketplace,": "snapshot_lock",
        "INSERT INTO listings ": "snapshot_insert",
        "UPDATE listings SET": "snapshot_update",
    }
    result = []
    for statement in statements:
        matches = [name for prefix, name in prefixes.items() if statement.startswith(prefix)]
        assert len(matches) == 1
        if matches[0] == "snapshot_lock":
            # SQLite omits row locking on execution; the emitted SQLAlchemy statement
            # must still request it for PostgreSQL.
            assert statement.endswith("FOR UPDATE")
        result.extend(matches)
    return result


@pytest.mark.parametrize("mode", [None, True, False], ids=["default", "record", "snapshot_only"])
@pytest.mark.parametrize("caller", [False, True], ids=["owned", "caller"])
def test_history_mode_preserves_query_order_and_snapshot_rules(repository, source, mode, caller):
    equal = source.model_copy(update={"title": "Synthetic equal-time change"})
    older = source.model_copy(
        update={"title": "Synthetic older change", "last_observed_at": NOW - timedelta(hours=1)}
    )
    newer = source.model_copy(
        update={
            "title": "Synthetic newer change",
            "first_observed_at": NOW + timedelta(minutes=1),
            "last_observed_at": NOW + timedelta(hours=1),
            "details_observed_at": None,
            "item_specifics": {},
            "source_metadata": {},
        }
    )
    for index, incoming in enumerate((source, equal, older, newer)):
        untouched = incoming.model_dump(mode="json")
        with sql_trace(repository.engine) as statements:
            assert upsert(repository, incoming, mode, caller) == (
                "new" if index == 0 else "updated"
            )
        expected = ["tombstone"]
        if mode is not False:
            expected += ["history_read"]
            if index != 1:
                expected += ["history_insert"]
        expected += ["snapshot_lock"]
        if index != 2:
            expected += ["snapshot_insert" if index == 0 else "snapshot_update"]
        assert operations(statements) == expected
        assert incoming.model_dump(mode="json") == untouched
        saved = repository.get("ebay", source.marketplace_item_id)
        assert saved.title == (equal.title if index == 2 else incoming.title)
        assert saved.first_observed_at == NOW
        assert saved.last_observed_at == (newer.last_observed_at if index == 3 else NOW)
        assert saved.source_metadata == source.source_metadata
        assert saved.item_specifics == source.item_specifics
        assert saved.details_observed_at == NOW
        assert ("item_specifics_stale" in saved.quality_flags) == (index == 3)

    history = repository.get_observations("ebay", source.marketplace_item_id)
    expected_history = [] if mode is False else [older, source, newer]
    assert history == expected_history  # equal-time changes never rewrite history


@pytest.mark.parametrize("caller", [False, True], ids=["owned", "caller"])
def test_skipped_history_can_later_be_recorded_and_never_rewrites_existing_history(
    repository, source, caller
):
    upsert(repository, source, False, caller)
    assert repository.get_observations("ebay", source.marketplace_item_id) == []
    upsert(repository, source, None, caller)
    changed = source.model_copy(update={"title": "Synthetic cached change"})
    with sql_trace(repository.engine) as statements:
        upsert(repository, changed, False, caller)
    assert operations(statements) == ["tombstone", "snapshot_lock", "snapshot_update"]
    assert repository.get_observations("ebay", source.marketplace_item_id) == [source]
    assert repository.get("ebay", source.marketplace_item_id).title == changed.title


@pytest.mark.parametrize("record", [False, True])
def test_caller_keeps_control_of_transaction(repository, source, record):
    with repository.engine.connect() as conn:
        transaction = conn.begin()
        assert repository.upsert(source, connection=conn, record_observation=record) == "new"
        assert transaction.is_active
        assert conn.execute(select(listings)).first()
        transaction.rollback()
    assert repository.count() == 0
    assert repository.get_observations("ebay", source.marketplace_item_id) == []


@pytest.mark.parametrize("record", [False, True])
@pytest.mark.parametrize("caller", [False, True], ids=["owned", "caller"])
def test_seller_deletion_suppresses_before_any_history_or_snapshot_access(
    repository, source, record, caller
):
    repository.upsert(source)
    assert repository.delete_ebay_seller(source.seller_id) == 1
    with sql_trace(repository.engine) as statements:
        assert upsert(repository, source, record, caller) == "suppressed"
    assert operations(statements) == ["tombstone"]
    assert repository.count() == 0
    assert repository.get_observations("ebay", source.marketplace_item_id) == []


@pytest.mark.parametrize("record", [False, True])
@pytest.mark.parametrize("caller", [False, True], ids=["owned", "caller"])
@pytest.mark.parametrize(
    "failure", ["tombstone", "snapshot_lock", "snapshot_insert", "snapshot_update"]
)
def test_retained_sql_failure_propagates_and_rolls_back(
    repository, source, record, caller, failure
):
    existing = failure == "snapshot_update"
    if existing:
        repository.upsert(source)
    incoming = source.model_copy(update={"last_observed_at": NOW + timedelta(hours=1)})

    def fail(conn, statement, multiparams, params, execution_options):
        if operations([" ".join(str(statement).split())]) == [failure]:
            raise SQLAlchemyError("synthetic database failure")

    event.listen(repository.engine, "before_execute", fail)
    try:
        with pytest.raises(PersistenceError, match="Database write failed"):
            upsert(repository, incoming, record, caller)
    finally:
        event.remove(repository.engine, "before_execute", fail)
    assert repository.get("ebay", source.marketplace_item_id) == (source if existing else None)
    assert repository.get_observations("ebay", source.marketplace_item_id) == (
        [source] if existing else []
    )


@pytest.mark.parametrize("record", [False, True])
@pytest.mark.parametrize("caller", [False, True], ids=["owned", "caller"])
@pytest.mark.parametrize("failures", [1, 2])
def test_insert_retry_limit_is_unchanged(repository, source, record, caller, failures):
    attempts = 0

    def fail(conn, statement, multiparams, params, execution_options):
        nonlocal attempts
        if str(statement).startswith("INSERT INTO listings "):
            attempts += 1
            if attempts <= failures:
                raise IntegrityError("synthetic insert", None, Exception("synthetic conflict"))

    event.listen(repository.engine, "before_execute", fail)
    try:
        if failures == 2:
            with pytest.raises(PersistenceError, match="after concurrent insert"):
                upsert(repository, source, record, caller)
        else:
            assert upsert(repository, source, record, caller) == "new"
    finally:
        event.remove(repository.engine, "before_execute", fail)
    assert attempts == 2
    assert repository.count() == (failures == 1)
    assert repository.get_observations("ebay", source.marketplace_item_id) == (
        [source] if record and failures == 1 else []
    )


@pytest.fixture
def cached(repository, source):
    migrate(repository.engine)
    repository.upsert(source, record_observation=False)
    store = WatchStore(repository.engine)
    store.add(SavedWatch(release_id=1), watch_id="synthetic-watch", now=NOW)
    claim = store.claim(now=NOW)
    queue = DiscoveryStore(repository.engine)
    target = EbaySearchTarget(id="synthetic", catalog_variant_id=1, queries=["Synthetic record"])
    state = queue.load(claim, target, NOW)
    state["evaluation_signature"] = "synthetic-before"
    queue.checkpoint(
        claim,
        state,
        NOW,
        items=[
            {
                "itemId": source.marketplace_item_id,
                "title": source.title,
                "itemOriginDate": (NOW - timedelta(days=1)).isoformat(),
            }
        ],
    )
    item = queue.due(claim, NOW, limit=1, pending=True)[0]
    review = {"notify": False, "status": "family_review", "policy": "synthetic-policy"}
    queue.disposition(
        claim, item, NOW, status="evaluated", listing=source, repository=repository, review=review
    )
    state["evaluation_signature"] = "synthetic-after"
    queue.checkpoint(claim, state, NOW)
    item = queue.due(claim, NOW, limit=1, pending=True)[0]
    assert item["reason"] == SETTINGS_CHANGED
    return queue, claim, state, item, review


def snapshot(repository):
    with repository.engine.connect() as conn:
        return {
            table.name: [dict(row) for row in conn.execute(select(table)).mappings()]
            for table in (
                listings,
                listing_observations,
                inbox,
                outbox,
                work,
                progress,
                verdicts,
                decisions,
            )
        }


@pytest.mark.parametrize("history", [False, True])
def test_cached_disposition_uses_one_fewer_read_and_keeps_outcomes(
    repository, source, cached, history
):
    queue, claim, state, item, review = cached
    if history:
        repository.upsert(source)
    before = snapshot(repository)
    with sql_trace(repository.engine) as statements:
        previous = repository.get("ebay", source.marketplace_item_id)
        assert queue.disposition(
            claim,
            item,
            NOW,
            status="evaluated",
            listing=previous,
            repository=repository,
            review=review,
            state=state,
        ) == ("evaluated", 0)
    # The baseline retained-row path had 15 SQL statements (1 source read plus
    # 14 disposition statements). Only its unused observation lookup is removed.
    assert len(statements) == 14
    assert not any("listing_observations" in statement for statement in statements)
    assert sum(statement.startswith("UPDATE listings SET") for statement in statements) == 1
    after = snapshot(repository)
    for table in (listings, listing_observations, outbox, verdicts, decisions):
        assert after[table.name] == before[table.name]
    assert after[work.name][0]["status"] == "evaluated"
    assert after[inbox.name][0]["data"]["status"] == "family_review"


@pytest.mark.parametrize("failure", ["review", "outbox", "progress", "work"])
def test_cached_failure_rolls_back_snapshot_review_outbox_work_and_digest(
    repository, source, cached, failure
):
    queue, claim, state, item, review = cached
    before, state_before = snapshot(repository), deepcopy(state)
    incoming = source.model_copy(update={"title": "Synthetic equal-time change"})
    prefix = {
        "review": "UPDATE finder_inbox SET",
        "outbox": f"INSERT INTO {outbox.name} ",
        "progress": "UPDATE finder_discovery SET",
        "work": f"UPDATE {work.name} SET",
    }[failure]

    def fail(conn, cursor, statement, params, context, executemany):
        if statement.startswith(prefix):
            raise SQLAlchemyError("synthetic downstream failure")

    event.listen(repository.engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(SQLAlchemyError, match="synthetic downstream failure"):
            queue.disposition(
                claim,
                item,
                NOW,
                status="evaluated",
                listing=incoming,
                repository=repository,
                review={**review, "notify": True},
                state=state,
            )
    finally:
        event.remove(repository.engine, "after_cursor_execute", fail)
    assert snapshot(repository) == before
    assert state == state_before


def test_lost_lease_fences_cached_disposition_before_source_access(repository, source, cached):
    queue, claim, state, item, review = cached
    before = snapshot(repository)
    with sql_trace(repository.engine) as statements, pytest.raises(LostLease):
        queue.disposition(
            claim,
            item,
            NOW + timedelta(hours=1),
            status="evaluated",
            listing=source,
            repository=repository,
            review=review,
            state=state,
        )
    assert not any(
        "listing_observations" in statement or "FROM listings" in statement
        for statement in statements
    )
    assert snapshot(repository) == before


@pytest.mark.parametrize("caller", [False, True], ids=["owned", "caller"])
@pytest.mark.parametrize("failure", ["history_read", "history_insert"])
def test_recording_history_sql_errors_are_still_required_and_atomic(
    repository, source, caller, failure
):
    def fail(conn, statement, multiparams, params, execution_options):
        if operations([" ".join(str(statement).split())]) == [failure]:
            raise SQLAlchemyError("synthetic history failure")

    event.listen(repository.engine, "before_execute", fail)
    try:
        with pytest.raises(PersistenceError, match="Database write failed"):
            upsert(repository, source, None, caller)
    finally:
        event.remove(repository.engine, "before_execute", fail)
    assert repository.count() == 0
    assert repository.get_observations("ebay", source.marketplace_item_id) == []


@pytest.mark.parametrize("record", [False, True])
@pytest.mark.parametrize("caller", [False, True], ids=["owned", "caller"])
def test_non_sql_failure_after_source_write_is_unwrapped_and_rolled_back(
    repository, source, record, caller
):
    repository.upsert(source)
    incoming = source.model_copy(update={"last_observed_at": NOW + timedelta(hours=1)})

    def fail(conn, cursor, statement, params, context, executemany):
        if statement.startswith("UPDATE listings SET"):
            raise RuntimeError("synthetic application failure")

    event.listen(repository.engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic application failure"):
            upsert(repository, incoming, record, caller)
    finally:
        event.remove(repository.engine, "after_cursor_execute", fail)
    assert repository.get("ebay", source.marketplace_item_id) == source
    assert repository.get_observations("ebay", source.marketplace_item_id) == [source]


@pytest.mark.parametrize("verdict", ["mine", "other", "unsure", "bought"])
def test_cached_disposition_keeps_feedback_provenance_and_alert_suppression(
    repository, source, cached, verdict
):
    queue, claim, state, item, review = cached
    with repository.engine.begin() as conn:
        identity = dict(
            watch_id=claim["id"], marketplace="ebay", marketplace_item_id=source.marketplace_item_id
        )
        conn.execute(
            verdicts.insert().values(
                **identity, verdict=verdict, tier="family_review", decided_at=NOW.isoformat()
            )
        )
        conn.execute(
            decisions.insert().values(
                **identity,
                verdict=None if verdict == "bought" else verdict,
                purchased=verdict == "bought",
                tier=None if verdict == "bought" else "family_review",
                decided_at=None if verdict == "bought" else NOW.isoformat(),
                updated_at=NOW.isoformat(),
                prediction=None
                if verdict == "bought"
                else {"policy": "synthetic-original-policy", "status": "family_review"},
            )
        )
    before = snapshot(repository)
    assert queue.disposition(
        claim,
        item,
        NOW,
        status="evaluated",
        listing=source,
        repository=repository,
        review={**review, "notify": True},
        state=state,
    ) == ("evaluated", 0)
    after = snapshot(repository)
    for table in (verdicts, decisions, listing_observations):
        assert after[table.name] == before[table.name]
    assert after[outbox.name] == []
    assert not state["initial_digest_sent"]
