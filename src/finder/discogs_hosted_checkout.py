"""Server-side, read-only checkout preflight for an already-open Discogs cart.

The only browser operation is the existing fixed ``current_cart`` socket action.
No order, cart, payment, agreement, navigation, or credential capability exists here.
"""

import json
import logging
import os
import re
import socket
import stat
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from finder.discogs_candidate_detail import HOSTED_SOCKET, MAX_RESPONSE
from finder.discogs_candidate_pipeline import CandidateStopped
from finder.discogs_rules import amount

LISTING_ID = re.compile(r"[1-9][0-9]{0,15}\Z", re.ASCII)
MONEY = re.compile(r"[0-9]{1,7}(?:\.[0-9]{1,2})?\Z", re.ASCII)
OPERATOR_CART_LISTING = "3526572746"
CART_GRANT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
    re.ASCII,
)
LOGGER = logging.getLogger(__name__)
# The bridge may return a stop code derived from page inspection. Never log its
# unchecked value: an unexpected response could otherwise put account text in logs.
SAFE_CART_STOP_CODES = frozenset(
    {
        "access_restricted",
        "browser_context_unavailable",
        "browser_pages_unavailable",
        "cart_read_failed",
        "cart_read_timeout",
        "current_url_changed",
        "current_url_not_allowed",
        "exact_current_page_not_unique",
        "expected_listing_not_observed",
        "hosted_read_failed",
        "observation_too_large",
        "observation_unserializable",
        "operator_acknowledgement_required",
        "operator_recovery_unavailable",
        "page_changed_during_read",
        "read_in_progress",
        "request_timed_out",
        "unsupported_address_shape",
        "unsupported_cart_dom",
        "unsupported_cart_quantity",
        "unsupported_checkout_controls",
        "unsupported_operator_recovery",
        "unsupported_page_interface",
        "unsupported_quantity_controls",
        "unsupported_quote_shape",
    }
)


class CheckoutStopped(RuntimeError):
    """A sanitized stop code; never a browser or account payload."""


@dataclass(frozen=True)
class CartEvidence:
    listing_id: str
    subtotal: tuple[str, str] | None
    shipping: tuple[str, str] | None
    tax: tuple[str, str] | None
    fees: tuple[str, str] | None
    displayed_total: tuple[str, str] | None
    shipping_method_label: str | None
    tax_hidden_unknown: bool
    destination_verified: bool = False
    final_quote_verified: bool = False
    purchase_allowed: bool = False


def _money(value):
    if value is None:
        return None
    if (
        type(value) is not dict
        or set(value) != {"amount", "currency"}
        or type(value["amount"]) is not str
        or not MONEY.fullmatch(value["amount"])
        or value["currency"] not in {"USD", "EUR", "GBP", "CAD", "AUD"}
    ):
        raise CheckoutStopped("cart_quote_unverified")
    return amount(value["amount"]), value["currency"]


