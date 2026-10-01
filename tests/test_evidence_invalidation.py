"""A known change or failed read must not leave old evidence looking current."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update

from finder.adapters.ebay.discovery import summary_fingerprint
from finder.adapters.ebay.normalize import normalize_listing
from finder.adapters.ebay.target_search import EbaySearchTarget
from finder.discovery_store import DiscoveryStore, work
from finder.watch_store import (
    SavedWatch,
    WatchStore,
    decisions,
    inbox,
    migrate,
    outbox,
    verdicts,
    watches,
)

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


@pytest.mark.parametrize("outcome", ["evaluated", "error"])
def test_legacy_pending_change_recovers_shared_boundary_before_its_marker_disappears(
    known_review, outcome
):
    repo, queue, raw, listing, review, add = known_review
    a, state, item = add(111)
    b, _, _ = add(222)
    changed_at, fresh_at = NOW + timedelta(minutes=1), NOW + timedelta(minutes=2)
    changed = {**raw, "title": "Synthetic changed copy"}
    with repo.engine.begin() as conn:
        events = {row.watch_id: row.id for row in conn.execute(select(outbox))}
        # This is the durable state left by the pre-upgrade checkpoint: work
        # changed, but no shared or per-review invalidation marker exists yet.
        conn.execute(
            update(work)
            .where(work.c.watch_id == a["id"])
            .values(
                status="pending",
                kind="existing_listing_updated",
                reason=None,
                fingerprint=summary_fingerprint(changed),
                last_search_at=changed_at.isoformat(),
            )
        )
    fresh = listing.model_copy(
        update={"details_observed_at": fresh_at, "last_observed_at": fresh_at}
    )
    queue.disposition(
        a,
        item,
        fresh_at,
        status=outcome,
        listing=fresh if outcome == "evaluated" else None,
        repository=repo,
        review=review if outcome == "evaluated" else None,
        state=state,
        reason="detail_failed" if outcome == "error" else None,
    )
    with repo.engine.connect() as conn:
        rows = {row.watch_id: row for row in conn.execute(select(inbox))}
        after = {row.watch_id: row for row in conn.execute(select(outbox))}
    assert rows[b["id"]].data["evidence_invalidated_at"] == changed_at.isoformat()
    assert not rows[b["id"]].data["notify"]
    assert after[b["id"]].status == "expired"
    for watch_id in events:
        assert after[watch_id].id == events[watch_id] and after[watch_id].attempts == 0
    assert after[a["id"]].status == ("pending" if outcome == "evaluated" else "expired")


@pytest.fixture
def known_review(repository, search_payload):
    migrate(repository.engine)
    store = WatchStore(repository.engine)
    queue = DiscoveryStore(repository.engine)
    raw = {
        "itemId": "v1|900100|0",
        "itemOriginDate": (NOW - timedelta(days=1)).isoformat(),
        "title": "Synthetic green record",
        "price": {"value": "20", "currency": "USD"},
    }
    listing = normalize_listing(
        {**search_payload["itemSummaries"][0], "itemId": raw["itemId"]}, NOW
    ).model_copy(
        update={
            "details_observed_at": NOW,
            "listing_ends_at": None,
            "current_price": Decimal("20"),
            "shipping_cost": Decimal("5"),
        }
    )
    review = {
        "notify": True,
        "policy": "private-target-review-v9",
        "status": "possible_pressing",
        "subtotal": "25",
        "budget": "within_ceiling",
        "clues": ["seller_color_claim"],
        "verify": [],
    }

    def add_watch(release_id, *, evaluate=True):
        store.add(SavedWatch(release_id=release_id), now=NOW)
        claim = store.claim(now=NOW)
        target = EbaySearchTarget(id="test", catalog_variant_id=release_id, queries=["Album"])
        state = queue.load(claim, target, NOW)
        queue.checkpoint(claim, state, NOW, items=[raw])
        item = queue.due(claim, NOW, limit=1, pending=True)[0]
        if evaluate:
            queue.disposition(
                claim,
                item,
                NOW,
                status="evaluated",
                listing=listing,
                repository=repository,
                review=review,
                state=state,
                refresh_hours=6,
            )
        return claim, state, item

    return repository, queue, raw, listing, review, add_watch


def test_known_change_blocks_pending_alert_before_a_detail_slot_runs(known_review):
    repo, queue, raw, _, _, add = known_review
    claim, state, _ = add(111)
    with repo.engine.connect() as conn:
        assert conn.execute(select(outbox)).first() is not None
    queue.checkpoint(
        claim,
        state,
        NOW + timedelta(minutes=1),
        items=[
            {**raw, "title": "Synthetic red record", "price": {"value": "99", "currency": "USD"}}
        ],
    )
    with repo.engine.connect() as conn:
        data = conn.execute(select(inbox.c.data)).scalar_one()
        assert data["notify"] is False
        assert (
            conn.execute(select(outbox).where(outbox.c.status.in_(("pending", "sending")))).first()
            is None
        )
        assert data.get("evidence_invalidated_at") is not None


def test_failed_detail_read_invalidates_recent_evidence_and_respects_backoff(known_review):
    repo, queue, _, _, _, add = known_review
    claim, _, item = add(111)
    now = NOW + timedelta(minutes=1)
    queue.disposition(claim, item, now, status="error", reason="detail_failed", refresh_hours=1)
    with repo.engine.connect() as conn:
        data = conn.execute(select(inbox.c.data)).scalar_one()
    assert data.get("evidence_invalidated_at") == now.isoformat()
    assert data["notify"] is False
    assert not queue.due(claim, now, limit=1, pending=True)
    assert queue.due(claim, now + timedelta(hours=1), limit=1, pending=True)


def test_invalidation_advances_review_version_without_freshening_provider_details(known_review):
    repo, queue, raw, _, _, add = known_review
    claim, state, _ = add(111)
    previous_review = NOW + timedelta(minutes=3)
    with repo.engine.begin() as conn:
        conn.execute(update(inbox).values(last_seen_at=previous_review.isoformat()))
    changed_at = NOW + timedelta(minutes=1)
    queue.checkpoint(claim, state, changed_at, items=[{**raw, "title": "Changed synthetic record"}])
    with repo.engine.connect() as conn:
        version = conn.execute(select(inbox.c.last_seen_at)).scalar_one()
    assert datetime.fromisoformat(version) > previous_review
    assert repo.get("ebay", raw["itemId"]).details_observed_at == NOW


def test_shared_change_invalidates_both_reviews_and_old_completion_cannot_clear_it(known_review):
    repo, queue, raw, listing, review, add = known_review
    a, state_a, _ = add(111)
    b, state_b, item_b = add(222)
    changed_at = NOW + timedelta(minutes=1)
    queue.checkpoint(a, state_a, changed_at, items=[{**raw, "title": "Synthetic red record"}])
    with repo.engine.connect() as conn:
        rows = conn.execute(select(inbox.c.data)).scalars().all()
    assert len(rows) == 2 and all(not row["notify"] for row in rows)
    assert queue.due(b, changed_at, limit=1, pending=False), "Other watch needs prompt reassessment"
    # B's provider request started before the seller change and finishes afterward.
    queue.disposition(
        b,
        item_b,
        changed_at + timedelta(seconds=1),
        status="evaluated",
        listing=listing,
        repository=repo,
        review=review,
        state=state_b,
    )
    with repo.engine.connect() as conn:
        data = conn.execute(select(inbox.c.data).where(inbox.c.watch_id == b["id"])).scalar_one()
    assert data.get("evidence_invalidated_at") == changed_at.isoformat()
    assert not data["notify"]
    with repo.engine.connect() as conn:
        assert not conn.execute(select(outbox).where(outbox.c.status == "pending")).first()
    assert queue.due(b, changed_at + timedelta(seconds=1), limit=1, pending=False)


def test_fresh_success_clears_only_its_own_review_marker(known_review):
    repo, queue, raw, listing, review, add = known_review
    a, state_a, _ = add(111)
    b, state_b, item_b = add(222)
    changed_at = NOW + timedelta(minutes=1)
    queue.checkpoint(a, state_a, changed_at, items=[{**raw, "title": "Synthetic red record"}])
    fresh_at = NOW + timedelta(minutes=2)
    fresh = listing.model_copy(
        update={"details_observed_at": fresh_at, "last_observed_at": fresh_at}
    )
    queue.disposition(
        b,
        item_b,
        fresh_at,
        status="evaluated",
        listing=fresh,
        repository=repo,
        review={**review, "notify": False},
        state=state_b,
    )
    with repo.engine.connect() as conn:
        rows = dict(conn.execute(select(inbox.c.watch_id, inbox.c.data)).all())
    assert rows[a["id"]].get("evidence_invalidated_at") == changed_at.isoformat()
    assert rows[b["id"]].get("evidence_invalidated_at") is None


def test_unchanged_summary_keeps_valid_evidence_and_queued_event(known_review):
    repo, queue, raw, _, _, add = known_review
    claim, state, _ = add(111)
    queue.checkpoint(claim, state, NOW + timedelta(minutes=1), items=[raw])
    with repo.engine.connect() as conn:
        data = conn.execute(select(inbox.c.data)).scalar_one()
        assert data["notify"] and data.get("evidence_invalidated_at") is None
        assert conn.execute(select(outbox)).first() is not None


def test_same_change_seen_by_another_watch_does_not_invalidate_fresh_hydration(known_review):
    from finder.adapters.ebay.discovery import summary_fingerprint
    from finder.evidence import INVALIDATED_AT, SUMMARY_FINGERPRINT

    repo, queue, raw, listing, review, add = known_review
    a, state_a, item_a = add(111)
    b, state_b, _ = add(222)
    changed = {**raw, "title": "Changed synthetic record"}
    queue.checkpoint(a, state_a, NOW + timedelta(minutes=1), items=[changed])
    fresh_at = NOW + timedelta(minutes=2)
    fresh = listing.model_copy(
        update={"details_observed_at": fresh_at, "last_observed_at": fresh_at}
    )
    queue.disposition(
        a,
        item_a,
        fresh_at,
        status="evaluated",
        listing=fresh,
        repository=repo,
        review=review,
        state=state_a,
    )
    queue.checkpoint(b, state_b, NOW + timedelta(minutes=3), items=[changed])
    with repo.engine.connect() as conn:
        data = conn.execute(select(inbox.c.data).where(inbox.c.watch_id == a["id"])).scalar_one()
        event = conn.execute(select(outbox).where(outbox.c.watch_id == a["id"])).mappings().one()
    assert data.get("evidence_invalidated_at") is None
    assert data["notify"] and event["status"] == "pending"
    newer = {**raw, "title": "Another genuine seller change"}
    changed_again = NOW + timedelta(minutes=4)
    queue.checkpoint(a, state_a, changed_again, items=[newer])
    # A delayed older search cannot roll back the shared fingerprint/boundary pair.
    queue.checkpoint(b, state_b, NOW + timedelta(seconds=30), items=[raw])
    saved = repo.get("ebay", raw["itemId"])
    assert saved.source_metadata[INVALIDATED_AT] == changed_again.isoformat()
    assert saved.source_metadata[SUMMARY_FINGERPRINT] == summary_fingerprint(newer)
    with repo.engine.connect() as conn:
        assert (
            conn.execute(select(inbox.c.data).where(inbox.c.watch_id == a["id"])).scalar_one()[
                "evidence_invalidated_at"
            ]
            == changed_again.isoformat()
        )


@pytest.mark.parametrize(
    "status,attempts,resumes",
    [("pending", 0, True), ("pending", 1, False), ("sending", 1, False), ("sent", 1, False)],
)
def test_only_never_attempted_event_resumes_once_after_fresh_evidence(
    known_review, status, attempts, resumes
):
    repo, queue, raw, listing, review, add = known_review
    claim, state, item = add(111)
    with repo.engine.begin() as conn:
        event_id = conn.execute(select(outbox.c.id)).scalar_one()
        conn.execute(update(outbox).values(status=status, attempts=attempts))
    changed_at = NOW + timedelta(minutes=1)
    queue.checkpoint(claim, state, changed_at, items=[{**raw, "title": "Changed synthetic record"}])
    fresh_at = NOW + timedelta(minutes=2)
    fresh = listing.model_copy(
        update={"details_observed_at": fresh_at, "last_observed_at": fresh_at}
    )
    for when in (fresh_at, fresh_at + timedelta(seconds=1)):
        queue.disposition(
            claim,
            item,
            when,
            status="evaluated",
            listing=fresh,
            repository=repo,
            review=review,
            state=state,
        )
    with repo.engine.connect() as conn:
        events = conn.execute(select(outbox)).mappings().all()
        data = conn.execute(select(inbox.c.data)).scalar_one()
    assert len(events) == 1 and events[0]["id"] == event_id
    assert events[0]["status"] == (
        "pending" if resumes else "sent" if status == "sent" else "expired"
    )
    assert events[0]["attempts"] == attempts
    assert data.get("evidence_invalidated_at") is None
    assert data.get("evidence_pending_event_id") is None
    assert state["initial_digest_sent"] is True


@pytest.mark.parametrize("saved", ["mine", "bought", "dismissed"])
def test_saved_owner_state_blocks_resumption_and_is_preserved(known_review, saved):
    repo, queue, raw, listing, review, add = known_review
    claim, state, item = add(111)
    with repo.engine.begin() as conn:
        if saved == "dismissed":
            conn.execute(update(inbox).values(dismissed=True))
        else:
            key = dict(watch_id=claim["id"], marketplace="ebay", marketplace_item_id=raw["itemId"])
            conn.execute(
                verdicts.insert().values(
                    **key, verdict=saved, tier="family_review", decided_at=NOW.isoformat()
                )
            )
            conn.execute(
                decisions.insert().values(
                    **key,
                    verdict="mine" if saved == "mine" else None,
                    purchased=saved == "bought",
                    tier="family_review",
                    decided_at=NOW.isoformat(),
                    updated_at=NOW.isoformat(),
                    prediction={"status": "family_review", "source": "recorded_prediction"},
                )
            )
        before = [dict(r) for r in conn.execute(select(decisions)).mappings()]
    queue.checkpoint(
        claim,
        state,
        NOW + timedelta(minutes=1),
        items=[{**raw, "title": "Changed synthetic record"}],
    )
    fresh_at = NOW + timedelta(minutes=2)
    fresh = listing.model_copy(
        update={"details_observed_at": fresh_at, "last_observed_at": fresh_at}
    )
    queue.disposition(
        claim,
        item,
        fresh_at,
        status="evaluated",
        listing=fresh,
        repository=repo,
        review=review,
        state=state,
    )
    with repo.engine.connect() as conn:
        assert not conn.execute(
            select(outbox).where(outbox.c.status.in_(("pending", "sending")))
        ).first()
        assert [dict(r) for r in conn.execute(select(decisions)).mappings()] == before
        assert conn.execute(select(inbox.c.dismissed)).scalar_one() == (saved == "dismissed")


def test_deleted_suspended_event_does_not_create_a_replacement(known_review):
    repo, queue, raw, listing, review, add = known_review
    claim, state, item = add(111)
    queue.checkpoint(
        claim,
        state,
        NOW + timedelta(minutes=1),
        items=[{**raw, "title": "Changed synthetic record"}],
    )
    with repo.engine.begin() as conn:
        conn.execute(delete(outbox))
    fresh_at = NOW + timedelta(minutes=2)
    fresh = listing.model_copy(
        update={"details_observed_at": fresh_at, "last_observed_at": fresh_at}
    )
    queue.disposition(
        claim,
        item,
        fresh_at,
        status="evaluated",
        listing=fresh,
        repository=repo,
        review=review,
        state=state,
    )
    with repo.engine.connect() as conn:
        assert not conn.execute(select(outbox)).first()


def test_a_failed_read_is_local_but_delayed_seller_change_does_not_overwrite_newer_evidence(
    known_review,
):
    repo, queue, raw, listing, review, add = known_review
    a, state_a, item_a = add(111)
    b, state_b, item_b = add(222)
    queue.disposition(
        a, item_a, NOW + timedelta(seconds=30), status="error", reason="detail_failed"
    )
    with repo.engine.connect() as conn:
        rows = dict(conn.execute(select(inbox.c.watch_id, inbox.c.data)).all())
    assert rows[a["id"]].get("evidence_invalidated_at") is not None
    assert rows[b["id"]].get("evidence_invalidated_at") is None
    fresh_at = NOW + timedelta(minutes=2)
    fresh = listing.model_copy(
        update={"details_observed_at": fresh_at, "last_observed_at": fresh_at}
    )
    queue.disposition(
        b,
        item_b,
        fresh_at,
        status="evaluated",
        listing=fresh,
        repository=repo,
        review=review,
        state=state_b,
    )
    # An older search response waited behind the shared listing lock.
    queue.checkpoint(
        a, state_a, NOW + timedelta(minutes=1), items=[{**raw, "title": "Changed synthetic record"}]
    )
    with repo.engine.connect() as conn:
        rows = dict(conn.execute(select(inbox.c.watch_id, inbox.c.data)).all())
    assert rows[a["id"]].get("evidence_invalidated_at") is not None
    assert rows[b["id"]].get("evidence_invalidated_at") is None
    assert rows[b["id"]]["notify"] is True


@pytest.mark.parametrize("cause", ["failed_query_reset", "shared_change_policy_resort"])
def test_worker_does_not_reuse_invalidated_cached_details(
    known_review, settings, discogs_release, detail_payload, monkeypatch, cause
):
    from contextlib import nullcontext
    from types import SimpleNamespace

    import httpx

    from finder import discovery_worker as worker
    from finder.adapters.discogs.adapter import AlternativeRetrieval
    from finder.adapters.discogs.normalize import normalize_release
    from finder.adapters.ebay.client import EbayClient

    repo, queue, raw, _, _, add = known_review
    claim, state, item = add(111)
    invalidated_at = NOW + timedelta(minutes=1)
    if cause == "failed_query_reset":
        queue.disposition(claim, item, invalidated_at, status="error", reason="detail_failed")
    else:
        other, other_state, _ = add(222)
        queue.checkpoint(
            other, other_state, invalidated_at, items=[{**raw, "title": "Changed synthetic record"}]
        )
        state["evaluation_signature"] = "changed-policy"
        queue.checkpoint(claim, state, invalidated_at)
    later = NOW + timedelta(minutes=2)
    variant = normalize_release(discogs_release, NOW)
    monkeypatch.setattr(
        worker, "DiscogsClient", lambda _: nullcontext(SimpleNamespace(requests=1, retries=0))
    )
    monkeypatch.setattr(worker, "load_profile", lambda *args: None)
    monkeypatch.setattr(
        worker,
        "DiscogsCatalogProvider",
        lambda _: SimpleNamespace(
            get_release=lambda _: variant,
            search_alternatives=lambda _: AlternativeRetrieval([], False),
        ),
    )
    monkeypatch.setattr(worker, "watch_queries", lambda *args: ["Album"])
    detail_reads = []

    def handle(request):
        if "oauth2" in request.url.path:
            return httpx.Response(200, json={"access_token": "synthetic", "expires_in": 3600})
        if "analytics" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "rateLimits": [
                        {
                            "apiContext": "buy",
                            "apiName": "browse",
                            "resources": [
                                {
                                    "name": "buy.browse",
                                    "rates": [
                                        {
                                            "remaining": 5000,
                                            "limit": 5000,
                                            "timeWindow": 86400,
                                            "reset": (NOW + timedelta(days=1)).isoformat(),
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )
        if "item_summary/search" in request.url.path:
            return httpx.Response(200, json={"total": 0, "itemSummaries": []})
        detail_reads.append(request.url.path)
        return httpx.Response(
            200,
            json={
                **detail_payload,
                "itemId": raw["itemId"],
                "itemEndDate": None,
                "price": {"value": "99", "currency": "USD"},
                "seller": {"userId": "synthetic-seller"},
            },
        )

    monkeypatch.setattr(
        worker, "EbayClient", lambda s: EbayClient(s, transport=httpx.MockTransport(handle))
    )
    result = worker.run_chunk(repo, settings, None, claim, now_fn=lambda: later)
    assert result["failed"] == 0
    assert len(detail_reads) == 1
    assert repo.get("ebay", raw["itemId"]).current_price == Decimal("99")
    with repo.engine.connect() as conn:
        data = conn.execute(
            select(inbox.c.data).where(inbox.c.watch_id == claim["id"])
        ).scalar_one()
    assert data.get("evidence_invalidated_at") is None
    assert data["details_observed_at"] == later.isoformat()


@pytest.mark.parametrize("remove_first_watch", [False, True])
def test_shared_watermark_protects_a_watch_without_a_previous_review(
    known_review, remove_first_watch
):
    from finder.adapters.ebay.discovery import summary_fingerprint
    from finder.evidence import INVALIDATED_AT, SUMMARY_FINGERPRINT

    repo, queue, raw, listing, review, add = known_review
    a, state_a, _ = add(111)
    b, state_b, item_b = add(222, evaluate=False)
    changed_at = NOW + timedelta(minutes=1)
    expected_fingerprint = summary_fingerprint({**raw, "title": "Changed synthetic record"})
    queue.checkpoint(a, state_a, changed_at, items=[{**raw, "title": "Changed synthetic record"}])
    if remove_first_watch:
        with repo.engine.begin() as conn:
            conn.execute(delete(watches).where(watches.c.id == a["id"]))
    listing = listing.model_copy(
        update={
            "source_metadata": {
                **listing.source_metadata,
                INVALIDATED_AT: (NOW - timedelta(minutes=1)).isoformat(),
                SUMMARY_FINGERPRINT: "older-summary",
            }
        }
    )
    # The stale object was read before the shared change; its upsert must retain
    # the global boundary even when B has no old review to carry that marker.
    queue.disposition(
        b,
        item_b,
        changed_at + timedelta(seconds=1),
        status="evaluated",
        listing=listing,
        repository=repo,
        review=review,
        state=state_b,
    )
    with repo.engine.connect() as conn:
        data = conn.execute(select(inbox.c.data).where(inbox.c.watch_id == b["id"])).scalar_one()
        assert not conn.execute(
            select(outbox).where(outbox.c.watch_id == b["id"], outbox.c.status == "pending")
        ).first()
    assert data.get("evidence_invalidated_at") == changed_at.isoformat()
    assert not data["notify"] and not state_b["initial_digest_sent"]
    assert repo.get("ebay", raw["itemId"]).source_metadata[INVALIDATED_AT] == changed_at.isoformat()
    assert (
        repo.get("ebay", raw["itemId"]).source_metadata[SUMMARY_FINGERPRINT] == expected_fingerprint
    )
    fresh_at = NOW + timedelta(minutes=2)
    fresh = listing.model_copy(
        update={"details_observed_at": fresh_at, "last_observed_at": fresh_at}
    )
    queue.disposition(
        b,
        item_b,
        fresh_at,
        status="evaluated",
        listing=fresh,
        repository=repo,
        review=review,
        state=state_b,
    )
    assert repo.get("ebay", raw["itemId"]).source_metadata[INVALIDATED_AT] == changed_at.isoformat()
    assert (
        repo.get("ebay", raw["itemId"]).source_metadata[SUMMARY_FINGERPRINT] == expected_fingerprint
    )
    with repo.engine.connect() as conn:
        data = conn.execute(select(inbox.c.data).where(inbox.c.watch_id == b["id"])).scalar_one()
    assert data.get("evidence_invalidated_at") is None and data["notify"]
    # A still-older completion must not replace the fresh review or revive old fields.
    queue.disposition(
        b,
        item_b,
        fresh_at + timedelta(seconds=1),
        status="evaluated",
        listing=listing,
        repository=repo,
        review={**review, "notify": False},
        state=state_b,
    )
    with repo.engine.connect() as conn:
        after = conn.execute(select(inbox.c.data).where(inbox.c.watch_id == b["id"])).scalar_one()
    assert after == data
