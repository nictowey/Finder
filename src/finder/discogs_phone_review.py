"""Protected, explicit known-listing review. No listener or authentication provisioner.

The host must supply a verified principal callback and the existing aggregate DB.
No CloudApp, saved rules, notification, cart, checkout or credential capabilities.
"""

import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from finder.discogs_listing import ListingReadError, ReadBudgetBusy, listing_id, normalize
from finder.discogs_listing_preview import PreviewReader, evaluate_preview
from finder.discogs_read_guard import DatabaseReadBudget, ReadSafetyHalt, guard_row
from finder.discogs_rules import GRADES, amount, release_id

PATH = "/listing-review"
STYLE = """
body{font:17px system-ui;background:#101827;color:#eef3ff;margin:0;padding:20px}
main{max-width:600px;margin:auto}h1{font-size:1.8rem}label{display:block;margin:18px 0}
input,select,button,.button{font:inherit;box-sizing:border-box;width:100%;padding:14px;
border-radius:10px;border:1px solid #8798b4;background:#fff;color:#101827}
button,.button{background:#b7f2cf;font-weight:700;display:block;text-align:center}
a{color:#b7f2cf}p,li{line-height:1.5;overflow-wrap:anywhere}li{margin-bottom:10px}
section{border:1px solid #8798b4;border-radius:12px;padding:16px;margin:20px 0}
small{line-height:1.5;display:block}.comments{white-space:pre-wrap;overflow-wrap:anywhere}
"""


class PublicPreviewReader(PreviewReader):
    # Ignore proxy and netrc environment credentials. Never use DiscogsAppAuth.
    _trust_env = False


def _guard_authorizer(action, table, column, database, source):
    """Restrict request connection writes, not a substitute for OS separation."""
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_TRANSACTION):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and column == "max":
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (
        table == "busy_timeout"
        or (table in {"application_id", "synchronous", "journal_mode"} and column is None)
    ):
        return sqlite3.SQLITE_OK
    allowed = {
        "discogs_api_read_guard": {"pending_token", "pending_since", "known_deadline"},
        "discogs_monitor_control": {"next_request"},
        "discogs_phone_review_state": {"access_denied"},
    }
    if action == sqlite3.SQLITE_UPDATE and column in allowed.get(table, set()):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class ReviewReadBudget(DatabaseReadBudget):
    """Retain the durable guard until the response is classified.

    Classify/persist an access refusal before releasing the shared request slot.
    """

    response_delay = None

    def response(self, seconds):
        self.response_delay = seconds

    def finish(self):
        if self.response_delay is not None:
            super().response(self.response_delay)


def initialize_review_state(db):
    """Explicit operator provisioning only, never called by a web request.

    Additive page-local stop state in the real shared DB. Never clears a latch.
    """
    if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
        raise ReadSafetyHalt("review_wrong_workspace")
    guard_row(db)
    with db:
        db.execute("BEGIN IMMEDIATE")
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='discogs_phone_review_state'"
        ).fetchone()
        if not exists:
            db.execute(
                "CREATE TABLE discogs_phone_review_state ("
                "id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL, "
                "access_denied INTEGER NOT NULL CHECK(access_denied IN (0,1)))"
            )
            db.execute("INSERT INTO discogs_phone_review_state VALUES(1,1,0)")
        _review_state(db)


def _review_state(db):
    rows = db.execute(
        "SELECT id,schema_version,access_denied FROM discogs_phone_review_state"
    ).fetchall()
    if len(rows) != 1 or tuple(rows[0]) not in {(1, 1, 0), (1, 1, 1)}:
        raise ReadSafetyHalt("review_state_invalid")
    return rows[0][2]


def _enabled_control(db):
    rows = db.execute("SELECT enabled,revision FROM discogs_price_control WHERE id=1").fetchall()
    if len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not int or rows[0][1] < 0:
        raise ReadSafetyHalt("review_stopped")
    return tuple(rows[0])


