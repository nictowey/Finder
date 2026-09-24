"""Scheduled worker entrypoint. Only anonymous counts may reach Actions logs."""

import json
import logging
import os
import sys

from sqlalchemy.engine import make_url

from finder.config import load_discogs_settings, load_settings
from finder.persistence import SqlAlchemyRepository
from finder.watch_store import SavedWatch, WatchStore, migrate
from finder.watch_worker import run_due_watches


def main():
    logging.disable(logging.CRITICAL)
    try:
        settings = load_settings()
        url = settings.database_url.get_secret_value()
        if settings.ebay_environment != "production" or make_url(url).get_backend_name() not in (
            "postgres",
            "postgresql",
        ):
            raise ValueError("Shared Production database required")
        repo = SqlAlchemyRepository.from_url(url)
        try:
            migrate(repo.engine)
            # Optional secret-backed bootstrap. Never commit real catalog identities.
            if "--seed" in sys.argv:
                store = WatchStore(repo.engine)
                for index, value in enumerate(
                    os.environ.get("FINDER_SEED_RELEASES", "").split(",")
                ):
                    if value.strip().isdigit():
                        store.add(SavedWatch(release_id=int(value)), watch_id=f"seed-{index + 1}")
            report = run_due_watches(
                repo,
                settings,
                load_discogs_settings(),
                context={
                    "run_id": os.environ.get("GITHUB_RUN_ID"),
                    "source": os.environ.get("FINDER_TRIGGER_SOURCE", "local"),
                    "dispatch_id": os.environ.get("FINDER_DISPATCH_ID"),
                },
            )
        finally:
            repo.close()
        print(json.dumps(report))
        return 1 if report["failed"] or report["quota_paused"] else 0
    except Exception:
        print('{"status":"failed","reason":"worker_unavailable"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
