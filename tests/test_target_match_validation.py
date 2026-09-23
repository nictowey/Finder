import json

import pytest

from finder.adapters.discogs.adapter import CandidateRetrieval
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.errors import ConfigurationError
from scripts.validate_target_match import (
    render_comparison_markdown,
    select_probe_listing,
    summarize_match,
)


def test_select_probe_listing_uses_private_legacy_id(repository, search_payload, observed_at):
    raw = search_payload["itemSummaries"][0]
    raw["itemId"] = "v1|123456789012|0"
    listing = normalize_listing(raw, observed_at)
    repository.upsert(listing)
    assert select_probe_listing(repository, "123456789012") == listing
    with pytest.raises(ConfigurationError, match="exactly one"):
        select_probe_listing(repository, "123456789013")


def test_target_match_summary_never_emits_provider_values(
    search_payload, discogs_release, observed_at
):
    raw = search_payload["itemSummaries"][0]
    raw["itemId"] = "v1|123456789012|0"
    raw["title"] = "Example Artist Example Album vinyl private listing marker"
    listing = normalize_listing(raw, observed_at)
    listing = listing.model_copy(
        update={"item_specifics": {**listing.item_specifics, "Format": ["Long-playing record"]}}
    )
    variant = normalize_release(discogs_release, observed_at)
    retrieval = CandidateRetrieval(
        variants=[variant, variant.model_copy(update={"catalog_variant_id": "222"})],
        query_kinds=["q"],
        search_truncated=True,
        candidate_limit_reached=False,
        identifiers_omitted=False,
        target_release_id=int(variant.catalog_variant_id),
        target_not_in_search=True,
    )
    summary = summarize_match(listing, retrieval, int(variant.catalog_variant_id))
    public_log = json.dumps(summary)
    markdown = render_comparison_markdown(summary)
    assert summary["target_retrieved"]
    assert summary["retrieval"]["incomplete"]
    assert summary["retrieval"]["same_family_competitors"] == 1
    assert summary["decision"]["outcome"] != "exact_variant"
    assert "format" in summary["target_unmatched_soft_fields"]
    assert "format" not in summary["target_conflicting_fields"]
    assert [candidate["role"] for candidate in summary["comparison"]["candidates"]] == [
        "target",
        "same_family_1",
    ]
    assert "Data provided by Discogs" in markdown
    for sensitive in (
        "123456789012",
        "private listing marker",
        listing.title,
        "Example Artist",
        '"222"',
    ):
        assert sensitive not in public_log
        assert sensitive not in markdown


def test_numbered_target_report_distinguishes_missing_claim_from_mismatched_variant(
    search_payload, discogs_release, observed_at
):
    raw = search_payload["itemSummaries"][0]
    raw["title"] = "Example Artist Example Album hand-numbered blue vinyl private marker"
    raw["localizedAspects"] = [
        {"name": "Artist", "value": "Example Artist"},
        {"name": "Color", "value": "Blue"},
    ]
    listing = normalize_listing(raw, observed_at)
    numbered = normalize_release(
        {
            **discogs_release,
            "formats": [{"name": "Vinyl", "descriptions": ["LP", "Numbered"], "text": "Blue"}],
        },
        observed_at,
    )
    ordinary = numbered.model_copy(
        update={
            "catalog_variant_id": "222",
            "formats": [{"name": "Vinyl", "descriptions": ["LP"], "text": "Red"}],
        }
    )
    retrieval = CandidateRetrieval(
        variants=[numbered, ordinary],
        query_kinds=["q", "target_family"],
        search_truncated=True,
        candidate_limit_reached=False,
        identifiers_omitted=False,
        target_release_id=111,
        target_not_in_search=True,
    )
    report = summarize_match(listing, retrieval, 111)["comparison"]
    target, competitor = report["candidates"]
    assert report["seller_title_numbered_claim"]
    assert not report["seller_structured_numbered_claim"]
    assert target["catalog_numbered"]
    assert {"barcode", "catalog_number", "matrix_runout", "structured_numbered_claim"} <= set(
        target["missing_seller_evidence"]
    )
    assert "color" in target["matched_fields"]
    assert "color" in competitor["seller_catalog_disagreements"]
    assert "structured_numbered_claim" not in competitor["missing_seller_evidence"]

    with_runout = listing.model_copy(
        update={
            "item_specifics": {**listing.item_specifics, "Matrix / Runout": ["private etching"]}
        }
    )
    with_runout_report = summarize_match(with_runout, retrieval, 111)
    assert with_runout_report["comparison"]["candidates"][0]["unscored_present_fields"] == [
        "matrix_runout"
    ]
    assert "private etching" not in json.dumps(with_runout_report)

    negated = listing.model_copy(
        update={
            "title": "Example Artist Example Album not numbered vinyl",
            "item_specifics": {**listing.item_specifics, "Features": ["Not Numbered"]},
        }
    )
    negated_report = summarize_match(negated, retrieval, 111)["comparison"]
    assert not negated_report["seller_title_numbered_claim"]
    assert not negated_report["seller_structured_numbered_claim"]
