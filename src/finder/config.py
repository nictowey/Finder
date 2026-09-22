"""Explicit TOML monitors and environment configuration, validated before I/O."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from finder.domain import Monitor
from finder.errors import ConfigurationError


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ebay_client_id: SecretStr
    ebay_client_secret: SecretStr
    ebay_environment: Literal["production", "sandbox"] = "production"
    database_url: SecretStr = SecretStr("sqlite:///finder.db")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    delivery_country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    delivery_postal_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9 -]{1,16}$")

    @property
    def ebay_base_url(self) -> str:
        return (
            "https://api.ebay.com"
            if self.ebay_environment == "production"
            else "https://api.sandbox.ebay.com"
        )


class DiscogsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr
    user_agent: str = Field(min_length=8, max_length=255)
    database_url: SecretStr = SecretStr("sqlite:///finder.db")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


def _ebay_credentials(environment: str) -> tuple[str, str]:
    """Require the selected environment's complete keyset."""
    prefix = f"EBAY_{environment.upper()}_"
    scoped = [
        os.environ.get(f"{prefix}{name}", "").strip() for name in ("CLIENT_ID", "CLIENT_SECRET")
    ]
    if any(scoped) and not all(scoped):
        raise ConfigurationError(f"Set both {prefix}CLIENT_ID and {prefix}CLIENT_SECRET.")
    return scoped[0], scoped[1]


def load_settings(env_file: Path = Path(".env")) -> Settings:
    load_dotenv(env_file, override=False)
    environment = os.environ.get("EBAY_ENVIRONMENT", "production").strip().lower()
    if environment not in ("production", "sandbox"):
        raise ConfigurationError("Invalid environment settings: ebay_environment")
    client_id, client_secret = _ebay_credentials(environment)
    try:
        settings = Settings(
            ebay_client_id=client_id,
            ebay_client_secret=client_secret,
            ebay_environment=environment,
            database_url=os.environ.get("FINDER_DATABASE_URL", "sqlite:///finder.db"),
            log_level=os.environ.get("FINDER_LOG_LEVEL", "INFO").upper(),
            delivery_country=os.environ.get("EBAY_DELIVERY_COUNTRY") or None,
            delivery_postal_code=os.environ.get("EBAY_DELIVERY_POSTAL_CODE") or None,
        )
    except ValidationError as exc:
        fields = ", ".join(str(e["loc"][0]) for e in exc.errors())
        raise ConfigurationError(f"Invalid environment settings: {fields}") from None
    if not all(
        x.get_secret_value() for x in (settings.ebay_client_id, settings.ebay_client_secret)
    ):
        scoped = f"EBAY_{environment.upper()}_CLIENT_ID/_CLIENT_SECRET"
        raise ConfigurationError(f"Set {scoped} in .env or environment.")
    if settings.delivery_postal_code and not settings.delivery_country:
        raise ConfigurationError("EBAY_DELIVERY_POSTAL_CODE also requires EBAY_DELIVERY_COUNTRY.")
    try:
        make_url(settings.database_url.get_secret_value())
    except ArgumentError:
        raise ConfigurationError("FINDER_DATABASE_URL must be a SQLAlchemy database URL.") from None
    return settings


def load_discogs_settings(env_file: Path = Path(".env")) -> DiscogsSettings:
    load_dotenv(env_file, override=False)
    try:
        settings = DiscogsSettings(
            token=os.environ.get("DISCOGS_TOKEN", "").strip(),
            user_agent=os.environ.get(
                "DISCOGS_USER_AGENT",
                "Finder/0.2 +https://github.com/nictowey/Finder",
            ).strip(),
            database_url=os.environ.get("FINDER_DATABASE_URL", "sqlite:///finder.db"),
            log_level=os.environ.get("FINDER_LOG_LEVEL", "INFO").upper(),
        )
    except ValidationError as exc:
        fields = ", ".join(str(error["loc"][0]) for error in exc.errors())
        raise ConfigurationError(f"Invalid Discogs settings: {fields}") from None
    if not settings.token.get_secret_value():
        raise ConfigurationError("Set DISCOGS_TOKEN in .env or the environment.")
    try:
        make_url(settings.database_url.get_secret_value())
    except ArgumentError:
        raise ConfigurationError("FINDER_DATABASE_URL must be a SQLAlchemy database URL.") from None
    return settings


def load_database_url(env_file: Path = Path(".env")) -> str:
    """Read stored listings without requiring marketplace or catalog credentials."""
    load_dotenv(env_file, override=False)
    url = os.environ.get("FINDER_DATABASE_URL", "sqlite:///finder.db")
    if (
        os.environ.get("EBAY_ENVIRONMENT", "").strip().lower() == "sandbox"
        and url == "sqlite:///finder.db"
    ):
        url = "sqlite:///finder-sandbox.db"
    try:
        make_url(url)
    except ArgumentError:
        raise ConfigurationError("FINDER_DATABASE_URL must be a SQLAlchemy database URL.") from None
    return url


def load_monitor(path: Path, monitor_id: str) -> Monitor:
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
        if set(data) != {"monitors"} or not isinstance(data["monitors"], list):
            raise ValueError("Expected only a [[monitors]] list")
        monitors = [Monitor.model_validate(value) for value in data["monitors"]]
        if len({m.id for m in monitors}) != len(monitors):
            raise ValueError("Duplicate monitor IDs")
    except (OSError, ValueError, TypeError) as exc:
        # Validation errors can include user input; only expose their class here.
        raise ConfigurationError(
            f"Cannot load monitor configuration ({type(exc).__name__})."
        ) from None
    for monitor in monitors:
        if monitor.id == monitor_id:
            return monitor
    raise ConfigurationError(f"Monitor '{monitor_id}' not found in configuration.")
