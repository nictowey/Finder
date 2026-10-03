"""Loopback-only, zero-service-use development review server.

This is a regression boundary for trusted local code, not a hostile-local-process sandbox.
It deliberately does not load live configuration, provider clients, or dotenv files.
"""

import argparse
import json
import os
import secrets
import socket
import sys
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from finder.local_ui import render_page
from finder.offline import OfflineSafetyError, _check_environment

MAX_BODY_BYTES = 256 * 1024
READ_TIMEOUT_SECONDS = 5
_active_boundary: tuple[str, int] | None = None
_guard_installed = False


class LocalWebSafetyError(RuntimeError):
    """An operation would leave the local review boundary."""


def _audit_local(event: str, args: tuple) -> None:
    if _active_boundary is None:
        return
    database, port = _active_boundary
    if event.startswith("socket."):
        if event == "socket.__new__":
            if args[1:3] == (socket.AF_INET, socket.SOCK_STREAM) and args[3] == 0:
                return
        elif event == "socket.bind" and args[1] == ("127.0.0.1", port):
            return
        raise LocalWebSafetyError("Only the local review listener is allowed; outbound I/O is off.")
    if event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}:
        raise LocalWebSafetyError("Subprocesses are disabled in local review mode.")
    if event == "sqlite3.connect" and str(args[0]) != database:
        raise LocalWebSafetyError("Local review can only open its selected SQLite workspace.")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        name = Path(os.fsdecode(args[0])).name
        if name == ".env" or name.startswith(".env."):
            raise LocalWebSafetyError("Local review does not read dotenv files.")


@contextmanager
def local_boundary(database: Path, port: int):
    """Allow one loopback listener, selected SQLite file, and no service access."""
    global _active_boundary, _guard_installed
    if _active_boundary is not None:
        raise LocalWebSafetyError("A local review server is already running.")
    if not _guard_installed:
        sys.addaudithook(_audit_local)
        _guard_installed = True
    _active_boundary = (str(database), port)
    try:
        yield
    finally:
        _active_boundary = None


class LocalReviewServer(HTTPServer):
    """Serial bounded requests avoid concurrent SQLite writes and keep state ordered."""

    allow_reuse_address = False
    request_queue_size = 5

    def __init__(self, workspace, port: int = 8765):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Port must be between 0 and 65535.")
        self.workspace = workspace
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), LocalReviewHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def server_bind(self):
        # HTTPServer's implementation calls getfqdn; no DNS is needed for loopback.
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]

    def handle_error(self, request, client_address):
        # Never write incoming paths, identifiers, payloads, or tracebacks to access logs.
        pass


