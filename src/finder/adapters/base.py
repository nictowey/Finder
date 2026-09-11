from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from finder.domain import Listing, Monitor


@dataclass
class AdapterStats:
    fetched: int = 0
    limit_reached: bool = False


@dataclass(frozen=True)
class ListingObservation:
    listing: Listing | None = None
    skip_reason: str | None = None


class MarketplaceAdapter(Protocol):
    stats: AdapterStats

    def search(self, monitor: Monitor) -> Iterator[ListingObservation]: ...
