"""Protected, explicit known-listing review. No listener or authentication provisioner.

The host must supply a verified principal callback and the existing aggregate DB.
No CloudApp, saved rules, notification, cart, checkout or credential capabilities.
"""

import re
import secrets
import sqlite3
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from finder.discogs_listing import ListingReadError, ReadBudgetBusy, listing_id, normalize
from finder.discogs_listing_preview import evaluate_preview
from finder.discogs_read_guard import ReadSafetyHalt
from finder.discogs_review_backend import (
    DirectReviewBackend,
)
from finder.discogs_review_backend import (
    _guard_authorizer as _guard_authorizer,
)
from finder.discogs_review_backend import (
    initialize_review_state as initialize_review_state,
)
from finder.discogs_rules import GRADES, amount, release_id

PATH = "/listing-review"
INPUT_FIELDS = {"listing", "release", "under", "media", "sleeve"}
STYLE = """
body{font:17px system-ui;background:#101827;color:#eef3ff;margin:0;padding:20px}
main{max-width:600px;margin:auto}h1{font-size:1.8rem}label{display:block;margin:18px 0}
input,select,button,.button{font:inherit;box-sizing:border-box;width:100%;padding:14px;
border-radius:10px;border:1px solid #8798b4;background:#fff;color:#101827}
button,.button{background:#b7f2cf;font-weight:700;display:block;text-align:center}
a{color:#b7f2cf}p,li{line-height:1.5;overflow-wrap:anywhere}li{margin-bottom:10px}
section{border:1px solid #8798b4;border-radius:12px;padding:16px;margin:20px 0}
small{line-height:1.5;display:block}.comments{white-space:pre-wrap;overflow-wrap:anywhere}
summary{cursor:pointer;padding:14px 0}details{margin-top:28px}
"""


def _parse_fields(raw, allowed):
    """Decode bounded ASCII form data once, without ignoring blanks or bad escapes."""
    if (
        not isinstance(raw, str)
        or len(raw) > 2048
        or re.search(r"[^\x21-\x7e]|%(?![0-9a-fA-F]{2})", raw)
    ):
        raise ValueError()
    data = parse_qs(
        raw,
        keep_blank_values=True,
        strict_parsing=True,
        max_num_fields=len(allowed),
        encoding="ascii",
        errors="strict",
    )
    if not set(data) <= allowed or any(len(value) != 1 for value in data.values()):
        raise ValueError()
    fields = {key: value[0] for key, value in data.items()}
    if any(re.search(r"[^\x20-\x7e]", value) for value in fields.values()):
        raise ValueError()
    return fields


def _criteria(fields):
    """Validate every supplied criterion; absent values remain visibly unset."""
    values = {}
    if "release" in fields:
        if not re.fullmatch(r"[1-9][0-9]{0,15}", fields["release"]):
            raise ValueError()
        values["release"] = str(release_id(fields["release"]))
    if "under" in fields:
        if not re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,2})?", fields["under"]):
            raise ValueError()
        values["under"] = amount(fields["under"])
        if Decimal(values["under"]) <= 0:
            raise ValueError()
    for field in ("media", "sleeve"):
        if field in fields:
            if fields[field] not in GRADES:
                raise ValueError()
            values[field] = fields[field]
    return values


def _prefill(query):
    fields = _parse_fields(query, INPUT_FIELDS)
    values = _criteria(fields)
    # The optional GET builder sends listing= when preparing criteria alone.
    # Do not trim, follow, resolve, or accept anything except this exact URL shape.
    if fields.get("listing"):
        if not re.fullmatch(
            r"https://(?:www\.)?discogs\.com/sell/item/[1-9][0-9]{0,15}", fields["listing"]
        ):
            raise ValueError()
        try:
            ident = listing_id(fields["listing"])
        except ListingReadError:
            raise ValueError() from None
        values["listing"] = f"https://www.discogs.com/sell/item/{ident}"
    return values


def _input_fields(values, *, prepare=False):
    label = "Known listing link (optional)" if prepare else "Known listing URL or ID"
    attributes = 'type="url"' if prepare else "required"
    fields = (
        f'<label>{label}<input name="listing" {attributes} maxlength="120" '
        f'autocomplete="off" value="{escape(values.get("listing", ""), quote=True)}"></label>'
        '<label>Exact release ID<input name="release" required inputmode="numeric" '
        f'pattern="[1-9][0-9]*" maxlength="16" value="{escape(values.get("release", ""))}">'
        '</label><label>Item price strictly under (USD)<input name="under" required '
        'inputmode="decimal" placeholder="Amount in USD" maxlength="10" '
        f'value="{escape(values.get("under", ""))}"></label>'
    )
    for field, label in (("media", "Minimum media grade"), ("sleeve", "Minimum sleeve grade")):
        options = "".join(
            f"<option{' selected' if grade == values.get(field, 'NM') else ''}>{grade}</option>"
            for grade in GRADES
        )
        fields += f'<label>{label}<select name="{field}">{options}</select></label>'
    return fields


