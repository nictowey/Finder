"""Bounded work chunks over unbounded-in-time, durable inventory passes."""

import hashlib
import json
import os
import time
import tomllib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from finder.adapters.discogs.adapter import DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.discovery import iso, search_page
from finder.adapters.ebay.quota import summarize_browse_quota
from finder.categories.vinyl_target import target_from_release
from finder.discovery_store import (
    EPOCH,
    OVERLAP,
    DiscoveryStore,
    LostLease,
    StorageBudget,
    advance_pass,
    new_pass,
)
from finder.errors import CatalogError, RateLimitError
from finder.watch_store import SavedWatch, WatchStore
from finder.watch_store import settings as private_settings

# These are chunk limits, never coverage limits. Hard client attempt cap includes retries.
SEARCH_CALLS = 8
DETAIL_CALLS = 16
ATTEMPT_LIMIT = 40
CHUNK_SECONDS = 150


def rollout_slots(engine):
    with engine.connect() as conn:
        config = conn.execute(
            select(private_settings.c.data).where(private_settings.c.key == "discovery_rollout")
        ).scalar()
    return config.get("slots", []) if config else []


def prepare_passes(state, now, reconciliation_hours):
    for q in state["queries"]:
        inc = q["incremental"]
        if inc["status"] == "search_exhausted" and now - datetime.fromisoformat(
            inc["started_at"].replace("Z", "+00:00")
        ) >= timedelta(minutes=30):
            # No jump after an outage: start from the last exhausted upper bound.
            lower = datetime.fromisoformat(q["watermark"].replace("Z", "+00:00")) - OVERLAP
            q["incremental"] = new_pass(iso(lower), iso(now))
        base = q["baseline"]
        rec = q["reconciliation"]
        finished = (
            rec["finished_at"]
            if rec and rec["status"] == "search_exhausted"
            else base["finished_at"]
        )
        if (
            base["status"] == "search_exhausted"
            and (rec is None or rec["status"] == "search_exhausted")
            and finished
            and now - datetime.fromisoformat(finished.replace("Z", "+00:00"))
            >= timedelta(hours=reconciliation_hours)
        ):
            q["reconciliation"] = new_pass(EPOCH, iso(now))


def next_task(state):
    # Rotate every lane and query, including incremental during long baseline work.
    tasks = [
        (i, name)
        for name in ("incremental", "baseline", "reconciliation")
        for i in range(len(state["queries"]))
    ]
    for step in range(len(tasks)):
        index = (state["round_robin"] + step) % len(tasks)
        i, name = tasks[index]
        current = state["queries"][i].get(name)
        if current and current["status"] not in ("search_exhausted", "partial_provider_limit"):
            return i, name, (index + 1) % len(tasks)
    return None


