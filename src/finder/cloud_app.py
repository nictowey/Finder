"""Owner-only cloud WSGI foundation. No browser, orders or payment implementation."""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from finder.discogs_listing import ListingReader, ListingReadError, ReleaseStatsReader, observation
from finder.discogs_monitor import MonitorLoop, ReleaseMonitor
from finder.discogs_rules import RulesStore
from finder.discogs_ui import render_page


def https_url(value, *, origin=False):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (origin and parsed.path not in ("", "/"))
    ):
        raise ValueError("A fixed HTTPS origin/auth endpoint must be configured.")
    return value.rstrip("/")


class OwnerSession:
    """Same verified-owner contract as the existing Finder auth gateway; no login/grants."""

    def __init__(self, auth_url, owner, origin, *, transport=None):
        self.auth_url = https_url(auth_url)
        self.origin = https_url(origin, origin=True)
        if not isinstance(owner, str) or not owner.strip() or "@" not in owner:
            raise ValueError("Configure the existing verified owner identity.")
        self.owner = owner.strip().lower()
        self.transport = transport

    def verify(self, cookie):
        if not cookie or len(cookie) > 8192 or "\r" in cookie or "\n" in cookie:
            return False
        try:
            with httpx.Client(
                transport=self.transport, timeout=5, follow_redirects=False
            ) as client:
                with client.stream(
                    "GET",
                    self.auth_url + "/get-session",
                    headers={"Cookie": cookie, "Origin": self.origin, "Accept": "application/json"},
                ) as response:
                    if response.status_code != 200:
                        return False
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 32768:
                            return False
            data = json.loads(body)
            user = data.get("user", {})
            session = data.get("session", {})
            expiry = datetime.fromisoformat(session.get("expiresAt", "").replace("Z", "+00:00"))
            return (
                user.get("emailVerified") is True
                and isinstance(user.get("email"), str)
                and user["email"].lower() == self.owner
                and expiry.tzinfo is not None
                and expiry > datetime.now(UTC)
            )
        except (
            httpx.HTTPError,
            ValueError,
            TypeError,
            AttributeError,
            ImportError,
            UnicodeError,
            RecursionError,
        ):
            return False


