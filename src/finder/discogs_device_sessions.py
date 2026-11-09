"""Default-off, dedicated-host remembered devices. No password or provider access.

The Caddy-only socket verifies Basic owner identity for bootstrap. The isolated
session database contains hashes, never bearer tokens. No initializer runs during
HTTP requests or normal startup. One WSGI process owns expiring CSRF challenges.
"""

import argparse
import base64
import hashlib
import html
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import closing
from email.utils import formatdate
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ICON = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAALQAAAC0CAIAAACyr5FlAAACbUlEQVR4nO3cwU0EQRAEQUw4Ce/xC1d44wD5veoVIYUBo+p8z8fr8wV/+pi/gLPEQRIHSRwkcZDEQRIHSRwkcZDEQRIHSRwkcZDEQRIHSRwkcZDEQRIH6ZFxfP98Pc58NHHcNR9NHHfNRxPHXfPRxHHXfDRx3DUfTRx3zUcTx13z0cRx13w0cdw1H00cd81HE8dd89HEcdd8NHHcNR9NHHfNRxPHXfPRxHHXfDRx3DUfTRx3zUcTx13z0Z4Ux38b+onEQRIHSRwkcZDEQRIHSRwkcZDEQRIHSRwkcZDEQRIHSRwkcZDEQRIHSRwkcZDEQRIHSRwkcZDEQRIHSRwkcZDEQRIHSRwkcZDEQRIHSRwkcZDEQRIHSRwkn9S+yXw0cdw1H00cd81HE8dd89HEcdd8NHHcNR9NHHfNRxPHXfPRxHHXfDRx3DUfTRx3zUcTx13z0cRx13w0cdw1H00cd81HE8dd89HEcdd8NHHcNR9NHHfNRxPHXfPR/kscT/TEsMTxJuIgiYMkDpI4SOIgiYMkDpI4SOIgiYMkDpI4SOIgiYMkDpI4SOIgiYMkDpI4SOIgiYMkDpI4SOIgiYMkDpI4SOIgiYMkDpI4SOIgiYMkDpI4SOIgiYMkjjeN9UTzOsVx1/zS4rhrfmlx3DW/tDjuml9aHHfNLy2Ou+aXFsdd80uL4675pcVx1/zS4rhrfmlx3DW/tDjuml9aHHfNLy2Ou+aXFsdd80uL4675pcVx1/zS4rhrfmlx3DW/9JPi4D5xkMRBEgdJHCRxkMRBEgdJHCRxkMRBEgdJHCRxkMRBEgdJHCRxkMRBEgfpF4aSQLgzyW5SAAAAAElFTkSuQmCC"
)

COOKIE = "__Host-sniper_session"
LIFETIME = 30 * 24 * 60 * 60
MAX_DEVICES = 32
APPLICATION_ID = 1178882644
INTERNAL_PRINCIPAL = "finder.device_principal"


def validate_origin(origin, hostname, *, allow_ip_origin=False, ip_address=None):
    if allow_ip_origin or ip_address is not None:
        if allow_ip_origin is not True or hostname is not None or type(ip_address) is not str:
            raise ValueError(
                "IP sessions require an explicit opt-in and one IP, without DNS hostname"
            )
        try:
            address = ipaddress.IPv4Address(ip_address)
        except ipaddress.AddressValueError:
            raise ValueError("A canonical public IPv4 address is required") from None
        if (
            str(address) != ip_address
            or not address.is_global
            or address.is_multicast
            or address.is_reserved
            or origin != "https://" + ip_address
        ):
            raise ValueError("An exact canonical public IPv4 HTTPS origin on port 443 is required")
        return ip_address
    parsed = urlsplit(origin)
    if (
        type(hostname) is not str
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{1,251})[a-z0-9]", hostname)
        or "." not in hostname
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")
        )
        or hostname.endswith(".localhost")
        or parsed.scheme != "https"
        or parsed.hostname != hostname
        or origin != "https://" + hostname
    ):
        raise ValueError("An exact dedicated DNS hostname and canonical HTTPS origin are required")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return hostname
    raise ValueError("A dedicated DNS hostname, or explicit approved IP opt-in, is required")


