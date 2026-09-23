"""Bounded scheduled collector review. Never emits exact identity or fair value."""

from datetime import UTC, datetime, timedelta

from finder.adapters.discogs.adapter import DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.target_search import (
    REFRESH_PAGE_SIZE,
    InventoryCursor,
    run_target_scan,
)
from finder.categories.target_review import review_target_with_alternatives
from finder.categories.vinyl_target import target_from_release
from finder.errors import CatalogError
from finder.watch_store import SavedWatch, WatchStore


def assess_review(watch, listing, variant, *, now, alternatives=None, search_incomplete=True):
    review = review_target_with_alternatives(
        listing, variant, alternatives, search_incomplete=search_incomplete
    )
    reasons = list(review.verify)
    subtotal = listing.total_acquisition_cost
    budget = "no_ceiling"
    if watch.maximum_subtotal is not None:
        budget = "unknown"
        if listing.currency != watch.currency:
            reasons.append("currency_mismatch")
        elif subtotal is None:
            reasons.append("shipping_or_price_unknown")
        elif listing.price_kind != "fixed_price":
            reasons.append("auction_final_price_unknown")
        else:
            budget = "within_ceiling" if subtotal <= watch.maximum_subtotal else "over_ceiling"
    blocked = []
    if review.alternatives_checked is None:
        blocked.append("catalog_alternatives_not_checked")
    else:
        if search_incomplete:
            blocked.append("catalog_alternative_search_incomplete")
        if review.alternatives_not_ruled_out:
            blocked.append("other_pressings_not_ruled_out")
    if listing.price_kind != "fixed_price":
        blocked.append("auction_final_price_unknown")
    if watch.condition_ids and listing.condition_id not in watch.condition_ids:
        blocked.append("condition_not_accepted")
    if listing.listing_ends_at and listing.listing_ends_at <= now:
        blocked.append("listing_ended")
    if listing.last_observed_at < now - timedelta(hours=1):
        blocked.append("listing_stale")
    if not listing.details_observed_at or any(
        flag in listing.quality_flags
        for flag in ("details_unavailable", "item_specifics_stale", "details_not_requested")
    ):
        blocked.append("details_need_refresh")
    if not watch.country or not watch.postal_code:
        reasons.append("destination_not_set")
        if watch.maximum_subtotal is not None:
            blocked.append("destination_not_set")
    elif (
        listing.source_metadata.get("delivery_country") != watch.country
        or listing.source_metadata.get("delivery_postal_code") != watch.postal_code
    ):
        blocked.append("destination_quote_unconfirmed")
    reasons.extend(blocked)
    reasons.append("verify_photos_condition_and_checkout_total")
    return {
        **review.model_dump(),
        "verify": sorted(set(reasons)),
        "budget": budget,
        "subtotal": str(subtotal) if subtotal is not None else None,
        "currency": listing.currency,
        "notify": review.status == "possible_pressing"
        and not blocked
        and budget in ("no_ceiling", "within_ceiling"),
        "policy": "private-target-review-v3",
    }


