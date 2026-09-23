"""One-time, bounded catalog and eBay review probe for a private target list.

Input IDs come from an Actions secret, never a repository fixture or public log. No
seller listing or catalog detail is persisted. Counts are policy output, not truth
labels, discovery recall, pressing precision, or market value.
"""

import argparse
import json
import os
from collections import Counter

from finder.adapters.discogs.adapter import DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.target_search import plan_target_search
from finder.categories.target_review import review_target_with_alternatives
from finder.categories.vinyl import from_listing
from finder.categories.vinyl_target import parse_discogs_release_id, target_from_release
from finder.config import load_discogs_settings, load_settings
from finder.errors import ConfigurationError, FinderError
from finder.matching import _artist_matches_catalog

BATCH_SIZE = 4
PAGE_SIZE = 5
MAX_TARGETS = 12
STATUSES = ("possible_pressing", "family_review", "conflicting", "unrelated")
CONFLICT_REASONS = (
    "artist_conflict",
    "barcode",
    "catalog_number",
    "color_conflict",
    "competing_album_title_claim",
    "non_vinyl_claim",
)


def parse_private_ids(raw: str) -> list[int]:
    """Validate the whole bounded list before making any external request."""
    parts = [part.strip() for part in raw.split(",")]
    if not 1 <= len(parts) <= MAX_TARGETS or any(not part for part in parts):
        raise ConfigurationError("Supply one to twelve private release IDs.")
    try:
        ids = [parse_discogs_release_id(part) for part in parts]
    except ConfigurationError:
        raise ConfigurationError("The private release list contains an invalid ID.") from None
    if len(ids) != len(set(ids)):
        raise ConfigurationError("The private release list contains a duplicate ID.")
    return ids


