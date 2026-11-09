"""Fixed public known-listing detail read for a candidate dry run.

This module does not discover listing IDs or run a background reader. Reported
shipping is evidence only; it is never a delivered destination quote.
"""

import json
import os
import socket
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from finder.discogs_completeness import current_release_media
from finder.discogs_listing import ListingReader, ListingReadError, listing_id, observation
from finder.discogs_rules import Observation, StrictInput, release_id

HOSTED_SOCKET = Path("/var/lib/sniper/browser-bridge/sniper-read-only.sock")
MAX_RESPONSE = 262_144


@dataclass(frozen=True)
class VerifiedPublicDetail:
    observation: Observation
    requested_release: int
    requested_listing: str
    route: str
    started_at: float
    completed_at: float
    reported_shipping: str | None
    shipping_blocked: bool


@dataclass(frozen=True)
class VerifiedHostedDetail:
    observation: Observation
    requested_release: int
    requested_listing: str
    route: str
    started_at: float
    completed_at: float
    reported_shipping_text: str | None
    cart_state: Literal["addable", "already_in_cart", "unknown"]
    seller_handle: str | None = None


class HostedDetailPayload(StrictInput):
    schema_name: Literal["discogs_listing_detail_dom_v1"] = Field(alias="schema")
    source: Literal["discogs_rendered_listing"]
    status: Literal["observed"]
    complete: Literal[True]
    release_id: int = Field(gt=0, le=9007199254740991)
    listing_id: str = Field(pattern=r"^[1-9][0-9]{0,15}$")
    url: str
    observed_at: str
    media: str | None
    sleeve: str | None
    seller_percent: float | None = Field(ge=0, le=100)
    feedback: int | None = Field(ge=0)
    seller_handle: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    item: str | None
    currency: None
    reported_shipping_text: str | None = Field(max_length=80)
    seller_comments: str | None = Field(max_length=4096)
    seller_comments_supported: bool
    available: bool
    cart_state: Literal["addable", "already_in_cart", "unknown"]
    purchase_allowed: Literal[False]


class HostedListingCandidateReader:
    """One exact fixed-action read through the existing private browser socket."""

    def __init__(self, path=HOSTED_SOCKET, *, clock=time.time, catalog=None):
        if catalog is not None and type(catalog) is not ListingReader:
            raise ValueError("Use the fixed public release catalog reader.")
        self.path, self.clock, self.catalog = Path(path), clock, catalog

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

    def __call__(self, release, ident):
        release, ident = release_id(release), listing_id(ident)
        if not self._private():
            raise ListingReadError("Private hosted listing reader is unavailable.")
        request = (
            json.dumps(
                {"action": "observe_listing", "release_id": str(release), "listing_id": str(ident)}
            )
            + "\n"
        )
        started = self.clock()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(35)
                connection.connect(str(self.path))
                connection.sendall(request.encode())
                with connection.makefile("rb") as stream:
                    raw = stream.readline(MAX_RESPONSE + 2)
        except OSError:
            raise ListingReadError("Private hosted listing read failed.") from None
        completed = self.clock()
        if not raw.endswith(b"\n") or len(raw) > MAX_RESPONSE + 1:
            raise ListingReadError("Private hosted listing response was incomplete.")
        try:
            reply = json.loads(raw)
            if (
                type(reply) is dict
                and reply.get("source") == "hosted_browser_read_only"
                and reply.get("purchase_allowed") is False
                and reply.get("stop_required") is True
                and reply.get("status") in {"blocked", "incomplete"}
            ):
                code = reply.get("code")
                if code in {
                    "access_restricted",
                    "auth_required",
                    "browser_context_unavailable",
                    "hosted_read_failed",
                    "http_response_unavailable",
                    "listing_cooldown",
                    "listing_not_ready",
                    "listing_observation_unverified",
                    "navigation_or_read_failed",
                    "page_changed_during_read",
                    "rate_limited",
                    "read_cancelled",
                    "read_in_progress",
                    "request_timed_out",
                    "service_unavailable",
                    "unexpected_http_status",
                    "unexpected_redirect",
                }:
                    raise ListingReadError(f"Private hosted listing read stopped: {code}.")
            if (
                type(reply) is not dict
                or reply.get("source") != "hosted_browser_read_only"
                or reply.get("purchase_allowed") is not False
                or reply.get("stop_required") is not True
                or reply.get("status") != "observed"
            ):
                raise ValueError("blocked")
            page = HostedDetailPayload.model_validate(reply.get("observation"))
            timestamp = datetime.fromisoformat(page.observed_at.replace("Z", "+00:00"))
            if (
                page.release_id != release
                or page.listing_id != str(ident)
                or page.url
                not in {
                    f"https://www.discogs.com/shop/item/{ident}",
                    f"https://www.discogs.com/sell/item/{ident}",
                }
                or timestamp.tzinfo is None
                or not started <= timestamp.timestamp() <= completed + 1
            ):
                raise ValueError("identity_or_time")
        except ListingReadError:
            raise
        except (TypeError, ValueError, ValidationError):
            raise ListingReadError("Private hosted listing response was unverified.") from None
        # Item-page format summaries are not authoritative playable quantities.
        # A separately configured, fixed public catalog reader may attach only
        # fresh exact-release formats. It cannot supply currency or a quote.
        metadata = provenance = None
        if self.catalog is not None:
            catalog = self.catalog.read_release_media(release)
            completed = self.clock()
            if catalog is not None:
                metadata = current_release_media(
                    *catalog, release, now=datetime.fromtimestamp(completed, UTC)
                )
                provenance = dict(catalog[1]) if metadata is not None else None
        return VerifiedHostedDetail(
            observation=Observation(
                source="discogs_rendered_listing",
                listing_id=str(ident),
                release_id=release,
                media=page.media,
                sleeve=page.sleeve,
                seller_percent=page.seller_percent,
                feedback=page.feedback,
                item=page.item,
                currency=None,
                observed_at=page.observed_at,
                available=page.available,
                pressing_evidence="unknown",
                seller_comments=page.seller_comments,
                seller_comments_supported=page.seller_comments_supported,
                release_media_metadata=metadata,
                release_media_provenance=provenance,
            ),
            requested_release=release,
            requested_listing=str(ident),
            route=f"/sell/item/{ident}",
            started_at=started,
            completed_at=completed,
            reported_shipping_text=page.reported_shipping_text,
            cart_state=page.cart_state,
            seller_handle=page.seller_handle,
        )


class PublicKnownListingCandidateReader:
    """One official fixed-route read of an ID supplied by a complete snapshot."""

    def __init__(self, *, reader=None, clock=time.time):
        self.reader = reader or ListingReader()
        self.clock = clock

    def __call__(self, release, ident):
        release, ident = release_id(release), listing_id(ident)
        started = self.clock()
        listing = self.reader.read_for_release(ident, release)
        completed = self.clock()
        if listing["release_id"] != release or listing["listing_id"] != str(ident):
            raise ListingReadError("Known listing did not match the exact release and listing.")
        return VerifiedPublicDetail(
            observation=observation(listing, now=completed),
            requested_release=release,
            requested_listing=str(ident),
            route=f"/marketplace/listings/{ident}",
            started_at=started,
            completed_at=completed,
            reported_shipping=listing["reported_shipping"],
            shipping_blocked=listing["shipping_blocked"],
        )
