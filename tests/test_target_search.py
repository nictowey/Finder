import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from finder import cli
from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.discogs.normalize import normalize_release
from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.target_search import EbaySearchTarget, plan_target_search, run_target_scan
from finder.categories.vinyl_target import parse_discogs_release_id, target_from_release
from finder.config import load_search_target
from finder.errors import ConfigurationError


def test_ds2_target_search_is_broad_and_bounded():
    target = load_search_target(Path("config/watch_targets.toml"), "future-ds2-7609839")
    initial = plan_target_search(target, mode="initial")
    refresh = plan_target_search(target, mode="refresh")
    assert [monitor.query for monitor in initial] == ["Future DS2", "Future Dirty Sprite 2"]
    assert target.catalog_variant_id == 7609839
    assert [monitor.source_options["sort"] for monitor in initial] == ["bestMatch"] * 2
    assert [monitor.source_options["sort"] for monitor in refresh] == ["newlyListed"] * 2
    assert all(
        monitor.source_options["max_pages"] == 1
        and monitor.source_options["page_size"] == 10
        and monitor.source_options["category_ids"] == ["176985"]
        for monitor in refresh
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1234", 1234),
        ("https://www.discogs.com/release/1234-Synthetic-Record", 1234),
        ("https://discogs.com/release/1234/", 1234),
    ],
)
def test_parse_discogs_release(value, expected):
    assert parse_discogs_release_id(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "1" * 21,
        "-1",
        "https://www.discogs.com/master/1234",
        "http://discogs.com/release/1234",
        "https://discogs.com.evil.test/release/1234",
        "https://discogs.com/release/1234?x=1",
        "https://discogs.com:bad/release/1234",
    ],
)
def test_rejects_other_discogs_identifiers(value):
    with pytest.raises(ConfigurationError):
        parse_discogs_release_id(value)


def test_vinyl_target_accepts_any_genre_and_rejects_cd(discogs_release):
    release = {
        **discogs_release,
        "id": 1234,
        "title": "Invented Orchestral Album",
        "artists": [{"name": "Sample Quartet (2)"}],
        "genres": ["Classical"],
    }
    variant = normalize_release(release, datetime(2026, 9, 23, tzinfo=UTC))
    target = target_from_release(variant)
    assert target.queries == ["Sample Quartet Invented Orchestral Album"]
    assert target.catalog_variant_id == 1234
    assert len(plan_target_search(target, mode="refresh")) == 1
    override = target_from_release(variant, queries=["Sample Quartet Invented Album", "Album LP"])
    assert len(plan_target_search(override, mode="initial")) == 2
    with pytest.raises(ConfigurationError, match="not cataloged as vinyl"):
        target_from_release(
            normalize_release(
                {**release, "formats": [{"name": "CD"}]}, datetime(2026, 9, 23, tzinfo=UTC)
            )
        )
    with pytest.raises(ConfigurationError, match="distinct"):
        target_from_release(variant, queries=["album", "ALBUM"])
    compilation = normalize_release(
        {**release, "artists": [{"name": "Various"}]}, datetime(2026, 9, 23, tzinfo=UTC)
    )
    assert target_from_release(compilation).queries == ["Invented Orchestral Album"]


def test_default_search_includes_seller_spelling_alias(discogs_release, observed_at):
    variant = normalize_release(
        {
            **discogs_release,
            "title": "Don't Be Dumb",
            "artists": [{"name": "A$AP Rocky"}],
        },
        observed_at,
    )
    target = target_from_release(variant)
    assert target.queries == ["A$AP Rocky Don't Be Dumb", "ASAP Rocky Dont Be Dumb"]


@pytest.mark.parametrize(
    "queries",
    [[], ["Future DS2"] * 2, ["Future DS2", " ", "Future Dirty Sprite 2"], ["x"] * 4],
)
def test_invalid_query_plan_rejected(queries):
    with pytest.raises(ValueError):
        EbaySearchTarget(id="ds2", catalog_variant_id=7609839, queries=queries)


def test_duplicate_target_ids_fail_closed(tmp_path):
    config = tmp_path / "targets.toml"
    config.write_text(
        '[[targets]]\nid="ds2"\ncatalog_variant_id=1\nqueries=["Future DS2"]\n'
        '[[targets]]\nid="ds2"\ncatalog_variant_id=2\nqueries=["Future"]\n'
    )
    with pytest.raises(ConfigurationError, match="Cannot load target search configuration"):
        load_search_target(config, "ds2")


