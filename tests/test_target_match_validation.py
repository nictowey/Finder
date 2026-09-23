import json

import pytest

from finder.adapters.discogs.adapter import CandidateRetrieval
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from finder.errors import ConfigurationError
from scripts.validate_target_match import select_probe_listing, summarize_match


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
    variant = normalize_release(discogs_release, observed_at)
    retrieval = CandidateRetrieval(
        variants=[variant],
        query_kinds=["q"],
        search_truncated=True,
        candidate_limit_reached=False,
        identifiers_omitted=False,
        target_release_id=int(variant.catalog_variant_id),
        target_not_in_search=True,
    )
    summary = summarize_match(listing, retrieval, int(variant.catalog_variant_id))
    public_log = json.dumps(summary)
    assert summary["target_retrieved"]
    assert summary["retrieval"]["incomplete"]
    assert summary["decision"]["outcome"] != "exact_variant"
    for sensitive in ("123456789012", "private listing marker", listing.title, "Example Artist"):
        assert sensitive not in public_log