def run_chunk(repository, settings, discogs_settings, claim, *, now_fn=lambda: datetime.now(UTC)):
    from finder.watch_worker import assess_review

    store, queue = WatchStore(repository.engine), DiscoveryStore(repository.engine)
    watch = SavedWatch.model_validate(claim["config"])
    deadline = time.monotonic() + CHUNK_SECONDS
    state = None
    requests = {
        "search_requests": 0,
        "detail_requests": 0,
        "browse_requests": 0,
        "browse_retries": 0,
        "quota_requests": 0,
        "catalog_requests": 0,
    }
    added = 0
    partial = None
    failure = False
    try:
        with DiscogsClient(discogs_settings) as catalog_client:
            provider = DiscogsCatalogProvider(catalog_client)
            variant = provider.get_release(watch.release_id)
            requests["catalog_requests"] += 1
            try:
                alternatives = provider.search_alternatives(variant)
                requests["catalog_requests"] += 1 + len(alternatives.variants)
            except CatalogError:
                alternatives = None
        requests["catalog_requests"] = catalog_client.requests
        requests["catalog_retries"] = catalog_client.retries
        target = target_from_release(variant).model_copy(update={"id": "private-watch"})
        plan_file = Path("config/watch_targets.toml")
        if plan_file.exists():
            for configured_target in tomllib.loads(plan_file.read_text()).get("targets", []):
                if str(configured_target.get("catalog_variant_id")) == str(watch.release_id):
                    from finder.adapters.ebay.target_search import EbaySearchTarget

                    alias_plan = EbaySearchTarget.model_validate(configured_target)
                    queries = list(dict.fromkeys(target.queries + alias_plan.queries))
                    if len(queries) > 3:
                        raise ValueError("Alias plan exceeds the supported three queries")
                    target = target.model_copy(update={"queries": queries})
        state = queue.load(claim, target, now_fn())

        def catalog_content(value):
            data = value.model_dump(mode="json")
            for key in ("observed_at", "first_observed_at", "last_observed_at"):
                data.pop(key, None)
            return data

        state["evaluation_signature"] = hashlib.sha256(
            json.dumps(
                [
                    catalog_content(variant),
                    [catalog_content(v) for v in alternatives.variants] if alternatives else None,
                    alternatives.search_incomplete if alternatives else True,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        prepare_passes(
            state, now_fn(), max(6, int(os.environ.get("FINDER_RECONCILIATION_HOURS", "24")))
        )
        queue.checkpoint(claim, state, now_fn())
        configured = settings.model_copy(
            update={"delivery_country": watch.country, "delivery_postal_code": watch.postal_code}
        )
        with EbayClient(configured) as client:
            client.request_limit = ATTEMPT_LIMIT
            try:
                # Actual app quota, not the published default. Shared atomic debits in
                # EbayClient subsequently enforce the reserve on every network attempt.
                quota = summarize_browse_quota(
                    client.get(
                        "/developer/analytics/v1_beta/rate_limit/",
                        headers={},
                        params={"api_context": "buy", "api_name": "browse"},
                    )
                )
                pool = [r for r in quota["resources"] if r["name"] == "buy.browse"]
                if len(pool) != 1:
                    raise RateLimitError("Shared quota missing")
                remaining = min(r["remaining"] for r in pool[0]["rates"])
                requests.update(quota_remaining=remaining, quota_required=ATTEMPT_LIMIT + 200)
                if remaining < ATTEMPT_LIMIT + 200:
                    raise RateLimitError("Insufficient quota for chunk")
                for _ in range(SEARCH_CALLS):
                    if time.monotonic() >= deadline:
                        partial = "execution_budget"
                        break
                    task = next_task(state)
                    if task is None:
                        break
                    i, lane, rotation = task
                    current = state["queries"][i][lane]
                    part = current["frontier"][0]
                    try:
                        page = search_page(
                            client,
                            target,
                            i,
                            lower=part["lower"],
                            upper=part["upper"],
                            offset=part["offset"],
                        )
                        next_state = deepcopy(state)
                        advanced = advance_pass(current, page, now_fn())
                        next_state["queries"][i][lane] = advanced
                        if lane == "incremental" and advanced["status"] == "search_exhausted":
                            next_state["queries"][i]["watermark"] = current["started_at"]
                        next_state["round_robin"] = rotation
                        next_state["last_activity_at"] = iso(now_fn())
                        queue.checkpoint(
                            claim,
                            next_state,
                            now_fn(),
                            items=page.items,
                            kind="new_to_finder"
                            if lane != "incremental"
                            else "newly_listed_on_ebay",
                        )
                        state = next_state
                        if advanced["status"] == "interrupted":
                            failure = True
                            break
                    except (RateLimitError, LostLease, StorageBudget):
                        raise
                    except Exception:
                        state["queries"][i][lane]["status"] = "interrupted"
                        state["queries"][i][lane]["reason"] = "search_or_window_validation_failed"
                        state["round_robin"] = rotation
                        queue.checkpoint(claim, state, now_fn())
                        failure = True
                        break
                # Reserve a quarter of detail slots for oldest existing leads. Pending
                # failures are delayed, so one bad item cannot monopolize the queue.
                old = queue.due(claim, now_fn(), limit=4, pending=False)
                pending = queue.due(claim, now_fn(), limit=DETAIL_CALLS - len(old), pending=True)
                adapter = EbayAdapter(client, now=now_fn)
                for item in old + pending:
                    if time.monotonic() >= deadline:
                        partial = "execution_budget"
                        break
                    try:
                        previous = repository.get("ebay", item["item_id"])
                        if (
                            item["status"] == "pending"
                            and item["kind"] != "existing_listing_updated"
                            and previous
                            and previous.details_observed_at
                            and previous.details_observed_at >= now_fn() - timedelta(hours=1)
                            and previous.source_metadata.get("delivery_country") == watch.country
                            and previous.source_metadata.get("delivery_postal_code")
                            == watch.postal_code
                            and not any(
                                f in previous.quality_flags
                                for f in (
                                    "details_unavailable",
                                    "item_specifics_stale",
                                    "details_not_requested",
                                )
                            )
                        ):
                            from finder.adapters.base import ListingObservation

                            observation = ListingObservation(listing=previous)
                        else:
                            observation = adapter.refresh_known(item["item_id"])
                        if observation.listing:
                            listing = observation.listing
                            review = assess_review(
                                watch,
                                listing,
                                variant,
                                now=now_fn(),
                                alternatives=alternatives.variants if alternatives else None,
                                search_incomplete=alternatives.search_incomplete
                                if alternatives
                                else True,
                            )
                            status, inserted = queue.disposition(
                                claim,
                                item,
                                now_fn(),
                                status="evaluated",
                                listing=listing,
                                repository=repository,
                                review=review,
                                state=state,
                                refresh_hours=4
                                if review["status"] in ("possible_pressing", "family_review")
                                else 24,
                            )
                            added += inserted or 0
                        else:
                            unavailable = observation.skip_reason in (
                                "item_unavailable",
                                "listing_ended",
                            )
                            queue.disposition(
                                claim,
                                item,
                                now_fn(),
                                status="unavailable" if unavailable else "error",
                                reason=observation.skip_reason,
                                refresh_hours=24 if unavailable else 1,
                            )
                    except (RateLimitError, LostLease):
                        raise
                    except Exception:
                        queue.disposition(
                            claim,
                            item,
                            now_fn(),
                            status="error",
                            reason="detail_failed",
                            refresh_hours=min(24, 2 ** min(item["failures"], 4)),
                        )
                if next_task(state):
                    partial = partial or "chunk_budget"
            except (RateLimitError, ValueError):
                partial = "quota_or_attempt_budget"
            except StorageBudget:
                partial = "storage_budget"
            finally:
                for field in (
                    "browse_requests",
                    "browse_retries",
                    "search_requests",
                    "detail_requests",
                    "quota_requests",
                ):
                    requests[field] = getattr(client, field, 0)
        if partial:
            for q in state["queries"]:
                for lane in ("baseline", "incremental", "reconciliation"):
                    if q.get(lane) and q[lane]["status"] in (
                        "not_started",
                        "in_progress",
                        "partial_budget",
                    ):
                        q[lane]["status"] = "partial_budget"
                        q[lane]["reason"] = partial
        queue.checkpoint(claim, state, now_fn())
        coverage = queue.coverage(claim, state)
        coverage["partial_reason"] = partial
        # Active work resumes at the next ten-minute catch-up, not the full monitor interval.
        # Rate limit and provider failures back off; the shared guard still applies.
        delay = (
            30
            if failure or partial == "quota_or_attempt_budget"
            else 1
            if next_task(state) or coverage["pending"]
            else 30
        )
        result = store.finish(
            claim,
            [],
            catalog=variant,
            summary={
                "worker_finished": True,
                "new_inbox_rows": added,
                "coverage": coverage,
                "discovery": requests,
                "catalog_search_incomplete": alternatives.search_incomplete
                if alternatives
                else True,
                "catalog_check_failed": alternatives is None,
            },
            success=not failure and partial != "quota_or_attempt_budget",
            next_delay_minutes=delay,
            now=now_fn(),
        )
        return {
            "completed": int(
                result is not None and not failure and partial != "quota_or_attempt_budget"
            ),
            "failed": int(failure),
            "superseded": int(result is None),
            "quota_paused": int(partial == "quota_or_attempt_budget"),
            "new_inbox_rows": added,
            "search_requests": requests["search_requests"],
            "detail_requests": requests["detail_requests"],
            "pending": coverage["pending"],
            "unique_retrieved": coverage["unique_retrieved"],
            "evaluated": coverage["outcomes"].get("evaluated", 0),
            "initial_queries_exhausted": coverage["initial"]["queries_exhausted"],
            "initial_pages_committed": coverage["initial"]["pages"],
        }
    except LostLease:
        return {"superseded": 1, "new_inbox_rows": added}
    except Exception:
        summary = {"error": "discovery_failed", "discovery": requests}
        if state:
            summary["coverage"] = queue.coverage(claim, state)
        store.finish(claim, [], success=False, summary=summary, now=now_fn())
        return {"failed": 1, "new_inbox_rows": added}
