"""OAuth and bounded HTTP retries, with injectable transport and clock for tests."""

import logging
import math
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from finder.config import Settings
from finder.errors import (
    AuthenticationError,
    ItemUnavailableError,
    RateLimitError,
    RequestError,
    ResponseError,
)

log = logging.getLogger("finder.ebay")


class EbayClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_retries: int = 3,
    ):
        self.settings = settings
        self.http = httpx.Client(
            base_url=settings.ebay_base_url,
            timeout=httpx.Timeout(30, connect=10),
            transport=transport,
            follow_redirects=False,
        )
        self.sleep = sleep
        self.clock = clock
        self.max_retries = max_retries
        self._token: str | None = None
        self._expires_at = 0.0

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "EbayClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        if response is not None and (header := response.headers.get("Retry-After")):
            try:
                delay = float(header)
            except ValueError:
                try:
                    date = parsedate_to_datetime(header)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=UTC)
                    delay = (date - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    delay = 0
            if not math.isfinite(delay):
                raise RateLimitError(
                    "eBay returned an invalid retry interval; retry the scan later."
                )
            if delay > 60:
                # Never shorten a server-mandated delay and retry too early.
                raise RateLimitError("eBay requests a wait longer than 60s; retry the scan later.")
            if delay > 0:
                return delay
        return min(2**attempt + random.uniform(0, 0.25), 30)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            response = None
            try:
                response = self.http.request(method, path, **kwargs)
            except httpx.RequestError:
                if attempt == self.max_retries:
                    raise RequestError("eBay request failed after bounded retries.") from None
            else:
                if response.status_code != 429 and response.status_code < 500:
                    return response
                if attempt == self.max_retries:
                    if response.status_code == 429:
                        raise RateLimitError("eBay rate limit persists; retry the scan later.")
                    raise RequestError("eBay server unavailable after bounded retries.")
            delay = self._retry_delay(response, attempt)
            log.warning(
                "ebay_request_retry",
                extra={
                    "fields": {
                        "attempt": attempt + 1,
                        "delay_seconds": round(delay, 3),
                        "status": response.status_code if response is not None else None,
                    }
                },
            )
            self.sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            raise ResponseError("eBay returned invalid JSON.") from None
        if not isinstance(payload, dict):
            raise ResponseError("eBay returned a non-object response.")
        return payload

    def _access_token(self) -> str:
        if self._token and self.clock() < self._expires_at:
            return self._token
        response = self._request(
            "POST",
            "/identity/v1/oauth2/token",
            auth=httpx.BasicAuth(
                self.settings.ebay_client_id.get_secret_value(),
                self.settings.ebay_client_secret.get_secret_value(),
            ),
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
        )
        if response.status_code != 200:
            raise AuthenticationError(
                "eBay OAuth failed; check your application credentials and environment."
            )
        payload = self._json(response)
        token, expiry = payload.get("access_token"), payload.get("expires_in")
        if (
            not isinstance(token, str)
            or not token.strip()
            or isinstance(expiry, bool)
            or not isinstance(expiry, (int, float))
            or not math.isfinite(expiry)
            or expiry <= 0
        ):
            raise ResponseError("eBay returned a malformed OAuth response.")
        self._token = token
        self._expires_at = self.clock() + max(0, expiry - 60)
        return token

    def authenticate(self) -> float:
        """Obtain (or reuse) an application token; return seconds until planned renewal.

        The token itself is never returned, so callers cannot log it by accident.
        """
        self._access_token()
        return max(0.0, self._expires_at - self.clock())

    def get(self, path: str, *, headers: dict[str, str], params: dict | None = None) -> dict:
        # Paths are constructed internally; never follow provider-supplied pagination/item URLs.
        for refresh in range(2):
            request_headers = {**headers, "Authorization": f"Bearer {self._access_token()}"}
            response = self._request("GET", path, headers=request_headers, params=params)
            if response.status_code == 401 and refresh == 0:
                self._token = None
                continue
            if response.status_code in (401, 403):
                raise AuthenticationError(
                    "eBay Browse access denied; check credentials, environment, and API access."
                )
            if response.status_code in (404, 410):
                raise ItemUnavailableError("eBay item or endpoint is no longer available.")
            if not response.is_success:
                raise RequestError(f"eBay rejected the request (HTTP {response.status_code}).")
            payload = self._json(response)
            if payload.get("errors"):
                raise ResponseError("eBay returned API errors in a successful HTTP response.")
            if payload.get("warnings"):
                log.warning("ebay_api_warnings", extra={"fields": {"present": True}})
            return payload
        raise AuthenticationError(
            "eBay authentication failed after renewing the application token."
        )
