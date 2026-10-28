"""Dormant fixed-scope transport for a future private hosted cart helper.

No listener or socket directory is created here. The existing read-only browser
socket is deliberately not used. The helper-side action and live hookup are not
configured; constructing this adapter defaults to unavailable.

Future mount: host /var/lib/sniper/cart-bridge to browser /cart-bridge. The
browser service owns the 2710 directory and 0660 socket (UID 10001, group
sniper 113); the web dispatcher must hold that existing group. No mount,
group membership or socket is configured by this module.
"""

import hashlib
import json
import os
import re
import socket
import stat
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from finder.discogs_candidate_detail import VerifiedHostedDetail
from finder.discogs_candidate_pipeline import observation_fingerprint
from finder.discogs_hosted_cartprep import CartInspectionAuthorization

CART_SOCKET = Path("/var/lib/sniper/cart-bridge/sniper-cart-prepare.sock")
SHARED_BROWSER_UID = 10001
SHARED_GROUP = 113
MAX_REQUEST = 1024
MAX_RESPONSE = 4096
TIMEOUT = 15
ITEM_AMOUNT = re.compile(r"^(?:0|[1-9][0-9]{0,6})\.[0-9]{2}$")
SAFE_INTEGER = 9007199254740991


def visible_item_digest(detail):
    """Hash only fields available from the same rendered item DOM in JavaScript."""
    if not isinstance(detail, VerifiedHostedDetail):
        return None
    observation = detail.observation
    if (
        observation.source != "discogs_rendered_listing"
        or type(observation.release_id) is not int
        or type(observation.listing_id) is not str
        or type(detail.seller_handle) is not str
        or type(observation.available) is not bool
        or type(observation.seller_comments_supported) is not bool
        or type(observation.item) is not str
        or not ITEM_AMOUNT.fullmatch(observation.item)
        or observation.feedback is not None
        and (type(observation.feedback) is not int or not 0 <= observation.feedback <= SAFE_INTEGER)
    ):
        return None
    try:
        rating = (
            None
            if observation.seller_percent is None
            else Decimal(str(observation.seller_percent)) * Decimal(1_000_000)
        )
        if rating is not None and (
            rating != rating.to_integral_value() or not 0 <= rating <= SAFE_INTEGER
        ):
            return None
    except (InvalidOperation, ValueError):
        return None
    projection = {
        "release_id": observation.release_id,
        "listing_id": observation.listing_id,
        "seller_handle": detail.seller_handle,
        "media": observation.media,
        "sleeve": observation.sleeve,
        "seller_percent_micro": int(rating) if rating is not None else None,
        "feedback": observation.feedback,
        "item": observation.item,
        "currency": observation.currency,
        "seller_comments": observation.seller_comments,
        "seller_comments_supported": observation.seller_comments_supported,
        "available": observation.available,
        "cart_state": detail.cart_state,
        "reported_shipping_text": detail.reported_shipping_text,
    }
    payload = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class HostedCartPrepareAdapter:
    """Send one exact, no-order cart preparation request when explicitly enabled."""

    def __init__(
        self,
        authorization,
        *,
        detail=None,
        path=CART_SOCKET,
        enabled=False,
        clock=time.time,
        socket_factory=socket.socket,
    ):
        if not isinstance(authorization, CartInspectionAuthorization):
            raise ValueError("An exact cart inspection authorization is required.")
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        if Path(path) != CART_SOCKET:
            raise ValueError("Use the dedicated fixed cart socket only.")
        self.authorization = authorization
        self.detail = detail
        self.item_digest = None
        if detail is not None:
            if (
                not isinstance(detail, VerifiedHostedDetail)
                or detail.observation.source != "discogs_rendered_listing"
                or detail.requested_release != authorization.release_id
                or detail.requested_listing != authorization.listing_id
                or detail.route != f"/sell/item/{authorization.listing_id}"
                or detail.seller_handle != authorization.seller_handle
                or observation_fingerprint(detail.observation)
                != authorization.observation_fingerprint
            ):
                raise ValueError("Hosted detail does not match the scoped candidate review.")
            self.item_digest = visible_item_digest(detail)
            if self.item_digest is None:
                raise ValueError("Hosted visible item projection is unsupported.")
        self.path = Path(path)
        self.enabled = enabled
        self.clock = clock
        self.socket_factory = socket_factory

    def _private(self):
        try:
            parent = self.path.parent.lstat()
            item = self.path.lstat()
            if not stat.S_ISDIR(parent.st_mode) or not stat.S_ISSOCK(item.st_mode):
                return False
            if self.path == CART_SOCKET:
                return (
                    parent.st_uid == item.st_uid == SHARED_BROWSER_UID
                    and parent.st_gid == item.st_gid == os.getgid() == SHARED_GROUP
                    and stat.S_IMODE(parent.st_mode) == 0o2710
                    and stat.S_IMODE(item.st_mode) == 0o660
                )
            return (
                parent.st_uid == item.st_uid == os.getuid()
                and stat.S_IMODE(parent.st_mode) == 0o700
                and stat.S_IMODE(item.st_mode) == 0o600
            )
        except OSError:
            return False

    def prepare(self, *, release_id, listing_id):
        """Return ``cart_observed`` only for one exact reconciled row.

        Every other condition is uncertain to the caller's durable claim. This
        method never retries or submits an order.
        """
        grant = self.authorization
        if (
            not self.enabled
            or self.item_digest is None
            or type(release_id) is not int
            or type(listing_id) is not str
            or release_id != grant.release_id
            or listing_id != grant.listing_id
            or not grant.issued_at <= self.clock() < grant.expires_at
            or not self._private()
        ):
            return "uncertain"
        scope = grant.model_dump()
        request = {
            "schema": "discogs_hosted_cart_prepare_v1",
            "action": "prepare_cart_quote_only",
            "scope": scope,
            "item_digest": self.item_digest,
            "purchase_allowed": False,
        }
        raw_request = (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(raw_request) > MAX_REQUEST:
            return "uncertain"
        try:
            with self.socket_factory(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(TIMEOUT)
                connection.connect(str(self.path))
                connection.sendall(raw_request)
                with connection.makefile("rb") as stream:
                    raw = stream.readline(MAX_RESPONSE + 2)
        except (OSError, TimeoutError):
            return "uncertain"
        if len(raw) > MAX_RESPONSE + 1 or not raw.endswith(b"\n"):
            return "uncertain"
        try:
            reply = json.loads(raw)
            returned_scope = reply.get("scope") if type(reply) is dict else None
            if (
                type(reply) is not dict
                or set(reply)
                != {
                    "schema",
                    "status",
                    "code",
                    "scope",
                    "item_digest",
                    "cart",
                    "stop_required",
                    "purchase_allowed",
                    "order_submission",
                }
                or reply["schema"] != "discogs_hosted_cart_prepare_v1"
                or reply["status"] != "observed"
                or reply["code"] != "cart_observed_after_add"
                or reply["item_digest"] != self.item_digest
                or type(returned_scope) is not dict
                or json.dumps(returned_scope, sort_keys=True, separators=(",", ":"))
                != json.dumps(scope, sort_keys=True, separators=(",", ":"))
                or reply["stop_required"] is not True
                or reply["purchase_allowed"] is not False
                or reply["order_submission"] != "disabled"
                or not grant.issued_at <= self.clock() < grant.expires_at
            ):
                return "uncertain"
            cart = reply["cart"]
            if (
                type(cart) is not dict
                or set(cart)
                != {
                    "status",
                    "item_row_count",
                    "listing_id",
                    "single_item_single_seller_summary_observed",
                    "identity_provenance",
                    "unrelated_changes",
                }
                or cart["status"] != "observed"
                or type(cart["item_row_count"]) is not int
                or cart["item_row_count"] != 1
                or type(cart["listing_id"]) is not str
                or cart["listing_id"] != grant.listing_id
                or cart["single_item_single_seller_summary_observed"] is not True
                or cart["identity_provenance"] != "verified_pre_click_item_plus_post_add_cart_link"
                or cart["unrelated_changes"] is not False
            ):
                return "uncertain"
            return "cart_observed"
        except (ValueError, TypeError, KeyError):
            return "uncertain"