class LocalReviewHandler(BaseHTTPRequestHandler):
    server: LocalReviewServer
    server_version = "FinderLocal"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self):
        self.request.settimeout(READ_TIMEOUT_SECONDS)
        super().setup()

    def log_message(self, format, *args):
        pass

    def parse_request(self):
        if not super().parse_request():
            return False
        host = self.headers.get_all("Host", [])
        if host != [f"127.0.0.1:{self.server.server_port}"]:
            self.send_error(421, "Use the exact local review address.")
            return False
        if self.headers.get_all("Transfer-Encoding"):
            self.send_error(400, "Transfer encoding is not supported.")
            return False
        return True

    def _send(self, status, body, content_type="application/json; charset=utf-8", *, nonce=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        policy = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        if nonce:
            policy += f"; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'"
        self.send_header("Content-Security-Policy", policy)
        if getattr(self, "path", None) == "/api/export" and status == 200:
            self.send_header(
                "Content-Disposition", 'attachment; filename="finder-local-review.json"'
            )
        self.end_headers()
        self.close_connection = True
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(body)

    def _json(self, status, data):
        self._send(status, json.dumps(data, ensure_ascii=True, allow_nan=False))

    def send_error(self, code, message=None, explain=None):
        # stdlib parser messages can contain raw request syntax; never reflect them.
        messages = {
            400: "Invalid local request headers or JSON body.",
            405: "Only GET and POST are supported.",
            408: "The request did not arrive in time.",
            413: "Inputs must be at most 256 KiB.",
            415: "Use application/json encoded as UTF-8.",
            421: "Use the exact local review address.",
        }
        self._json(code, {"error": messages.get(code, "The local request could not be completed.")})

    def __getattr__(self, name):
        if name.startswith("do_"):
            return self._unsupported
        raise AttributeError(name)

    def _unsupported(self):
        self.send_error(405, "Only GET and POST are supported.")

    def _run(self, operation):
        from finder.local_workspace import (
            WorkspaceConflict,
            WorkspaceError,
            WorkspaceNotFound,
            WorkspaceObservationConflict,
        )

        try:
            self._json(200, operation())
        except WorkspaceObservationConflict:
            self._json(
                409,
                {
                    "code": "observation_collision",
                    "error": "Different case data already uses this observation time. "
                    "Your draft is retained. Correct the unsaved draft, or supply the actual later "
                    "observation time if you observed it again. Saved history cannot be edited.",
                },
            )
        except WorkspaceConflict:
            self._json(
                409,
                {
                    "code": "stale_state",
                    "error": "Saved state changed. Your drafts are retained. Review the refreshed "
                    "profile and case before explicitly retrying.",
                },
            )
        except WorkspaceNotFound:
            self._json(404, {"error": "That case is no longer in this workspace."})
        except WorkspaceError as error:
            # The core contract exposes field paths and fixed text, never input values.
            self._json(400, {"error": str(error)})
        except Exception:
            self._json(
                500, {"error": "Operation failed. Refresh to check saved state before retrying."}
            )

    def do_GET(self):
        if self.headers.get_all("Content-Length", []) not in ([], ["0"]):
            self.send_error(400, "GET does not accept a request body.")
            return
        if self.path == "/":
            nonce = secrets.token_urlsafe(24)
            self._send(
                200,
                render_page(self.server.csrf_token, nonce),
                "text/html; charset=utf-8",
                nonce=nonce,
            )
        elif self.path == "/api/snapshot":
            self._run(self.server.workspace.snapshot)
        elif self.path == "/api/export":
            self._run(self.server.workspace.export_bundle)
        else:
            self._json(404, {"error": "Unknown local review route."})

    def _body(self):
        origins = self.headers.get_all("Origin", [])
        tokens = self.headers.get_all("X-Finder-CSRF", [])
        if (
            origins != [self.server.origin]
            or len(tokens) != 1
            or not secrets.compare_digest(tokens[0].encode(), self.server.csrf_token.encode())
        ):
            self._json(403, {"error": "Reload this local review page before saving."})
            return None
        if self.headers.get_all("Content-Type", []) not in (
            ["application/json"],
            ["application/json; charset=utf-8"],
        ):
            self.send_error(415, "Use application/json encoded as UTF-8.")
            return None
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            self.send_error(400, "One valid Content-Length is required.")
            return None
        if len(lengths[0]) > 6:
            self.send_error(413)
            return None
        length = int(lengths[0])
        if length > MAX_BODY_BYTES:
            self.send_error(413, "Inputs must be at most 256 KiB.")
            return None
        if length == 0:
            self.send_error(400, "A JSON object is required.")
            return None
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError
            data = json.loads(
                body.decode("utf-8"),
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeError, RecursionError):
            self.send_error(400, "A valid JSON object with unique keys is required.")
            return None
        except (TimeoutError, OSError):
            self.send_error(408, "The request body did not arrive in time.")
            return None
        return data

    def do_POST(self):
        data = self._body()
        if data is None:
            return
        if self.path == "/api/import":
            if set(data) == {"bundle", "expected_revision"}:
                if (
                    not isinstance(data["bundle"], dict)
                    or type(data["expected_revision"]) is not int
                ):
                    self.send_error(400)
                    return
                self._run(
                    lambda: self.server.workspace.import_bundle(
                        data["bundle"], expected_revision=data["expected_revision"]
                    )
                )
            else:
                self._run(lambda: self.server.workspace.import_bundle(data))
        elif self.path == "/api/profile":
            if (
                set(data) != {"profile", "expected_revision"}
                or not isinstance(data["profile"], dict)
                or type(data["expected_revision"]) is not int
            ):
                self.send_error(400)
                return
            self._run(
                lambda: self.server.workspace.save_profile(
                    data["profile"], expected_revision=data["expected_revision"]
                )
            )
        elif self.path == "/api/candidate":
            if (
                set(data) != {"observation", "expected_revision", "expected_current"}
                or not isinstance(data["observation"], dict)
                or type(data["expected_revision"]) is not int
                or not (
                    data["expected_current"] is None or isinstance(data["expected_current"], str)
                )
            ):
                self.send_error(400)
                return
            self._run(
                lambda: self.server.workspace.save_candidate(
                    data["observation"],
                    expected_revision=data["expected_revision"],
                    expected_current=data["expected_current"],
                )
            )
        elif self.path == "/api/verdict":
            if (
                set(data) != {"id", "verdict", "expected_revision", "review_fingerprint"}
                or not isinstance(data["id"], str)
                or data["verdict"] not in ("mine", "other", "unsure")
                or type(data["expected_revision"]) is not int
                or not isinstance(data["review_fingerprint"], str)
            ):
                self.send_error(
                    400, "A case, verdict, revision, and review fingerprint are required."
                )
                return
            self._run(
                lambda: self.server.workspace.set_verdict(
                    data["id"],
                    data["verdict"],
                    expected_revision=data["expected_revision"],
                    review_fingerprint=data["review_fingerprint"],
                )
            )
        else:
            self._json(404, {"error": "Unknown local review route."})


def _reject_constant(value):
    raise ValueError("Non-finite JSON numbers are not supported.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are not supported.")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Finder synthetic/manual local review (no services)"
    )
    parser.add_argument("--database", required=True, help="A local, tagged SQLite workspace file")
    parser.add_argument("--init", action="store_true", help="Create a new file; never overwrite")
    parser.add_argument(
        "--port", type=int, default=8765, help="Loopback port (0 selects a free port)"
    )
    args = parser.parse_args(argv)
    workspace_error = ()
    try:
        _check_environment(os.environ)
        if not 0 <= args.port <= 65535:
            raise LocalWebSafetyError("Port must be between 0 and 65535.")
        # File preflight belongs to the workspace; the guard is active before its first open.
        database = Path(args.database).expanduser().resolve()
        with local_boundary(database, args.port):
            from finder.local_workspace import LocalWorkspace, WorkspaceError

            workspace_error = WorkspaceError
            workspace = (LocalWorkspace.create if args.init else LocalWorkspace.open)(args.database)
            try:
                with LocalReviewServer(workspace, args.port) as server:
                    print(f"Finder local development review: {server.origin}", flush=True)
                    print("Synthetic/user-authored inputs only. Press Ctrl+C to stop.", flush=True)
                    try:
                        server.serve_forever(poll_interval=0.25)
                    except KeyboardInterrupt:
                        pass
            finally:
                workspace.close()
    except (OfflineSafetyError, LocalWebSafetyError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        if isinstance(error, workspace_error):
            print(str(error), file=sys.stderr)
        else:
            print(
                "Cannot open the local review workspace or listener. Check path and port.",
                file=sys.stderr,
            )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
