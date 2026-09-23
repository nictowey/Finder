import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.engine import make_url

from finder.adapters.discogs.adapter import DiscogsCatalogProvider
from finder.adapters.discogs.client import DiscogsClient
from finder.adapters.ebay.adapter import EbayAdapter
from finder.adapters.ebay.client import EbayClient
from finder.adapters.ebay.target_search import plan_target_search, run_target_scan
from finder.categories.target_review import review_target_listing
from finder.categories.vinyl_target import parse_discogs_release_id, target_from_release
from finder.config import (
    load_database_url,
    load_discogs_settings,
    load_monitor,
    load_search_target,
    load_settings,
)
from finder.errors import ConfigurationError, FinderError
from finder.logging import configure_logging
from finder.matching import decide_match, rank_variants
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

    for command, help_text in (
        ("target-plan", "Inspect bounded searches for a saved or Discogs vinyl target"),
        ("scan-target", "Scan bounded searches for a saved or Discogs vinyl target"),
    ):
        target_command = commands.add_parser(command, help=help_text)
        target_command.add_argument(
            "--config", type=Path, default=Path("config/watch_targets.toml")
        )
        selection = target_command.add_mutually_exclusive_group(required=True)
        selection.add_argument("--target", help="Saved target ID from the configuration file")
        selection.add_argument("--release", help="Discogs vinyl release ID or HTTPS release URL")
        target_command.add_argument(
            "--query", action="append", help="Replace the catalog-derived search (1–3 times)"
        )
        target_command.add_argument("--mode", choices=("initial", "refresh"), default="refresh")
        target_command.add_argument("--json", action="store_true")
        target_command.add_argument("--env-file", type=Path, default=Path(".env"))
        if command == "scan-target":
            target_command.add_argument(
                "--review",
                action="store_true",
                help="Privately triage this scan against the selected pressing",
            )
            target_command.add_argument(
                "--show-listings",
                action="store_true",
                help="Show newly discovered listing identities privately for follow-up review",
            )
            target_command.add_argument(
                "--probe-legacy-id",
                help="Privately report whether this legacy eBay item appeared in this run",
            )

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
    match.add_argument(
        "--target-release-id",
        type=int,
        help="Include this saved Discogs release in the bounded candidate set for review",
    )
    match.add_argument("--env-file", type=Path, default=Path(".env"))
    match.add_argument("--json", action="store_true", help="Print machine-readable results")
    stored = commands.add_parser("listings", help="Browse recent stored listings for review")
    stored.add_argument("--marketplace", default="ebay")
    stored.add_argument("--limit", type=int, default=20, choices=range(1, 101))
    stored.add_argument("--env-file", type=Path, default=Path(".env"))
    stored.add_argument("--json", action="store_true", help="Print machine-readable results")
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


def _target(args: argparse.Namespace) -> int:
    selected_variant = None
    if args.target:
        if args.query:
            raise ConfigurationError(
                "--query requires --release; saved targets use configured queries."
            )
        target = load_search_target(args.config, args.target)
    else:
        release_id = parse_discogs_release_id(args.release)
        discogs_settings = load_discogs_settings(args.env_file)
        with DiscogsClient(discogs_settings) as client:
            variant = DiscogsCatalogProvider(client).get_release(release_id)
        selected_variant = variant
        target = target_from_release(variant, queries=args.query)
    monitors = plan_target_search(target, mode=args.mode, include_initial_newest=True)
    payload = {
        "target": target.id,
        "catalog_source": target.catalog_source,
        "catalog_variant_id": target.catalog_variant_id,
        "plan_version": target.plan_version,
        "mode": args.mode,
        "searches": [
            {"monitor": monitor.id, "query": monitor.query, "options": monitor.source_options}
            for monitor in monitors
        ],
        "maximum_search_requests": len(monitors),
        "maximum_detail_requests": 10 * len(monitors),
    }
    if args.release:
        payload["attribution"] = "Data provided by Discogs"
        payload["attribution_url"] = "https://www.discogs.com"
    if args.command == "target-plan":
        if args.json:
            print(json.dumps(payload))
        else:
            print(json.dumps(payload, indent=2))
        return 0

    if args.probe_legacy_id is not None and not re.fullmatch(r"[0-9]{9,20}", args.probe_legacy_id):
        raise ConfigurationError("The probe legacy item ID must contain 9–20 digits.")

    if args.review and selected_variant is None:
        discogs_settings = load_discogs_settings(args.env_file)
        with DiscogsClient(discogs_settings) as client:
            selected_variant = DiscogsCatalogProvider(client).get_release(target.catalog_variant_id)

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
            run = run_target_scan(target, EbayAdapter(client), repository, mode=args.mode)
        if args.review:
            reviewed = []
            counts = {
                status: 0
                for status in ("possible_pressing", "family_review", "conflicting", "unrelated")
            }
            for item_id in sorted(run.discovered_item_ids):
                listing = repository.get("ebay", item_id)
                if listing is None:
                    continue
                assessment = review_target_listing(listing, selected_variant)
                counts[assessment.status] += 1
                if assessment.status == "unrelated":
                    continue
                reviewed.append(
                    {
                        "item_id": listing.marketplace_item_id,
                        "title": listing.title,
                        "url": listing.listing_url,
                        "delivered_subtotal": (
                            str(listing.total_acquisition_cost)
                            if listing.total_acquisition_cost is not None
                            else None
                        ),
                        "currency": listing.currency,
                        **assessment.model_dump(),
                    }
                )
            priority = {"possible_pressing": 0, "family_review": 1, "conflicting": 2}
            reviewed.sort(key=lambda row: (priority[row["status"]], row["item_id"]))
            payload["review"] = {
                "scope": "selected_catalog_release_only",
                "not_verified_pressings": True,
                "counts": counts,
                "listings": reviewed,
            }
        if args.show_listings:
            payload["discovered_listings"] = [
                {
                    "item_id": listing.marketplace_item_id,
                    "title": listing.title,
                    "delivered_subtotal": (
                        str(listing.total_acquisition_cost)
                        if listing.total_acquisition_cost is not None
                        else None
                    ),
                    "currency": listing.currency,
                    "quality_flags": listing.quality_flags,
                }
                for item_id in sorted(run.discovered_item_ids)
                if (listing := repository.get("ebay", item_id)) is not None
            ]
    finally:
        repository.close()
    payload["results"] = [asdict(summary) for summary in run.summaries]
    payload["complete"] = len(run.summaries) == len(monitors) and all(
        summary.status == "completed" for summary in run.summaries
    )
    payload["coverage_truncated"] = any(summary.limit_reached for summary in run.summaries)
    if args.probe_legacy_id is not None:
        payload["probe_found_in_this_run"] = run.found_legacy_item(args.probe_legacy_id)
    if args.json:
        print(json.dumps(payload))
    else:
        print(json.dumps(payload, indent=2))
    return 0 if payload["complete"] else 1


