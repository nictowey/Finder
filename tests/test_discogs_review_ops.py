"""Offline tests only. All passwords in this file are synthetic fixtures."""

import importlib.util
import json
import os
import re
from pathlib import Path

import bcrypt
import httpx
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "review_ops", Path(__file__).parents[1] / "scripts/discogs_review_ops.py"
)
ops = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ops)
PASSWORD = b"fixture-only-private-password"


def test_prepare_secure_files_and_literal_bcrypt(tmp_path, capsys):
    directory = tmp_path / "private"
    ops.prepare(directory, PASSWORD)
    assert directory.stat().st_mode & 0o777 == 0o700
    for file in directory.iterdir():
        assert file.stat().st_mode & 0o777 == 0o600
    assert ops.read_password(directory) == PASSWORD
    rendered = (directory / "site-public.caddy").read_text()
    hashed = re.search(r'review-owner "(\$2[aby]\$12\$[^"\n]+)"', rendered)[1]
    assert bcrypt.checkpw(PASSWORD, hashed.encode())
    assert "{$REVIEW_" not in rendered
    assert ops.ORIGIN in rendered
    local = (directory / "site-loopback.caddy").read_text()
    assert local.replace("    bind 127.0.0.1\n", "", 1) == rendered
    assert PASSWORD.decode() not in rendered
    assert (directory / "provenance.json").is_file()
    provenance = json.loads((directory / "provenance.json").read_text())
    assert provenance["template_sha256"] == ops.TEMPLATE_SHA256
    assert len(provenance["helper_sha256"]) == 64
    assert not capsys.readouterr().out


