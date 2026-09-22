"""Bounded live eBay Browse validation: OAuth, one search, normalization, and persistence.

Run with ``EBAY_ENVIRONMENT=sandbox`` (or ``production``) and the matching scoped credentials,
for example ``EBAY_SANDBOX_CLIENT_ID``/``EBAY_SANDBOX_CLIENT_SECRET``. By default, the search
comes from the ``ebay-api-smoke`` monitor in ``config/monitors.toml``.

Pass ``--monitor rap-vinyl-validation`` for a bounded sample of the main discovery monitor.
Output is aggregate and non-identifying because this repository's Actions logs are public.
Exit codes: 0 passed, 1 a validation stage failed.
"""

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from finder.adapters.base import AdapterStats, ListingObservation
from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.config import load_monitor, load_settings
from finder.diagnostics import changed_fields, summarize_listings
from finder.domain import Listing, Monitor
from finder.errors import FinderError
from finder.logging import configure_logging
from finder.persistence import SqlAlchemyRepository
from finder.service import run_scan


class RecordingAdapter:
    """Pass-through adapter that keeps normalized listings for post-scan verification."""

    def __init__(self, inner: EbayAdapter):
        self.inner = inner
        self.listings: list[Listing] = []

    @property
    def stats(self) -> AdapterStats:
        return self.inner.stats

    def search(self, monitor: Monitor) -> Iterator[ListingObservation]:
        for observation in self.inner.search(monitor):
            if observation.listing is not None:
                self.listings.append(observation.listing)
            yield observation


def _finish(report: dict[str, Any], stage: str | None = None, error: str | None = None) -> int:
    if stage is None:
        report["status"] = "passed"
    else:
        report.update(status="failed", failed_stage=stage, error=error)
    print(json.dumps(report, indent=2, default=str))
    return 0 if stage is None else 1


def main(env_file: Path = Path(".env"), monitor_id: str = "ebay-api-smoke") -> int:
    report: dict[str, Any] = {}
    try:
        monitor = load_monitor(Path("config/monitors.toml"), monitor_id)
        settings = load_settings(env_file)
    except FinderError as exc:
        return _finish(report, "configuration", str(exc))
    configure_logging(settings.log_level)
    report.update(
        environment=settings.ebay_environment,
        api_host=settings.ebay_base_url,
        monitor=monitor.id,
        query=monitor.query,
        credentials_loaded=True,
    )

    database_url = settings.database_url.get_secret_value()
    if database_url == "sqlite:///finder.db":
        database_url = "sqlite:///:memory:"
    try:
        repository = SqlAlchemyRepository.from_url(database_url)
    except FinderError as exc:
        return _finish(report, "persistence", str(exc))
    try:
        with EbayClient(settings) as client:
            # Step 2: client-credentials OAuth application token.
            try:
                seconds = client.authenticate()
            except FinderError as exc:
                return _finish(report, "oauth", str(exc))
            report["oauth"] = {"status": "ok", "token_valid_for_seconds": int(seconds)}

            # Step 3: bounded Browse search through the production scan path.
            adapter = RecordingAdapter(EbayAdapter(client))
            try:
                summary = run_scan(monitor, adapter, repository)
            except FinderError as exc:
                return _finish(report, "browse", str(exc))
        report["browse"] = {
            "status": summary.status,
            "fetched": summary.fetched,
            "normalized_and_stored": summary.new + summary.updated,
            "skipped": summary.skipped_invalid,
            "skip_reasons": summary.skip_reasons,
            "detail_enrichment_failures": summary.partial_details,
            "limit_reached": summary.limit_reached,
        }
        if summary.status != "completed":
            return _finish(report, "browse", summary.error)
        if summary.fetched == 0:
            return _finish(
                report,
                "browse",
                "Search succeeded but returned no listings; nothing to normalize.",
            )

        # Step 4: normalization and persistence round trip.
        report["normalization"] = summarize_listings(adapter.listings)
        if not adapter.listings:
            return _finish(report, "normalization", "No fetched listing survived normalization.")
        wrong_environment = sum(
            listing.source_metadata.get("environment") != settings.ebay_environment
            for listing in adapter.listings
        )
        if wrong_environment:
            return _finish(
                report,
                "normalization",
                f"{wrong_environment} listing(s) carry the wrong environment tag.",
            )
        if not any(listing.current_price is not None for listing in adapter.listings):
            return _finish(report, "normalization", "No listing produced a usable price.")
        drift: set[str] = set()
        missing = 0
        missing_observations = 0
        observation_drift: set[str] = set()
        for listing in adapter.listings:
            stored = repository.get(listing.marketplace, listing.marketplace_item_id)
            observations = repository.get_observations(
                listing.marketplace, listing.marketplace_item_id
            )
            current_observation = next(
                (
                    item
                    for item in observations
                    if item.last_observed_at == listing.last_observed_at
                ),
                None,
            )
            if current_observation is None:
                missing_observations += 1
            else:
                observation_drift.update(changed_fields(listing, current_observation))
            if stored is None:
                missing += 1
            else:
                # The current row retains the first scan's timestamp on repeat scans.
                expected = listing.model_copy(
                    update={"first_observed_at": stored.first_observed_at}
                )
                drift.update(changed_fields(expected, stored))
                if (
                    not observations
                    or stored.first_observed_at != observations[0].first_observed_at
                ):
                    drift.add("first_observed_at")
        report["persistence"] = {
            "missing": missing,
            "fields_changed_on_reload": sorted(drift),
            "missing_observations": missing_observations,
            "observation_fields_changed_on_reload": sorted(observation_drift),
        }
        if missing or drift or missing_observations or observation_drift:
            return _finish(report, "persistence", "Stored listings do not round-trip exactly.")
        return _finish(report)
    finally:
        repository.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor", default="ebay-api-smoke")
    args = parser.parse_args()
    raise SystemExit(main(monitor_id=args.monitor))