class CloudApp:
    def __init__(
        self,
        database,
        origin,
        authenticator,
        *,
        listing_reader=None,
        stats_reader=None,
        start_monitor=False,
    ):
        self.database = Path(database)
        self.origin = https_url(origin, origin=True)
        self.host = urlsplit(self.origin).netloc
        self.authenticator = authenticator
        self.reader = listing_reader
        self.lock = threading.RLock()
        self.csrf_key = secrets.token_bytes(32)  # Ephemeral CSRF binding, not a login credential.
        store = RulesStore(self.database)
        with store.db:
            store.db.executescript("""
                CREATE TABLE IF NOT EXISTS cloud_control (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    killed INTEGER NOT NULL CHECK(killed IN(0,1)),
                    revision INTEGER NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO cloud_control VALUES(1,1,0);
                CREATE TABLE IF NOT EXISTS cloud_audit (sequence INTEGER PRIMARY KEY,
                    at TEXT NOT NULL, operation TEXT NOT NULL, outcome TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS cloud_audit_no_update BEFORE UPDATE ON cloud_audit
                    BEGIN SELECT RAISE(ABORT,'append-only'); END;
                CREATE TRIGGER IF NOT EXISTS cloud_audit_no_delete BEFORE DELETE ON cloud_audit
                    BEGIN SELECT RAISE(ABORT,'append-only'); END;
                CREATE TRIGGER IF NOT EXISTS cloud_audit_no_replace BEFORE INSERT ON cloud_audit
                    WHEN EXISTS(SELECT 1 FROM cloud_audit WHERE sequence=NEW.sequence)
                    BEGIN SELECT RAISE(ABORT,'append-only'); END;
            """)
        store.close()
        self.monitor = ReleaseMonitor(
            self.database, stats_reader=stats_reader, listing_reader=listing_reader
        )
        self.monitor_loop = MonitorLoop(self.monitor) if start_monitor else None
        if self.monitor_loop:
            self.monitor_loop.start()

    def token(self, cookie):
        return hmac.new(self.csrf_key, cookie.encode(), hashlib.sha256).hexdigest()

    def __call__(self, env, start_response):
        def reply(status, data, *, html=False, nonce=None):
            body = data.encode() if html else json.dumps(data, allow_nan=False).encode()
            policy = (
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
            )
            if nonce:
                policy += (
                    f"; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'"
                )
            headers = [
                ("Content-Type", "text/html; charset=utf-8" if html else "application/json"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
                ("Content-Security-Policy", policy),
                ("X-Content-Type-Options", "nosniff"),
                ("Referrer-Policy", "no-referrer"),
                ("X-Frame-Options", "DENY"),
                ("Strict-Transport-Security", "max-age=31536000"),
                ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
            ]
            start_response(f"{status} {HTTPStatus(status).phrase}", headers)
            return [body]

        path = env.get("PATH_INFO", "")
        method = env.get("REQUEST_METHOD", "")
        if env.get("HTTP_HOST") != self.host:
            return reply(421, {"error": "Unexpected host."})
        if env.get("HTTP_SEC_FETCH_SITE") in ("cross-site", "none") and method != "GET":
            return reply(403, {"error": "Cross-site request denied."})
        if method == "GET" and path == "/health":
            return reply(200, {"status": "ok", "checkout_available": False})
        if method not in ("GET", "POST"):
            return reply(405, {"error": "Method not allowed."})
        if method == "POST" and (
            env.get("HTTP_ORIGIN") != self.origin or env.get("CONTENT_TYPE") != "application/json"
        ):
            return reply(403, {"error": "Invalid origin or content type."})
        cookie = env.get("HTTP_COOKIE", "")
        if not self.authenticator.verify(cookie):
            return reply(401, {"error": "An existing verified owner session is required."})
        csrf = self.token(cookie)
        provided_csrf = env.get("HTTP_X_FINDER_CSRF", "")
        if method == "POST" and (
            not provided_csrf.isascii() or not hmac.compare_digest(provided_csrf, csrf)
        ):
            return reply(403, {"error": "Invalid CSRF token. Reload the authenticated page."})
        if method == "GET" and path == "/":
            nonce = secrets.token_urlsafe(24)
            page = render_page(csrf, nonce, cloud=True).replace(
                "credentials:'omit'", "credentials:'same-origin'"
            )
            return reply(200, page, html=True, nonce=nonce)
        with self.lock:
            store = RulesStore(self.database)
            try:
                control = store.db.execute(
                    "SELECT killed,revision FROM cloud_control WHERE id=1"
                ).fetchone()
                killed, control_revision = bool(control[0]), control[1]
                if method == "GET" and path == "/api/snapshot":
                    return reply(
                        200,
                        {
                            **store.snapshot(),
                            "capabilities": {
                                "listing_read_enabled": self.reader is not None and not killed,
                                "automatic_listing_discovery": False,
                                "checkout_available": False,
                            },
                            "kill_switch": killed,
                            "control_revision": control_revision,
                            "monitor": {
                                **self.monitor.snapshot(store),
                                "worker_running": self.monitor_loop is not None
                                and self.monitor_loop.thread.is_alive(),
                            },
                        },
                    )
                if method != "POST":
                    return reply(404, {"error": "Unknown route."})
                try:
                    length = int(env.get("CONTENT_LENGTH", "0"))
                    if not 0 < length <= 65536:
                        return reply(413, {"error": "Request must be at most 64 KiB."})
                    raw = env["wsgi.input"].read(length)
                    if len(raw) != length:
                        raise ValueError("Incomplete body.")
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise ValueError("Object required.")
                    if path in {
                        "/api/target",
                        "/api/owned",
                        "/api/kill",
                        "/api/monitor/track",
                        "/api/monitor/run",
                    }:
                        with store.db:
                            store.db.execute(
                                "INSERT INTO cloud_audit(at,operation,outcome) VALUES(?,?,?)",
                                (datetime.now(UTC).isoformat(), path, "intent"),
                            )
                    if path == "/api/target" and set(data) == {"target", "expected_revision"}:
                        result = store.save(data["target"], data["expected_revision"])
                    elif path == "/api/owned" and set(data) == {
                        "release_id",
                        "owned",
                        "expected_revision",
                    }:
                        result = store.mark_owned(**data)
                    elif path == "/api/evaluate" and set(data) == {"release_id", "observation"}:
                        if (
                            not isinstance(data["observation"], dict)
                            or data["observation"].get("source") != "synthetic"
                        ):
                            raise ValueError("Synthetic input required.")
                        result = store.review(data["release_id"], data["observation"])
                    elif (
                        path == "/api/kill"
                        and set(data) == {"killed", "expected_revision"}
                        and type(data["killed"]) is bool
                    ):
                        if type(data["expected_revision"]) is not int:
                            raise ValueError("Control revision required.")
                        with store.db:
                            store.db.execute("BEGIN IMMEDIATE")
                            changed = store.db.execute(
                                "UPDATE cloud_control SET killed=?,revision=revision+1 "
                                "WHERE id=1 AND revision=?",
                                (int(data["killed"]), data["expected_revision"]),
                            ).rowcount
                            if changed != 1:
                                raise RuntimeError("Control changed.")
                        result = {
                            "kill_switch": data["killed"],
                            "checkout_available": False,
                            "control_revision": data["expected_revision"] + 1,
                        }
                    elif path == "/api/listing" and set(data) == {"release_id", "listing"}:
                        if killed or self.reader is None:
                            return reply(
                                409, {"error": "Listing reads are stopped or unavailable."}
                            )
                        if type(data["release_id"]) is not int or not any(
                            t["release_id"] == data["release_id"]
                            for t in store.snapshot()["targets"]
                        ):
                            raise ValueError("Save target first.")
                        listing = self.monitor.read_known(data["listing"])
                        evaluation = store.review(
                            data["release_id"], observation(listing).model_dump()
                        )
                        if listing["shipping_blocked"]:
                            evaluation["reasons"].append(
                                "Shipping availability is blocked or unverified."
                            )
                        result = {
                            "listing": listing,
                            "evaluation": evaluation,
                            "warning": "Shipping, tax, fees and pressing remain unverified. "
                            "No checkout capability.",
                        }
                    elif path == "/api/monitor/track" and set(data) == {
                        "release_id",
                        "listing",
                        "remove",
                        "expected_revision",
                        "monitor_revision",
                    }:
                        result = self.monitor.track(store, **data)
                    elif path == "/api/monitor/run" and not data:
                        result = self.monitor.run_once()
                    else:
                        return reply(404, {"error": "Unknown route. Checkout is not implemented."})
                    with store.db:
                        store.db.execute(
                            "INSERT INTO cloud_audit(at,operation,outcome) VALUES(?,?,?)",
                            (datetime.now(UTC).isoformat(), path, "success"),
                        )
                    return reply(200, result)
                except sqlite3.Error:
                    return reply(
                        503,
                        {
                            "error": "Storage/audit unavailable. Outcome may be uncertain; "
                            "reload before retrying."
                        },
                    )
                except ListingReadError:
                    return reply(
                        502,
                        {"error": "Listing read unavailable; no availability or total verified."},
                    )
                except (ValueError, TypeError, KeyError, RecursionError):
                    return reply(400, {"error": "Invalid fields."})
                except RuntimeError:
                    return reply(409, {"error": "Saved state changed. Reload before retrying."})
            except sqlite3.Error:
                return reply(
                    503, {"error": "Saved monitoring state is unavailable. Refresh later."}
                )
            finally:
                store.close()


def create_app():
    """Missing deployment configuration fails startup; no dotenv or real sessions loaded."""
    origin = os.environ["FINDER_CLOUD_ORIGIN"]
    owner = os.environ["FINDER_CLOUD_OWNER_EMAIL"]
    auth_url = os.environ["FINDER_CLOUD_AUTH_URL"]
    path = Path(os.environ["FINDER_CLOUD_DATABASE"])
    monitor_enabled = os.environ.get("FINDER_CLOUD_MONITOR") == "1"
    return CloudApp(
        path,
        origin,
        OwnerSession(auth_url, owner, origin),
        listing_reader=ListingReader()
        if monitor_enabled or os.environ.get("FINDER_CLOUD_LISTING_READ") == "1"
        else None,
        stats_reader=ReleaseStatsReader() if monitor_enabled else None,
        start_monitor=monitor_enabled,
    )