def _digest(token):
    if type(token) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def cookie_token(environ):
    raw = environ.get("HTTP_COOKIE", "")
    if type(raw) is not str or len(raw) > 4096:
        return None
    values = []
    for part in raw.split(";"):
        key, separator, value = part.strip().partition("=")
        if key == COOKIE:
            if not separator or _digest(value) is None:
                return None
            values.append(value)
    return values[0] if len(values) == 1 else None


class SessionStore:
    """Private separate auth state. Existing-store constructor never creates schema."""

    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        self._permissions()
        with self._db() as db:
            self._validate(db)

    def _permissions(self):
        parent, file = self.path.parent.lstat(), self.path.lstat()
        if (
            not self.path.is_absolute()
            or not stat.S_ISDIR(parent.st_mode)
            or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.getuid()
            or not stat.S_ISREG(file.st_mode)
            or stat.S_IMODE(file.st_mode) != 0o600
            or file.st_uid != os.getuid()
            or file.st_nlink != 1
            or self.path.parent.resolve() != self.path.parent
        ):
            raise ValueError(
                "Session store requires private real directory0700/file0600 owned by UID"
            )

    @staticmethod
    def _validate(db):
        if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("Wrong session store")
        if db.execute("SELECT version FROM metadata WHERE id=1").fetchall() != [(1,)]:
            raise ValueError("Unsupported session schema")
        db.execute("SELECT digest,principal,created,expires FROM sessions LIMIT 1").fetchall()

    def _db(self):
        self._permissions()
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=2)
        try:
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
                raise ValueError("Private rollback-journal session store required")
            self._validate(db)
            return closing(db)
        except Exception:
            db.close()
            raise

    @classmethod
    def initialize(cls, path):
        """Explicit operator action only; refuses any existing destination."""
        path = Path(path)
        parent = path.parent.lstat()
        if (
            not path.is_absolute()
            or path.parent.resolve() != path.parent
            or not stat.S_ISDIR(parent.st_mode)
            or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.getuid()
        ):
            raise ValueError("Existing private UID-owned directory0700 required")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            db.execute("CREATE TABLE metadata(id INTEGER PRIMARY KEY,version INTEGER NOT NULL)")
            db.execute("INSERT INTO metadata VALUES(1,1)")
            db.execute(
                "CREATE TABLE sessions(digest TEXT PRIMARY KEY,principal TEXT NOT NULL,"
                "created REAL NOT NULL,expires REAL NOT NULL)"
            )
        return cls(path)

    def create(self, principal):
        return self.rotate(principal)

    def rotate(self, principal, old_token=None):
        """Replace only this owner's valid session atomically, including at capacity.

        Any insertion/capacity/commit failure rolls back deletion of the old row.
        Unknown, expired, future-dated and other-owner cookies cannot free a slot.
        """
        token, now = secrets.token_urlsafe(32), self.clock()
        with self._db() as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
            db.execute(
                "DELETE FROM sessions WHERE digest=? AND principal=? AND created<=? "
                "AND expires>? AND expires<=created+?",
                (_digest(old_token), principal, now, now, LIFETIME),
            )
            if db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= MAX_DEVICES:
                raise RuntimeError("Device limit reached; revoke devices before signing in")
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (_digest(token), principal, now, now + LIFETIME),
            )
        return token, now + LIFETIME

    def authenticate(self, token, principal):
        digest = _digest(token)
        if digest is None:
            return None
        with self._db() as db:
            row = db.execute(
                "SELECT principal,created,expires FROM sessions WHERE digest=?", (digest,)
            ).fetchone()
        now = self.clock()
        if row and row[0] == principal and row[1] <= now < row[2] <= row[1] + LIFETIME:
            return f"{principal}:{digest}"
        return None

    def revoke(self, token):
        with self._db() as db, db:
            db.execute("DELETE FROM sessions WHERE digest=?", (_digest(token),))

    def revoke_all(self, principal):
        with self._db() as db, db:
            db.execute("DELETE FROM sessions WHERE principal=?", (principal,))


