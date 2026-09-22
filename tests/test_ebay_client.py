import json

import httpx
import pytest

from finder.adapters.ebay.client import EbayClient
from finder.errors import AuthenticationError, RateLimitError, RequestError, ResponseError


def test_oauth_cache_and_refresh_on_401(settings):
    calls = {"token": 0, "get": 0}

    def handler(request):
        if request.method == "POST":
            calls["token"] += 1
            assert request.url.path == "/identity/v1/oauth2/token"
            assert request.headers["Authorization"].startswith("Basic ")
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(
                200, json={"access_token": f"token-{calls['token']}", "expires_in": 7200}
            )
        calls["get"] += 1
        if calls["get"] == 1:
            return httpx.Response(401)
        assert request.headers["Authorization"] == "Bearer token-2"
        return httpx.Response(200, json={"total": 0})

    with EbayClient(settings, transport=httpx.MockTransport(handler)) as client:
        assert client.get("/test", headers={}) == {"total": 0}
        client.get("/test", headers={})
    assert calls == {"token": 2, "get": 3}


def test_token_expiry(settings):
    now = [0]
    token_calls = []

    def handler(request):
        if request.method == "POST":
            token_calls.append(1)
            return httpx.Response(200, json={"access_token": "token", "expires_in": 120})
        return httpx.Response(200, json={})

    with EbayClient(
        settings, transport=httpx.MockTransport(handler), clock=lambda: now[0]
    ) as client:
        client.get("/test", headers={})
        now[0] = 59
        client.get("/test", headers={})
        now[0] = 61
        client.get("/test", headers={})
    assert len(token_calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403])
def test_oauth_auth_failure_has_no_response_secret(settings, status):
    with EbayClient(
        settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(status, text="test-secret")),
    ) as client:
        with pytest.raises(AuthenticationError) as exc:
            client.get("/test", headers={})
    assert "test-secret" not in str(exc.value)


@pytest.mark.parametrize(
    "status,error",
    [
        (401, AuthenticationError),
        (403, AuthenticationError),
        (429, RateLimitError),
        (500, RequestError),
        (400, RequestError),
    ],
)
def test_browse_failures(settings, status, error):
    calls = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        calls.append(request)
        return httpx.Response(status)

    with EbayClient(
        settings, transport=httpx.MockTransport(handler), sleep=lambda _: None
    ) as client:
        with pytest.raises(error):
            client.get("/test", headers={})
    assert len(calls) <= 4


def test_retry_after_respected(settings):
    delays, calls = [], []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"total": 0})

    with EbayClient(
        settings, transport=httpx.MockTransport(handler), sleep=delays.append
    ) as client:
        client.get("/test", headers={})
    assert delays == [7]


def test_browse_request_count_includes_retries_but_excludes_oauth(settings):
    calls = 0

    def handler(request):
        nonlocal calls
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        calls += 1
        return httpx.Response(500 if calls == 1 else 200, json={"total": 0})

    with EbayClient(
        settings, transport=httpx.MockTransport(handler), sleep=lambda _: None
    ) as client:
        client.get("/buy/browse/v1/item_summary/search", headers={})
        assert client.browse_requests == 2
        assert client.browse_retries == 1


def test_long_retry_after_fails_without_early_retry(settings):
    delays = []
    with EbayClient(
        settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "300"})
        ),
        sleep=delays.append,
    ) as client:
        with pytest.raises(RateLimitError):
            client.get("/test", headers={})
    assert not delays


def test_network_failure_is_bounded(settings):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("do not log URL or credentials", request=request)

    with EbayClient(
        settings, transport=httpx.MockTransport(handler), sleep=lambda _: None
    ) as client:
        with pytest.raises(RequestError):
            client.get("/test", headers={})
    assert len(calls) == 4


@pytest.mark.parametrize("body", [b"html error", b"[]", b'{"errors": [{"errorId": 1}]}'])
def test_malformed_api_response(settings, body):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})
        return httpx.Response(200, content=body)

    with EbayClient(settings, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ResponseError):
            client.get("/test", headers={})


def test_json_logs_do_not_contain_credentials(settings, capsys):
    from finder.logging import configure_logging

    configure_logging("DEBUG")
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, text="test-secret")
        return httpx.Response(400, text="test-secret")

    with EbayClient(
        settings, transport=httpx.MockTransport(handler), sleep=lambda _: None
    ) as client:
        with pytest.raises(AuthenticationError):
            client.get("/test", headers={})
    output = capsys.readouterr().err
    assert "test-secret" not in output
    assert "test-client" not in output
    assert json.loads(output)["event"] == "ebay_request_retry"


def test_authenticate_returns_lifetime_not_token(settings):
    now = [100.0]
    token_calls = []

    def handler(request):
        token_calls.append(request.url.path)
        return httpx.Response(200, json={"access_token": "token-value", "expires_in": 7200})

    with EbayClient(
        settings, transport=httpx.MockTransport(handler), clock=lambda: now[0]
    ) as client:
        lifetime = client.authenticate()
        assert lifetime == 7140
        now[0] += 40
        assert client.authenticate() == 7100
    assert token_calls == ["/identity/v1/oauth2/token"]
    assert "token-value" not in repr(lifetime)
