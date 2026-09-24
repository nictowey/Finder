"""Bounded scheduled collector review. Never emits exact identity or fair value."""

import time
from datetime import timedelta

from finder.categories.target_review import review_target_with_alternatives
from finder.categories.vinyl_clues import apply_cheat_sheet
from finder.watch_store import WatchStore

POLICY = "private-target-review-v5"


def _compare(watch, listing, limit, reasons):
    """Delivered subtotal against one owner-set price. Auctions compare the current bid."""
    if limit is None:
        return "no_ceiling"
    subtotal = listing.total_acquisition_cost
    if listing.currency != watch.currency:
        reasons.append("currency_mismatch")
    elif subtotal is None:
        reasons.append("shipping_or_price_unknown")
    elif listing.price_kind not in ("fixed_price", "current_bid"):
        reasons.append("price_kind_unknown")
    else:
        return "within_ceiling" if subtotal <= limit else "over_ceiling"
    return "unknown"


def assess_review(watch, listing, variant, *, now, alternatives=None, search_incomplete=True):
    review = review_target_with_alternatives(
        listing, variant, alternatives, search_incomplete=search_incomplete
    ).model_dump()
    review = apply_cheat_sheet(review, listing, variant, watch.tells, watch.anti_tells)
    status = review["status"]
    reasons = list(review["verify"])
    budget = _compare(watch, listing, watch.maximum_subtotal, reasons)
    # Likely matches use the main price; unclear listings alert only under the lower
    # "gamble" price, and only when the owner set one.
    if status == "possible_pressing":
        alert_budget, accepted = budget, ("no_ceiling", "within_ceiling")
    elif status == "family_review" and watch.gamble_max is not None:
        alert_budget, accepted = _compare(watch, listing, watch.gamble_max, []), ("within_ceiling",)
    else:
        alert_budget, accepted = "not_alerting", ()
    blocked = []
    uncertainty = []
    if review["alternatives_checked"] is None:
        uncertainty.append("catalog_alternatives_not_checked")
    else:
        if search_incomplete:
            uncertainty.append("catalog_alternative_search_incomplete")
        if review["alternatives_not_ruled_out"]:
            uncertainty.append("other_pressings_not_ruled_out")
    if watch.alert_mode == "strict":
        blocked.extend(uncertainty)
        if status == "family_review":
            blocked.append("pressing_unclear")
    reasons.extend(uncertainty)
    if listing.price_kind == "current_bid":
        reasons.append("auction_current_bid_only")
        window = timedelta(minutes=watch.auction_alert_minutes)
        if not watch.auction_alert_minutes:
            blocked.append("auction_alerts_off")
        elif not listing.listing_ends_at or listing.listing_ends_at - now > window:
            blocked.append("auction_not_ending_soon")
    elif listing.price_kind != "fixed_price":
        blocked.append("price_kind_unknown")
    if watch.condition_ids and listing.condition_id not in watch.condition_ids:
        blocked.append("condition_not_accepted")
    if listing.listing_ends_at and listing.listing_ends_at <= now:
        blocked.append("listing_ended")
    if listing.last_observed_at < now - timedelta(hours=1):
        blocked.append("listing_stale")
    if (
        not listing.details_observed_at
        or listing.details_observed_at < now - timedelta(hours=1)
        or any(
            flag in listing.quality_flags
            for flag in ("details_unavailable", "item_specifics_stale", "details_not_requested")
        )
    ):
        blocked.append("details_need_refresh")
    if not watch.country or not watch.postal_code:
        reasons.append("destination_not_set")
        if watch.maximum_subtotal is not None or watch.gamble_max is not None:
            blocked.append("destination_not_set")
    elif (
        listing.source_metadata.get("delivery_country") != watch.country
        or listing.source_metadata.get("delivery_postal_code") != watch.postal_code
    ):
        blocked.append("destination_quote_unconfirmed")
    reasons.extend(blocked)
    reasons.append("verify_photos_condition_and_checkout_total")
    subtotal = listing.total_acquisition_cost
    return {
        **review,
        "verify": sorted(set(reasons)),
        "budget": budget,
        "alert_budget": alert_budget,
        "subtotal": str(subtotal) if subtotal is not None else None,
        "currency": listing.currency,
        "notify": not blocked and alert_budget in accepted,
        "policy": POLICY,
        "alert_mode": watch.alert_mode,
    }


def refresh_hours(watch, listing, review, now):
    """When to re-read a listing: likely leads often, others rarely, auctions near the end."""
    status = review["status"]
    hours = 6 if status == "possible_pressing" else 24 if status == "family_review" else 168
    ends = listing.listing_ends_at
    if (
        listing.price_kind == "current_bid"
        and watch.auction_alert_minutes
        and ends
        and ends > now
        and status in ("possible_pressing", "family_review")
    ):
        alert_at = ends - timedelta(minutes=watch.auction_alert_minutes)
        target = alert_at if alert_at > now else ends
        # Just after the window opens (or the auction ends), never in the past.
        hours = min(hours, max((target - now).total_seconds() / 3600 + 0.02, 0.1))
    return hours


def run_due_watches(
    repository,
    settings,
    discogs_settings,
    *,
    limit=60,
    run_seconds=540,
    context=None,
    clock=time.monotonic,
):
    """Claim due watches until the run's time budget cannot fit another full chunk."""
    from finder.discovery_worker import CHUNK_SECONDS, run_chunk

    store = WatchStore(repository.engine)
    report = {
        "attempted": 0,
        "completed": 0,
        "failed": 0,
        "quota_paused": 0,
        "new_inbox_rows": 0,
        "superseded": 0,
    }
    deadline = clock() + run_seconds
    for _ in range(limit):
        # Leave room for a whole chunk so a lease never outlives the job timeout.
        if clock() + CHUNK_SECONDS + 30 > deadline:
            break
        claim = store.claim(context=context)
        if claim is None:
            break
        report["attempted"] += 1
        result = run_chunk(repository, settings, discogs_settings, claim)
        for key, value in result.items():
            report[key] = report.get(key, 0) + value
        if result.get("quota_paused"):
            break
    return report
