from pathlib import Path

import pytest
from pydantic import ValidationError

from finder.adapters.ebay.options import EbayOptions
from finder.config import load_monitor, load_settings
from finder.errors import ConfigurationError


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith(("EBAY_", "FINDER_")):
            monkeypatch.delenv(key)


def test_default_monitor():
    monitor = load_monitor(Path("config/monitors.toml"), "rap-vinyl")
    assert monitor.marketplace == "ebay"
    assert "vinyl" in monitor.query
    options = EbayOptions.model_validate(monitor.source_options)
    assert options.fetch_details
    assert options.category_ids == ["176985"]


def test_env_file_and_environment_precedence(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("EBAY_CLIENT_ID=file-id\nEBAY_CLIENT_SECRET=secret\nEBAY_ENVIRONMENT=sandbox\n")
    monkeypatch.setenv("EBAY_CLIENT_ID", "environment-id")
    settings = load_settings(env)
    assert settings.ebay_client_id.get_secret_value() == "environment-id"
    assert settings.ebay_base_url == "https://api.sandbox.ebay.com"
    assert "secret" not in repr(settings).replace("ebay_client_secret", "")


@pytest.mark.parametrize(
    "extra",
    [
        "",
        "EBAY_ENVIRONMENT=invalid\n",
        "FINDER_DATABASE_URL=bad\n",
        "EBAY_DELIVERY_POSTAL_CODE=12345\n",
    ],
)
def test_invalid_environment(tmp_path, extra):
    env = tmp_path / ".env"
    base = "EBAY_CLIENT_ID=id\nEBAY_CLIENT_SECRET=secret\n" if extra else ""
    env.write_text(base + extra)
    with pytest.raises(ConfigurationError):
        load_settings(env)


@pytest.mark.parametrize(
    "content",
    [
        "broken = [",
        "[unknown]\nx=1",
        "monitors = 1",
        '[[monitors]]\nid="a"\n',
        '[[monitors]]\nid="a"\nname="A"\nmarketplace="ebay"\nquery=" "',
    ],
)
def test_bad_toml(tmp_path, content):
    path = tmp_path / "monitors.toml"
    path.write_text(content)
    with pytest.raises(ConfigurationError):
        load_monitor(path, "a")


def test_missing_and_duplicate_monitor(tmp_path):
    with pytest.raises(ConfigurationError):
        load_monitor(Path("config/monitors.toml"), "absent")
    path = tmp_path / "monitors.toml"
    path.write_text('[[monitors]]\nid="a"\nname="A"\nmarketplace="ebay"\nquery="vinyl"\n' * 2)
    with pytest.raises(ConfigurationError):
        load_monitor(path, "a")


@pytest.mark.parametrize(
    "options",
    [
        {"page_size": 201},
        {"max_pages": 0},
        {"page_size": 200, "max_pages": 51},
        {"fetch_details": "false"},
        {"typo": 2},
        {"category_ids": ["abc"]},
        {"aspect_filter": "Genre:{Rap}"},
    ],
)
def test_bad_ebay_options(options):
    with pytest.raises(ValidationError):
        EbayOptions.model_validate(options)
