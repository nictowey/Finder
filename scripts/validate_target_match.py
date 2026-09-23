"""Privately evaluate one stored target listing against bounded Discogs catalog candidates.

Only controlled decision codes and evidence field names may reach public Actions logs.
The listing and catalog response are kept in memory; this check writes no records.
"""

import json
import os
import re
import sys
from pathlib import Path

from finder.adapters.discogs.adapter import CandidateRetrieval, DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.categories.vinyl_review import compare_pressings
from finder.config import load_discogs_settings, load_search_target
from finder.domain import Listing
from finder.errors import ConfigurationError, FinderError
from finder.matching import decide_match, rank_variants
from finder.persistence import SqlAlchemyListingRepository


def select_probe_listing(repository: SqlAlchemyListingRepository, legacy_id: str) -> Listing:
    """Find exactly one of the newest 100 stored items without exposing its identity."""
    matches = []
    for listing in repository.list_recent("ebay", 100):
        parts = listing.marketplace_item_id.split("|")
        if listing.marketplace_item_id == legacy_id or (len(parts) == 3 and parts[1] == legacy_id):
            matches.append(listing)
    if len(matches) != 1:
        raise ConfigurationError("Expected exactly one recent stored target listing.")
    return matches[0]


def summarize_match(listing: Listing, retrieval: CandidateRetrieval, target_id: int) -> dict:
    """Project the decision onto an allowlist free of seller and catalog values."""
    ranked = rank_variants(listing, retrieval.variants)
    target = next((item for item in ranked if item.catalog_variant_id == str(target_id)), None)
    target_variant = next(
        (item for item in retrieval.variants if item.catalog_variant_id == str(target_id)), None
    )
    decision = decide_match(listing, retrieval.variants, retrieval_incomplete=retrieval.incomplete)
    comparison = compare_pressings(listing, retrieval.variants, ranked, target_id)
    return {
        "status": "completed",
        "attribution": "Data provided by Discogs",
        "target_retrieved": target is not None,
        "target_status": target.status if target else None,
        "target_score": target.score if target else None,
        "target_matched_fields": sorted(
            {item.field for item in target.evidence if item.matched} if target else set()
        ),
        "target_conflicting_fields": sorted(
            {
                item.field
                for item in target.evidence
                if not item.matched
                and item.field
                in (
                    "artist",
                    "barcode",
                    "catalog_number",
                    "color",
                    "edition",
                    "country",
                )
            }
            if target
            else set()
        ),
        "target_unmatched_soft_fields": sorted(
            {
                item.field
                for item in target.evidence
                if not item.matched and item.field in ("title", "format")
            }
            if target
            else set()
        ),
        "retrieval": {
            "query_kinds": retrieval.query_kinds,
            "releases_evaluated": len(retrieval.variants),
            "same_family_competitors": sum(
                item.catalog_variant_id != str(target_id)
                and item.catalog_product_id == target_variant.catalog_product_id
                for item in retrieval.variants
            )
            if target_variant
            else 0,
            "target_not_in_search": retrieval.target_not_in_search,
            "search_truncated": retrieval.search_truncated,
            "candidate_limit_reached": retrieval.candidate_limit_reached,
            "identifiers_omitted": retrieval.identifiers_omitted,
            "incomplete": retrieval.incomplete,
        },
        "decision": {
            "outcome": decision.outcome,
            "policy_version": decision.policy_version,
            "target_in_decision_candidates": str(target_id) in decision.candidate_ids,
            "conflicts": decision.conflicts,
            "missing_evidence": decision.missing_evidence,
        },
        "comparison": comparison,
    }


def render_comparison_markdown(summary: dict) -> str:
    """Render only the sanitized projection into a GitHub Actions job summary."""
    comparison = summary["comparison"]

    def fields(values: list[str]) -> str:
        return ", ".join(values) if values else "—"

    structured_claim = str(comparison.get("seller_structured_numbered_claim", False)).lower()
    title_claim = str(comparison.get("seller_title_numbered_claim", False)).lower()
    headers = (
        "Candidate",
        "Score",
        "Catalog numbered",
        "Matched",
        "Seller/catalog disagreements",
        "Missing seller evidence",
        "Soft text misses",
        "Unscored present",
    )
    lines = [
        "## Internal pressing comparison",
        "",
        f"Decision: **{summary['decision']['outcome']}**. "
        "Review required for exact pressing identity.",
        "The target was selected by the collector; its direct retrieval is not "
        "evidence from the seller.",
        "",
        f"Evaluated {summary['retrieval']['releases_evaluated']} releases: "
        f"{comparison['same_family_competitors']} same-family alternatives, "
        f"{comparison['other_family_releases']} other-family releases. "
        f"Search incomplete: {str(summary['retrieval']['incomplete']).lower()}.",
        f"Seller structured numbered claim: {structured_claim}; "
        f"seller title numbered claim: {title_claim}.",
        "",
        "| " + " | ".join(headers) + " |",
        "| --- | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for candidate in comparison["candidates"]:
        lines.append(
            "| "
            + " | ".join(
                (
                    candidate["role"],
                    str(candidate["heuristic_score"]),
                    "yes" if candidate["catalog_numbered"] else "no",
                    fields(candidate["matched_fields"]),
                    fields(candidate["seller_catalog_disagreements"]),
                    fields(candidate["missing_seller_evidence"]),
                    fields(candidate["unmatched_soft_text"]),
                    fields(candidate["unscored_present_fields"]),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Missing seller evidence is a review gap, not a required listing field. "
            "A score or seller claim does not verify an individual numbered copy. "
            "Runout text is displayed as unscored when present on both sides.",
            "",
            "Data provided by Discogs",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    try:
        legacy_id = os.environ.get("TARGET_PROBE_LEGACY_ID", "")
        if not re.fullmatch(r"[0-9]{8,20}", legacy_id):
            raise ConfigurationError("Set a numeric TARGET_PROBE_LEGACY_ID secret.")
        target = load_search_target(Path("config/watch_targets.toml"), "future-ds2-7609839")
        settings = load_discogs_settings()
        repository = SqlAlchemyListingRepository.from_url(settings.database_url.get_secret_value())
        try:
            listing = select_probe_listing(repository, legacy_id)
            with DiscogsClient(settings) as client:
                retrieval = DiscogsCatalogProvider(client).search_for_listing(
                    listing, limit=10, target_release_id=target.catalog_variant_id
                )
            summary = summarize_match(listing, retrieval, target.catalog_variant_id)
            if report_path := os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(report_path, "a", encoding="utf-8") as report:
                    report.write(render_comparison_markdown(summary))
            print(json.dumps(summary))
            return 0
        finally:
            repository.close()
    except FinderError as exc:
        print(json.dumps({"status": "failed", "reason": type(exc).__name__}), file=sys.stderr)
        return 1
    except Exception:
        # Unexpected provider or decoding errors may embed seller text in a traceback.
        print(json.dumps({"status": "failed", "reason": "unexpected_error"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
