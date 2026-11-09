#!/usr/bin/env python3
"""Fixed-target private provisioning/QA. No live service/config changes or API reads."""

import argparse
import getpass
import hashlib
import json
import logging
import os
import re
import resource
import secrets
import stat
import subprocess
import sys
import warnings
from pathlib import Path

ORIGIN = "https://vps-a0c5b746.vps.ovh.us:8443"
PRINCIPAL = "review-owner"
INITIAL = Path("/run/sniper-review-provision")
ROTATION = Path("/run/sniper-review-rotation")
TLS_CANDIDATE = Path("/run/sniper-review-tls-candidate")
TEMPLATE = Path(__file__).resolve().parents[1] / "deploy/phone-review/Caddyfile.fragment"
TEMPLATE_SHA256 = "dfa1df4929d9536459542c6168d29725a3c2db17466eae8a27c4c48530f9d452"
LEGACY_TEMPLATE_SHA256 = "988a8f3d83a0dac4cd28dea464fb2d3f7e302779a40c173fc6c0d53c2968de3a"
SOURCE_BASE_COMMIT = "51fca28922862d07ed9ff99dc691638709cffff9"
TLS_BLOCK = "    tls {\n        issuer acme https://acme-v02.api.letsencrypt.org/directory\n    }\n"


class SafeStop(Exception):
    """Only fixed, nonsecret error identifiers may be surfaced."""


class PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal error includes unknown argument values.
        raise SafeStop("invalid_arguments")


def curl_transport(*, loopback=False):
    import httpx

    class FixedOrigin(httpx.BaseTransport):
        def handle_request(self, request):
            if str(request.url) not in (
                ORIGIN + "/listing-review",
                ORIGIN + "/_proxy_gate",
            ) or request.method not in ("GET", "POST"):
                raise SafeStop("qa_target_rejected")

            def quote(value):
                if "\n" in value or "\r" in value or "\x00" in value:
                    raise SafeStop("qa_config_value_rejected")
                return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

            config = [
                "silent",
                "globoff",
                "retry = 0",
                "max-time = 10",
                "connect-timeout = 5",
                'proto = "=https"',
                'proto-redir = "=https"',
                'noproxy = "*"',
                "url = " + quote(str(request.url)),
                "request = " + quote(request.method),
                'output = "/dev/null"',
                'write-out = "%{http_code} %{header_json}"',
            ]
            if loopback:
                config.append('resolve = "vps-a0c5b746.vps.ovh.us:8443:127.0.0.1"')
            for key, value in request.headers.multi_items():
                config.append("header = " + quote(key + ": " + value))
            body = request.read()
            if body:
                if len(body) > 2049:
                    raise SafeStop("qa_body_rejected")
                config.append("data-binary = " + quote(body.decode("ascii")))
            # Authorization is present only in this in-memory stdin config, never
            # argv, files, stdout, stderr, or an inherited proxy/TLS-keylog setting.
            completed = subprocess.run(
                ["/usr/bin/curl", "--disable", "--config", "-"],
                input=("\n".join(config) + "\n").encode("ascii"),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
                env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"},
            )
            if completed.returncode != 0 or len(completed.stdout) > 65536:
                raise SafeStop("qa_curl_transport_failed")
            status_text, header_json = completed.stdout.decode("ascii").split(" ", 1)
            headers = json.loads(header_json)
            cache = headers.get("cache-control", [])
            if not isinstance(cache, list) or any(not isinstance(item, str) for item in cache):
                raise SafeStop("qa_curl_headers_invalid")
            return httpx.Response(int(status_text), headers={"cache-control": ", ".join(cache)})

    return FixedOrigin()


def private_directory(directory):
    # Only fixed /run children are used by CLI. No symlinks or reuse after a
    # partial/uncertain preparation: an operator must reconcile existing files.
    parent = directory.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise SafeStop("unsafe_parent_directory")
    directory.mkdir(mode=0o700)
    info = directory.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise SafeStop("unsafe_private_directory")


