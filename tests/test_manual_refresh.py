"""The real owner endpoint feeds the real worker; all provider traffic is synthetic."""

import json
import os
import shutil
import subprocess
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, update

from finder import discovery_worker as worker
from finder.adapters.discogs.adapter import AlternativeRetrieval
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.client import EbayClient
from finder.discovery_store import SETTINGS_CHANGED, DiscoveryStore, progress, work
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

# Provider-only workflows intentionally install only Python. The required isolated
# PostgreSQL PR job installs both runtimes and explicitly runs this integration suite.
bridge_available = bool(shutil.which("node")) and all(
    Path("node_modules", package).exists() for package in ("tsx", "@electric-sql/pglite")
)
if os.environ.get("FINDER_REQUIRE_REFRESH_INTEGRATION") == "1" and not bridge_available:
    raise pytest.UsageError(
        "Required refresh integration needs Node, tsx and PGlite; run npm ci before pytest."
    )
pytestmark = pytest.mark.skipif(
    not bridge_available,
    reason="Cross-runtime refresh checks require npm ci (required in PostgreSQL PR CI)",
)


def request_refresh(repository, claim, item, now, *, expected_status=200):
    """Bridge only persisted row values between PGlite endpoint and SQLite worker."""
    result = subprocess.run(
        ["node", "--import", "tsx", "tests/support/refresh_endpoint.ts"],
        input=json.dumps(
            {
                **claim,
                "watch_id": claim["id"],
                "item": dict(item) if item is not None else None,
                "now": now.isoformat(),
            }
        ),
        capture_output=True,
        text=True,
        check=True,
    )
    response = json.loads(result.stdout)
    assert response["status"] == expected_status, response
    assert response["watch"]["enabled"] == claim.get("enabled", True)
    saved = response["item"]
    if expected_status != 200:
        assert response["watch"]["next_scan_at"] == now.isoformat()
        return response
    # Use the endpoint's actual committed values, without inferring its SQL or intent.
    with repository.engine.begin() as conn:
        conn.execute(
            update(work)
            .where(work.c.watch_id == claim["id"], work.c.item_id == item["item_id"])
            .values(**{key: value for key, value in saved.items() if key in work.c})
        )
    return saved


@pytest.fixture
def seeded(repository, settings, discogs_release, detail_payload, monkeypatch):
    now = datetime.now(UTC)
    clock = [now - timedelta(hours=2)]
    migrate(repository.engine)
    store = WatchStore(repository.engine)
    store.add(SavedWatch(release_id=111), now=clock[0])
    variant = normalize_release(discogs_release, clock[0])
    monkeypatch.setattr(
        worker, "DiscogsClient", lambda _: nullcontext(SimpleNamespace(requests=1, retries=0))
    )
    monkeypatch.setattr(
        worker,
        "DiscogsCatalogProvider",
        lambda _: SimpleNamespace(
            get_release=lambda _: variant,
            search_alternatives=lambda _: AlternativeRetrieval([], False),
        ),
    )
    monkeypatch.setattr(worker, "watch_queries", lambda *args: ["Synthetic album"])
    item = {
        **detail_payload,
        "itemId": "v1|900001|0",
        "itemOriginDate": (clock[0] - timedelta(days=1)).isoformat(),
        "itemEndDate": None,
        "seller": {"userId": "synthetic-seller"},
    }
    calls = {"details": 0, "remaining": 5000, "result": "success"}

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
                                            "remaining": calls["remaining"],
                                            "limit": 5000,
                                            "timeWindow": 86400,
                                            "reset": (now + timedelta(days=1)).isoformat(),
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )
        if "item_summary/search" in request.url.path:
            return httpx.Response(200, json={"total": 1, "itemSummaries": [item]})
        calls["details"] += 1
        if calls["result"] == "error":
            return httpx.Response(200, json={"itemId": "wrong-synthetic-id"})
        if calls["result"] == "unavailable":
            return httpx.Response(404, json={"errors": []})
        return httpx.Response(200, json=item)

    monkeypatch.setattr(
        worker, "EbayClient", lambda s: EbayClient(s, transport=httpx.MockTransport(handle))
    )
    claim = store.claim(now=clock[0])
    worker.run_chunk(repository, settings, None, claim, now_fn=lambda: clock[0])
    assert calls["details"] == 1
    clock[0] = now + timedelta(seconds=10)
    claim = store.claim(now=clock[0])
    assert claim
    return repository, settings, store, claim, clock, calls


