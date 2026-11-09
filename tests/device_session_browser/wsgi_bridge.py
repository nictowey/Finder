"""Synthetic HTTPS interception bridge; stdio only, no listener or provider access."""

import base64
import io
import json
import tempfile
from pathlib import Path

from finder.discogs_device_sessions import DeviceSessionApp, SessionStore

ORIGIN = "https://watch.example"


def make_app(directory):
    store = SessionStore.initialize(Path(directory) / "devices.db")

    def protected(_env, start):
        start("200 OK", [("Content-Type", "text/plain")])
        return [b"synthetic protected page"]

    return DeviceSessionApp(
        protected,
        store=store,
        origin=ORIGIN,
        hostname="watch.example",
        principal="fixture-owner",
        # Simulates only the already-verified private Basic proxy assertion.
        # No real password, credential loading or production authentication.
        basic_authenticate=lambda env: env.get("HTTP_X_REVIEW_PRINCIPAL"),
    )


def dispatch(app, request):
    raw = base64.b64decode(request["body"], validate=True)
    env = {
        "PATH_INFO": request["path"],
        "QUERY_STRING": "",
        "REQUEST_METHOD": request["method"],
        "HTTP_HOST": "watch.example",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
        "wsgi.url_scheme": "https",
    }
    for name, value in request["headers"].items():
        if name.lower() in {"content-length", "host", "x-review-principal"}:
            continue
        key = (
            "CONTENT_TYPE"
            if name.lower() == "content-type"
            else "HTTP_" + name.upper().replace("-", "_")
        )
        env[key] = value
    if request["path"] in {"/session/login", "/session/start"}:
        env["HTTP_X_REVIEW_PRINCIPAL"] = "fixture-owner"
    result = []
    body = b"".join(app(env, lambda status, headers: result.extend([status, headers])))
    headers = {name.lower(): value for name, value in result[1]}
    if request["policy"] == "legacy":
        headers["referrer-policy"] = "no-referrer"
    elif request["policy"] != "current":
        raise ValueError("Unknown test policy")
    return {
        "status": int(result[0].split()[0]),
        "headers": headers,
        "body": base64.b64encode(body).decode("ascii"),
        # Evidence comes only from browser-emitted headers, never a default Origin.
        "origin": env.get("HTTP_ORIGIN"),
    }


def main():
    import sys

    with tempfile.TemporaryDirectory(prefix="finder-native-form-") as directory:
        app = make_app(directory)
        for line in sys.stdin:
            request = json.loads(line)
            response = dispatch(app, request)
            print(json.dumps({"id": request["id"], **response}), flush=True)


if __name__ == "__main__":
    main()