class PhoneReviewApp:
    """Single process, threaded WSGI app; tokens are bounded, one-use and principal-bound.

    authenticate(environ) must return an independently verified, stable nonempty owner
    principal or None. Never trust client-provided headers directly. Unconfigured = deny.
    transport is for offline tests only. Constructor does not open DB or make requests.
    """

    def __init__(self, *, database, origin, authenticate=None, transport=None, clock=time.time):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or origin != f"https://{parsed.netloc}"
        ):
            raise ValueError("Configure one canonical HTTPS origin without a path.")
        self.database = Path(database).resolve()
        self.origin, self.host = origin, parsed.netloc
        self.authenticate, self.transport, self.clock = authenticate, transport, clock
        self.tokens = {}
        self.lock = threading.Lock()

    def _token(self, principal):
        with self.lock:
            now = self.clock()
            self.tokens = {k: v for k, v in self.tokens.items() if v[1] > now}
            if len(self.tokens) >= 128:
                return None
            token = secrets.token_urlsafe(32)
            self.tokens[token] = (principal, now + 600)
            return token

    def _consume(self, token, principal):
        with self.lock:
            value = self.tokens.get(token)
            if value is None or value[0] != principal or value[1] <= self.clock():
                return False
            del self.tokens[token]
            return True

    def _form(self, principal):
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
            if _review_state(db):
                return (
                    "<p>Page reads stopped after Discogs denied public listing access. "
                    "No retry or fallback. Operator review is required; "
                    "this page cannot reset it.</p>"
                )
            _enabled_control(db)
            guard_row(db)
        token = self._token(principal)
        if token is None:
            return "<p>Too many open forms. Close old tabs and try again after ten minutes.</p>"
        fields = (
            '<label>Known listing URL or ID<input name="listing" required maxlength="120" '
            'autocomplete="off"></label>'
            '<label>Exact release ID<input name="release" required inputmode="numeric" '
            'pattern="[1-9][0-9]*" maxlength="16"></label>'
            '<label>Item price strictly under (USD)<input name="under" required '
            'inputmode="decimal" placeholder="175.00" maxlength="10"></label>'
        )
        for field, label in (("media", "Minimum media grade"), ("sleeve", "Minimum sleeve grade")):
            options = "".join(
                f"<option{' selected' if grade == 'NM' else ''}>{grade}</option>"
                for grade in GRADES
            )
            fields += f'<label>{label}<select name="{field}">{options}</select></label>'
        return (
            f'<form method="post" action="{PATH}"><input type="hidden" name="csrf" '
            f'value="{token}">{fields}<button type="submit">Review this listing once</button>'
            "</form><small>One public read. No automatic retries, discovery or purchases. "
            "The price cap excludes shipping, tax and fees.</small>"
        )

    def _review(self, fields):
        try:
            ident = listing_id(fields["listing"])
        except ListingReadError:
            raise ValueError() from None
        if not re.fullmatch(r"[1-9][0-9]{0,15}", fields["release"]):
            raise ValueError()
        release = release_id(fields["release"])
        limit = amount(fields["under"])
        if Decimal(limit) <= 0 or any(fields[key] not in GRADES for key in ("media", "sleeve")):
            raise ValueError()
        # mode=rw never creates a replacement DB; no schema initialization or guard repair.
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=rw", uri=True)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.set_authorizer(_guard_authorizer)
            guard_row(db)
            control = _enabled_control(db)

            def check():
                if _review_state(db):
                    raise ReadSafetyHalt("review_access_denied")
                if _enabled_control(db) != control:
                    raise ReadSafetyHalt("review_control_changed")

            budget = ReviewReadBudget(db, clock=self.clock, check=check)
            reader = PublicPreviewReader(
                budget=budget, transport=self.transport, wall_clock=self.clock
            )
            try:
                payload = reader._request(
                    f"/marketplace/listings/{ident}", params={"curr_abbr": "USD"}
                )
            except ListingReadError as error:
                if error.access_restricted:
                    # Keep shared reservation until the page-only stop is durable.
                    # If this write fails, leave pending intact: persistence is uncertain.
                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute(
                            "UPDATE discogs_phone_review_state SET access_denied=1 WHERE id=1"
                        )
                budget.finish()
                raise
            budget.finish()
            check()
        result = evaluate_preview(payload, ident, release, under=limit, currency="USD")
        listing = normalize(payload, ident)
        checks = result["checks"]
        del checks["media_at_least_nm"], checks["sleeve_at_least_nm"]
        for key in ("media", "sleeve"):
            grade = listing[key]
            checks[f"{key}_minimum"] = grade in GRADES and GRADES.index(grade) >= GRADES.index(
                fields[key]
            )
        result["item_criteria_match"] = all(checks.values())
        result["seller_comments"] = listing["seller_comments"]
        result["observed_at"] = datetime.fromtimestamp(self.clock(), UTC).isoformat()
        result["minimums"] = {key: fields[key] for key in ("media", "sleeve")}
        return result

    def _result(self, result):
        title = "Reported item criteria match" if result["item_criteria_match"] else "Needs review"
        price = result["price"]
        price_text = f"{price['display_value']} {price['currency']}" if price else "Unknown"
        original = result["original_price"]
        original_text = (
            f"{original['display_value']} {original['currency']}" if original else "Unknown"
        )
        shipping = result["shipping"]
        reported = shipping["reported"]
        shipping_text = (
            f"{reported['display_value']} {reported['currency']}" if reported else "Unknown"
        )
        blocked = "Blocked or unknown" if shipping["blocked_or_unknown"] else "Not reported blocked"
        rows = "".join(
            f"<li>{escape(key.replace('_', ' '))}: {'Pass' if value else 'Not met / unknown'}</li>"
            for key, value in result["checks"].items()
        )
        evidence = "".join(
            f"<li>{escape(reason)}</li>"
            for category in ("media_completeness", "media_defects")
            for reason in result[category]["reasons"]
        )
        warnings = "".join(f"<li>{escape(text)}</li>" for text in result["warnings"])
        return (
            f"<section><h2>{title}</h2><p>Listing {result['listing_id']}; "
            f"target release {result['target_release_id']}; "
            f"reported release {result['release_id']}.</p>"
            f"<p>Checked at {escape(result['observed_at'])}. "
            "This result is not updated automatically.</p>"
            f"<p>Reported item price: {escape(price_text)}</p>"
            f"<p>Original provider price: {escape(original_text)}. "
            "No conversion performed here.</p>"
            f"<p>Reported shipping: {escape(shipping_text)}. Eligibility: {blocked}. "
            "Destination and delivered total remain unverified.</p>"
            f"<p>Cap: strictly under {escape(result['threshold']['strictly_under'])} USD, "
            "excluding shipping, tax and fees.</p>"
            f"<p>Minimum media: {escape(result['minimums']['media'])}; "
            f"minimum sleeve: {escape(result['minimums']['sleeve'])}.</p>"
            f"<p>Media: {escape(result['media'] or 'Unknown')}. "
            f"Sleeve: {escape(result['sleeve'] or 'Unknown')}.</p><ul>{rows}</ul>"
            '<h2>Seller description</h2><p class="comments">'
            f"{escape(result['seller_comments'] or 'Unknown / unsupported')}</p>"
            f"<h2>Completeness and defects</h2><ul>{evidence}</ul>"
            f"<h2>Before you act</h2><ul>{warnings}</ul>"
            f'<a class="button" href="{escape(result["url"], quote=True)}" '
            'rel="noreferrer noopener" target="_blank">Open listing on Discogs</a>'
            "<p>Manual handoff only. Recheck current availability and total on Discogs.</p>"
            "<small>Data provided by Discogs</small></section>"
        )

    def __call__(self, environ, start_response):
        status, body = "200 OK", ""
        try:
            if environ.get("HTTP_HOST") != self.host or environ.get("QUERY_STRING", ""):
                status, body = "400 Bad Request", "<p>Invalid request.</p>"
            elif environ.get("PATH_INFO") != PATH:
                status, body = "404 Not Found", "<p>Not found.</p>"
            else:
                principal = self.authenticate(environ) if self.authenticate else None
                if not isinstance(principal, str) or not principal or len(principal) > 256:
                    status, body = "403 Forbidden", "<p>Owner authentication required.</p>"
                elif environ.get("REQUEST_METHOD") == "GET":
                    if environ.get("HTTP_SEC_FETCH_SITE") == "cross-site" and (
                        environ.get("HTTP_SEC_FETCH_MODE") != "navigate"
                        or environ.get("HTTP_SEC_FETCH_DEST") != "document"
                    ):
                        status, body = "403 Forbidden", "<p>Open the review page directly.</p>"
                    else:
                        body = self._form(principal)
                elif environ.get("REQUEST_METHOD") != "POST":
                    status, body = "405 Method Not Allowed", "<p>Use the review form.</p>"
                elif (
                    environ.get("HTTP_ORIGIN") != self.origin
                    or environ.get("HTTP_SEC_FETCH_SITE") not in (None, "same-origin")
                    or environ.get("CONTENT_TYPE") != "application/x-www-form-urlencoded"
                    or environ.get("HTTP_TRANSFER_ENCODING")
                ):
                    status, body = "403 Forbidden", "<p>Open a new review form.</p>"
                else:
                    length = int(environ.get("CONTENT_LENGTH", "0"))
                    if not 0 < length <= 2048:
                        raise ValueError()
                    raw = environ["wsgi.input"].read(length)
                    if len(raw) != length:
                        raise ValueError()
                    data = parse_qs(raw.decode("ascii"), strict_parsing=True, max_num_fields=6)
                    if set(data) != {"csrf", "listing", "release", "under", "media", "sleeve"}:
                        raise ValueError()
                    if any(len(value) != 1 for value in data.values()):
                        raise ValueError()
                    fields = {key: value[0] for key, value in data.items()}
                    if not self._consume(fields["csrf"], principal):
                        status, body = (
                            "409 Conflict",
                            "<p>Form expired or already submitted. "
                            "No read attempted. Open a new form to explicitly review again.</p>",
                        )
                    else:
                        body = self._result(self._review(fields))
        except ReadBudgetBusy:
            status, body = (
                "503 Service Unavailable",
                "<p>Shared read pending or cooldown active. "
                "No request sent. Wait, then open a new form if you want to try again.</p>",
            )
        except (ReadSafetyHalt, sqlite3.Error, OSError):
            status, body = (
                "503 Service Unavailable",
                "<p>Shared read safety unavailable. "
                "Stopped without retry. The page may be stopped after access refusal, or shared "
                "safety state is unavailable. Operator review is required.</p>",
            )
        except ListingReadError:
            status, body = (
                "502 Bad Gateway",
                "<p>Public listing unavailable or unsupported. "
                "Stopped without retry or fallback. No listing verified.</p>",
            )
        except (ValueError, KeyError, UnicodeError):
            status, body = (
                "400 Bad Request",
                "<p>Invalid form. Open a new form and check inputs.</p>",
            )
        except Exception:
            # Authentication/integration errors fail closed without exception payload logging.
            status, body = (
                "503 Service Unavailable",
                "<p>Review unavailable. No automatic retry.</p>",
            )
        html = (
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>Known listing review</title><style>{STYLE}</style><main>"
            "<h1>Known listing review</h1><p>Check one copy against your criteria.</p>"
            "<p>Independent tool, not affiliated with Discogs.</p>"
            f'{body}<p><a href="{PATH}">Open a new review form</a></p></main></html>'
        ).encode()
        start_response(
            status,
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(html))),
                ("Cache-Control", "no-store"),
                ("Referrer-Policy", "no-referrer"),
                ("X-Content-Type-Options", "nosniff"),
                (
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                ),
            ],
        )
        return [html]