@pytest.mark.parametrize("reason", [None, SETTINGS_CHANGED])
@pytest.mark.parametrize("age_minutes", [10, 120])
def test_manual_request_requires_provider_details_during_policy_catchup(
    seeded, age_minutes, reason
):
    repository, settings, _, claim, clock, calls = seeded
    # Both the settings-only cache path and the <1-hour shared cache path must yield.
    saved = repository.get("ebay", "v1|900001|0")
    repository.upsert(
        saved.model_copy(
            update={
                "details_observed_at": clock[0] - timedelta(minutes=age_minutes),
                "last_observed_at": clock[0],
            }
        )
    )
    with repository.engine.begin() as conn:
        conn.execute(update(work).values(status="pending", reason=reason))
        state = conn.execute(select(progress.c.data)).scalar_one()
        conn.execute(
            update(progress).values(data={**state, "evaluation_signature": "synthetic-old-policy"})
        )
        item = conn.execute(select(work)).mappings().one()
    request_refresh(repository, claim, item, clock[0])
    worker.run_chunk(repository, settings, None, claim, now_fn=lambda: clock[0])
    assert calls["details"] == 2, "Accepted manual refresh was consumed by cached reassessment"
    assert repository.get("ebay", item["item_id"]).details_observed_at == clock[0]
    with repository.engine.connect() as conn:
        assert (
            conn.execute(select(inbox.c.data)).scalar_one()["details_observed_at"]
            == clock[0].isoformat()
        )


@pytest.mark.parametrize("outcome", ["success", "unavailable", "error", "quota"])
def test_manual_request_outcomes_and_backoff(seeded, outcome):
    repository, settings, _, claim, clock, calls = seeded
    with repository.engine.connect() as conn:
        item = conn.execute(select(work)).mappings().one()
    requested = request_refresh(repository, claim, item, clock[0])
    calls["result"] = outcome
    if outcome == "quota":
        calls["remaining"] = 200
    worker.run_chunk(repository, settings, None, claim, now_fn=lambda: clock[0])
    with repository.engine.connect() as conn:
        current = dict(conn.execute(select(work)).mappings().one())
    if outcome in ("success", "unavailable"):
        assert current["refresh_token"] is None
        assert current["refresh_after"] is None
        assert current["status"] == ("evaluated" if outcome == "success" else "unavailable")
    else:
        assert current["refresh_token"] == requested["refresh_token"]
        if outcome == "error":
            assert datetime.fromisoformat(current["refresh_after"]) == clock[0] + timedelta(hours=1)
            # Repeated clicks cannot erase the retry delay, even if legacy fields change.
            repeated = request_refresh(repository, claim, current, clock[0])
            assert repeated["refresh_token"] != current["refresh_token"]
            assert repeated["refresh_after"] == current["refresh_after"]
            queue = DiscoveryStore(repository.engine)
            assert not queue.due(claim, clock[0], limit=10, pending=True)
            assert not queue.due(claim, clock[0], limit=10, pending=False)
    assert calls["details"] == (1 if outcome == "quota" else 2)


@pytest.mark.parametrize("completion", ["cached", "success", "unavailable", "error", "legacy"])
def test_older_completion_cannot_consume_or_postpone_newer_request(seeded, completion):
    repository, _, _, claim, clock, _ = seeded
    queue = DiscoveryStore(repository.engine)
    with repository.engine.connect() as conn:
        old = dict(conn.execute(select(work)).mappings().one())
    first = request_refresh(repository, claim, old, clock[0])
    old.update(first)
    second = request_refresh(repository, claim, old, clock[0])
    assert first["refresh_token"] != second["refresh_token"]
    listing = repository.get("ebay", old["item_id"])
    if completion == "legacy":
        # A v19 worker knows none of the new fields and schedules its old snapshot far out.
        with repository.engine.begin() as conn:
            conn.execute(
                update(work).values(
                    status="evaluated",
                    reason=None,
                    next_check_at=(clock[0] + timedelta(days=7)).isoformat(),
                )
            )
    else:
        queue.disposition(
            claim,
            old,
            clock[0],
            status="evaluated" if completion in ("cached", "success") else completion,
            listing=listing if completion in ("cached", "success") else None,
            repository=repository,
            hydrated=completion != "cached",
            refresh_hours=24,
        )
    with repository.engine.connect() as conn:
        current = conn.execute(select(work)).mappings().one()
    assert current["refresh_token"] == second["refresh_token"]
    assert current["refresh_after"] == second["refresh_after"]
    assert len(queue.due(claim, clock[0], limit=10, pending=True)) == 1
    assert not queue.due(claim, clock[0], limit=10, pending=False)
    with repository.engine.connect() as conn:
        state = conn.execute(select(progress.c.data)).scalar_one()
    assert queue.coverage(claim, state)["pending"] == 1


