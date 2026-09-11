import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.config import load_monitor, load_settings
from finder.errors import ConfigurationError, FinderError
from finder.logging import configure_logging
from finder.persistence import SqlAlchemyListingRepository
from finder.service import run_scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finder", description="Finder marketplace ingestion")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Scan a configured monitor once")
    scan.add_argument("--config", type=Path, default=Path("config/monitors.toml"))
    scan.add_argument("--monitor", default="rap-vinyl")
    scan.add_argument("--env-file", type=Path, default=Path(".env"))
    scan.add_argument("--json", action="store_true", help="Print machine-readable summary")
    args = parser.parse_args(argv)
    configure_logging()
    try:
        monitor = load_monitor(args.config, args.monitor)
        if monitor.marketplace != "ebay":
            raise ConfigurationError(
                f"No adapter implemented for marketplace '{monitor.marketplace}'."
            )
        settings = load_settings(args.env_file)
        configure_logging(settings.log_level)
        # Isolate Sandbox inventory from real observations even with identical IDs.
        database_url = settings.database_url.get_secret_value()
        if settings.ebay_environment == "sandbox" and database_url == "sqlite:///finder.db":
            database_url = "sqlite:///finder-sandbox.db"
        repository = SqlAlchemyListingRepository.from_url(database_url)
        try:
            with EbayClient(settings) as client:
                summary = run_scan(monitor, EbayAdapter(client), repository)
        finally:
            repository.close()
    except FinderError as exc:
        print(f"Finder: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Finder: interrupted; previously committed listings remain stored.", file=sys.stderr)
        return 130
    if args.json:
        print(json.dumps(asdict(summary)))
    else:
        print(f"Scan: {summary.status}")
        print(f"Listings fetched: {summary.fetched}")
        print(f"New listings: {summary.new}")
        print(f"Updated listings: {summary.updated}")
        print(f"Skipped/invalid listings: {summary.skipped_invalid}")
        print(f"Total listings currently stored: {summary.total_stored}")
        if summary.partial_details:
            print(f"Listings with failed detail enrichment: {summary.partial_details}")
        if summary.limit_reached:
            print("Configured scan limit reached; additional results may exist.")
        if summary.unprocessed:
            print(f"Fetched but unprocessed: {summary.unprocessed}")
        if summary.error:
            print(f"Error: {summary.error}")
    return 1 if summary.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
