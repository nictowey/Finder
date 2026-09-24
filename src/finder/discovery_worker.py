"""Bounded work chunks over unbounded-in-time, durable inventory passes."""

import hashlib
import json
import math
import os
import re
import time
import tomllib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from finder.adapters.discogs.adapter import AlternativeRetrieval, DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.discovery import iso, search_page
from finder.adapters.ebay.quota import summarize_browse_quota
from finder.categories.vinyl_target import target_from_release
from finder.discovery_store import (
    EPOCH,
    OVERLAP,
    SETTINGS_CHANGED,
    DiscoveryStore,
    LostLease,
    StorageBudget,
    advance_pass,
    new_pass,
    progress,
)
from finder.errors import CatalogError, RateLimitError
from finder.watch_profile import load_profile, siblings_of
from finder.watch_store import SavedWatch, WatchStore, watches

# These are chunk limits, never coverage limits. Hard client attempt cap includes retries.
SEARCH_CALLS = 8
DETAIL_CALLS = 16
ATTEMPT_LIMIT = 40
CHUNK_SECONDS = 150
MAX_QUERIES = 6
RESORT_LIMIT = 300
# Browse searches per day set aside for new-listing polls across every enabled watch.
# The rest of the 5,000-call quota covers details, reconciliation and retries.
POLL_BUDGET = 2000
MIN_POLL_MINUTES = 10
_quota_cache = {}


def poll_minutes(engine):
    """New-listing cadence: every 10 minutes for a few watches, stretching as they grow."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(progress.c.data)
            .select_from(watches.outerjoin(progress, progress.c.watch_id == watches.c.id))
            .where(watches.c.enabled.is_(True))
        ).scalars()
        queries = sum(
            len(data["queries"]) if isinstance(data, dict) and data.get("queries") else 3
            for data in rows
        )
    return max(MIN_POLL_MINUTES, math.ceil(1440 * queries / POLL_BUDGET))


def watch_queries(target_queries, variant, watch):
    """Album searches first, then the owner's own searches, then the barcode."""
    queries = list(target_queries) + list(watch.extra_queries)
    barcodes = [
        digits
        for kind, values in variant.identifiers.items()
        if "barcode" in kind.casefold()
        for value in values
        if len(digits := re.sub(r"\D", "", value)) in (12, 13)
    ]
    if barcodes:
        queries.append(f"gtin:{barcodes[0]}")
    unique = []
    for query in queries:
        if query.strip() and query.casefold() not in {q.casefold() for q in unique}:
            unique.append(query.strip())
    return unique[:MAX_QUERIES]


def prepare_passes(state, now, reconciliation_hours, poll_every=30):
    for q in state["queries"]:
        inc = q["incremental"]
        if inc["status"] == "search_exhausted" and now - datetime.fromisoformat(
            inc["started_at"].replace("Z", "+00:00")
        ) >= timedelta(minutes=poll_every):
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


def detail_batch(queue, claim, now):
    """Guarantee old work a share, and use spare pending capacity for due refreshes."""
    old = queue.due(claim, now, limit=DETAIL_CALLS, pending=False)
    reserved = old[:4]
    pending = queue.due(claim, now, limit=DETAIL_CALLS - len(reserved), pending=True)
    return reserved + pending + old[4 : 4 + DETAIL_CALLS - len(reserved) - len(pending)]


