"""Opt-in process entry point for the durable, read-only hosted scheduler.

Installing this module does not start a process, resume a schedule, or clear a
browser access stop. The scheduler's SQLite lease and cadence survive restarts.
"""

import argparse
import json
import signal
import threading
from pathlib import Path

from finder.discogs_hosted_scheduler import (
    SHARED_SOCKET,
    HostedReleaseScheduler,
    HostedSocketReader,
)


def run(database: Path, socket_path: Path, stop: threading.Event, *, wait_seconds=30):
    """Run until signaled or a fail-closed scheduler pause; never resume it."""
    reader = HostedSocketReader(socket_path)
    scheduler = HostedReleaseScheduler(database, reader)
    while not stop.is_set():
        state = scheduler.health()
        if state["paused"] or state["kill_switch"] or not state["reader_ready"]:
            return {
                "status": "stopped",
                "reason": (
                    "schedule_paused"
                    if state["paused"]
                    else "kill_switch"
                    if state["kill_switch"]
                    else "reader_unavailable"
                ),
                "purchase_allowed": False,
            }
        result = scheduler.tick()
        if result["status"] == "blocked":
            state = scheduler.health()
            if state["paused"]:
                return {
                    "status": "stopped",
                    "reason": state["last_error"],
                    "purchase_allowed": False,
                }
        stop.wait(wait_seconds)
    return {"status": "stopped", "reason": "signal", "purchase_allowed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in read-only Sniper monitor worker")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--socket", type=Path, default=SHARED_SOCKET)
    parser.add_argument("--run", action="store_true", help="Actually start the scheduler loop")
    args = parser.parse_args(argv)
    scheduler = HostedReleaseScheduler(args.database, HostedSocketReader(args.socket))
    if not args.run:
        print(json.dumps(scheduler.health(), sort_keys=True))
        return 0
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(json.dumps(run(args.database, args.socket, stop), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