def _catalog_components(args: argparse.Namespace):
    settings = load_discogs_settings(args.env_file)
    configure_logging(settings.log_level)
    repository = SqlAlchemyRepository.from_url(settings.database_url.get_secret_value())
    return settings, repository


def _listings(args: argparse.Namespace) -> int:
    repository = SqlAlchemyRepository.from_url(load_database_url(args.env_file))
    try:
        listings = repository.list_recent(args.marketplace, args.limit)
    finally:
        repository.close()
    rows = [
        {
            "marketplace": listing.marketplace,
            "item_id": listing.marketplace_item_id,
            "title": listing.title,
            "price": str(listing.current_price) if listing.current_price is not None else None,
            "shipping": str(listing.shipping_cost) if listing.shipping_cost is not None else None,
            "delivered_subtotal": (
                str(listing.total_acquisition_cost)
                if listing.total_acquisition_cost is not None
                else None
            ),
            "currency": listing.currency,
            "quality_flags": listing.quality_flags,
            "last_observed_at": listing.last_observed_at.isoformat(),
        }
        for listing in listings
    ]
    if args.json:
        print(json.dumps({"listings": rows}))
    else:
        for row in rows:
            cost = row["delivered_subtotal"] or "unknown delivered subtotal"
            print(f"{row['item_id']} | {row['title']} | {cost} {row['currency'] or ''}")
        print(f"Stored {args.marketplace} listings shown: {len(rows)}")
    return 0


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
            retrieval = provider.search_for_listing(
                listing, limit=args.limit, target_release_id=args.target_release_id
            )
            variants = retrieval.variants
            candidates = rank_variants(listing, variants)
            for variant in variants:
                repository.upsert_product(provider.product_for(variant))
                repository.upsert_variant(variant)
            decision = decide_match(listing, variants, retrieval_incomplete=retrieval.incomplete)
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
        "retrieval": {
            "query_kinds": retrieval.query_kinds,
            "search_truncated": retrieval.search_truncated,
            "candidate_limit_reached": retrieval.candidate_limit_reached,
            "identifiers_omitted": retrieval.identifiers_omitted,
            "target_release_id": retrieval.target_release_id,
            "target_not_in_search": retrieval.target_not_in_search,
            "incomplete": retrieval.incomplete,
        },
        "decision": decision.model_dump(mode="json"),
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
        print(f"Decision: {decision.outcome} ({decision.policy_version})")
        if retrieval.incomplete:
            print("Catalog search was bounded; other pressings may be missing.")
        if retrieval.target_not_in_search:
            print("Saved release was not found by the listing search; it was checked directly.")
        if decision.conflicts:
            print(f"Conflicting fields: {', '.join(decision.conflicts)}")
        if decision.missing_evidence:
            print(f"Missing evidence: {', '.join(decision.missing_evidence)}")
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
        if args.command in ("target-plan", "scan-target"):
            return _target(args)
        if args.command == "catalog-search":
            return _catalog_search(args)
        if args.command == "listings":
            return _listings(args)
        return _match(args)
    except FinderError as exc:
        print(f"Finder: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Finder: interrupted; previously committed data remains stored.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
