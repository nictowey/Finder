"""Real loopback HTTP check with a synthetic owner; never contacts auth or Discogs."""

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from finder.cloud_app import CloudApp, OwnerSession
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
        check(CloudApp(path, ORIGIN, verifier))
        print("Cloud HTTP fixture passed: owner auth, save, stale revision, absent checkout.")
