import json
from pathlib import Path

import httpx
import pytest

from finder import cli
from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.target_search import EbaySearchTarget, plan_target_search, run_target_scan
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
        results = run_target_scan(
            target, EbayAdapter(client, now=lambda: observed_at), repository, mode="initial"
        )
    assert searches == target.queries
    assert detail_calls == [item_a["itemId"], item_b["itemId"]]
    assert [(result.new, result.skipped_invalid) for result in results] == [(1, 0), (1, 1)]
    assert results[1].skip_reasons == {"duplicate_in_scan": 1}
    assert repository.count() == 2


def test_target_scan_stops_after_failed_query(settings, repository):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        return httpx.Response(200, json={"total": 1, "itemSummaries": []})

    target = EbaySearchTarget(id="ds2", catalog_variant_id=7609839, queries=["one", "two"])
    with EbayClient(settings, transport=httpx.MockTransport(handler)) as client:
        results = run_target_scan(target, EbayAdapter(client), repository, mode="refresh")
    assert len(results) == 1
    assert results[0].status == "failed"


def test_target_plan_cli_needs_no_credentials(monkeypatch, capsys):
    monkeypatch.delenv("EBAY_PRODUCTION_CLIENT_ID", raising=False)
    assert cli.main(["target-plan", "--target", "future-ds2-7609839", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["maximum_search_requests"] == 2
    assert plan["maximum_detail_requests"] == 20
    assert plan["searches"][0]["query"] == "Future DS2"


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
    assert cli.main(["scan-target", "--target", "future-ds2-7609839", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["complete"] is True
    assert result["coverage_truncated"] is True
    assert result["results"][0]["new"] == 1
    assert result["results"][1]["skip_reasons"] == {"duplicate_in_scan": 1}
    assert result["results"][1]["total_stored"] == 1


def test_target_scan_production_requires_shared_db(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EBAY_ENVIRONMENT", "production")
    monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_ID", "fake-client")
    monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("FINDER_DATABASE_URL", f"sqlite:///{tmp_path / 'production.db'}")
    assert cli.main(["scan-target", "--target", "future-ds2-7609839"]) == 2
    assert "shared PostgreSQL database" in capsys.readouterr().err