def run_due_watches(repository, settings, discogs_settings, *, limit=3):
    store = WatchStore(repository.engine)
    report = {"attempted": 0, "completed": 0, "failed": 0, "new_inbox_rows": 0}
    for _ in range(limit):
        claim = store.claim()
        if claim is None:
            break
        report["attempted"] += 1
        try:
            watch = SavedWatch.model_validate(claim["config"])
            with DiscogsClient(discogs_settings) as client:
                provider = DiscogsCatalogProvider(client)
                variant = provider.get_release(watch.release_id)
                try:
                    catalog_check = provider.search_alternatives(variant)
                except CatalogError:
                    # Still surface discovered listings, but no notifications based on a
                    # missing comparison. Do not expose provider payloads in public logs.
                    catalog_check = None
            target = target_from_release(variant).model_copy(update={"id": "private-watch"})
            cursor = InventoryCursor.load(target, claim["revision"], claim["summary"])
            mode = "refresh" if claim["last_success_at"] else "initial"
            inventory = cursor.pass_for_refresh() if mode == "refresh" else None
            destination_settings = settings.model_copy(
                update={
                    "delivery_country": watch.country,
                    "delivery_postal_code": watch.postal_code,
                }
            )
            candidate_recheck = "not_due"
            unavailable_item_ids = []
            with EbayClient(destination_settings) as client:
                adapter = EbayAdapter(client)
                scan = run_target_scan(
                    target,
                    adapter,
                    repository,
                    mode=mode,
                    refresh_page_size=REFRESH_PAGE_SIZE,
                    inventory=inventory,
                )
                complete = len(scan.summaries) == scan.expected_queries and all(
                    row.status == "completed" for row in scan.summaries
                )
                if complete and mode == "refresh" and inventory is None:
                    item_id = store.recheck_candidate(claim, excluded=scan.discovered_item_ids)
                    candidate_recheck = "none_eligible"
                    if item_id is not None:
                        previous = repository.get("ebay", item_id)
                        if previous is None:
                            candidate_recheck = "snapshot_missing"
                        else:
                            observation = adapter.refresh_known(
                                item_id, source_metadata=previous.source_metadata
                            )
                            if observation.listing:
                                result = repository.upsert(observation.listing)
                                candidate_recheck = result
                                if result != "suppressed":
                                    scan.discovered_item_ids.add(item_id)
                            elif observation.skip_reason in ("item_unavailable", "listing_ended"):
                                unavailable_item_ids.append(item_id)
                                candidate_recheck = "unavailable"
                            else:
                                candidate_recheck = observation.skip_reason or "invalid"
                browse_requests = client.browse_requests
                browse_retries = client.browse_retries
            next_cursor = cursor.after_success(scan.inventory_summary) if complete else cursor
            now = datetime.now(UTC)
            reviews = []
            for item_id in scan.discovered_item_ids:
                listing = repository.get("ebay", item_id)
                if listing:
                    reviews.append(
                        (
                            listing,
                            assess_review(
                                watch,
                                listing,
                                variant,
                                now=now,
                                alternatives=catalog_check.variants if catalog_check else None,
                                search_incomplete=catalog_check.search_incomplete
                                if catalog_check
                                else True,
                            ),
                        )
                    )
            report["new_inbox_rows"] += store.finish(
                claim,
                reviews,
                catalog=variant,
                summary={
                    "observed": len(reviews),
                    "page_cap_reached": any(row.limit_reached for row in scan.summaries),
                    "complete": complete,
                    "inventory": next_cursor.as_summary(),
                    "discovery": {
                        "mode": mode,
                        "plan_version": target.plan_version,
                        "newest_page_size": REFRESH_PAGE_SIZE if mode == "refresh" else 10,
                        "browse_requests": browse_requests,
                        "browse_retries": browse_retries,
                        "newest": [
                            {
                                "query_position": i + 1,
                                "pages": item.pages_fetched,
                                "returned": item.fetched,
                                "reported_total": item.reported_total,
                                "cap_reached": item.limit_reached,
                                "partial_details": item.partial_details,
                                "status": item.status,
                            }
                            for i, item in enumerate(
                                scan.summaries[: len(target.queries)]
                                if mode == "refresh"
                                else scan.summaries[len(target.queries) :]
                            )
                        ],
                        "initial_relevance": [
                            {
                                "query_position": i + 1,
                                "returned": item.fetched,
                                "cap_reached": item.limit_reached,
                                "status": item.status,
                            }
                            for i, item in enumerate(scan.summaries[: len(target.queries)])
                        ]
                        if mode == "initial"
                        else None,
                        "inventory_sample": {
                            "query_position": inventory.query_index + 1,
                            "offset": inventory.offset,
                            "returned": scan.inventory_summary.fetched,
                            "reported_total": scan.inventory_summary.reported_total,
                            "cap_reached": scan.inventory_summary.limit_reached,
                            "depth_capped": scan.inventory_summary.limit_reached
                            and inventory.offset == 30,
                            "status": scan.inventory_summary.status,
                        }
                        if inventory is not None and scan.inventory_summary is not None
                        else None,
                        "inventory_due_next_scan": next_cursor.due,
                        "candidate_recheck": candidate_recheck,
                        "marketplace_recall_measured": False,
                    },
                    "catalog_alternatives_checked": len(catalog_check.variants)
                    if catalog_check
                    else None,
                    "catalog_search_incomplete": catalog_check.search_incomplete
                    if catalog_check
                    else True,
                    "catalog_check_failed": catalog_check is None,
                    "ambiguous_leads": sum(
                        row[1]["status"] == "possible_pressing"
                        and bool(row[1]["alternatives_not_ruled_out"])
                        for row in reviews
                    ),
                },
                success=complete,
                unavailable_item_ids=unavailable_item_ids,
                now=now,
            )
            report["completed" if complete else "failed"] += 1
        except Exception:
            # No exception bodies: provider, database and validation messages may contain
            # identities or connection information in this public workflow.
            store.finish(
                claim,
                [],
                success=False,
                summary={
                    "error": "scan_failed",
                    "inventory": claim["summary"].get("inventory")
                    if isinstance(claim["summary"], dict)
                    else None,
                },
            )
            report["failed"] += 1
    return report