class HostedCartReadOnlyReader:
    """Read one current cart through the approved owner-only fixed-action socket."""

    def __init__(self, path=HOSTED_SOCKET, *, operator_one_use=False, operator_grant_id=None):
        if type(operator_one_use) is not bool:
            raise ValueError("operator_one_use must be boolean")
        if operator_grant_id is not None and (
            not operator_one_use
            or type(operator_grant_id) is not str
            or not CART_GRANT_ID.fullmatch(operator_grant_id)
        ):
            raise ValueError("operator_grant_id must be a one-use UUID")
        self.path = Path(path)
        self.operator_one_use = operator_one_use
        self.operator_grant_id = operator_grant_id

    def _private(self):
        try:
            info = self.path.lstat()
            if not stat.S_ISSOCK(info.st_mode):
                return False
            if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600:
                return True
            if self.path != HOSTED_SOCKET:
                return False
            parent = self.path.parent.lstat()
            return (
                stat.S_ISDIR(parent.st_mode)
                and parent.st_uid == info.st_uid == 10001
                and parent.st_gid == info.st_gid == os.getgid()
                and stat.S_IMODE(parent.st_mode) == 0o2710
                and stat.S_IMODE(info.st_mode) == 0o660
            )
        except OSError:
            return False

    def __call__(self, listing_id):
        if type(listing_id) is not str or not LISTING_ID.fullmatch(listing_id):
            raise CheckoutStopped("invalid_listing_id")
        if self.operator_one_use and listing_id != OPERATOR_CART_LISTING:
            raise CheckoutStopped("operator_cart_listing_mismatch")
        if not self._private():
            raise CheckoutStopped("private_cart_reader_unavailable")
        payload = {
            "action": "operator_current_cart" if self.operator_one_use else "current_cart",
            "listing_id": listing_id,
        }
        if self.operator_grant_id is not None:
            payload["grant_id"] = self.operator_grant_id
        request = json.dumps(payload) + "\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(15)
                connection.connect(str(self.path))
                connection.sendall(request.encode())
                with connection.makefile("rb") as stream:
                    raw = stream.readline(MAX_RESPONSE + 2)
        except OSError:
            raise CheckoutStopped("private_cart_read_failed") from None
        if len(raw) > MAX_RESPONSE + 1 or not raw.endswith(b"\n"):
            raise CheckoutStopped("cart_response_incomplete")
        try:
            reply = json.loads(raw)
            if (
                type(reply) is not dict
                or reply.get("source") != "hosted_browser_read_only"
                or reply.get("stop_required") is not True
                or reply.get("purchase_allowed") is not False
                or reply.get("status") != "blocked"
            ):
                raise CheckoutStopped("cart_response_unverified")
            code = reply.get("code")
            if type(code) is not str or code not in {
                "final_quote_unverified",
                "missing_address_and_unknown_final_quote",
            }:
                safe_code = (
                    code if type(code) is str and code in SAFE_CART_STOP_CODES else "unknown"
                )
                LOGGER.warning("hosted_cart_read_stopped code=%s", safe_code)
                raise CheckoutStopped(f"cart_read_stopped:{safe_code}")
            observations = reply.get("observations")
            if (
                type(observations) is not list
                or not all(type(item) is str for item in observations)
                or not {"expected_listing_link_observed", "single_cart_row_observed"}.issubset(
                    observations
                )
            ):
                raise CheckoutStopped("cart_identity_unverified")
            quote = reply.get("observed_quote")
            method = reply.get("observed_shipping_method")
            if (
                type(quote) is not dict
                or quote.get("source") != "visible_cart"
                or quote.get("final_quote_verified") is not False
                or type(method) is not dict
                or method.get("source") != "visible_cart"
                or method.get("selection_verified") is not False
            ):
                raise CheckoutStopped("cart_quote_unverified")
            label = method.get("selected_label")
            if label is not None and (
                type(label) is not str
                or not 0 < len(label) <= 100
                or any(ord(char) < 32 or ord(char) == 127 for char in label)
            ):
                raise CheckoutStopped("cart_shipping_unverified")
            return CartEvidence(
                listing_id=listing_id,
                subtotal=_money(quote.get("subtotal")),
                shipping=_money(quote.get("shipping")),
                tax=_money(quote.get("tax")),
                fees=_money(quote.get("fees")),
                displayed_total=_money(quote.get("total")),
                shipping_method_label=label,
                tax_hidden_unknown="tax_hidden_unknown" in observations,
            )
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            raise CheckoutStopped("cart_response_unverified") from None


class HostedCheckoutPreflight:
    """Recheck a durable candidate and one-copy claim around a cart read.

    This is deliberately a terminal, no-click preflight. A final payable quote,
    destination/payment binding and a separate purchase authorization are absent.
    """

    def __init__(self, candidates, reader=None):
        self.candidates = candidates
        self.reader = reader or HostedCartReadOnlyReader()

    def inspect(self, release_id, listing_id, idempotency_key):
        try:
            before = self.candidates.review_bundle(release_id, listing_id, idempotency_key)
        except CandidateStopped:
            raise CheckoutStopped("candidate_or_claim_unavailable") from None
        if before["claim"]["state"] != "cart_observed":
            raise CheckoutStopped("cart_outcome_requires_reconciliation")
        evidence = self.reader(listing_id)
        if (
            type(evidence) is not CartEvidence
            or evidence.listing_id != listing_id
            or evidence.purchase_allowed
            or evidence.final_quote_verified
            or evidence.destination_verified
        ):
            raise CheckoutStopped("cart_evidence_unverified")
        try:
            after = self.candidates.review_bundle(release_id, listing_id, idempotency_key)
        except CandidateStopped:
            raise CheckoutStopped("candidate_changed_during_cart_read") from None
        if before != after or after["claim"]["state"] != "cart_observed":
            raise CheckoutStopped("candidate_changed_during_cart_read")
        target = next(
            (item for item in after["snapshot"]["targets"] if item["release_id"] == release_id),
            None,
        )
        if target is None or evidence.listing_id != listing_id:
            raise CheckoutStopped("cart_identity_unverified")
        total = evidence.displayed_total
        if total and total[1] != target["currency"]:
            raise CheckoutStopped("cart_currency_mismatch")
        if total and Decimal(total[0]) > Decimal(target["max_total"]):
            raise CheckoutStopped("observed_total_above_ceiling")
        return {
            "status": "blocked",
            "code": "final_payable_quote_unverified",
            "source": "hosted_cart_read_only",
            "release_id": release_id,
            "listing_id": listing_id,
            "rules_revision": after["snapshot"]["revision"],
            "claim_state": "cart_observed",
            "evidence": evidence,
            "unverified": (
                "tax_treatment_and_final_payable_total",
                "destination_match",
                "payment_reference",
                "selected_shipping_finality",
                "purchase_authorization",
            ),
            "order_submission": "unavailable",
            "purchase_allowed": False,
        }