def test_cached_completion_cannot_acknowledge_matching_request(seeded):
    repository, _, _, claim, clock, _ = seeded
    with repository.engine.connect() as conn:
        old = dict(conn.execute(select(work)).mappings().one())
    requested = request_refresh(repository, claim, old, clock[0])
    old.update(requested)
    DiscoveryStore(repository.engine).disposition(
        claim,
        old,
        clock[0],
        status="evaluated",
        listing=repository.get("ebay", old["item_id"]),
        repository=repository,
    )
    with repository.engine.connect() as conn:
        assert conn.execute(select(work.c.refresh_token)).scalar_one() == requested["refresh_token"]


def test_error_request_due_is_independent_of_legacy_fields_and_evidence_invalidation(seeded):
    repository, _, _, claim, clock, _ = seeded
    due = clock[0] + timedelta(hours=1)
    with repository.engine.begin() as conn:
        conn.execute(
            update(work).values(
                refresh_token="synthetic-token",
                refresh_after=due.isoformat(),
                status="evaluated",
                next_check_at=(clock[0] - timedelta(days=1)).isoformat(),
            )
        )
        data = conn.execute(select(inbox.c.data)).scalar_one()
        conn.execute(
            update(inbox).values(data={**data, "evidence_invalidated_at": clock[0].isoformat()})
        )
    queue = DiscoveryStore(repository.engine)
    assert not queue.due(claim, clock[0], limit=10, pending=True)
    assert not queue.due(claim, clock[0], limit=10, pending=False)
    assert len(queue.due(claim, due, limit=10, pending=True)) == 1
    assert not queue.due(claim, due, limit=10, pending=False)


def test_manual_refresh_preserves_judgment_provenance_and_alert_suppression(seeded):
    repository, settings, _, claim, clock, calls = seeded
    original = {
        "watch_id": claim["id"],
        "marketplace": "ebay",
        "marketplace_item_id": "v1|900001|0",
        "verdict": "mine",
        "purchased": True,
        "tier": "family_review",
        "decided_at": (clock[0] - timedelta(hours=1)).isoformat(),
        "updated_at": clock[0].isoformat(),
        "prediction": {"status": "family_review", "policy": "synthetic-first-judgment"},
        "legacy": None,
    }
    with repository.engine.begin() as conn:
        conn.execute(decisions.insert().values(**original))
        conn.execute(
            verdicts.insert().values(
                **{
                    key: original[key]
                    for key in (
                        "watch_id",
                        "marketplace",
                        "marketplace_item_id",
                        "verdict",
                        "tier",
                        "decided_at",
                    )
                }
            )
        )
        conn.execute(outbox.delete())
        item = conn.execute(select(work)).mappings().one()
    request_refresh(repository, claim, item, clock[0])
    worker.run_chunk(repository, settings, None, claim, now_fn=lambda: clock[0])
    assert calls["details"] == 2
    with repository.engine.connect() as conn:
        assert dict(conn.execute(select(decisions)).mappings().one()) == original
        assert not conn.execute(select(outbox)).first()


def test_manual_refresh_deleted_seller_cannot_restore_identity(seeded):
    repository, _, _, claim, clock, _ = seeded
    queue = DiscoveryStore(repository.engine)
    with repository.engine.connect() as conn:
        item = dict(conn.execute(select(work)).mappings().one())
    requested = request_refresh(repository, claim, item, clock[0])
    item.update(requested)
    listing = repository.get("ebay", item["item_id"])
    repository.delete_ebay_seller(listing.seller_id)
    assert queue.disposition(
        claim,
        item,
        clock[0],
        status="evaluated",
        listing=listing,
        repository=repository,
        hydrated=True,
    ) == ("suppressed", 0)
    with repository.engine.connect() as conn:
        assert not conn.execute(select(work)).first()
        assert not conn.execute(select(inbox)).first()


def test_manual_request_orders_by_own_due_time_once(seeded):
    repository, _, _, claim, clock, _ = seeded
    with repository.engine.begin() as conn:
        first = dict(conn.execute(select(work)).mappings().one())
        conn.execute(
            update(work).values(
                refresh_token="synthetic-token",
                refresh_after=(clock[0] - timedelta(minutes=1)).isoformat(),
                status="evaluated",
                next_check_at=(clock[0] + timedelta(days=7)).isoformat(),
            )
        )
        conn.execute(
            work.insert().values(
                **{
                    **first,
                    "item_id": "v1|900002|0",
                    "status": "pending",
                    "next_check_at": clock[0].isoformat(),
                }
            )
        )
    queue = DiscoveryStore(repository.engine)
    assert [item["item_id"] for item in queue.due(claim, clock[0], limit=1, pending=True)] == [
        first["item_id"]
    ]
    assert not queue.due(claim, clock[0], limit=10, pending=False)


