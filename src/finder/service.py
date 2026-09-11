import logging
from collections import Counter
from dataclasses import asdict, dataclass, field

from finder.adapters.base import MarketplaceAdapter
from finder.domain import Monitor
from finder.errors import ConfigurationError, FinderError
from finder.persistence import ListingRepository

log = logging.getLogger("finder.scan")


@dataclass
class ScanSummary:
    monitor: str
    fetched: int = 0
    new: int = 0
    updated: int = 0
    skipped_invalid: int = 0
    total_stored: int | None = None
    partial_details: int = 0
    limit_reached: bool = False
    unprocessed: int = 0
    status: str = "completed"
    skip_reasons: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def run_scan(
    monitor: Monitor,
    adapter: MarketplaceAdapter,
    repository: ListingRepository,
) -> ScanSummary:
    summary = ScanSummary(monitor=monitor.id)
    reasons: Counter[str] = Counter()
    log.info("scan_started", extra={"fields": {"monitor": monitor.id}})
    try:
        for observation in adapter.search(monitor):
            if observation.listing is None:
                summary.skipped_invalid += 1
                reasons[observation.skip_reason or "invalid_listing"] += 1
                log.warning(
                    "listing_skipped", extra={"fields": {"reason": observation.skip_reason}}
                )
                continue
            result = repository.upsert(observation.listing)
            if result == "new":
                summary.new += 1
            else:
                summary.updated += 1
            if "details_unavailable" in observation.listing.quality_flags:
                summary.partial_details += 1
            if observation.listing.quality_flags:
                log.info(
                    "listing_partial_data",
                    extra={
                        "fields": {
                            "item_id": observation.listing.marketplace_item_id,
                            "flags": observation.listing.quality_flags,
                        }
                    },
                )
    except ConfigurationError:
        raise
    except FinderError as exc:
        summary.status = "failed"
        summary.error = str(exc)
        log.error("scan_failed", extra={"fields": {"error_type": type(exc).__name__}})
    summary.fetched = adapter.stats.fetched
    summary.limit_reached = adapter.stats.limit_reached
    summary.unprocessed = summary.fetched - summary.new - summary.updated - summary.skipped_invalid
    summary.skip_reasons = dict(reasons)
    try:
        summary.total_stored = repository.count()
    except FinderError as exc:
        summary.status = "failed"
        summary.error = summary.error or str(exc)
    log.info("scan_finished", extra={"fields": asdict(summary)})
    return summary
