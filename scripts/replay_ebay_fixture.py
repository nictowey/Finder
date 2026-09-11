"""Replay a sanitized eBay response bundle into Finder's normal persistence service."""

import argparse
import json
from pathlib import Path

from finder.adapters.ebay.replay import EbayReplayAdapter
from finder.config import load_monitor
from finder.persistence import SqlAlchemyRepository
from finder.service import run_scan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--database-url", default="sqlite:///finder-replay.db")
    parser.add_argument("--config", type=Path, default=Path("config/monitors.toml"))
    parser.add_argument("--monitor", default="rap-vinyl")
    args = parser.parse_args()
    monitor = load_monitor(args.config, args.monitor)
    bundle = json.loads(args.fixture.read_text(encoding="utf-8"))
    repository = SqlAlchemyRepository.from_url(args.database_url)
    try:
        summary = run_scan(monitor, EbayReplayAdapter(bundle), repository)
    finally:
        repository.close()
    print(json.dumps(summary.__dict__, sort_keys=True))
    return 0 if summary.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