def test_snapshot_without_token_cannot_consume_first_manual_request(seeded):
    repository, _, _, claim, clock, _ = seeded
    with repository.engine.connect() as conn:
        old = dict(conn.execute(select(work)).mappings().one())
    assert old["refresh_token"] is None
    requested = request_refresh(repository, claim, old, clock[0])
    DiscoveryStore(repository.engine).disposition(
        claim,
        old,
        clock[0],
        status="evaluated",
        listing=repository.get("ebay", old["item_id"]),
        repository=repository,
        hydrated=True,
    )
    with repository.engine.connect() as conn:
        assert conn.execute(select(work.c.refresh_token)).scalar_one() == requested["refresh_token"]


def test_first_manual_request_preserves_ordinary_error_backoff(seeded):
    repository, _, _, claim, clock, _ = seeded
    later = clock[0] + timedelta(hours=4)
    with repository.engine.begin() as conn:
        conn.execute(
            update(work).values(status="error", failures=3, next_check_at=later.isoformat())
        )
        old = conn.execute(select(work)).mappings().one()
    requested = request_refresh(repository, claim, old, clock[0])
    assert requested["refresh_after"] == later.isoformat()
    assert requested["next_check_at"] == later.isoformat()  # Legacy workers also wait.
    assert requested["failures"] == 3
    queue = DiscoveryStore(repository.engine)
    assert not queue.due(claim, clock[0], limit=10, pending=True)
    assert len(queue.due(claim, later, limit=10, pending=True)) == 1


def test_request_racing_final_schedule_survives_until_normal_poll(seeded):
    repository, _, store, claim, clock, _ = seeded
    queue = DiscoveryStore(repository.engine)
    with repository.engine.connect() as conn:
        old = conn.execute(select(work)).mappings().one()
        state = conn.execute(select(progress.c.data)).scalar_one()
    assert queue.coverage(claim, state)["pending"] == 0
    requested = request_refresh(repository, claim, old, clock[0])
    # A finalizer can already have chosen its ordinary delay before the request.
    store.finish(claim, [], now=clock[0], next_delay_minutes=10)
    later = clock[0] + timedelta(minutes=10)
    next_claim = store.claim(now=later)
    assert next_claim
    due = queue.due(next_claim, later, limit=10, pending=True)
    assert due[0]["refresh_token"] == requested["refresh_token"]


def test_paused_watch_rejects_new_refresh_without_resuming_or_changing_work(seeded):
    repository, _, _, claim, clock, _ = seeded
    with repository.engine.connect() as conn:
        before = dict(conn.execute(select(work)).mappings().one())
    response = request_refresh(
        repository, {**claim, "enabled": False}, before, clock[0], expected_status=409
    )
    assert "paused" in response["body"]["error"]
    assert response["item"]["refresh_token"] is None
    with repository.engine.connect() as conn:
        assert dict(conn.execute(select(work)).mappings().one()) == before


def test_missing_refresh_reference_is_not_accepted(seeded):
    repository, _, _, claim, clock, _ = seeded
    response = request_refresh(repository, claim, None, clock[0], expected_status=404)
    assert response["body"] == {"error": "Listing not found"}


def test_accepted_request_survives_pause_and_resumes_normally(seeded):
    repository, settings, store, claim, clock, calls = seeded
    with repository.engine.connect() as conn:
        item = dict(conn.execute(select(work)).mappings().one())
    requested = request_refresh(repository, claim, item, clock[0])
    item.update(requested)
    response = request_refresh(
        repository, {**claim, "enabled": False}, item, clock[0], expected_status=409
    )
    assert response["item"]["refresh_token"] == requested["refresh_token"]
    with repository.engine.begin() as conn:
        conn.execute(
            update(watches).values(
                enabled=False, lease_token=None, lease_until=None, next_scan_at=clock[0].isoformat()
            )
        )
    assert store.claim(now=clock[0]) is None
    with repository.engine.begin() as conn:
        assert conn.execute(select(work.c.refresh_token)).scalar_one() == requested["refresh_token"]
        conn.execute(update(watches).values(enabled=True))
    resumed = store.claim(now=clock[0])
    assert resumed
    worker.run_chunk(repository, settings, None, resumed, now_fn=lambda: clock[0])
    assert calls["details"] == 2
    with repository.engine.connect() as conn:
        assert conn.execute(select(work.c.refresh_token)).scalar_one() is None