def test_two_queries_dedupe_detail_and_persist_distinct_items(
    settings, repository, search_payload, detail_payload, observed_at
):
    item_a = search_payload["itemSummaries"][0]
    item_b = search_payload["itemSummaries"][1]
    details = {item_a["itemId"]: detail_payload, item_b["itemId"]: item_b}
    details[item_a["itemId"]] = {**detail_payload, "itemId": item_a["itemId"]}
    searches = []
    detail_calls = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if request.url.path.endswith("/search"):
            searches.append(request.url.params["q"])
            assert request.url.params["category_ids"] == "176985"
            assert request.url.params["limit"] == "10"
            assert "sort" not in request.url.params
            items = [item_a] if len(searches) == 1 else [item_a, item_b]
            return httpx.Response(200, json={"total": len(items), "itemSummaries": items})
        item_id = request.url.path.rsplit("/", 1)[-1]
        detail_calls.append(item_id)
        return httpx.Response(200, json=details[item_id])

    target = EbaySearchTarget(
        id="ds2", catalog_variant_id=7609839, queries=["Future DS2", "Future Dirty Sprite 2"]
    )
    with EbayClient(settings, transport=httpx.MockTransport(handler)) as client:
        run = run_target_scan(
            target, EbayAdapter(client, now=lambda: observed_at), repository, mode="initial"
        )
    assert searches == target.queries
    assert detail_calls == [item_a["itemId"], item_b["itemId"]]
    assert [(result.new, result.skipped_invalid) for result in run.summaries] == [(1, 0), (1, 1)]
    assert run.summaries[1].skip_reasons == {"duplicate_in_scan": 1}
    assert run.found_legacy_item(item_a["itemId"].split("|")[1])
    assert not run.found_legacy_item("406542550752")
    assert repository.count() == 2


def test_target_scan_stops_after_failed_query(settings, repository):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        return httpx.Response(200, json={"total": 1, "itemSummaries": []})

    target = EbaySearchTarget(id="ds2", catalog_variant_id=7609839, queries=["one", "two"])
    with EbayClient(settings, transport=httpx.MockTransport(handler)) as client:
        run = run_target_scan(target, EbayAdapter(client), repository, mode="refresh")
    assert len(run.summaries) == 1
    assert run.summaries[0].status == "failed"
    assert not run.found_legacy_item("406542550752")


