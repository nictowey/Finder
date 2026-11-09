"""Offline bridge checks only; these do not claim that a browser was launched."""

import base64
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_form_bridge", ROOT / "tests/device_session_browser/wsgi_bridge.py"
)
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


def request(app, *, method="GET", origin=None, body=b"", policy="current"):
    headers = {"content-type": "application/x-www-form-urlencoded"}
    if origin is not None:
        headers["origin"] = origin
    return BRIDGE.dispatch(
        app,
        {
            "method": method,
            "path": "/session/login" if method == "GET" else "/session/start",
            "headers": headers,
            "body": base64.b64encode(body).decode("ascii"),
            "policy": policy,
        },
    )


@pytest.mark.parametrize("origin", [None, "null", "https://attacker.example"])
def test_bridge_does_not_invent_origin_and_guard_stays_strict(tmp_path, origin):
    tmp_path.chmod(0o700)
    app = BRIDGE.make_app(tmp_path)
    page = request(app)
    csrf = re.search(rb'name="csrf" value="([^"]+)"', base64.b64decode(page["body"]))[1]
    response = request(app, method="POST", origin=origin, body=b"csrf=" + csrf)
    assert response["origin"] == origin
    assert response["status"] == 403
    assert "set-cookie" not in response["headers"]


def test_bridge_changes_only_legacy_response_policy(tmp_path):
    tmp_path.chmod(0o700)
    app = BRIDGE.make_app(tmp_path)
    page = request(app, policy="legacy")
    assert page["headers"]["referrer-policy"] == "no-referrer"
    assert page["origin"] is None
    csrf = re.search(rb'name="csrf" value="([^"]+)"', base64.b64decode(page["body"]))[1]
    response = request(app, method="POST", origin=BRIDGE.ORIGIN, body=b"csrf=" + csrf)
    assert response["status"] == 303
    assert response["headers"]["referrer-policy"] == "same-origin"
    cookie = response["headers"]["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie


def test_native_browser_ci_is_opt_in_synthetic_and_network_isolated():
    workflow = (ROOT / ".github/workflows/device-session-native-browser.yml").read_text()
    dockerfile = (ROOT / "deployment/DeviceSessionBrowser.Dockerfile").read_text()
    script = (ROOT / "tests/device_session_browser/native_form.mjs").read_text()
    assert "--network none --read-only --cap-drop ALL" in workflow
    assert "--security-opt no-new-privileges" in workflow
    assert "USER 10001:10001" in dockerfile
    assert "npm ci --ignore-scripts" in dockerfile
    assert "playwright install --with-deps chromium webkit" in dockerfile
    assert "https://watch.example" in script and "https://attacker.example" in script
    assert "request.allHeaders()" in script
    assert "request.postDataBuffer()" in script
    assert ".getByRole('button'" in script and ").click()" in script
    assert "setExtraHTTPHeaders" not in script
    assert "ignoreHTTPSErrors" not in script