class DeviceSessionApp:
    def __init__(
        self,
        app,
        *,
        store,
        origin,
        hostname,
        principal,
        basic_authenticate,
        clock=time.time,
        allow_ip_origin=False,
        ip_address=None,
    ):
        expected_host = validate_origin(
            origin, hostname, allow_ip_origin=allow_ip_origin, ip_address=ip_address
        )
        self.app, self.store = app, store
        self.origin, self.hostname, self.principal = origin, expected_host, principal
        self.basic_authenticate, self.clock = basic_authenticate, clock
        self.tokens, self.lock = {}, threading.Lock()

    def _token(self, purpose, identity):
        with self.lock:
            now = self.clock()
            self.tokens = {k: v for k, v in self.tokens.items() if v[2] > now}
            if len(self.tokens) >= 128:
                raise RuntimeError("Too many open forms; wait ten minutes")
            token = secrets.token_urlsafe(32)
            self.tokens[token] = (purpose, identity, now + 600)
            return token

    def _consume(self, token, purpose, identity):
        with self.lock:
            value = self.tokens.get(token)
            if value is None or value[:2] != (purpose, identity) or value[2] <= self.clock():
                return False
            del self.tokens[token]
            return True

    def _post(self, env, purpose, identity):
        if (
            env.get("HTTP_ORIGIN") != self.origin
            or env.get("HTTP_SEC_FETCH_SITE") not in (None, "same-origin")
            or env.get("CONTENT_TYPE") != "application/x-www-form-urlencoded"
            or env.get("HTTP_TRANSFER_ENCODING") is not None
            or not re.fullmatch(r"[0-9]{1,4}", env.get("CONTENT_LENGTH", ""))
            or not 0 < int(env["CONTENT_LENGTH"]) <= 512
        ):
            return False
        raw = env["wsgi.input"].read(int(env["CONTENT_LENGTH"]))
        if len(raw) != int(env["CONTENT_LENGTH"]):
            return False
        pairs = parse_qsl(
            raw.decode("ascii"), keep_blank_values=True, strict_parsing=True, max_num_fields=2
        )
        return (
            len(pairs) == 1
            and pairs[0][0] == "csrf"
            and self._consume(pairs[0][1], purpose, identity)
        )

    @staticmethod
    def _cookie(token="", expires=None):
        age = LIFETIME if expires is not None else 0
        expiry = formatdate(expires if expires is not None else 0, usegmt=True)
        return (
            f"{COOKIE}={token}; Path=/; Max-Age={age}; Expires={expiry}; "
            "Secure; HttpOnly; SameSite=Strict"
        )

    def _reply(self, start, code, text, *, location=None, cookie=None, json_body=False):
        nonce = secrets.token_urlsafe(24)
        style = (
            f'<style nonce="{nonce}">'
            ":root{color-scheme:dark}*{box-sizing:border-box}"
            "body{margin:0;background:#101310;color:#f2f5eb;font:17px/1.6 system-ui,sans-serif}"
            "main{max-width:620px;margin:7vh auto;padding:28px}"
            "h1{font-size:36px;line-height:1.15;letter-spacing:-1px}"
            "p{color:#b6c1b3}a{color:#d5f69a}"
            "button{font:inherit;font-weight:700;color:#15200b;background:#d5f69a;"
            "border:0;border-radius:9px;padding:14px 20px;cursor:pointer;min-height:48px}"
            "form{margin:24px 0}button:focus-visible,a:focus-visible{outline:3px solid #d5f69a;"
            "outline-offset:4px}</style>"
        )
        body = (
            json.dumps({"error": text}).encode()
            if json_body
            else (
                '<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                "<title>Sniper device sign-in</title>"
                '<link rel="manifest" href="/manifest.webmanifest">'
                '<link rel="apple-touch-icon" href="/session/icon.png">'
                + style
                + "<body><main>"
                + text
                + "</main></body></html>"
            ).encode()
        )
        headers = [
            ("Content-Type", "application/json" if json_body else "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            # Native form POSTs serialize Origin as null under no-referrer.
            # Keep same-origin verification working without external referrer sharing.
            ("Referrer-Policy", "same-origin"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            (
                "Content-Security-Policy",
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
                "manifest-src 'self'; img-src 'self'; " + f"style-src 'nonce-{nonce}'",
            ),
        ]
        if location:
            headers.append(("Location", location))
        if cookie:
            headers.append(("Set-Cookie", cookie))
        start(f"{code} {HTTPStatus(code).phrase}", headers)
        return [body]

    def _form(self, action, label, identity):
        token = self._token(action, identity)
        return (
            f'<form method="post" action="{action}"><input type="hidden" name="csrf" '
            f'value="{token}"><button type="submit">{html.escape(label)}</button></form>'
        )

    def __call__(self, env, start):
        try:
            return self._handle(env, start)
        except (
            ValueError,
            TypeError,
            KeyError,
            UnicodeError,
            sqlite3.Error,
            OSError,
            RuntimeError,
        ):
            return self._reply(
                start,
                503,
                "Sign-in is unavailable. Reload before retrying. "
                "An uncertain sign-in or sign-out must not be automatically repeated.",
            )

    def _handle(self, env, start):
        path, method = env.get("PATH_INFO"), env.get("REQUEST_METHOD")
        if env.get("HTTP_HOST") != self.hostname:
            return self._reply(start, 421, "Unexpected host.")
        query = env.get("QUERY_STRING", "")
        if query:
            # Preserve the independently validated private listing-prefill handler.
            # Query fields can never authenticate a device or carry session tokens.
            valid_query = False
            if path == "/listing-review" and method == "GET" and len(query) <= 2048:
                try:
                    pairs = parse_qsl(
                        query, keep_blank_values=True, strict_parsing=True, max_num_fields=5
                    )
                    keys = [key for key, _ in pairs]
                    valid_query = len(keys) == len(set(keys)) and set(keys) <= {
                        "listing",
                        "release",
                        "under",
                        "media",
                        "sleeve",
                    }
                except ValueError:
                    pass
            if not valid_query:
                return self._reply(start, 400, "Use only supported private listing criteria.")
        if path in {"/manifest.webmanifest", "/session/icon.png"} and method == "GET":
            manifest = {
                "name": "Sniper",
                "short_name": "Sniper",
                "id": "/",
                "start_url": "/watches",
                "scope": "/",
                "display": "standalone",
                "background_color": "#101310",
                "theme_color": "#101310",
                "icons": [{"src": "/session/icon.png", "sizes": "180x180", "type": "image/png"}],
            }
            body = ICON if path.endswith(".png") else json.dumps(manifest).encode()
            start(
                "200 OK",
                [
                    (
                        "Content-Type",
                        "image/png" if path.endswith(".png") else "application/manifest+json",
                    ),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                    ("X-Content-Type-Options", "nosniff"),
                ],
            )
            return [body]
        if any(key in env for key in ("HTTP_AUTHORIZATION", "HTTP_FORWARDED")):
            return self._reply(start, 403, "Unverified proxy request.")
        if path in {"/session/login", "/session/start"}:
            # Only the dedicated Basic-protected Caddy routes may assert identity.
            basic_env = {k: v for k, v in env.items() if k != "HTTP_COOKIE"}
            if self.basic_authenticate(basic_env) != self.principal:
                return self._reply(start, 403, "Sign in through the private owner login.")
            if path == "/session/login" and method == "GET":
                return self._reply(
                    start,
                    200,
                    "<h1>Remember this device</h1><p>Stay signed in "
                    "for 30 days. Anyone using this browser can manage your watches until you "
                    "sign out, revoke devices or the session expires. "
                    "Use only a private device.</p>"
                    + self._form(
                        "/session/start", "Remember this device for 30 days", self.principal
                    ),
                )
            if path == "/session/start" and method == "POST":
                if not self._post(env, path, self.principal):
                    return self._reply(
                        start, 403, "Form expired or invalid. Open the sign-in page again."
                    )
                # Rotate a current session; never adopt a browser-supplied token.
                token, expires = self.store.rotate(self.principal, cookie_token(env))
                return self._reply(
                    start,
                    303,
                    "Device remembered.",
                    location="/session",
                    cookie=self._cookie(token, expires),
                )
            return self._reply(start, 405, "Use the sign-in form.")
        if path not in {
            "/session",
            "/session/logout",
            "/session/revoke-all",
            "/watches",
            "/watches/state",
            "/watches/save",
            "/listing-review",
        }:
            return self._reply(start, 404, "Not found.")
        if "HTTP_X_REVIEW_PRINCIPAL" in env:
            return self._reply(start, 403, "Unexpected identity assertion.")
        token = cookie_token(env)
        identity = self.store.authenticate(token, self.principal)
        if identity is None:
            if method == "GET" and path in {"/session", "/watches", "/listing-review"}:
                return self._reply(
                    start,
                    303,
                    "Your device session expired or was revoked. Sign in again.",
                    location="/session/login",
                    cookie=self._cookie(),
                )
            return self._reply(
                start,
                401,
                "Device sign-in expired or was revoked. "
                "Open /session/login, sign in, then refresh saved watches.",
                json_body=True,
            )
        if path == "/session" and method == "GET":
            return self._reply(
                start,
                200,
                "<h1>Device sign-in</h1><p>This device is remembered "
                "for up to 30 days from sign-in. Signing out does not erase "
                "a password your browser "
                'may have saved.</p><p><a href="/watches">Open watches</a> · '
                '<a href="/listing-review">Review a listing</a></p>'
                + self._form("/session/logout", "Sign out this device", identity)
                + "<p>Lost a device? This signs out every remembered device, including this one. "
                "Your owner login still works.</p>"
                + self._form("/session/revoke-all", "Sign out all devices", identity),
            )
        if path in {"/session/logout", "/session/revoke-all"} and method == "POST":
            if not self._post(env, path, identity):
                return self._reply(
                    start, 403, "Form expired or invalid. Reload device sign-in settings."
                )
            if path == "/session/logout":
                self.store.revoke(token)
            else:
                self.store.revoke_all(self.principal)
            return self._reply(
                start,
                200,
                "<h1>Signed out</h1><p>Close this tab on a shared device. "
                "Browser-saved owner passwords are separate.</p>"
                '<p><a href="/session/login">Sign in again</a></p>',
                cookie=self._cookie(),
            )
        if path.startswith("/session"):
            return self._reply(start, 405, "Use the device sign-in form.")
        # Existing apps never see bearer tokens or client/proxy identity headers.
        clean = {
            k: v
            for k, v in env.items()
            if k not in {"HTTP_COOKIE", "HTTP_X_REVIEW_PRINCIPAL", INTERNAL_PRINCIPAL}
        }
        clean[INTERNAL_PRINCIPAL] = identity
        return self.app(clean, start)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit private session-store maintenance")
    parser.add_argument("operation", choices=["initialize", "revoke-all"])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--principal", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", args.principal):
        parser.error("Invalid owner principal")
    if args.operation == "initialize":
        SessionStore.initialize(args.database)
    else:
        SessionStore(args.database).revoke_all(args.principal)


if __name__ == "__main__":
    main()
