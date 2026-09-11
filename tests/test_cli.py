import json
from pathlib import Path

import httpx

from finder import cli
from finder.adapters.ebay.client import EbayClient


def test_cli_acceptance_scan_twice(tmp_path, monkeypatch, capsys, search_payload):
    config = tmp_path / "monitors.toml"
    config.write_text(
        '[[monitors]]\nid="rap-vinyl"\nname="Rap"\nmarketplace="ebay"\nquery="vinyl"\n'
        "[monitors.source_options]\nfetch_details=false\n"
    )
    monkeypatch.setenv("EBAY_CLIENT_ID", "fake-client")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "production")
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
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    assert cli.main(["scan", "--env-file", str(tmp_path / "absent.env")]) == 2
    assert "EBAY_CLIENT_ID" in capsys.readouterr().err


def test_env_example_contains_no_credentials():
    for line in Path(".env.example").read_text().splitlines():
        if line.startswith(("EBAY_CLIENT_ID=", "EBAY_CLIENT_SECRET=")):
            assert line.endswith("=")