def probe_batch(
    ids: list[int],
    *,
    batch: int,
    catalog: DiscogsCatalogProvider,
    adapter: EbayAdapter,
    coverage_audit: bool = False,
) -> dict:
    """Review up to four exact catalog targets against small, current eBay pages."""
    selected = ids[batch * BATCH_SIZE : (batch + 1) * BATCH_SIZE]
    if not selected:
        raise ConfigurationError("The selected private target batch is empty.")
    rows = []
    for index, release_id in enumerate(selected, start=batch * BATCH_SIZE + 1):
        row: dict = {"ordinal": index}
        try:
            variant = catalog.get_release(release_id)
            target = target_from_release(variant)
            alternatives = catalog.search_alternatives(variant)
            monitors = plan_target_search(target, mode="initial", page_size=PAGE_SIZE)
            seen: set[str] = set()
            baseline_observed: set[str] = set()
            counts: Counter[str] = Counter()
            conflicts: Counter[str] = Counter()
            artist_credit_shapes: Counter[str] = Counter()
            possible_with_catalog_uncertainty = 0
            capped = 0
            for monitor in monitors:
                for observation in adapter.search(monitor, seen_item_ids=seen):
                    if observation.listing is None:
                        continue
                    baseline_observed.add(observation.listing.marketplace_item_id)
                    review = review_target_with_alternatives(
                        observation.listing,
                        variant,
                        alternatives.variants,
                        search_incomplete=alternatives.search_incomplete,
                    )
                    counts[review.status] += 1
                    if len(variant.artists) > 1:
                        seller_artists = from_listing(observation.listing).artists
                        if seller_artists:
                            exact_single = any(
                                _artist_matches_catalog(value, [name])
                                for value in seller_artists
                                for name in variant.artists
                            )
                            joined = any(
                                _artist_matches_catalog(value, variant.artists)
                                for value in seller_artists
                            )
                            shape = (
                                "single_catalog_artist"
                                if exact_single
                                else "joined_catalog_artists"
                                if joined
                                else "unresolved_artist_credit"
                            )
                            artist_credit_shapes[shape] += 1
                    if review.status == "conflicting":
                        conflicts.update(
                            reason for reason in review.verify if reason in CONFLICT_REASONS
                        )
                    if review.status == "possible_pressing" and (
                        alternatives.search_incomplete or review.alternatives_not_ruled_out
                    ):
                        possible_with_catalog_uncertainty += 1
                capped += adapter.stats.limit_reached
            if coverage_audit:
                # A deliberately different, still bounded search sample. These are
                # additional API observations, not an independent market census.
                broad = monitors[0]
                audit_monitors = [
                    broad.model_copy(
                        update={
                            "id": f"{broad.id}-audit-newest",
                            "source_options": {
                                **broad.source_options,
                                "sort": "newlyListed",
                                "offset": PAGE_SIZE,
                            },
                        }
                    )
                ]
                if len(variant.artists) > 1:
                    alternate = f"{variant.artists[1]} {variant.title}".strip()
                    if len(alternate) <= 100 and alternate.casefold() not in {
                        query.casefold() for query in target.queries
                    }:
                        audit_monitors.append(
                            broad.model_copy(
                                update={"id": f"{broad.id}-audit-artist", "query": alternate}
                            )
                        )
                audit_seen: set[str] = set()
                audit_counts: Counter[str] = Counter()
                audit_overlap = 0
                audit_capped = 0
                for monitor in audit_monitors:
                    for observation in adapter.search(monitor, seen_item_ids=audit_seen):
                        if observation.listing is None:
                            continue
                        if observation.listing.marketplace_item_id in baseline_observed:
                            audit_overlap += 1
                            continue
                        review = review_target_with_alternatives(
                            observation.listing,
                            variant,
                            alternatives.variants,
                            search_incomplete=alternatives.search_incomplete,
                        )
                        audit_counts[review.status] += 1
                    audit_capped += adapter.stats.limit_reached
                row["coverage_audit"] = {
                    "queries": len(audit_monitors),
                    "queries_capped": audit_capped,
                    "overlap_with_initial": audit_overlap,
                    "additional_sampled": sum(audit_counts.values()),
                    "additional_review_counts": {
                        status: audit_counts[status] for status in STATUSES
                    },
                }
            row.update(
                {
                    "queries": len(monitors),
                    "queries_capped": capped,
                    "distinct_listings": sum(counts.values()),
                    "review_counts": {status: counts[status] for status in STATUSES},
                    "conflict_reasons": {
                        reason: conflicts[reason]
                        for reason in CONFLICT_REASONS
                        if conflicts[reason]
                    },
                    "multi_artist_credit_shapes": dict(artist_credit_shapes),
                    "possible_with_catalog_uncertainty": possible_with_catalog_uncertainty,
                    "catalog_alternatives_checked": len(alternatives.variants),
                    "catalog_alternative_search_incomplete": alternatives.search_incomplete,
                }
            )
        except FinderError as exc:
            # No provider payloads, titles, IDs, queries, or seller data in public logs.
            row["error_type"] = type(exc).__name__
            rows.append(row)
            break
        rows.append(row)
    return {
        "status": (
            "complete"
            if len(rows) == len(selected) and all("error_type" not in row for row in rows)
            else "partial"
        ),
        "scope": "private_catalog_targets_bounded_ebay_us_pages",
        "batch": batch,
        "targets_expected": len(selected),
        "maximum_browse_requests_without_retries": len(selected)
        * (3 + (2 if coverage_audit else 0))
        * (1 + PAGE_SIZE),
        "results": rows,
        "limitations": (
            "Seller claims are unverified. One initial best-match page of five per query; "
            "up to five catalog alternatives. No independent listing denominator, "
            "pressing truth labels, sold prices, destination quote, or persisted listings. "
            "Optional alternate-sort/artist sample is same-API incremental coverage only, "
            "not marketplace-wide recall or a stable snapshot."
        ),
        "attribution": "Data provided by Discogs",
        "attribution_url": "https://www.discogs.com",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded private target evaluation")
    parser.add_argument("--batch", type=int, choices=range(3), required=True)
    parser.add_argument("--coverage-audit", action="store_true")
    args = parser.parse_args()
    ids = parse_private_ids(os.environ.get("FINDER_EVAL_RELEASE_IDS", ""))
    if args.batch * BATCH_SIZE >= len(ids):
        raise ConfigurationError("The selected private target batch is empty.")
    discogs_settings, ebay_settings = load_discogs_settings(), load_settings()
    with DiscogsClient(discogs_settings, max_retries=1) as discogs_client:
        with EbayClient(ebay_settings, max_retries=0) as ebay_client:
            result = probe_batch(
                ids,
                batch=args.batch,
                catalog=DiscogsCatalogProvider(discogs_client),
                adapter=EbayAdapter(ebay_client),
                coverage_audit=args.coverage_audit,
            )
            result["browse_requests"] = ebay_client.browse_requests
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
