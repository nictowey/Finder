import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from finder import cli
from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.normalize import normalize_listing
from finder.persistence import SqlAlchemyRepository


def test_cli_acceptance_scan_twice(tmp_path, monkeypatch, capsys, search_payload):
    config = tmp_path / "monitors.toml"
    config.write_text(
        '[[monitors]]\nid="rap-vinyl"\nname="Rap"\nmarketplace="ebay"\nquery="vinyl"\n'
        "[monitors.source_options]\nfetch_details=false\n"
    )
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_ID", "fake-client")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("FINDER_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")

    def handler(request):
        return httpx.Response(
            200,
            json=(
                {"access_token": "token", "expires_in": 7200}
                if request.method == "POST"
                else search_payload
            ),
        )

    monkeypatch.setattr(
        cli,
        "EbayClient",
        lambda settings: EbayClient(settings, transport=httpx.MockTransport(handler)),
    )
    args = ["scan", "--config", str(config), "--env-file", str(tmp_path / "missing.env"), "--json"]
    assert cli.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli.main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert (first["new"], first["total_stored"]) == (2, 2)
    assert (second["updated"], second["new"], second["total_stored"]) == (2, 0, 2)


def test_missing_credentials_is_actionable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("EBAY_PRODUCTION_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_PRODUCTION_CLIENT_SECRET", raising=False)
    assert cli.main(["scan", "--env-file", str(tmp_path / "absent.env")]) == 2
    assert "EBAY_PRODUCTION_CLIENT_ID" in capsys.readouterr().err


def test_production_scan_requires_shared_postgres(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EBAY_ENVIRONMENT", "production")
    monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_ID", "fake-client")
    monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("FINDER_DATABASE_URL", f"sqlite:///{tmp_path / 'production.db'}")
    assert cli.main(["scan", "--env-file", str(tmp_path / "absent.env")]) == 2
    assert "shared PostgreSQL database" in capsys.readouterr().err


def test_env_example_contains_no_credentials():
    for line in Path(".env.example").read_text().splitlines():
        if line.startswith(
            (
                "EBAY_SANDBOX_CLIENT_ID=",
                "EBAY_SANDBOX_CLIENT_SECRET=",
                "EBAY_PRODUCTION_CLIENT_ID=",
                "EBAY_PRODUCTION_CLIENT_SECRET=",
            )
        ):
            assert line.endswith("=")
    assert "DISCOGS_TOKEN=" in Path(".env.example").read_text().splitlines()


def test_catalog_search_cli(tmp_path, monkeypatch, capsys, discogs_release):
    monkeypatch.setenv("DISCOGS_TOKEN", "fake-token")
    monkeypatch.setenv("DISCOGS_USER_AGENT", "Finder/0.2 test@example.com")
    monkeypatch.setenv("FINDER_DATABASE_URL", f"sqlite:///{tmp_path / 'catalog.db'}")

    def handler(request):
        if request.url.path == "/database/search":
            return httpx.Response(
                200,
                json={"pagination": {"items": 1}, "results": [{"id": 111}]},
            )
        return httpx.Response(200, json=discogs_release)

    monkeypatch.setattr(
        cli,
        "DiscogsClient",
        lambda settings: DiscogsClient(settings, transport=httpx.MockTransport(handler)),
    )
    assert cli.main(["catalog-search", "Example Artist Example Album", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["results"] == 1
    assert result["attribution"] == "Data provided by Discogs"
    assert result["new_variants"] == 1
    assert result["variants"][0]["discogs_release_id"] == "111"


def test_match_cli_persists_auditable_candidate(
    tmp_path, monkeypatch, capsys, search_payload, discogs_release
):
    database_url = f"sqlite:///{tmp_path / 'match.db'}"
    repository = SqlAlchemyRepository.from_url(database_url)
    listing = normalize_listing(
        {
            **search_payload["itemSummaries"][0],
            "title": "Example Artist Example Album vinyl LP",
            "localizedAspects": [
                {"name": "Artist", "value": "Example Artist"},
                {"name": "UPC", "value": "0123456789012"},
            ],
        },
        datetime(2026, 9, 11, tzinfo=UTC),
    )
    repository.upsert(listing)
    repository.close()
    monkeypatch.setenv("DISCOGS_TOKEN", "fake-token")
    monkeypatch.setenv("DISCOGS_USER_AGENT", "Finder/0.2 test@example.com")
    monkeypatch.setenv("FINDER_DATABASE_URL", database_url)

    def handler(request):
        if request.url.path == "/database/search":
            return httpx.Response(200, json={"results": [{"id": 111}]})
        return httpx.Response(200, json=discogs_release)

    monkeypatch.setattr(
        cli,
        "DiscogsClient",
        lambda settings: DiscogsClient(settings, transport=httpx.MockTransport(handler)),
    )
    assert (
        cli.main(
            [
                "match",
                "--marketplace",
                "ebay",
                "--item-id",
                listing.marketplace_item_id,
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["candidates"][0]["status"] == "strong_candidate"
    assert result["decision"]["outcome"] == "probable_variant"
    assert result["retrieval"]["query_kinds"] == ["barcode", "q"]
    assert result["retrieval"]["incomplete"] is False
    repository = SqlAlchemyRepository.from_url(database_url)
    try:
        saved = repository.get_candidates("ebay", listing.marketplace_item_id, "discogs")
        assert saved[0].catalog_variant_id == "111"
    finally:
        repository.close()


def test_listings_cli_reads_recent_snapshots_without_api_keys(
    tmp_path, monkeypatch, capsys, search_payload
):
    database_url = f"sqlite:///{tmp_path / 'review.db'}"
    repository = SqlAlchemyRepository.from_url(database_url)
    listing = normalize_listing(
        search_payload["itemSummaries"][0], datetime(2026, 9, 11, tzinfo=UTC)
    )
    repository.upsert(listing)
    repository.close()
    monkeypatch.setenv("FINDER_DATABASE_URL", database_url)
    monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
    monkeypatch.delenv("EBAY_PRODUCTION_CLIENT_ID", raising=False)
    assert cli.main(["listings", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["listings"]
    assert len(rows) == 1
    assert rows[0]["item_id"] == listing.marketplace_item_id
    assert rows[0]["delivered_subtotal"] == (
        str(listing.total_acquisition_cost) if listing.total_acquisition_cost is not None else None
    )
