from pathlib import Path

import pytest
from pydantic import ValidationError

from finder.adapters.ebay.options import EbayOptions
from finder.config import load_discogs_settings, load_monitor, load_settings
from finder.errors import ConfigurationError


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith(("EBAY_", "FINDER_", "DISCOGS_")):
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


def test_discogs_settings_are_independent_of_ebay(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "DISCOGS_TOKEN=discogs-secret\nDISCOGS_USER_AGENT=Finder/0.2 owner@example.com\n"
    )
    settings = load_discogs_settings(env)
    assert settings.token.get_secret_value() == "discogs-secret"
    assert settings.user_agent == "Finder/0.2 owner@example.com"
    assert "discogs-secret" not in repr(settings)


def test_discogs_token_is_required(tmp_path):
    env = tmp_path / ".env"
    env.write_text("")
    with pytest.raises(ConfigurationError, match="DISCOGS_TOKEN"):
        load_discogs_settings(env)


def test_scoped_sandbox_credentials_take_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("EBAY_ENVIRONMENT", "Sandbox")
    monkeypatch.setenv("EBAY_CLIENT_ID", "generic-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "generic-secret")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
    monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_ID", "production-id")
    monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_SECRET", "production-secret")
    settings = load_settings(tmp_path / "absent.env")
    assert settings.ebay_environment == "sandbox"
    assert settings.ebay_client_id.get_secret_value() == "sandbox-id"
    assert settings.ebay_client_secret.get_secret_value() == "sandbox-secret"
    assert settings.ebay_base_url == "https://api.sandbox.ebay.com"


def test_scoped_production_credentials_never_use_sandbox_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_SECRET", "sandbox-secret")
    with pytest.raises(ConfigurationError) as exc:
        load_settings(tmp_path / "absent.env")
    assert "EBAY_PRODUCTION_CLIENT_ID" in str(exc.value)
    monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_ID", "production-id")
    monkeypatch.setenv("EBAY_PRODUCTION_CLIENT_SECRET", "production-secret")
    settings = load_settings(tmp_path / "absent.env")
    assert settings.ebay_client_id.get_secret_value() == "production-id"
    assert settings.ebay_base_url == "https://api.ebay.com"


def test_partial_scoped_credentials_are_not_mixed_with_generic(tmp_path, monkeypatch):
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_CLIENT_ID", "generic-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "generic-secret")
    monkeypatch.setenv("EBAY_SANDBOX_CLIENT_ID", "sandbox-id")
    with pytest.raises(ConfigurationError) as exc:
        load_settings(tmp_path / "absent.env")
    assert "sandbox-id" not in str(exc.value)
    assert "EBAY_SANDBOX_CLIENT_SECRET" in str(exc.value)
