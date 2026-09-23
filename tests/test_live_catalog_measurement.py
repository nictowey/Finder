import json
from datetime import UTC, datetime

from finder.adapters.discogs.adapter import CandidateRetrieval
from finder.adapters.discogs.normalize import normalize_release
from scripts import measure_live_catalog


def test_catalog_measurement_reports_only_aggregate_and_keeps_misses(monkeypatch, discogs_release):
    class Client:
        def __init__(self, settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Provider:
        def __init__(self, client):
            pass

        def search_releases(self, query, *, limit):
            genre = next(g for g, q, _ in measure_live_catalog.QUERIES if q == query)
            if genre == "folk":
                return []
            return [
                normalize_release(
                    {
                        **discogs_release,
                        "id": 9999,
                        "title": "Synthetic Private Album",
                        "artists": [{"name": "Private Example Artist"}],
                        "genres": [
                            next(
                                expected
                                for g, _, expected in measure_live_catalog.QUERIES
                                if g == genre
                            )
                        ],
                    },
                    datetime(2026, 9, 23, tzinfo=UTC),
                )
            ]

        def search_for_listing(self, listing, *, limit):
            assert "Private Example Artist" in listing.title
            return CandidateRetrieval(
                variants=[],
                query_kinds=["q"],
                search_truncated=False,
                candidate_limit_reached=False,
                identifiers_omitted=False,
                target_release_id=None,
                target_not_in_search=False,
            )

    monkeypatch.setattr(measure_live_catalog, "load_discogs_settings", lambda: object())
    monkeypatch.setattr(measure_live_catalog, "DiscogsClient", Client)
    monkeypatch.setattr(measure_live_catalog, "DiscogsCatalogProvider", Provider)
    result = measure_live_catalog.measure()
    assert len(result["panel"]) == 5
    assert sum(item["eligible_in_first_three"] for item in result["panel"]) == 4
    assert all(
        item["selected_release_in_title_results"] is False
        for item in result["panel"]
        if item["eligible_in_first_three"]
    )
    output = json.dumps(result)
    assert "Private Example Artist" not in output
    assert "Synthetic Private Album" not in output
    assert "9999" not in output