def test_target_plan_cli_needs_no_credentials(monkeypatch, capsys):
    monkeypatch.delenv("EBAY_PRODUCTION_CLIENT_ID", raising=False)
    assert cli.main(["target-plan", "--target", "future-ds2-7609839", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["maximum_search_requests"] == 2
    assert plan["maximum_detail_requests"] == 20
    assert plan["searches"][0]["query"] == "Future DS2"


def test_arbitrary_vinyl_release_plan_scan_and_private_listing_review(
    tmp_path, monkeypatch, capsys, discogs_release, search_payload, detail_payload
):
    release = {
        **discogs_release,
        "id": 1234,
        "title": "Invented String Record",
        "artists": [{"name": "Sample Quartet"}],
        "genres": ["Classical"],
        "uri": "/release/1234-Sample-Quartet-Invented-String-Record",
    }
    monkeypatch.setenv("DISCOGS_TOKEN", "fake-token")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_ID", "fake-client")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("FINDER_DATABASE_URL", f"sqlite:///{tmp_path / 'any-vinyl.db'}")
    discogs_paths = []

    def discogs_handler(request):
        discogs_paths.append(request.url.path)
        if request.url.path == "/database/search":
            assert request.url.params["format"] == "Vinyl"
            assert "genre" not in request.url.params
            return httpx.Response(200, json={"results": [{"id": 1234}]})
        return httpx.Response(200, json=release)

    monkeypatch.setattr(
        cli,
        "DiscogsClient",
        lambda settings: DiscogsClient(settings, transport=httpx.MockTransport(discogs_handler)),
    )
    release_url = "https://www.discogs.com/release/1234-Sample-Quartet-Invented-String-Record"
    assert cli.main(["target-plan", "--release", release_url, "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["searches"][0]["query"] == "Sample Quartet Invented String Record"
    assert plan["maximum_detail_requests"] == 10
    assert plan["attribution"] == "Data provided by Discogs"

    item = search_payload["itemSummaries"][0]
    ebay_searches = []

    def ebay_handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if request.url.path.endswith("/search"):
            ebay_searches.append(request.url.params["q"])
            return httpx.Response(200, json={"total": 1, "itemSummaries": [item]})
        return httpx.Response(
            200,
            json={
                **detail_payload,
                "title": "Sample Quartet Invented String Record vinyl LP",
                "localizedAspects": [
                    {"name": "Artist", "value": "Sample Quartet"},
                    {"name": "UPC", "value": "0123456789012"},
                ],
            },
        )

    monkeypatch.setattr(
        cli,
        "EbayClient",
        lambda settings: EbayClient(settings, transport=httpx.MockTransport(ebay_handler)),
    )
    assert (
        cli.main(
            [
                "scan-target",
                "--release",
                "1234",
                "--mode",
                "initial",
                "--show-listings",
                "--review",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["complete"] is True
    assert len(result["discovered_listings"]) == 1
    assert result["discovered_listings"][0]["item_id"] == item["itemId"]
    assert result["review"]["counts"]["family_review"] == 1
    assert result["review"]["listings"][0]["status"] == "family_review"
    assert result["review"]["listings"][0]["url"]
    assert result["review"]["not_verified_pressings"] is True
    assert ebay_searches == ["Sample Quartet Invented String Record"]
    assert (
        cli.main(["match", "--item-id", item["itemId"], "--target-release-id", "1234", "--json"])
        == 0
    )
    match_result = json.loads(capsys.readouterr().out)
    assert match_result["retrieval"]["target_release_id"] == 1234
    assert match_result["candidates"][0]["catalog_variant_id"] == "1234"
    assert match_result["decision"]["outcome"] in ("probable_variant", "family_only")
    assert discogs_paths == [
        "/releases/1234",
        "/releases/1234",
        "/releases/1234",
        "/database/search",
        "/database/search",
        "/database/search",
    ]


def test_release_target_rejects_non_vinyl_and_saved_query_override(
    monkeypatch, capsys, discogs_release
):
    monkeypatch.setenv("DISCOGS_TOKEN", "fake-token")
    release = {**discogs_release, "formats": [{"name": "CD"}]}
    monkeypatch.setattr(
        cli,
        "DiscogsClient",
        lambda settings: DiscogsClient(
            settings,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=release)),
        ),
    )
    assert cli.main(["target-plan", "--release", "111"]) == 2
    assert "not cataloged as vinyl" in capsys.readouterr().err
    assert cli.main(["target-plan", "--target", "future-ds2-7609839", "--query", "Override"]) == 2
    assert "requires --release" in capsys.readouterr().err


def test_scan_target_cli_reports_bounded_coverage(tmp_path, monkeypatch, capsys, search_payload):
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_ID", "fake-client")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("FINDER_DATABASE_URL", f"sqlite:///{tmp_path / 'target.db'}")
    item = search_payload["itemSummaries"][0]

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"total": 11, "itemSummaries": [item]})
        return httpx.Response(200, json={"itemId": item["itemId"]})

    monkeypatch.setattr(
        cli,
        "EbayClient",
        lambda settings: EbayClient(settings, transport=httpx.MockTransport(handler)),
    )
    legacy_id = item["itemId"].split("|")[1]
    assert (
        cli.main(
            [
                "scan-target",
                "--target",
                "future-ds2-7609839",
                "--probe-legacy-id",
                legacy_id,
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["complete"] is True
    assert result["coverage_truncated"] is True
    assert result["results"][0]["new"] == 1
    assert result["results"][1]["skip_reasons"] == {"duplicate_in_scan": 1}
    assert result["results"][1]["total_stored"] == 1
    assert result["probe_found_in_this_run"] is True
    assert legacy_id not in json.dumps(result)


def test_probe_reports_current_run_not_an_earlier_stored_listing(
    tmp_path, monkeypatch, capsys, search_payload
):
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_ID", "fake-client")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("FINDER_DATABASE_URL", f"sqlite:///{tmp_path / 'probe.db'}")
    item = search_payload["itemSummaries"][0]
    legacy_id = item["itemId"].split("|")[1]
    search_calls = 0

    def handler(request):
        nonlocal search_calls
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        if request.url.path.endswith("/search"):
            search_calls += 1
            items = [item] if search_calls <= 2 else []
            return httpx.Response(200, json={"total": len(items), "itemSummaries": items})
        return httpx.Response(200, json={"itemId": item["itemId"]})

    monkeypatch.setattr(
        cli,
        "EbayClient",
        lambda settings: EbayClient(settings, transport=httpx.MockTransport(handler)),
    )
    command = [
        "scan-target",
        "--target",
        "future-ds2-7609839",
        "--probe-legacy-id",
        legacy_id,
        "--json",
    ]
    assert cli.main(command) == 0
    assert json.loads(capsys.readouterr().out)["probe_found_in_this_run"] is True
    assert cli.main(command) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["probe_found_in_this_run"] is False
    assert second["results"][1]["total_stored"] == 1


def test_target_scan_production_requires_shared_db(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EBAY_ENVIRONMENT", "production")
    monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_ID", "fake-client")
    monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("FINDER_DATABASE_URL", f"sqlite:///{tmp_path / 'production.db'}")
    assert cli.main(["scan-target", "--target", "future-ds2-7609839"]) == 2
    assert "shared PostgreSQL database" in capsys.readouterr().err
