"""A synthetic, local-only development entrypoint, separate from the live CLI."""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path


class OfflineSafetyError(RuntimeError):
    """The requested operation is outside the offline development boundary."""


_active_database: str | None = None
_guard_installed = False
_DATABASE_ENV = {
    "DATABASE_URL",
    "DATABASE_URL_UNPOOLED",
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "PGPASSFILE",
    "PGSERVICE",
    "PGSERVICEFILE",
}


def _check_environment(environment: Mapping[str, str]) -> None:
    # Report names only; values can contain passwords or provider tokens.
    blocked = sorted(
        key
        for key, value in environment.items()
        if value
        and (
            key in _DATABASE_ENV
            or key.startswith(("EBAY_", "DISCOGS_", "NEON_"))
            or key.startswith("FINDER_")
        )
    )
    if blocked:
        raise OfflineSafetyError(
            "Offline development requires a clean environment; unset: " + ", ".join(blocked)
        )


def _audit_offline(event: str, args: tuple) -> None:
    if _active_database is None:
        return
    if event.startswith("socket.") or event in {
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
    }:
        raise OfflineSafetyError("Network access and subprocesses are disabled in offline mode.")
    if event == "sqlite3.connect" and str(args[0]) != _active_database:
        raise OfflineSafetyError("Offline mode can only open its newly created SQLite database.")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        name = Path(os.fsdecode(args[0])).name
        if name == ".env" or name.startswith(".env."):
            raise OfflineSafetyError("Offline mode does not read dotenv files.")


@contextmanager
def _offline_boundary(database: Path):
    """Fail closed on accidental network, subprocess, dotenv, or other SQLite I/O.

    This guards the dedicated development command against regressions, rather than
    sandboxing untrusted code. The audit hook stays inert outside this context.
    """
    global _active_database, _guard_installed
    if _active_database is not None:
        raise OfflineSafetyError("An offline demonstration is already running.")
    if not _guard_installed:
        sys.addaudithook(_audit_offline)
        _guard_installed = True
    _active_database = str(database)
    try:
        yield
    finally:
        _active_database = None


def _new_database_path(value: str) -> Path:
    # Accept a local filename, never a database URL or a SQLite URI with options.
    if not value or ":" in value or "\\" in value or value.startswith("//"):
        raise OfflineSafetyError("--database must be a local file path, not a URL or URI.")
    path = Path(value).expanduser()
    if path.name == ".env" or path.name.startswith(".env."):
        raise OfflineSafetyError("Offline mode does not create or use dotenv files.")
    if path.is_symlink():
        raise OfflineSafetyError("Offline mode will not use a symlink as its database.")
    path = path.resolve()
    # SQLite can recover, truncate, or delete sidecars even when the main file is
    # new. Inspect without following symlinks before creating anything. Like the
    # runtime guard, this preflight is not a hostile/concurrent filesystem sandbox.
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise OfflineSafetyError("Cannot safely inspect SQLite sidecar paths.") from None
        raise OfflineSafetyError(
            "Offline mode will not use an existing SQLite sidecar; choose a new file path."
        )
    try:
        # O_EXCL also rejects existing files, directories, and symlinks atomically.
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise OfflineSafetyError(
            "Offline mode never overwrites an existing database; choose a new file path."
        ) from None
    except OSError:
        raise OfflineSafetyError(
            "Cannot create the offline database; choose a writable existing directory."
        ) from None
    os.close(descriptor)
    return path


