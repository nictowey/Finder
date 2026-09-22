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
    monkeypatch.setattr(module, "main", lambda: original(tmp_path / "absent.env"))
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
    assert monitor.source_options["page_size"] <= 50


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
    output = capsys.readouterr().out
    report = json.loads(output)
    assert seen_hosts == {"api.sandbox.ebay.com"}
    assert report["status"] == "passed"
    assert report["environment"] == "sandbox"
    assert report["oauth"]["status"] == "ok"
    assert report["normalization"]["listings"] == 1
    assert report["browse"]["skip_reasons"] == {"item_unavailable": 1}
    assert report["persistence"] == {"missing": 0, "fields_changed_on_reload": []}
    for forbidden in (*SECRET_VALUES, detail_payload["itemId"], detail_payload["title"]):
        assert forbidden not in output


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
