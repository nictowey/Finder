"""Explicit aggregate-only worker entry point. No activation or secret defaults."""

import argparse
import json
import os
import signal
import sqlite3
import stat
import threading
import time
from pathlib import Path

from finder.discogs_app_auth import AppCredentials, AuthenticatedReleaseStatsReader
from finder.discogs_listing import ReleaseStatsReader
from finder.discogs_ntfy import NtfySender, PrivateNtfyConfig, deliver_one
from finder.discogs_price_alerts import (
    MAX_POLL_SECONDS,
    MIN_POLL_SECONDS,
    POLL_SECONDS,
    AggregatePriceAlerts,
)


def private_topic(path):
    """Read a preconfigured owner-only file; never create, print or repair it."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise ValueError("Private notification configuration unavailable.")
            raw = source.read(66)
            if len(raw) > 65:
                raise ValueError("Private notification configuration unavailable.")
            return raw.decode("ascii").removesuffix("\n")
    except (OSError, UnicodeError):
        raise ValueError("Private notification configuration unavailable.") from None


def tick(alerts, *, reader=None, sender=None, stop=None):
    """Executable bridge: one stats read and at most one separately enabled send."""
    poll = alerts.poll_once(reader)
    return {
        "poll": poll,
        "delivery": deliver_one(alerts, None if stop is not None and stop.is_set() else sender),
        "purchase_allowed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Opt-in aggregate item-price leads; buying unavailable"
    )
    parser.add_argument(
        "--discogs-app-file",
        type=Path,
        help="Existing owner-only app key/secret JSON file; never put secrets in argv or env",
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--poll", action="store_true", help="Permit one public stats GET per tick")
    parser.add_argument(
        "--deliver", action="store_true", help="Permit ntfy attempts for configured alerts"
    )
    destinations = parser.add_mutually_exclusive_group()
    destinations.add_argument(
        "--ntfy-config-file",
        type=Path,
        help="Existing owner-only private ntfy JSON configuration; never put secrets in argv",
    )
    destinations.add_argument(
        "--topic-file", type=Path, help="Existing owner-only topic file; never put topic in argv"
    )
    parser.add_argument(
        "--run", action="store_true", help="Repeat until stopped; default is one tick"
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=POLL_SECONDS,
        help=(
            "Per-release stats interval in seconds (120–86400; default 600); "
            "does not enable polling"
        ),
    )
    args = parser.parse_args(argv)
    if not MIN_POLL_SECONDS <= args.poll_seconds <= MAX_POLL_SECONDS:
        parser.error("--poll-seconds must be between 120 and 86400")
    if args.discogs_app_file is not None and not args.poll:
        parser.error("--discogs-app-file requires --poll")
    if args.deliver and args.topic_file is None and args.ntfy_config_file is None:
        parser.error("--deliver requires --topic-file or --ntfy-config-file")
    if args.ntfy_config_file is not None and not args.deliver:
        parser.error("--ntfy-config-file requires --deliver")
    if args.run and not (args.poll or args.deliver):
        parser.error("--run requires explicit --poll or --deliver")
    stop = threading.Event()
    previous = {}
    try:
        credentials = AppCredentials(args.discogs_app_file) if args.discogs_app_file else None
        alerts = AggregatePriceAlerts(args.database, poll_seconds=args.poll_seconds)
        reader = (
            AuthenticatedReleaseStatsReader(credentials)
            if credentials
            else ReleaseStatsReader()
            if args.poll
            else None
        )
        sender = None
        if args.deliver:
            sender = (
                NtfySender(private_config=PrivateNtfyConfig(args.ntfy_config_file), enabled=True)
                if args.ntfy_config_file
                else NtfySender(private_topic(args.topic_file), enabled=True)
            )
        if args.run:
            for name in (signal.SIGTERM, signal.SIGINT):
                previous[name] = signal.signal(name, lambda *_: stop.set())
        while not stop.is_set():
            started = time.monotonic()
            result = tick(alerts, reader=reader, sender=sender, stop=stop)
            print(json.dumps(result, sort_keys=True), flush=True)
            if not args.run:
                return 0
            stop.wait(max(0.5, 3 - (time.monotonic() - started)))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        # Exception text may include paths or secrets; emit only a fixed status.
        print('{"status":"price_alert_worker_unavailable","purchase_allowed":false}', flush=True)
        return 1
    finally:
        for name, handler in previous.items():
            signal.signal(name, handler)


if __name__ == "__main__":
    raise SystemExit(main())