def run_chunk(repository, settings, discogs_settings, claim, *, now_fn=lambda: datetime.now(UTC)):
    from finder.watch_worker import assess_review, refresh_hours

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
    diagnostic = {}
    try:
        with DiscogsClient(discogs_settings) as catalog_client:
            provider = DiscogsCatalogProvider(catalog_client)
            variant = provider.get_release(watch.release_id)
            profile = load_profile(repository.engine, claim["id"], provider, variant, now_fn())
            if profile is not None:
                # Every vinyl version on Discogs, cached weekly, replaces a capped search.
                alternatives = AlternativeRetrieval(siblings_of(profile), profile["partial"])
            else:
                try:
                    alternatives = provider.search_alternatives(variant)
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
                    target = target.model_copy(update={"queries": queries})
        target = target.model_copy(
            update={"queries": watch_queries(target.queries, variant, watch)}
        )
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
        poll_every = poll_minutes(repository.engine)
        prepare_passes(
            state,
            now_fn(),
            max(6, int(os.environ.get("FINDER_RECONCILIATION_HOURS", "24"))),
            poll_every,
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
                # Many short chunks share one worker run: reuse a live reading for five
                # minutes. The per-request shared debit still enforces the reserve.
                cached = _quota_cache.get("browse")
                live = getattr(client, "_live_transport", False)
                if live and cached and time.monotonic() - cached[0] < 300:
                    remaining = cached[1]
                else:
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
                    if live:
                        _quota_cache["browse"] = (time.monotonic(), remaining)
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
                    before_search = getattr(client, "search_requests", 0)
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
                        reconciliations = [q.get("reconciliation") for q in next_state["queries"]]
                        if all(p and p["status"] == "search_exhausted" for p in reconciliations):
                            next_state["last_reconciliation_at"] = max(
                                p["finished_at"] for p in reconciliations
                            )
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
                    except Exception as exc:
                        # Exception class names only: provider messages can carry identities.
                        barcode = target.queries[i].startswith("gtin:")
                        diagnostic[
                            f"search_failed_{'barcode' if barcode else 'keywords'}_"
                            f"{type(exc).__name__}"
                        ] = 1
                        if barcode:
                            # The barcode search is an optional extra. If eBay rejects it or
                            # it cannot honor the date window, stop using it for this pass
                            # rather than failing the whole watch on every scan.
                            state["queries"][i][lane]["status"] = "partial_provider_limit"
                            state["queries"][i][lane]["reason"] = "barcode_search_unavailable"
                            state["round_robin"] = rotation
                            queue.checkpoint(claim, state, now_fn())
                            continue
                        state["queries"][i][lane]["status"] = "interrupted"
                        state["queries"][i][lane]["reason"] = "search_or_window_validation_failed"
                        state["round_robin"] = rotation
                        queue.checkpoint(claim, state, now_fn())
                        failure = True
                        break
                    finally:
                        metric = f"{lane}_search_requests"
                        requests[metric] = requests.get(metric, 0) + (
                            getattr(client, "search_requests", 0) - before_search
                        )
                # Reserve a quarter of detail slots for oldest existing leads. Pending
                # failures are delayed, so one bad item cannot monopolize the queue.
                # A price or cheat-sheet edit re-sorts known listings from stored details,
                # with no eBay calls. Plausible ones are re-read soon so alerts stay fresh.
                for item in queue.due(claim, now_fn(), limit=RESORT_LIMIT, pending=True):
                    if item["reason"] != SETTINGS_CHANGED:
                        continue
                    if time.monotonic() >= deadline:
                        partial = "execution_budget"
                        break
                    previous = repository.get("ebay", item["item_id"])
                    if (
                        previous is None
                        or previous.source_metadata.get("delivery_country") != watch.country
                        or previous.source_metadata.get("delivery_postal_code") != watch.postal_code
                    ):
                        continue  # Left pending; the detail loop reads it from eBay.
                    review = assess_review(
                        watch,
                        previous,
                        variant,
                        now=now_fn(),
                        alternatives=alternatives.variants if alternatives else None,
                        search_incomplete=alternatives.search_incomplete if alternatives else True,
                    )
                    hours = refresh_hours(watch, previous, review, now_fn())
                    if review["status"] in ("possible_pressing", "family_review") and (
                        "details_need_refresh" in review["verify"]
                    ):
                        hours = min(hours, 0.1)
                    _, inserted = queue.disposition(
                        claim,
                        item,
                        now_fn(),
                        status="evaluated",
                        listing=previous,
                        repository=repository,
                        review=review,
                        state=state,
                        refresh_hours=hours,
                    )
                    added += inserted or 0
                batch = detail_batch(queue, claim, now_fn())
                adapter = EbayAdapter(client, now=now_fn)
                for item in batch:
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
                                refresh_hours=refresh_hours(watch, listing, review, now_fn()),
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
            except (RateLimitError, ValueError) as exc:
                from finder.adapters.ebay.budget import BudgetTelemetryError

                if isinstance(exc, BudgetTelemetryError):
                    requests["budget_failure"] = exc.reason
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
            max(30, poll_every)
            if failure or partial == "quota_or_attempt_budget"
            else 1
            if next_task(state)
            or coverage["pending"]
            or queue.due(claim, now_fn(), limit=1, pending=False)
            else poll_every
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
            **diagnostic,
        }
    except LostLease:
        return {"superseded": 1, "new_inbox_rows": added}
    except Exception as exc:
        diagnostic[f"chunk_failed_{type(exc).__name__}"] = 1
        summary = {"error": "discovery_failed", "discovery": requests}
        if state:
            summary["coverage"] = queue.coverage(claim, state)
        store.finish(claim, [], success=False, summary=summary, now=now_fn())
        return {"failed": 1, "new_inbox_rows": added, **diagnostic}
