"""Private aggregate watch manager; no listener, credential setup, reads or sends.

The host supplies verified owner authentication and a narrow backend. Construct the
backend only in the existing DB-owning process, never grant the web UID DB access.
This module does not mount itself into any existing production service.
"""

import json
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from finder.discogs_price_alerts import PriceLeadRule
from finder.discogs_rules import release_id
from finder.discogs_watch_ui import render_page

PATH = "/watches"
LIMIT = 200
FIELDS = {"release", "title", "currency", "price", "any_price", "enabled", "revision", "csrf"}


class WatchBackend:
    """Narrow adapter over the *existing* initialized AggregatePriceAlerts service.

    No rule migration, schema creation, provider call or notification operation.
    Saving delegates to the canonical revision-checked outbox-invalidating writer.
    """

    def __init__(self, alerts):
        self.alerts = alerts
        self.database = Path(alerts.database).resolve()

    def snapshot(self):
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
                raise RuntimeError("Wrong workspace")
            control = db.execute("SELECT enabled FROM discogs_price_control WHERE id=1").fetchone()
            if control is None:
                raise RuntimeError("Missing control")
            rows = db.execute(
                "SELECT r.revision,r.data,s.last_success,s.price,s.count,s.status "
                "FROM discogs_price_rules r LEFT JOIN discogs_price_samples s "
                "ON r.release_id=s.release_id ORDER BY r.release_id",
            ).fetchall()
            watches = []
            for revision, data, last_success, price, count, status in rows:
                rule = PriceLeadRule.model_validate_json(data)
                watches.append(
                    {
                        **rule.model_dump(),
                        "revision": revision,
                        "last_success": last_success,
                        "lowest_item_price": price or None,
                        "count": count,
                        "status": status,
                    }
                )
            return {
                "watches": watches,
                "monitor_enabled": control[0] == 1,
                "purchase_available": False,
                "aggregate_only": True,
            }

    def save(self, fields):
        ident = release_id(fields["release"])
        if type(fields["revision"]) is not int or fields["revision"] < 0:
            raise ValueError("Use a current revision")
        if type(fields["any_price"]) is not bool or type(fields["enabled"]) is not bool:
            raise ValueError("Use explicit settings")
        current = self.snapshot()
        old = next((w for w in current["watches"] if w["release_id"] == ident), None)
        if (old["revision"] if old else 0) != fields["revision"]:
            raise RuntimeError("Watch changed")
        if old is None and len(current["watches"]) >= LIMIT:
            raise ValueError("Watch limit reached")
        # Preserve existing notification preferences, including any-price/count mode.
        # New watches have initial-under-limit and later under-limit price-drop leads.
        rule = PriceLeadRule(
            release_id=ident,
            public_title=fields["title"],
            currency=fields["currency"],
            item_price_ceiling=fields["price"],
            any_price=fields["any_price"],
            enabled=fields["enabled"],
            alert_initial_low=old["alert_initial_low"] if old else True,
            alert_price_drops=old["alert_price_drops"] if old else not fields["any_price"],
            alert_count_increases=old["alert_count_increases"] if old else fields["any_price"],
        )
        # The canonical writer repeats the revision check under BEGIN IMMEDIATE.
        return self.alerts.save_rule(
            rule, expected_revision=fields["revision"], maximum_rules=LIMIT, unchanged_is_noop=True
        )


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


class WatchApp:
    """Authenticated, CSRF-protected WSGI UI; all writes are explicit POSTs."""

    def __init__(self, *, backend, origin, authenticate, clock=time.time):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or origin != f"https://{parsed.netloc}"
        ):
            raise ValueError("One canonical HTTPS origin required")
        _ = parsed.port
        self.backend, self.origin, self.host = backend, origin, parsed.netloc
        self.authenticate, self.clock = authenticate, clock
        self.tokens, self.lock = {}, threading.Lock()

    def _token(self, principal):
        with self.lock:
            now = self.clock()
            self.tokens = {k: v for k, v in self.tokens.items() if v[1] > now}
            if len(self.tokens) >= 128:
                raise RuntimeError("Too many open forms")
            token = secrets.token_urlsafe(32)
            self.tokens[token] = (principal, now + 600)
            return token

    def _consume(self, token, principal):
        if not isinstance(token, str):
            return False
        with self.lock:
            value = self.tokens.get(token)
            if value is None or value[0] != principal or value[1] <= self.clock():
                return False
            del self.tokens[token]
            return True

    def __call__(self, env, start_response):
        def reply(code, data, *, page=False, nonce=None):
            body = data.encode() if page else json.dumps(data, allow_nan=False).encode()
            policy = (
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
            )
            if nonce:
                policy += (
                    f"; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'"
                )
            from http import HTTPStatus

            start_response(
                f"{code} {HTTPStatus(code).phrase}",
                [
                    ("Content-Type", "text/html; charset=utf-8" if page else "application/json"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                    ("Content-Security-Policy", policy),
                    ("X-Content-Type-Options", "nosniff"),
                    ("Referrer-Policy", "no-referrer"),
                    ("X-Frame-Options", "DENY"),
                    ("Strict-Transport-Security", "max-age=31536000"),
                    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
                ],
            )
            return [body]

        if env.get("HTTP_HOST") != self.host:
            return reply(421, {"error": "Unexpected host."})
        principal = self.authenticate(env)
        if not isinstance(principal, str) or not principal:
            return reply(401, {"error": "Sign in to your private Sniper page."})
        path, method = env.get("PATH_INFO"), env.get("REQUEST_METHOD")
        if env.get("QUERY_STRING"):
            return reply(400, {"error": "Watch details belong in the private form, not the URL."})
        if method == "GET" and path == PATH:
            nonce = secrets.token_urlsafe(24)
            return reply(200, render_page(nonce), page=True, nonce=nonce)
        try:
            if method == "GET" and path == PATH + "/state":
                return reply(200, {**self.backend.snapshot(), "csrf": self._token(principal)})
            if method != "POST" or path != PATH + "/save":
                return reply(404, {"error": "Not found."})
            if (
                env.get("HTTP_ORIGIN") != self.origin
                or env.get("HTTP_SEC_FETCH_SITE") not in (None, "same-origin")
                or env.get("CONTENT_TYPE") != "application/json"
            ):
                return reply(403, {"error": "Invalid request origin."})
            length = env.get("CONTENT_LENGTH", "")
            if not length.isdigit() or not 0 < int(length) <= 4096:
                return reply(413, {"error": "Invalid request size."})
            raw = env["wsgi.input"].read(int(length))
            if len(raw) != int(length):
                raise ValueError()
            fields = json.loads(
                raw,
                object_pairs_hook=_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if type(fields) is not dict or set(fields) != FIELDS:
                raise ValueError()
            if not self._consume(fields["csrf"], principal):
                return reply(
                    403,
                    {
                        "error": "Form expired or already submitted. "
                        "Refresh saved watches before retrying."
                    },
                )
            self.backend.save(fields)
            # A successful write is final even if a later state read fails. The browser
            # refreshes independently and never automatically repeats a mutation.
            return reply(200, {"saved": True})
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return reply(
                400,
                {
                    "error": "Check the release, title, currency and positive price limit. "
                    "Refresh saved watches before trying again."
                },
            )
        except RuntimeError:
            return reply(
                409,
                {
                    "error": "Saved watches changed or the form is unavailable. Draft retained. "
                    "Refresh, review the current watch, then save again."
                },
            )
        except sqlite3.Error:
            return reply(
                503,
                {
                    "error": "Watch storage is unavailable. Do not repeat an uncertain save; "
                    "refresh to check the saved watch first."
                },
            )
