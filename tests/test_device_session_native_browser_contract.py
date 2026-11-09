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


def test_dockerfile_specific_context_allows_only_required_browser_inputs():
    # Docker uses this adjacent file instead of the root .dockerignore. Keep
    # parent-directory exceptions explicit so nested COPY inputs can be reached.
    ignore = ROOT / "deployment/DeviceSessionBrowser.Dockerfile.dockerignore"
    lines = ignore.read_text().splitlines()
    assert lines[0] == "**"
    assert {line for line in lines if line.startswith("!")} == {
        "!pyproject.toml",
        "!README.md",
        "!src/",
        "!src/**",
        "!tests/",
        "!tests/cloud_browser/",
        "!tests/cloud_browser/package.json",
        "!tests/cloud_browser/package-lock.json",
        "!tests/device_session_browser/",
        "!tests/device_session_browser/native_form.mjs",
        "!tests/device_session_browser/wsgi_bridge.py",
        "!deployment/",
        "!deployment/DeviceSessionBrowser.Dockerfile",
        "!deployment/DeviceSessionBrowser.Dockerfile.dockerignore",
    }
    for denied in (
        "**/__pycache__",
        "**/*.pyc",
        "**/.env*",
        "**/*.db",
        "**/*.sqlite*",
        "**/node_modules",
        "**/*.pem",
        "**/*.key",
    ):
        assert lines.index(denied) > max(i for i, line in enumerate(lines) if line.startswith("!"))
    for source in (
        "pyproject.toml",
        "README.md",
        "tests/cloud_browser/package.json",
        "tests/cloud_browser/package-lock.json",
        "tests/device_session_browser/native_form.mjs",
        "tests/device_session_browser/wsgi_bridge.py",
    ):
        assert (ROOT / source).is_file()
        assert "!" + source in lines
    dockerfile = (ROOT / "deployment/DeviceSessionBrowser.Dockerfile").read_text()
    assert (
        "COPY tests/cloud_browser/package.json tests/cloud_browser/package-lock.json" in dockerfile
    )
    assert "COPY tests/device_session_browser ./tests/device_session_browser/" in dockerfile
    workflow = (ROOT / ".github/workflows/device-session-native-browser.yml").read_text()
    assert workflow.count("'deployment/DeviceSessionBrowser.Dockerfile.dockerignore'") == 2


def test_browser_fixture_preserves_decision_without_claiming_native_redirect():
    script = (ROOT / "tests/device_session_browser/native_form.mjs").read_text()
    assert "assert.equal(response.headers.location, '/session')" in script
    assert "fixtureStatus = 200" in script
    assert "delete fixtureHeaders.location" in script
    assert "const fixtureHeaders = {...response.headers}" in script
    assert "const account = await page.goto(origin + '/session')" in script
    assert "assert.equal(account.status(), 200)" in script
    assert "native_redirect_verified: false" in script
    assert "restart_persistence_verified: false" in script
    assert "application_status: response.status" in script
    assert "fixture_delivery_status: fixtureStatus" in script
    assert "if (failures.length) throw new AggregateError" in script
    assert "if (browser) await browser.close()" in script
