import importlib.util
import json
import os
from pathlib import Path

import httpx
import pytest

from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.normalize import normalize_listing
from finder.config import load_monitor
from finder.diagnostics import changed_fields, summarize_listings
from finder.errors import PersistenceError
from finder.persistence import SqlAlchemyRepository

ROOT = Path(__file__).resolve().parents[1]
SECRET_VALUES = ("sandbox-id-value", "sandbox-secret-value", "token-value")


@pytest.fixture
def script(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith(("EBAY_", "FINDER_")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_ID", SECRET_VALUES[0])
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", SECRET_VALUES[1])
    monkeypatch.setenv("FINDER_DATABASE_URL", f"sqlite:///{tmp_path / 'smoke.db'}")
    spec = importlib.util.spec_from_file_location(
        "validate_live_ebay", ROOT / "scripts/validate_live_ebay.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Never read a developer's real .env during tests.
    original = module.main
    monkeypatch.setattr(
        module,
        "main",
        lambda monitor_id="ebay-api-smoke": original(tmp_path / "absent.env", monitor_id),
    )
    return module


def _use_transport(monkeypatch, script, handler):
    def factory(settings):
        return EbayClient(settings, transport=httpx.MockTransport(handler), sleep=lambda _: None)

    monkeypatch.setattr(script, "EbayClient", factory)


def _future(payload):
    for item in payload.get("itemSummaries", []):
        item.pop("itemEndDate", None)
    payload.pop("itemEndDate", None)
    return payload


def test_smoke_monitor_is_bounded():
    monitor = load_monitor(ROOT / "config/monitors.toml", "ebay-api-smoke")
    assert monitor.query == "vinyl"
    assert monitor.source_options["max_pages"] == 1
    assert monitor.source_options["page_size"] == 10


def test_production_validation_monitor_matches_main_scope_with_ten_item_cap():
    main = load_monitor(ROOT / "config/monitors.toml", "rap-vinyl")
    sample = load_monitor(ROOT / "config/monitors.toml", "rap-vinyl-validation")
    assert sample.query == main.query
    assert sample.source_options["category_ids"] == main.source_options["category_ids"]
    assert sample.source_options["buying_options"] == main.source_options["buying_options"]
    assert sample.source_options["page_size"] == 10
    assert sample.source_options["max_pages"] == 1
    assert sample.source_options["fetch_details"]


def test_live_validation_selects_bounded_vinyl_monitor(
    monkeypatch, capsys, script, search_payload, detail_payload
):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token-value", "expires_in": 7200})
        if request.url.path.endswith("/item_summary/search"):
            assert request.url.params["q"] == "vinyl (rap,hip-hop,hip hop)"
            assert request.url.params["limit"] == "10"
            assert request.url.params["category_ids"] == "176985"
            return httpx.Response(200, json=_future(search_payload))
        if detail_payload["itemId"] in str(request.url.raw_path, "ascii").replace("%7C", "|"):
            return httpx.Response(200, json=_future(detail_payload))
        return httpx.Response(404)

    _use_transport(monkeypatch, script, handler)
    assert script.main("rap-vinyl-validation") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["monitor"] == "rap-vinyl-validation"
    assert report["status"] == "passed"
    assert script.main("rap-vinyl-validation") == 0
    repeat = json.loads(capsys.readouterr().out)
    assert repeat["status"] == "passed"
    assert repeat["persistence"]["missing_observations"] == 0
    repository = SqlAlchemyRepository.from_url(os.environ["FINDER_DATABASE_URL"])
    try:
        assert repository.count() == 1
        observations = repository.get_observations("ebay", detail_payload["itemId"])
        assert len(observations) == 2
        stored = repository.get("ebay", detail_payload["itemId"])
        assert stored.first_observed_at == observations[0].first_observed_at
    finally:
        repository.close()


def test_live_validation_passes_and_prints_no_identities(
    monkeypatch, capsys, script, search_payload, detail_payload
):
    seen_hosts = set()

    def handler(request):
        seen_hosts.add(request.url.host)
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token-value", "expires_in": 7200})
        if request.url.path.endswith("/item_summary/search"):
            assert request.url.params["q"] == "vinyl"
            return httpx.Response(200, json=_future(search_payload))
        if detail_payload["itemId"] in str(request.url.raw_path, "ascii").replace("%7C", "|"):
            return httpx.Response(200, json=_future(detail_payload))
        return httpx.Response(404)

    _use_transport(monkeypatch, script, handler)
    assert script.main() == 0
    captured = capsys.readouterr()
    output = captured.out
    report = json.loads(output)
    assert seen_hosts == {"api.sandbox.ebay.com"}
    assert report["status"] == "passed"
    assert report["environment"] == "sandbox"
    assert report["oauth"]["status"] == "ok"
    assert report["normalization"]["listings"] == 1
    assert report["browse"]["skip_reasons"] == {"item_unavailable": 1}
    assert report["persistence"] == {
        "missing": 0,
        "fields_changed_on_reload": [],
        "missing_observations": 0,
        "observation_fields_changed_on_reload": [],
    }
    for forbidden in (*SECRET_VALUES, detail_payload["itemId"], detail_payload["title"]):
        assert forbidden not in output + captured.err


def test_live_validation_reports_oauth_failure(monkeypatch, capsys, script):
    _use_transport(monkeypatch, script, lambda request: httpx.Response(401, text="secret body"))
    assert script.main() == 1
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["failed_stage"] == "oauth"
    assert "secret body" not in output


def test_live_validation_fails_on_empty_search(monkeypatch, capsys, script):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token-value", "expires_in": 7200})
        return httpx.Response(200, json={"total": 0})

    _use_transport(monkeypatch, script, handler)
    assert script.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["failed_stage"] == "browse"
    assert "no listings" in report["error"]


def test_live_validation_reports_missing_credentials(monkeypatch, capsys, script):
    monkeypatch.delenv("EBAY_SANDBOX_CLIENT_SECRET")
    assert script.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["failed_stage"] == "configuration"
    assert "EBAY_SANDBOX_CLIENT_SECRET" in report["error"]


def test_live_validation_reports_database_failure(monkeypatch, capsys, script):
    class BrokenRepository:
        @staticmethod
        def from_url(url):
            raise PersistenceError("Cannot initialize database.")

    monkeypatch.setattr(script, "SqlAlchemyRepository", BrokenRepository)
    assert script.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["failed_stage"] == "persistence"


def test_smoke_defaults_to_temporary_database(monkeypatch, capsys, script):
    monkeypatch.delenv("FINDER_DATABASE_URL")
    urls = []

    class RecordingRepository:
        @staticmethod
        def from_url(url):
            urls.append(url)
            raise PersistenceError("Cannot initialize database.")

    monkeypatch.setattr(script, "SqlAlchemyRepository", RecordingRepository)
    assert script.main() == 1
    assert urls == ["sqlite:///:memory:"]
    assert json.loads(capsys.readouterr().out)["failed_stage"] == "persistence"


def test_detail_failure_logs_do_not_reveal_listing_id(monkeypatch, capsys, script, search_payload):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token-value", "expires_in": 7200})
        if request.url.path.endswith("/item_summary/search"):
            return httpx.Response(200, json=_future(search_payload))
        return httpx.Response(400)

    _use_transport(monkeypatch, script, handler)
    assert script.main() == 0
    captured = capsys.readouterr()
    for item in search_payload["itemSummaries"]:
        assert item["itemId"] not in captured.out + captured.err


def test_summary_is_aggregate(search_payload, observed_at):
    listings = [
        normalize_listing(raw, observed_at, source_metadata={"environment": "sandbox"})
        for raw in search_payload["itemSummaries"]
    ]
    summary = summarize_listings(listings)
    assert summary["listings"] == 2
    assert summary["environments"] == {"sandbox": 2}
    assert summary["field_coverage"]["current_price"] == 2
    rendered = json.dumps(summary, default=str)
    for listing in listings:
        assert listing.marketplace_item_id not in rendered
        assert listing.title not in rendered


def test_changed_fields_names_only(search_payload, observed_at):
    listing = normalize_listing(search_payload["itemSummaries"][0], observed_at)
    changed = listing.model_copy(update={"title": "Different"})
    assert changed_fields(listing, listing) == []
    assert changed_fields(listing, changed) == ["title"]
