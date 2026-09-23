"""Private, bounded one-release Production probe with aggregate public output.

The selected release comes from an encrypted workflow secret. No listing, seller,
catalog identity, title, URL, price, or evidence value is printed. This check does
not label or retain a real listing as a confirmed pressing.
"""

import json
import os
import re
from collections import Counter

from sqlalchemy.engine import make_url

from finder.adapters.discogs.adapter import DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.target_search import plan_target_search, run_target_scan
from finder.categories.target_review import review_target_listing
from finder.categories.vinyl import from_listing, from_variant
from finder.categories.vinyl_review import compare_pressings
from finder.categories.vinyl_target import parse_discogs_release_id, target_from_release
from finder.config import load_discogs_settings, load_settings
from finder.domain import Listing, Variant
from finder.errors import ConfigurationError, FinderError
from finder.logging import configure_logging
from finder.matching import decide_match, rank_variants
from finder.persistence import SqlAlchemyRepository


def _both_colors(values: list[str]) -> bool:
    words = set(re.findall(r"[a-z]+", " ".join(values).casefold()))
    return {"pink", "green"} <= words


def _queries(variant: Variant) -> list[str]:
    return target_from_release(variant).queries


def _claims(listing: Listing) -> dict[str, bool]:
    vinyl = from_listing(listing)
    return {
        "title_color_pair": _both_colors([listing.title]),
        "structured_color_pair": _both_colors(vinyl.colors),
        "seller_barcode": bool(vinyl.barcodes),
        "seller_catalog_number": bool(vinyl.catalog_numbers),
        "seller_runout": bool(vinyl.matrix_runouts),
    }


def summarize_discovery(listings: list[Listing]) -> dict:
    counts: Counter[str] = Counter()
    for listing in listings:
        counts.update(name for name, present in _claims(listing).items() if present)
    return {"stored_from_this_run": len(listings), **dict(sorted(counts.items()))}


def summarize_target_review(listings: list[Listing], variant: Variant) -> dict:
    """Only aggregate controlled status codes; never include listing evidence."""
    reviews = [review_target_listing(listing, variant) for listing in listings]
    counts = Counter(review.status for review in reviews)
    conflict_reasons = Counter(
        reason for review in reviews if review.status == "conflicting" for reason in review.verify
    )
    return {
        "statuses": dict(sorted(counts.items())),
        "conflicts": dict(sorted(conflict_reasons.items())),
    }


def choose_review_listing(listings: list[Listing]) -> tuple[Listing, str] | None:
    """Choose one claim-bearing listing for diagnosis, never as a verified match."""
    candidates = [listing for listing in listings if _claims(listing)["title_color_pair"]]
    candidates += [
        listing
        for listing in listings
        if _claims(listing)["structured_color_pair"] and listing not in candidates
    ]
    if not candidates:
        return None
    listing = max(
        candidates,
        key=lambda item: (
            _claims(item)["structured_color_pair"],
            _claims(item)["seller_barcode"],
            _claims(item)["seller_catalog_number"],
        ),
    )
    reason = (
        "structured_color_pair" if _claims(listing)["structured_color_pair"] else "title_color_pair"
    )
    return listing, reason


def summarize_review(listing: Listing, provider: DiscogsCatalogProvider, target_id: int) -> dict:
    retrieval = provider.search_for_listing(listing, target_release_id=target_id, limit=8)
    ranked = rank_variants(listing, retrieval.variants)
    decision = decide_match(listing, retrieval.variants, retrieval_incomplete=retrieval.incomplete)
    target = next((row for row in ranked if row.catalog_variant_id == str(target_id)), None)
    return {
        "selection": _claims(listing),
        "target_candidate_score": target.score if target else None,
        "target_candidate_status": target.status if target else None,
        "target_matched_fields": sorted(field.field for field in target.evidence if field.matched)
        if target
        else [],
        "target_conflicting_fields": sorted(
            field.field
            for field in target.evidence
            if not field.matched
            and field.field in ("artist", "barcode", "catalog_number", "color", "edition")
        )
        if target
        else [],
        "retrieval_incomplete": retrieval.incomplete,
        "target_not_in_seller_search": retrieval.target_not_in_search,
        "search_truncated": retrieval.search_truncated,
        "releases_evaluated": len(retrieval.variants),
        "decision": decision.outcome,
        "missing_evidence": decision.missing_evidence,
        "comparison": compare_pressings(listing, retrieval.variants, ranked, target_id),
    }


def main() -> int:
    report: dict = {"status": "failed", "attribution": "Data provided by Discogs"}
    try:
        release_id = parse_discogs_release_id(os.environ.get("FINDER_TARGET_RELEASE_ID", ""))
        discogs_settings = load_discogs_settings()
        settings = load_settings()
        if settings.ebay_environment != "production" or make_url(
            settings.database_url.get_secret_value()
        ).get_backend_name() not in ("postgres", "postgresql"):
            raise ConfigurationError(
                "Production target probe requires the shared PostgreSQL database."
            )
        configure_logging(settings.log_level)
        with DiscogsClient(discogs_settings) as client:
            provider = DiscogsCatalogProvider(client)
            variant = provider.get_release(release_id)
        vinyl = from_variant(variant)
        # Monitor IDs enter public scan logs, so the release identity must never be used there.
        target = target_from_release(variant, queries=_queries(variant)).model_copy(
            update={"id": "private-vinyl-probe"}
        )
        monitors = plan_target_search(target, mode="initial")
        report["catalog"] = {
            "vinyl": True,
            "pink_and_green": _both_colors(vinyl.colors),
            "barcode_present": bool(vinyl.barcodes),
            "catalog_number_present": bool(vinyl.catalog_numbers),
            "runout_present": bool(vinyl.matrix_runouts),
        }
        report["planned_searches"] = len(monitors)
        repository = SqlAlchemyRepository.from_url(settings.database_url.get_secret_value())
        try:
            with EbayClient(settings) as client:
                run = run_target_scan(target, EbayAdapter(client), repository, mode="initial")
            report["scan"] = {
                "searches_completed": sum(row.status == "completed" for row in run.summaries),
                "fetched": sum(row.fetched for row in run.summaries),
                "unique_observed": len(run.discovered_item_ids),
                "skipped": sum(row.skipped_invalid for row in run.summaries),
                "partial_details": sum(row.partial_details for row in run.summaries),
                "page_cap_reached": any(row.limit_reached for row in run.summaries),
                "complete": len(run.summaries) == len(monitors)
                and all(row.status == "completed" for row in run.summaries),
            }
            listings = [
                item
                for item_id in run.discovered_item_ids
                if (item := repository.get("ebay", item_id)) is not None
            ]
            report["seller_claims"] = summarize_discovery(listings)
            report["target_review_counts"] = summarize_target_review(listings, variant)
            selection = choose_review_listing(listings)
            if selection:
                listing, reason = selection
                with DiscogsClient(discogs_settings) as client:
                    report["review"] = summarize_review(
                        listing, DiscogsCatalogProvider(client), release_id
                    )
                report["review"]["selection_reason"] = reason
            else:
                report["review"] = {"claim_bearing_listing_in_sample": False}
            report["status"] = "passed" if report["scan"]["complete"] else "incomplete"
        finally:
            repository.close()
    except FinderError as exc:
        report["failure_type"] = type(exc).__name__
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
