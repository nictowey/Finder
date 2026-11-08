"""Private Unix-socket host; must only run behind the reviewed Caddy boundary.

No TCP mode, generic proxy trust, account management, or credential loading.
The socket directory is the authentication trust boundary, not the HTTP header.
"""

import argparse
import logging
import os
import re
import stat
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .discogs_phone_review import PhoneReviewApp


class PrivateBoundary:
    def __init__(self, app, *, principal, clock=time.monotonic):
        self.app = app
        self.principal = principal
        self.clock = clock
        self.lock = threading.Lock()
        self.started = clock()
        self.requests = 0

    def authenticate(self, environ):
        # Waitress AF_UNIX uses localhost, but REMOTE_ADDR is deliberately NOT
        # treated as proof: filesystem permissions enforce proxy exclusivity.
        if environ.get("HTTP_X_REVIEW_PRINCIPAL") != self.principal:
            return None
        if any(key in environ for key in ("HTTP_AUTHORIZATION", "HTTP_COOKIE", "HTTP_FORWARDED")):
            return None
        return self.principal

    def __call__(self, environ, start_response):
        # Caddy calls the private gate BEFORE password verification. Page calls
        # count too, so one authenticated browser request consumes two slots.
        with self.lock:
            now = self.clock()
            if now - self.started >= 60:
                self.started, self.requests = now, 0
            self.requests += 1
            limited = self.requests > 30
        if limited:
            body = b"Too many requests. Open a new form later."
            start_response(
                "429 Too Many Requests",
                [
                    ("Content-Type", "text/plain"),
                    ("Cache-Control", "no-store"),
                    ("Content-Length", str(len(body))),
                    ("Retry-After", "60"),
                ],
            )
            return [body]
        if environ.get("PATH_INFO") == "/_proxy_gate":
            if environ.get("REQUEST_METHOD") != "GET":
                start_response("405 Method Not Allowed", [("Cache-Control", "no-store")])
                return [b""]
            start_response("204 No Content", [("Cache-Control", "no-store")])
            return [b""]
        return self.app(environ, start_response)


def build_app(*, database, origin, principal, transport=None, clock=time.time):
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("one canonical HTTPS origin required")
    # Validate malformed ports now, before binding.
    _ = parsed.port
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", principal):
        raise ValueError("invalid owner principal")
    boundary = PrivateBoundary(None, principal=principal)
    boundary.app = PhoneReviewApp(
        database=database,
        origin=origin,
        authenticate=boundary.authenticate,
        transport=transport,
        clock=clock,
    )
    return boundary


def validate_socket_directory(path):
    directory = Path(path)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
        raise ValueError("socket directory must be a real directory")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o750:
        raise ValueError("socket directory must be runtime-owned and mode 0750")
    return directory / "review.sock"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--socket-directory", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.database.is_absolute() or not args.database.is_file():
        parser.error("existing absolute shared database required")
    socket = validate_socket_directory(args.socket_directory)
    app = build_app(database=args.database, origin=args.origin, principal=args.principal)
    # Never emit request/exception payloads to journald. Waitress receives only
    # sanitized requests; there is no request access logger in this host.
    logging.disable(logging.CRITICAL)
    from waitress import serve

    serve(
        app,
        unix_socket=str(socket),
        unix_socket_perms="660",
        threads=2,
        connection_limit=8,
        backlog=8,
        channel_timeout=15,
        cleanup_interval=1,
        max_request_header_size=8192,
        max_request_body_size=2048,
        expose_tracebacks=False,
        clear_untrusted_proxy_headers=True,
        log_socket_errors=False,
        ident="",
        url_scheme="https",
    )


if __name__ == "__main__":
    main()