class PhoneReviewApp:
    """Single process, threaded WSGI app; tokens are bounded, one-use and principal-bound.

    authenticate(environ) must return an independently verified, stable nonempty owner
    principal or None. Never trust client-provided headers directly. Unconfigured = deny.
    transport is for offline tests only. Constructor does not open DB or make requests.
    """

    def __init__(
        self,
        *,
        database=None,
        backend=None,
        origin,
        authenticate=None,
        transport=None,
        clock=time.time,
    ):
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
        if (database is None) == (backend is None):
            raise ValueError("Use exactly one backend or isolated fixture database.")
        self.database = Path(database).resolve() if database is not None else None
        self.backend = backend
        self.origin, self.host = origin, parsed.netloc
        self.authenticate, self.transport, self.clock = authenticate, transport, clock
        self.tokens = {}
        self.lock = threading.Lock()

    def _backend(self):
        return self.backend or DirectReviewBackend(
            self.database, transport=self.transport, clock=self.clock
        )

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

    def _form(self, principal, values):
        if self._backend().status() == "denied":
            return (
                "<p>Page reads stopped after Discogs denied public listing access. "
                "No retry or fallback. Operator review is required; "
                "this page cannot reset it.</p>"
            )
        token = self._token(principal)
        if token is None:
            return "<p>Too many open forms. Close old tabs and try again after ten minutes.</p>"
        prepared = ""
        if values:
            url = self.origin + PATH + "?" + urlencode(values)
            prepared = (
                "<section><h2>Prepared review link</h2><p>No Discogs read has been made. "
                "Check every prefilled value below before submitting.</p>"
                f'<label>Copy or bookmark this link<input readonly value="{escape(url)}">'
                "</label><small>Opening or sharing the link does not review the listing "
                "or grant access. Owner authentication is still required.</small></section>"
            )
        return (
            prepared + f'<form method="post" action="{PATH}"><input type="hidden" name="csrf" '
            f'value="{token}">{_input_fields(values)}'
            '<button type="submit">Review this listing once</button>'
            "</form><small>One public read. No automatic retries, discovery or purchases. "
            "The price cap excludes shipping, tax and fees.</small>"
            "<details><summary>Prepare a reusable link without reading Discogs</summary>"
            "<p>Choose your criteria here. Leave the listing blank to reuse them for another "
            "copy, or paste one canonical HTTPS Discogs sell/item link.</p>"
            f'<form method="get" action="{PATH}">{_input_fields(values, prepare=True)}'
            '<button type="submit">Prepare link only</button></form></details>'
            "<p>Prepared links put ordinary listing and criteria values in browser history, "
            "bookmarks and copied/shared links, which may sync between devices. "
            "The page does not save a watch or start monitoring. "
            "Nothing is read until you explicitly tap Review this listing once.</p>"
        )

    def _review(self, fields):
        try:
            ident = listing_id(fields["listing"])
        except ListingReadError:
            raise ValueError() from None
        criteria = _criteria(fields)
        if set(criteria) != INPUT_FIELDS - {"listing"}:
            raise ValueError()
        release, limit = int(criteria["release"]), criteria["under"]
        payload = self._backend().read(ident)
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
            if environ.get("HTTP_HOST") != self.host or (
                environ.get("QUERY_STRING", "") and environ.get("REQUEST_METHOD") != "GET"
            ):
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
                        body = self._form(principal, _prefill(environ.get("QUERY_STRING", "")))
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
                    fields = _parse_fields(raw.decode("ascii"), INPUT_FIELDS | {"csrf"})
                    if set(fields) != INPUT_FIELDS | {"csrf"}:
                        raise ValueError()
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
                # A no-referrer policy makes navigation POST Origin null (Fetch §3.2).
                # Keep same-origin forms compatible without leaking cross-origin referrers.
                ("Referrer-Policy", "same-origin"),
                ("X-Content-Type-Options", "nosniff"),
                (
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                ),
            ],
        )
        return [html]
