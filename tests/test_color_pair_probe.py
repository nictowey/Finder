import json

from finder.adapters.discogs.adapter import CandidateRetrieval
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.normalize import normalize_listing
from scripts.probe_color_pair import (
    _queries,
    choose_review_listing,
    summarize_discovery,
    summarize_review,
    summarize_target_review,
)


def test_color_pair_probe_distinguishes_structured_and_title_claims(
    discogs_release, search_payload, observed_at
):
    raw = search_payload["itemSummaries"][0]
    raw = {
        **raw,
        "itemId": "v1|123456789012|0",
        "title": "Private Example Artist Example Album Pink Green vinyl",
        "localizedAspects": [
            {"name": "Artist", "value": "Example Artist"},
            {"name": "Color", "value": "Pink & Green"},
        ],
    }
    listing = normalize_listing(raw, observed_at)
    title_only = listing.model_copy(update={"item_specifics": {"Artist": ["Example Artist"]}})
    variant = normalize_release(
        {**discogs_release, "formats": [{"name": "Vinyl", "text": "Pink / Green"}]},
        observed_at,
    )
    assert _queries(variant) == [
        "Example Artist Example Album",
        "Example Artist Example Album pink green",
    ]
    assert summarize_discovery([listing, title_only]) == {
        "stored_from_this_run": 2,
        "structured_color_pair": 1,
        "title_color_pair": 2,
    }
    assert summarize_target_review([listing, title_only], variant) == {"possible_pressing": 2}
    chosen = choose_review_listing([title_only, listing])
    assert chosen is not None and chosen[1] == "structured_color_pair"

    class Provider:
        def search_for_listing(self, listing, *, target_release_id, limit):
            assert target_release_id == 111 and limit == 8
            return CandidateRetrieval(
                variants=[variant],
                query_kinds=["q"],
                search_truncated=True,
                candidate_limit_reached=False,
                identifiers_omitted=False,
                target_release_id=111,
                target_not_in_search=True,
            )

    result = summarize_review(listing, Provider(), 111)
    public = json.dumps(result)
    assert result["decision"] != "exact_variant"
    assert result["retrieval_incomplete"]
    for private in (listing.marketplace_item_id, listing.title, "Example Artist", '"111"'):
        assert private not in public
