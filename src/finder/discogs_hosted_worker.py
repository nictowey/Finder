"""Opt-in process entry point for the durable, read-only hosted scheduler.

Installing this module does not start a process, resume a schedule, or clear a
browser access stop. The scheduler's SQLite lease and cadence survive restarts.
"""

import argparse
import json
import signal
import threading
import uuid
from pathlib import Path

from finder.discogs_hosted_scheduler import (
    SHARED_SOCKET,
    HostedReleaseScheduler,
    HostedSocketReader,
)


def run(
    database: Path,
    socket_path: Path,
    stop: threading.Event,
    *,
    allowed_releases,
    wait_seconds=30,
):
    """Run until signaled or a fail-closed scheduler pause; never resume it."""
    if not allowed_releases:
        raise ValueError("A live worker needs an explicit exact-release scope.")
    if type(wait_seconds) is not int or not 1 <= wait_seconds <= 60:
        raise ValueError("A bounded worker heartbeat interval is required.")
    reader = HostedSocketReader(socket_path, lease_read=True)
    scheduler = HostedReleaseScheduler(database, reader, allowed_releases=allowed_releases)
    run_id = uuid.uuid4().hex

    def stopped(reason):
        try:
            scheduler.worker_stopped(run_id, reason)
        except (OSError, RuntimeError, ValueError):
            reason = "worker_status_unavailable"
        return {"status": "stopped", "reason": reason, "purchase_allowed": False}

    try:
        scheduler.worker_started(run_id, allowed_releases)
    except (OSError, RuntimeError, ValueError):
        return {
            "status": "stopped",
            "reason": "worker_status_unavailable",
            "purchase_allowed": False,
        }
    try:
        scheduler.reconcile_outbox()
    except (OSError, RuntimeError, ValueError):
        return stopped("outbox_reconciliation_failed")
    while not stop.is_set():
        try:
            scheduler.worker_heartbeat(run_id)
        except (OSError, RuntimeError, ValueError):
            return stopped("worker_status_unavailable")
        state = scheduler.health()
        if state["paused"] or state["kill_switch"] or not state["reader_ready"]:
            return stopped(
                "schedule_paused"
                if state["paused"]
                else "kill_switch"
                if state["kill_switch"]
                else "reader_unavailable"
            )
        result = scheduler.tick(expected_worker_run_id=run_id)
        try:
            scheduler.worker_heartbeat(run_id, tick_status=result["status"])
        except (OSError, RuntimeError, ValueError):
            return stopped("worker_status_unavailable")
        if result["status"] == "blocked":
            state = scheduler.health()
            if state["paused"]:
                return stopped(state["last_error"] or "read_stopped")
        stop.wait(wait_seconds)
    return stopped("signal")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in read-only Sniper monitor worker")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--socket", type=Path, default=SHARED_SOCKET)
    parser.add_argument("--run", action="store_true", help="Actually start the scheduler loop")
    parser.add_argument(
        "--release-id",
        action="append",
        type=int,
        default=[],
        help="Exact release allowed in a running worker; repeat for each ID",
    )
    args = parser.parse_args(argv)
    if args.run and not args.release_id:
        parser.error("--run requires at least one --release-id")
    scheduler = HostedReleaseScheduler(
        args.database,
        HostedSocketReader(args.socket, lease_read=args.run),
        allowed_releases=args.release_id if args.run else None,
    )
    if not args.run:
        print(json.dumps(scheduler.health(), sort_keys=True))
        return 0
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(
        json.dumps(
            run(args.database, args.socket, stop, allowed_releases=args.release_id), sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