def test_prepare_never_overwrites(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    marker = directory / "password"
    marker.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        ops.prepare(directory, PASSWORD)
    assert marker.read_bytes() == b"existing"


def test_template_tampering_stops_before_creating(tmp_path, monkeypatch):
    template = tmp_path / "template"
    template.write_text("attacker config")
    monkeypatch.setattr(ops, "TEMPLATE", template)
    with pytest.raises(ops.SafeStop, match="template_provenance_mismatch"):
        ops.prepare(tmp_path / "private", PASSWORD)
    assert not (tmp_path / "private").exists()


def test_unsafe_parent(tmp_path):
    tmp_path.chmod(0o777)
    with pytest.raises(ops.SafeStop, match="unsafe_parent_directory"):
        ops.private_directory(tmp_path / "private")


@pytest.fixture
def private(tmp_path):
    directory = tmp_path / "private"
    ops.private_directory(directory)
    ops.write_private(directory, "password", PASSWORD)
    return directory


def test_password_modes_and_links(private, tmp_path):
    password = private / "password"
    password.chmod(0o644)
    with pytest.raises(ops.SafeStop, match="unsafe_password_file"):
        ops.read_password(private)
    password.chmod(0o600)
    os.link(password, tmp_path / "other")
    with pytest.raises(ops.SafeStop, match="unsafe_password_file"):
        ops.read_password(private)


def test_password_symlink_rejected(private, tmp_path):
    (private / "password").unlink()
    target = tmp_path / "secret"
    target.write_bytes(PASSWORD)
    (private / "password").symlink_to(target)
    with pytest.raises(OSError):
        ops.read_password(private)


def test_directory_symlink_rejected(private, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(private)
    with pytest.raises(ops.SafeStop, match="unsafe_private_directory"):
        ops.read_password(link)


def test_cli_root_guard_and_no_sensitive_error(monkeypatch, capsys):
    monkeypatch.setattr(ops.os, "getuid", lambda: -1)
    monkeypatch.setattr(ops.resource, "setrlimit", lambda *args: None)
    monkeypatch.setattr(ops.logging, "disable", lambda *args: None)
    monkeypatch.setattr(ops.os, "umask", lambda *args: None)
    assert ops.main(["prepare"]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "root_operator_required"


def test_cli_csprng_length_and_no_activation(monkeypatch, capsys):
    monkeypatch.setattr(ops.os, "getuid", lambda: 0)
    monkeypatch.setattr(ops.resource, "setrlimit", lambda *args: None)
    monkeypatch.setattr(ops.logging, "disable", lambda *args: None)
    monkeypatch.setattr(ops.os, "umask", lambda *args: None)
    seen = []
    monkeypatch.setattr(ops.secrets, "token_urlsafe", lambda count: seen.append(count) or "x" * 43)
    monkeypatch.setattr(
        ops, "prepare", lambda directory, password: seen.append((directory, password))
    )
    assert ops.main(["prepare"]) == 0
    assert seen == [32, (ops.INITIAL, b"x" * 43)]
    output = capsys.readouterr().out
    assert "x" * 43 not in output
    assert json.loads(output)["activated"] is False


def test_terminal_required_before_password_prompt(monkeypatch):
    monkeypatch.setattr(ops.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(ops.getpass, "getpass", lambda *args: pytest.fail("prompt reached"))
    with pytest.raises(ops.SafeStop, match="private_interactive_terminal_required"):
        ops.chosen_password()


def qa_handler(request, index):
    # A minimal offline response script, NOT a real Caddy/proxy validation.
    codes = [401, 401, 401, 401, 200, 200, 404, 404, 403, 403, 413]
    assert request.url.scheme == "https"
    assert request.url.host == "vps-a0c5b746.vps.ovh.us"
    assert request.url.port == 8443
    assert request.url.path in ("/listing-review", "/_proxy_gate")
    assert b"csrf=" not in request.content
    if request.headers.get("host") == "invalid.invalid":
        assert "authorization" not in request.headers
    return httpx.Response(
        codes[index] if index < len(codes) else 429, headers={"Cache-Control": "no-store"}
    )


def test_qa_fixed_target_no_api_no_secret_output(capsys):
    calls = []

    def handler(request):
        response = qa_handler(request, len(calls))
        calls.append(request)
        return response

    result = ops.run_qa(PASSWORD, exercise_rate_gate=True, transport=httpx.MockTransport(handler))
    assert result["completed"]
    assert len(calls) == 13
    assert PASSWORD.decode() not in json.dumps(result)
    assert "authorization" not in json.dumps(result)
    assert not capsys.readouterr().out
    assert result["checks"][-1] == {
        "check": "rate_gate_blocks_owner",
        "status": 429,
        "passed": True,
    }


@pytest.mark.parametrize("status", [200, 302, 403, 429, 500])
def test_qa_unexpected_status_stops_without_retry(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://attacker.invalid/"})

    result = ops.run_qa(PASSWORD, transport=httpx.MockTransport(handler))
    assert not result["completed"]
    assert result["error"] == "qa_unexpected_response"
    assert len(calls) == 1


def test_qa_transport_failure_is_sanitized():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError(PASSWORD.decode(), request=request)

    result = ops.run_qa(PASSWORD, transport=httpx.MockTransport(handler))
    assert result["error"] == "qa_transport_failed"
    assert PASSWORD.decode() not in json.dumps(result)
    assert len(calls) == 1


def test_rate_probe_is_bounded_and_missing_gate_stops():
    calls = []

    def handler(request):
        index = len(calls)
        calls.append(request)
        return qa_handler(request, index) if index < 11 else httpx.Response(401)

    result = ops.run_qa(PASSWORD, exercise_rate_gate=True, transport=httpx.MockTransport(handler))
    assert result["error"] == "qa_rate_gate_not_observed"
    assert len(calls) == 42


def test_wrong_password_72_bytes_is_not_truncation_alias():
    import base64

    calls = []
    password = b"a" * 72

    def handler(request):
        index = len(calls)
        calls.append(request)
        if index == 1:
            raw = base64.b64decode(request.headers["authorization"].split(" ")[1])
            wrong = raw.split(b":", 1)[1]
            assert len(wrong) == 72
            assert wrong != password
            assert wrong[1:] == password[1:]
        return qa_handler(request, index)

    assert ops.run_qa(password, transport=httpx.MockTransport(handler))["completed"]


@pytest.mark.parametrize("loopback", [False, True])
def test_curl_fixed_transport_keeps_credentials_out_of_argv(tmp_path, monkeypatch, loopback):
    import base64
    import subprocess

    seen = []
    credential = base64.b64encode(b"review-owner:" + PASSWORD).decode()

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        assert argv == ["/usr/bin/curl", "--disable", "--config", "-"]
        assert credential not in " ".join(argv)
        config = kwargs["input"].decode()
        assert credential in config
        assert "insecure" not in config
        assert 'proto = "=https"' in config
        assert 'noproxy = "*"' in config
        assert "location" not in config
        assert "retry = 0" in config
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert set(kwargs["env"]) == {"PATH", "HOME", "LANG", "LC_ALL"}
        assert config.count('header = "x-review-principal: ') == 2
        assert ("resolve =" in config) is loopback
        if loopback:
            assert "vps-a0c5b746.vps.ovh.us:8443:127.0.0.1" in config
        return subprocess.CompletedProcess(argv, 0, b'200 {"cache-control":["no-store"]}')

    monkeypatch.setattr(ops.subprocess, "run", run)
    request = httpx.Request(
        "GET",
        ops.ORIGIN + "/listing-review",
        headers=[
            ("Authorization", "Basic " + credential),
            ("X-Review-Principal", "one"),
            ("X-Review-Principal", "two"),
        ],
    )
    response = ops.curl_transport(loopback=loopback).handle_request(request)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert len(seen) == 1


def test_curl_rejects_other_target_before_execution(monkeypatch):
    monkeypatch.setattr(ops.subprocess, "run", lambda *args, **kwargs: pytest.fail("curl reached"))
    with pytest.raises(ops.SafeStop, match="qa_target_rejected"):
        ops.curl_transport(loopback=True).handle_request(
            httpx.Request("GET", "https://evil.invalid/")
        )


def test_curl_failure_is_not_retried(monkeypatch):
    import subprocess

    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 60, PASSWORD)

    monkeypatch.setattr(ops.subprocess, "run", run)
    result = ops.run_qa(PASSWORD, loopback=True)
    assert not result["completed"]
    assert result["error"] == "qa_curl_transport_failed"
    assert len(seen) == 1
    assert PASSWORD.decode() not in json.dumps(result)


def test_invalid_cli_never_echoes_unknown_argument(capsys):
    assert ops.main(["prepare", PASSWORD.decode()]) == 2
    output = capsys.readouterr()
    assert PASSWORD.decode() not in output.out + output.err
    assert json.loads(output.out)["error"] == "invalid_arguments"
