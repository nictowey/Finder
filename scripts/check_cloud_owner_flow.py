"""Real loopback HTTP check with a synthetic owner; never contacts auth or Discogs."""

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from finder.cloud_app import CloudApp, OwnerSession
from finder.discogs_listing import ListingReader, ReleaseStatsReader
from finder.discogs_rules import RulesStore, Target

ORIGIN = "https://finder.example"
COOKIE = "synthetic-session=owner"


def target():
    return Target(
        release_id=123,
        min_media="VG+",
        min_sleeve="VG",
        min_seller_percent=99.0,
        min_feedback=100,
        max_total="50",
        currency="USD",
        destination_country="US",
        destination_postal="00000",
    )


def check(app):
    import threading
    import urllib.error
    import urllib.request

    from waitress import create_server

    server = create_server(app, host="127.0.0.1", port=0, threads=1, asyncore_loop_timeout=0.1)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.effective_port}"

        def http(path, body=None, cookie=COOKIE):
            raw = None if body is None else json.dumps(body).encode()
            request = urllib.request.Request(
                url + path,
                raw,
                headers={
                    "Host": "finder.example",
                    "Cookie": cookie,
                    "Origin": ORIGIN,
                    "Content-Type": "application/json",
                    "X-Finder-CSRF": app.token(cookie),
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                return error.code, error.read()

        assert http("/")[0] == 200 and http("/", cookie="")[0] == 401
        data = {"target": target().model_dump(), "expected_revision": 0}
        assert http("/api/target", data)[0] == 200
        assert http("/api/target", data)[0] == 409
        assert http("/api/buy", {})[0] == 404
        assert json.loads(http("/api/snapshot")[1])["capabilities"]["checkout_available"] is False
        assert json.loads(http("/api/monitor/run", {})[1])["status"] == "stopped"
        assert http("/api/kill", {"killed": False, "expected_revision": 0})[0] == 200
        assert (
            http(
                "/api/monitor/track",
                {
                    "release_id": 123,
                    "listing": "123",
                    "remove": False,
                    "expected_revision": 1,
                    "monitor_revision": 0,
                },
            )[0]
            == 200
        )
        assert json.loads(http("/api/monitor/run", {})[1]) == {"status": "ok", "requests": 1}
        monitored = json.loads(http("/api/snapshot")[1])["monitor"]
        listing = next(row for row in monitored["rows"] if row["kind"] == "listing")
        assert listing["review"]["status"] == "blocked"
        assert listing["review"]["media_completeness"]["status"] == "unknown"
        assert listing["review"]["total"] is None
        assert listing["review"]["qualifies_all_in"] is False
        assert http("/api/kill", {"killed": True, "expected_revision": 1})[0] == 200
        assert json.loads(http("/api/monitor/run", {})[1])["requests"] == 0
        assert http("/api/checkout", {})[0] == 404
    finally:
        server.task_dispatcher.shutdown(timeout=5)
        server.close()
        thread.join(timeout=2)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "rules.db"
        RulesStore(path, create=True).close()

        def auth(request):
            assert str(request.url) == "https://auth.example/get-session"
            return httpx.Response(
                200,
                json={
                    "user": {"email": "owner@example.test", "emailVerified": True},
                    "session": {
                        "expiresAt": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
                    },
                },
            )

        verifier = OwnerSession(
            "https://auth.example",
            "owner@example.test",
            ORIGIN,
            transport=httpx.MockTransport(auth),
        )

        def provider(request):
            assert request.method == "GET" and request.url.host == "api.discogs.com"
            assert "cookie" not in request.headers and "authorization" not in request.headers
            assert request.url.path == "/marketplace/listings/123"
            return httpx.Response(
                200,
                json={
                    "id": 123,
                    "release": {"id": 123},
                    "status": "For Sale",
                    "condition": "Very Good Plus (VG+)",
                    "sleeve_condition": "Very Good (VG)",
                    "seller": {"stats": {"rating": "100", "total": 200}},
                    "price": {"value": "20.00", "currency": "USD"},
                    "shipping_is_blocked": False,
                },
            )

        transport = httpx.MockTransport(provider)
        check(
            CloudApp(
                path,
                ORIGIN,
                verifier,
                listing_reader=ListingReader(transport=transport),
                stats_reader=ReleaseStatsReader(transport=transport),
            )
        )
        print(
            "Cloud HTTP fixture passed: owner auth, revisions, monitor controls, unknown total, "
            "absent checkout. Synthetic provider responses only."
        )
