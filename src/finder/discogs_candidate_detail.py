"""Fixed public known-listing detail read for a candidate dry run.

This module does not discover listing IDs or run a background reader. Reported
shipping is evidence only; it is never a delivered destination quote.
"""

import time
from dataclasses import dataclass

from finder.discogs_listing import ListingReader, ListingReadError, listing_id, observation
from finder.discogs_rules import Observation, release_id


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