def write_private(directory, name, data):
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def read_password(directory):
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise SafeStop("unsafe_private_directory")
    fd = os.open(directory / "password", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise SafeStop("unsafe_password_file")
        password = handle.read(73)
    if not 20 <= len(password) <= 72 or any(c < 32 or c > 126 for c in password):
        raise SafeStop("invalid_password_file")
    return password


def chosen_password():
    # getpass otherwise falls back to an echoing input stream. Never permit that.
    if not sys.stdin.isatty():
        raise SafeStop("private_interactive_terminal_required")
    try:
        terminal = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        raise SafeStop("private_interactive_terminal_required") from None
    os.close(terminal)
    with warnings.catch_warnings():
        # A later terminal failure must raise before getpass can echo input.
        warnings.simplefilter("error", getpass.GetPassWarning)
        first = getpass.getpass("New page-only passphrase (20-72 printable ASCII characters): ")
        second = getpass.getpass("Repeat passphrase: ")
    if first != second:
        raise SafeStop("passphrases_differ")
    if not 20 <= len(first) <= 72 or any(ord(c) < 32 or ord(c) > 126 for c in first):
        raise SafeStop("passphrase_length_or_characters")
    return first.encode("ascii")


def checked_template():
    source = TEMPLATE.read_bytes()
    if hashlib.sha256(source).hexdigest() != TEMPLATE_SHA256:
        raise SafeStop("template_provenance_mismatch")
    return source.decode("ascii")


def render_hash(source, hashed):
    if not re.fullmatch(r"\$2[aby]\$12\$[./A-Za-z0-9]{53}", hashed):
        raise SafeStop("unexpected_bcrypt_format")
    # This is a Caddyfile, never a shell string. Quoting preserves literal '$'.
    rendered = (
        source.replace("{$REVIEW_ORIGIN}", ORIGIN)
        .replace("{$REVIEW_PRINCIPAL}", PRINCIPAL)
        .replace("{$REVIEW_PASSWORD_HASH}", '"' + hashed + '"')
    )
    if "{$REVIEW_" in rendered:
        raise SafeStop("unresolved_template_variable")
    return rendered


def finish_candidates(directory, rendered, *, preserved=False):
    # No `bind` variant: Caddy2.6 turns bind into ACME challenge policy settings.
    # First activation requires independently verified closed ingress, fixture DB.
    write_private(directory, "site-public.caddy", rendered.encode("ascii"))
    write_private(
        directory,
        "provenance.json",
        json.dumps(
            {
                "source_base_commit": SOURCE_BASE_COMMIT,
                "template_sha256": TEMPLATE_SHA256,
                "origin": ORIGIN,
                "principal": PRINCIPAL,
                "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "credential_hash_preserved": preserved,
                "staging_requires_closed_ingress": True,
            },
            sort_keys=True,
        ).encode("ascii"),
    )
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def prepare(directory, password):
    import bcrypt

    source = checked_template()
    hashed = bcrypt.hashpw(password, bcrypt.gensalt(rounds=12)).decode("ascii")
    rendered = render_hash(source, hashed)
    private_directory(directory)
    write_private(directory, "password", password)
    finish_candidates(directory, rendered)


def read_private_artifact(directory, name):
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise SafeStop("unsafe_private_directory")
    fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise SafeStop("unsafe_private_artifact")
        data = handle.read(16385)
    if len(data) > 16384:
        raise SafeStop("private_artifact_too_large")
    return data.decode("ascii")


def preserve_existing_tls():
    source = checked_template()
    legacy = source.replace(TLS_BLOCK, "", 1)
    if hashlib.sha256(legacy.encode("ascii")).hexdigest() != LEGACY_TEMPLATE_SHA256:
        raise SafeStop("legacy_template_provenance_mismatch")
    original = read_private_artifact(INITIAL, "site-public.caddy")
    hashes = re.findall(re.escape(PRINCIPAL) + r' "([^"\n]+)"', original)
    if len(hashes) != 1 or render_hash(legacy, hashes[0]) != original:
        raise SafeStop("legacy_public_fragment_mismatch")
    expected_loopback = original.replace(ORIGIN + " {\n", ORIGIN + " {\n    bind 127.0.0.1\n", 1)
    if read_private_artifact(INITIAL, "site-loopback.caddy") != expected_loopback:
        raise SafeStop("legacy_loopback_fragment_mismatch")
    provenance = json.loads(read_private_artifact(INITIAL, "provenance.json"))
    if (
        provenance.get("template_sha256") != LEGACY_TEMPLATE_SHA256
        or provenance.get("origin") != ORIGIN
        or provenance.get("principal") != PRINCIPAL
    ):
        raise SafeStop("legacy_manifest_mismatch")
    # Deliberately never read password, call bcrypt, overwrite, install or reload.
    rendered = render_hash(source, hashes[0])
    private_directory(TLS_CANDIDATE)
    finish_candidates(TLS_CANDIDATE, rendered, preserved=True)


def run_qa(password, *, exercise_rate_gate=False, loopback=False, transport=None):
    import httpx

    if loopback and transport is not None:
        raise SafeStop("qa_transport_conflict")
    if transport is None:
        transport = curl_transport(loopback=loopback)
    checks = []
    # Never consult environment proxies, netrc, custom CA paths, or redirects.
    # No valid form/CSRF is ever extracted/submitted by this helper.
    with httpx.Client(
        verify=True,
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(10, connect=5),
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        transport=transport,
    ) as client:

        def check(
            name,
            expected,
            *,
            auth=None,
            method="GET",
            path="/listing-review",
            headers=None,
            body=None,
        ):
            # Parameters are internal fixed test cases, not CLI inputs.
            with client.stream(
                method, ORIGIN + path, auth=auth, headers=headers, content=body
            ) as response:
                status = response.status_code
                passed = status in expected
                if status == 200:
                    passed = passed and response.headers.get("cache-control") == "no-store"
            checks.append({"check": name, "status": status, "passed": passed})
            if not passed:
                raise SafeStop("qa_unexpected_response")
            return status

        owner = httpx.BasicAuth(PRINCIPAL, password.decode("ascii"))
        wrong_password = (b"!" if password[:1] != b"!" else b"?") + password[1:]
        wrong = httpx.BasicAuth(PRINCIPAL, wrong_password.decode("ascii"))
        other = httpx.BasicAuth("other-review-principal", password.decode("ascii"))
        try:
            check("missing_auth", {401})
            check("wrong_password", {401}, auth=wrong)
            check("other_principal", {401}, auth=other)
            check("forged_without_auth", {401}, headers={"X-Review-Principal": PRINCIPAL})
            check("valid_owner", {200}, auth=owner)
            check(
                "forged_overwritten",
                {200},
                auth=owner,
                headers=[
                    ("X-Review-Principal", "attacker"),
                    ("X-Review-Principal", "other"),
                    ("X_Review_Principal", "underscore-attacker"),
                ],
            )
            # Wrong-Host routing carries NO credentials to an unintended vhost.
            check("wrong_host", {400, 404, 421}, headers={"Host": "invalid.invalid"})
            check("private_gate_not_public", {404}, path="/_proxy_gate")
            check(
                "wrong_origin",
                {403},
                method="POST",
                auth=owner,
                headers={
                    "Origin": "https://vps-a0c5b746.vps.ovh.us",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                body=b"x=1",
            )
            check(
                "missing_origin",
                {403},
                method="POST",
                auth=owner,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=b"x=1",
            )
            check(
                "oversize_body",
                {413},
                method="POST",
                auth=owner,
                headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
                body=b"x" * 2049,
            )
            if exercise_rate_gate:
                for _ in range(31):
                    status = check("rate_probe", {401, 429})
                    if status == 429:
                        break
                else:
                    raise SafeStop("qa_rate_gate_not_observed")
                check("rate_gate_blocks_owner", {429}, auth=owner)
        except SafeStop as error:
            return {"completed": False, "error": str(error), "checks": checks}
        except Exception:
            return {"completed": False, "error": "qa_transport_failed", "checks": checks}
    return {"completed": True, "checks": checks}


def main(argv=None):
    parser = PrivateParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("prepare", "prepare-rotation", "preserve-existing-tls", "qa", "qa-rotation"),
    )
    parser.add_argument("--exercise-rate-gate", action="store_true")
    parser.add_argument("--loopback", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SafeStop:
        print(json.dumps({"completed": False, "error": "invalid_arguments"}))
        return 2
    # Disable logging before any network/credential work. Never print exception
    # text/tracebacks, response data, headers, password, hash, or rendered config.
    logging.disable(logging.CRITICAL)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)
    if os.getuid() != 0:
        print(json.dumps({"completed": False, "error": "root_operator_required"}))
        return 2
    if (args.exercise_rate_gate or args.loopback) and args.action not in ("qa", "qa-rotation"):
        print(json.dumps({"completed": False, "error": "rate_flag_requires_qa"}))
        return 2
    try:
        if args.action == "prepare":
            prepare(INITIAL, secrets.token_urlsafe(32).encode("ascii"))
            result = {"completed": True, "prepared": "initial", "activated": False}
        elif args.action == "preserve-existing-tls":
            preserve_existing_tls()
            result = {
                "completed": True,
                "prepared": "tls-candidate",
                "activated": False,
                "credential_hash_preserved": True,
            }
        elif args.action == "prepare-rotation":
            prepare(ROTATION, chosen_password())
            result = {"completed": True, "prepared": "rotation", "activated": False}
        else:
            directory = ROTATION if args.action == "qa-rotation" else INITIAL
            result = run_qa(
                read_password(directory),
                exercise_rate_gate=args.exercise_rate_gate,
                loopback=args.loopback,
            )
    except SafeStop as error:
        result = {"completed": False, "error": str(error)}
    except Exception:
        # Partial preparation is preserved for operator reconciliation; no retry.
        result = {"completed": False, "error": "operation_stopped_reconcile_privately"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
