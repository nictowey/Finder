import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.engine import make_url

from finder.adapters.discogs.adapter import DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.config import load_discogs_settings, load_monitor, load_settings
from finder.errors import ConfigurationError, FinderError
from finder.logging import configure_logging
from finder.matching import score_variant
from finder.persistence import SqlAlchemyRepository
from finder.service import run_scan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finder", description="Finder marketplace discovery")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Scan a configured marketplace monitor once")
    scan.add_argument("--config", type=Path, default=Path("config/monitors.toml"))
    scan.add_argument("--monitor", default="rap-vinyl")
    scan.add_argument("--env-file", type=Path, default=Path(".env"))
    scan.add_argument("--json", action="store_true", help="Print machine-readable summary")

    catalog = commands.add_parser(
        "catalog-search", help="Search Discogs catalog releases and store normalized variants"
    )
    catalog.add_argument("query", help="Artist, title, barcode, or catalog number")
    catalog.add_argument("--limit", type=int, default=5, choices=range(1, 26))
    catalog.add_argument("--env-file", type=Path, default=Path(".env"))
    catalog.add_argument("--json", action="store_true", help="Print machine-readable results")

    match = commands.add_parser(
        "match", help="Find Discogs release candidates for one stored marketplace listing"
    )
    match.add_argument("--marketplace", default="ebay")
    match.add_argument("--item-id", required=True)
    match.add_argument("--limit", type=int, default=10, choices=range(1, 26))
    match.add_argument("--env-file", type=Path, default=Path(".env"))
    match.add_argument("--json", action="store_true", help="Print machine-readable results")
    return parser


def _scan(args: argparse.Namespace) -> int:
    monitor = load_monitor(args.config, args.monitor)
    if monitor.marketplace != "ebay":
        raise ConfigurationError(f"No adapter implemented for marketplace '{monitor.marketplace}'.")
    settings = load_settings(args.env_file)
    configure_logging(settings.log_level)
    database_url = settings.database_url.get_secret_value()
    if settings.ebay_environment == "production" and make_url(
        database_url
    ).get_backend_name() not in ("postgres", "postgresql"):
        raise ConfigurationError(
            "Production eBay scans require the shared PostgreSQL database "
            "used by the deletion endpoint."
        )
    if settings.ebay_environment == "sandbox" and database_url == "sqlite:///finder.db":
        database_url = "sqlite:///finder-sandbox.db"
    repository = SqlAlchemyRepository.from_url(database_url)
    try:
        with EbayClient(settings) as client:
            summary = run_scan(monitor, EbayAdapter(client), repository)
    finally:
        repository.close()
    if args.json:
        print(json.dumps(asdict(summary)))
    else:
        print(f"Scan: {summary.status}")
        print(f"Listings fetched: {summary.fetched}")
        print(f"New listings: {summary.new}")
        print(f"Updated listings: {summary.updated}")
        print(f"Skipped/invalid listings: {summary.skipped_invalid}")
        if summary.suppressed_deleted:
            print(f"Suppressed deleted sellers: {summary.suppressed_deleted}")
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


def _catalog_components(args: argparse.Namespace):
    settings = load_discogs_settings(args.env_file)
    configure_logging(settings.log_level)
    repository = SqlAlchemyRepository.from_url(settings.database_url.get_secret_value())
    return settings, repository


def _catalog_search(args: argparse.Namespace) -> int:
    settings, repository = _catalog_components(args)
    try:
        with DiscogsClient(settings) as client:
            provider = DiscogsCatalogProvider(client)
            variants = provider.search_releases(args.query, limit=args.limit)
            created = updated = 0
            for variant in variants:
                repository.upsert_product(provider.product_for(variant))
                result = repository.upsert_variant(variant)
                created += result == "new"
                updated += result == "updated"
    finally:
        repository.close()
    payload = {
        "query": args.query,
        "attribution": "Data provided by Discogs",
        "attribution_url": "https://www.discogs.com",
        "results": len(variants),
        "new_variants": created,
        "updated_variants": updated,
        "variants": [
            {
                "discogs_release_id": variant.catalog_variant_id,
                "discogs_master_id": variant.catalog_product_id,
                "artist": ", ".join(variant.artists),
                "title": variant.title,
                "year": variant.release_year,
                "country": variant.country,
                "url": variant.resource_url,
            }
            for variant in variants
        ],
    }
    if args.json:
        print(json.dumps(payload))
    else:
        print("Data provided by Discogs: https://www.discogs.com")
        print(f"Discogs releases found: {payload['results']}")
        print(f"New variants stored: {created}")
        print(f"Updated variants stored: {updated}")
        for variant in payload["variants"]:
            identity = " — ".join(part for part in (variant["artist"], variant["title"]) if part)
            print(
                f"{variant['discogs_release_id']}: {identity} "
                f"({variant['year'] or 'year unknown'}) — {variant['url']}"
            )
    return 0


def _match(args: argparse.Namespace) -> int:
    settings, repository = _catalog_components(args)
    try:
        listing = repository.get(args.marketplace, args.item_id)
        if listing is None:
            raise ConfigurationError(
                f"No stored {args.marketplace} listing with item ID '{args.item_id}'."
            )
        with DiscogsClient(settings) as client:
            provider = DiscogsCatalogProvider(client)
            variants = provider.search_releases(listing.title, limit=args.limit)
            candidates = []
            for variant in variants:
                repository.upsert_product(provider.product_for(variant))
                repository.upsert_variant(variant)
                candidates.append(score_variant(listing, variant))
            candidates.sort(key=lambda candidate: candidate.score, reverse=True)
            repository.replace_candidates(
                listing.marketplace,
                listing.marketplace_item_id,
                provider.name,
                candidates,
            )
    finally:
        repository.close()
    payload = {
        "marketplace": args.marketplace,
        "item_id": args.item_id,
        "listing_title": listing.title,
        "attribution": "Candidate catalog data provided by Discogs",
        "attribution_url": "https://www.discogs.com",
        "candidates": [
            {
                **candidate.model_dump(mode="json"),
                "catalog_url": next(
                    variant.resource_url
                    for variant in variants
                    if variant.catalog_variant_id == candidate.catalog_variant_id
                ),
            }
            for candidate in candidates
        ],
    }
    if args.json:
        print(json.dumps(payload))
    else:
        print("Candidate catalog data provided by Discogs: https://www.discogs.com")
        print(f"Listing: {listing.title}")
        print(f"Candidates evaluated: {len(candidates)}")
        for candidate in candidates:
            variant = next(
                item for item in variants if item.catalog_variant_id == candidate.catalog_variant_id
            )
            print(
                f"Discogs release {candidate.catalog_variant_id}: "
                f"{candidate.score}/100 ({candidate.status}) — {variant.resource_url}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging()
    try:
        if args.command == "scan":
            return _scan(args)
        if args.command == "catalog-search":
            return _catalog_search(args)
        return _match(args)
    except FinderError as exc:
        print(f"Finder: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Finder: interrupted; previously committed data remains stored.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