def _synthetic_inputs():
    from finder.categories.vinyl_clues import Clue
    from finder.domain import Listing, Product, Variant
    from finder.watch_store import SavedWatch

    observed_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    product = Product(
        catalog_source="synthetic",
        catalog_product_id="demo-album",
        title="Offline Horizons",
        artists=["Example Ensemble"],
        observed_at=observed_at,
        source_metadata={"synthetic": True},
    )
    target = Variant(
        **product.model_dump(exclude={"source_metadata"}),
        catalog_variant_id="1",
        formats=[{"name": "Vinyl", "text": "Blue"}],
        labels=[{"name": "Example Records", "catno": "DEMO-BLUE"}],
        source_metadata={"synthetic": True},
    )
    alternative = target.model_copy(
        update={
            "catalog_variant_id": "2",
            "formats": [{"name": "Vinyl", "text": "Black"}],
            "labels": [{"name": "Example Records", "catno": "DEMO-BLACK"}],
        }
    )
    watch = SavedWatch(
        release_id=1,
        label="Synthetic blue pressing",
        maximum_subtotal=Decimal("30.00"),
        gamble_max=Decimal("15.00"),
        country="US",
        postal_code="00000",
        tells=[Clue(kind="color", value="Blue", required=True)],
    )
    rows = [
        Listing(
            marketplace="synthetic",
            marketplace_item_id=case,
            title=f"Example Ensemble Offline Horizons {description} vinyl",
            current_price=Decimal(price),
            currency="USD",
            price_kind="fixed_price",
            shipping_cost=Decimal("4.00"),
            shipping_currency="USD",
            first_observed_at=observed_at,
            last_observed_at=observed_at,
            details_observed_at=observed_at,
            source_metadata={
                "synthetic": True,
                "delivery_country": "US",
                "delivery_postal_code": "00000",
            },
        )
        for case, description, price in (
            ("claimed-blue", "blue", "20.00"),
            ("color-unspecified", "LP", "10.00"),
            ("claimed-black", "black", "8.00"),
        )
    ]
    return product, target, alternative, watch, rows


def _populate_and_review(database: Path) -> dict:
    from sqlalchemy.engine import URL

    from finder.matching import score_variant
    from finder.persistence import SqlAlchemyRepository
    from finder.watch_worker import assess_review

    # Never pass user configuration to a URL parser or database driver selector.
    url = URL.create("sqlite", database=str(database)).render_as_string(hide_password=False)
    product, target, alternative, watch, rows = _synthetic_inputs()
    repository = SqlAlchemyRepository.from_url(url)
    try:
        repository.upsert_product(product)
        for variant in (target, alternative):
            repository.upsert_variant(variant)
        for row in rows:
            repository.upsert(row)
            repository.upsert(row)  # Same identity and observation must be idempotent.
        later = rows[0].model_copy(
            update={
                "current_price": Decimal("19.00"),
                "last_observed_at": rows[0].last_observed_at + timedelta(minutes=5),
                "details_observed_at": rows[0].last_observed_at + timedelta(minutes=5),
            }
        )
        repository.upsert(later)
        repository.upsert(rows[0])  # An older observation must not replace the new price.
        for row in (later, *rows[1:]):
            repository.replace_candidates(
                row.marketplace,
                row.marketplace_item_id,
                "synthetic",
                [
                    score_variant(row, variant, row.last_observed_at)
                    for variant in (target, alternative)
                ],
            )
    finally:
        repository.close()

    # Exercise persistence by closing and reopening before evaluating stored inputs.
    repository = SqlAlchemyRepository.from_url(url)
    try:
        target = repository.get_variant("synthetic", "1")
        alternative = repository.get_variant("synthetic", "2")
        reviews = []
        for sample in rows:
            row = repository.get("synthetic", sample.marketplace_item_id)
            review = assess_review(
                watch,
                row,
                target,
                now=later.last_observed_at,
                alternatives=[alternative],
                search_incomplete=False,
            )
            reviews.append(
                {
                    "case": row.marketplace_item_id,
                    "title": row.title,
                    "review": review,
                    "observations": len(
                        repository.get_observations("synthetic", row.marketplace_item_id)
                    ),
                    "stored_candidates": len(
                        repository.get_candidates("synthetic", row.marketplace_item_id, "synthetic")
                    ),
                }
            )
        return {
            "mode": "offline-synthetic",
            "database": str(database),
            "notice": (
                "Synthetic examples only; pressing tiers are review aids, not verified identities. "
                "No notifications are sent."
            ),
            "stored_listings": repository.count(),
            "reviews": reviews,
        }
    finally:
        repository.close()


def run_demo(database: str) -> dict:
    """Create and exercise a fresh local SQLite database using only synthetic inputs."""
    _check_environment(os.environ)
    path = _new_database_path(database)
    with _offline_boundary(path):
        return _populate_and_review(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="finder-offline", description="Synthetic offline development; no external services."
    )
    parser.add_argument(
        "--database", default="finder-offline-demo.sqlite3", help="New local SQLite file path"
    )
    args = parser.parse_args(argv)
    try:
        result = run_demo(args.database)
    except OfflineSafetyError as exc:
        print(f"Offline safety check: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
