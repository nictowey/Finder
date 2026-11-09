"""Offline private endpoint checks. Values below are synthetic, never credentials."""

import hashlib
import json
import os

import httpx
import pytest

from finder.discogs_ntfy import PRIVATE_NTFY_ORIGIN, NtfySender, PrivateNtfyConfig
from finder.discogs_price_alerts import availability_message
from finder.discogs_price_worker import main

TOPIC = "offline_fixture_destination_not_live"
TOKEN = "tk_" + "offlinefixture" * 3


def config(tmp_path, **changes):
    path = tmp_path / "ntfy.json"
    path.write_text(
        json.dumps({"origin": PRIVATE_NTFY_ORIGIN, "topic": TOPIC, "token": TOKEN, **changes})
    )
    path.chmod(0o600)
    return path


def test_private_endpoint_auth_redaction_and_legacy_binding(tmp_path):
    settings = PrivateNtfyConfig(config(tmp_path))
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200)

    sender = NtfySender(
        private_config=settings, enabled=True, transport=httpx.MockTransport(handle)
    )
    payload = availability_message("Offline fixture", 123)
    assert sender.send(payload).outcome == "accepted"
    request = requests[0]
    assert str(request.url) == PRIVATE_NTFY_ORIGIN + "/"
    assert request.headers["authorization"] == "Bearer " + TOKEN
    assert json.loads(request.content) == {"topic": TOPIC, **payload}
    for secret in [TOKEN, TOPIC]:
        assert secret not in repr(settings) + repr(sender) + repr(sender.binding)
    legacy = NtfySender(TOPIC, enabled=True)
    assert legacy.binding != sender.binding
    assert legacy.binding.destination_ref == hashlib.sha256(("ntfy:" + TOPIC).encode()).hexdigest()
    rotated = NtfySender(
        private_config=PrivateNtfyConfig(config(tmp_path, token=TOKEN + "X")), enabled=True
    )
    assert rotated.binding != sender.binding
    with pytest.raises(ValueError):
        NtfySender(TOPIC, private_config=settings, enabled=True)


@pytest.mark.parametrize(
    "origin",
    [
        "http://vps-a0c5b746.vps.ovh.us",
        "https://ntfy.sh",
        PRIVATE_NTFY_ORIGIN + "/",
        PRIVATE_NTFY_ORIGIN + ":443",
        PRIVATE_NTFY_ORIGIN + "@evil.test",
        "https://127.0.0.1",
    ],
)
def test_reject_unapproved_origins(tmp_path, origin):
    with pytest.raises(ValueError, match="^Private notification configuration unavailable\\.$"):
        PrivateNtfyConfig(config(tmp_path, origin=origin))


@pytest.mark.parametrize(
    "changes",
    [
        {"token": "x\r\nInjected: true"},
        {"token": ""},
        {"topic": "short"},
        {"extra": True},
        {"token": None},
    ],
)
def test_invalid_config_redacted(tmp_path, changes):
    with pytest.raises(ValueError, match="^Private notification configuration unavailable\\.$"):
        PrivateNtfyConfig(config(tmp_path, **changes))


def test_file_protections(tmp_path):
    path = config(tmp_path)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        PrivateNtfyConfig(path)
    path.chmod(0o600)
    link = tmp_path / "symlink"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        PrivateNtfyConfig(link)
    os.link(path, tmp_path / "hardlink")
    with pytest.raises(ValueError):
        PrivateNtfyConfig(path)
    (tmp_path / "hardlink").unlink()
    for content in ["{", "[]", "x" * 4097]:
        path.write_text(content)
        with pytest.raises(ValueError):
            PrivateNtfyConfig(path)


@pytest.mark.parametrize(
    "code,outcome",
    [(302, "failed"), (401, "failed"), (403, "failed"), (429, "rejected"), (500, "uncertain")],
)
def test_private_http_outcomes_do_not_redirect(tmp_path, code, outcome):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            code, headers={"Location": "https://unapproved.test", "Retry-After": "1000"}
        )

    sender = NtfySender(
        private_config=PrivateNtfyConfig(config(tmp_path)),
        enabled=True,
        transport=httpx.MockTransport(handle),
    )
    result = sender.send(availability_message("Offline fixture", 123))
    assert result.outcome == outcome
    assert len(calls) == 1
    if code == 429:
        assert result.retry_after == 1000


def test_worker_requires_explicit_single_destination(tmp_path, capsys):
    base = ["--database", str(tmp_path / "rules.db")]
    for args in [
        ["--deliver"],
        ["--ntfy-config-file", "unused"],
        ["--deliver", "--ntfy-config-file", "unused", "--topic-file", "unused"],
    ]:
        with pytest.raises(SystemExit):
            main(base + args)
    assert main(base + ["--deliver", "--ntfy-config-file", "missing"]) == 1
    assert "price_alert_worker_unavailable" in capsys.readouterr().out
