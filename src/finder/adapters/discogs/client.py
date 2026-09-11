"""Discogs API client limited to CC0 catalog endpoints."""

import logging
import math
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from finder.config import DiscogsSettings
from finder.errors import (
    CatalogAuthenticationError,
    CatalogRateLimitError,
    CatalogRequestError,
    CatalogResponseError,
)

log = logging.getLogger("finder.discogs")


class DiscogsClient:
    def __init__(
        self,
        settings: DiscogsSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ):
        self.settings = settings
        self.sleep = sleep
        self.max_retries = max_retries
        self.http = httpx.Client(
            base_url="https://api.discogs.com",
            timeout=httpx.Timeout(30, connect=10),
            transport=transport,
            follow_redirects=False,
            headers={
                "User-Agent": settings.user_agent,
                "Authorization": f"Discogs token={settings.token.get_secret_value()}",
            },
        )

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "DiscogsClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @staticmethod
    def _retry_after(response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if value:
            try:
                delay = float(value)
            except ValueError:
                try:
                    when = parsedate_to_datetime(value)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=UTC)
                    delay = (when - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = 0
            if not math.isfinite(delay):
                raise CatalogRateLimitError("Discogs returned an invalid retry interval.")
            if delay > 60:
                raise CatalogRateLimitError(
                    "Discogs requested a wait longer than 60 seconds; retry later."
                )
            if delay > 0:
                return delay
        return min(2**attempt + random.uniform(0, 0.25), 30)

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if path != "/database/search" and not re.fullmatch(r"/releases/[1-9][0-9]*", path):
            raise CatalogRequestError("Discogs client only permits CC0 catalog endpoints.")
        for attempt in range(self.max_retries + 1):
            try:
                response = self.http.get(path, params=params)
            except httpx.RequestError:
                if attempt == self.max_retries:
                    raise CatalogRequestError(
                        "Discogs request failed after bounded retries."
                    ) from None
                self.sleep(min(2**attempt + random.uniform(0, 0.25), 30))
                continue
            if response.status_code in (401, 403):
                raise CatalogAuthenticationError(
                    "Discogs access denied; check DISCOGS_TOKEN and User-Agent."
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == self.max_retries:
                    if response.status_code == 429:
                        raise CatalogRateLimitError("Discogs rate limit persists; retry later.")
                    raise CatalogRequestError("Discogs server unavailable after bounded retries.")
                delay = self._retry_after(response, attempt)
                log.warning(
                    "discogs_request_retry",
                    extra={
                        "fields": {
                            "attempt": attempt + 1,
                            "delay_seconds": round(delay, 3),
                            "status": response.status_code,
                        }
                    },
                )
                self.sleep(delay)
                continue
            if not response.is_success:
                raise CatalogRequestError(
                    f"Discogs rejected the request (HTTP {response.status_code})."
                )
            try:
                payload = response.json()
            except ValueError:
                raise CatalogResponseError("Discogs returned invalid JSON.") from None
            if not isinstance(payload, dict):
                raise CatalogResponseError("Discogs returned a non-object response.")
            return payload
        raise AssertionError("unreachable")
