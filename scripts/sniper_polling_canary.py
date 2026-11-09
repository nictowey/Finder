"""Explicit bounded aggregate canary on a disposable private database copy.

No sender/watcher is constructed. Requires an operator-reviewed candidate and an
explicit live-read flag; importing this module never reads files or the network.
"""

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import signal
import sqlite3
import stat
import tempfile
import time
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path

import httpx

from finder.discogs_app_auth import AppCredentials, AuthenticatedReleaseStatsReader
from finder.discogs_listing import ReadBudgetBusy
from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_read_guard import DatabaseReadBudget, ReadSafetyHalt, guard_row
from finder.discogs_read_quota import retry_seconds

MAX_SECONDS = 480
INSPECT_SECONDS = 2
MAX_REQUESTS = 100
EXPECTED_ENABLED = 19
EXPECTED_DISABLED = 1
DISABLED_RELEASE = 11398560
DISABLED_REVISION = 2
CYCLES = 3
SOURCE_WRITES = {
    ("discogs_monitor_control", "next_request"),
    ("discogs_api_read_guard", "pending_token"),
    ("discogs_api_read_guard", "pending_since"),
    ("discogs_api_read_guard", "known_deadline"),
}


class CanaryStop(Exception):
    """Sanitized, non-retryable local stop."""


@contextmanager
def bounded_run():
    if not hasattr(signal, "setitimer"):
        raise CanaryStop("hard_deadline_unavailable")
    previous = {}

    def stop(signum, frame):
        raise CanaryStop("deadline_reached" if signum == signal.SIGALRM else "interrupted")

    try:
        for kind in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
            previous[kind] = signal.signal(kind, stop)
        signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
        yield time.monotonic() + MAX_SECONDS
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for kind, handler in previous.items():
            signal.signal(kind, handler)


def backup_snapshot(source, destination, deadline):
    def progress(status, remaining, total):
        if time.monotonic() >= deadline:
            raise CanaryStop("deadline_reached")

    source.backup(destination, pages=64, sleep=0.01, progress=progress)


@contextmanager
def private_copy(source, *, directory=None, deadline=None):
    """Back up only an already migrated source during the coordinated reader pause."""
    if source.is_symlink() or not stat.S_ISREG(source.stat().st_mode):
        raise CanaryStop("source_not_regular")
    context = (
        tempfile.TemporaryDirectory(prefix="sniper-poll-canary-")
        if directory is None
        else nullcontext(directory)
    )
    with context as directory:
        info = Path(directory).lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.getuid()
        ):
            raise CanaryStop("private_directory_permissions_invalid")
        path = Path(directory) / "canary.db"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with closing(
            sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=0)
        ) as src:
            src.execute("PRAGMA query_only=ON")
            if src.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
                raise CanaryStop("source_not_tagged_workspace")
            guard_row(src)  # Never initialize, repair, or migrate the real source.
            with closing(sqlite3.connect(path, timeout=0)) as dest:
                backup_snapshot(src, dest, deadline or time.monotonic() + MAX_SECONDS)
        if stat.S_IMODE(path.stat().st_mode) != 0o600 or path.stat().st_uid != os.getuid():
            raise CanaryStop("private_copy_permissions_invalid")
        yield path  # TemporaryDirectory removes DB, WAL, SHM and any lock sidecars.


def scope_and_seed(path):
    with closing(sqlite3.connect(path, timeout=0)) as db, db:
        if db.execute("SELECT enabled FROM discogs_price_control WHERE id=1").fetchone() != (1,):
            raise CanaryStop("copied_monitor_not_enabled")
        rows = db.execute(
            "SELECT release_id,revision,data FROM discogs_price_rules ORDER BY release_id"
        ).fetchall()
        rules = [PriceLeadRule.model_validate_json(row[2]) for row in rows]
        if any(rule.release_id != row[0] for rule, row in zip(rules, rows, strict=True)):
            raise CanaryStop("rule_scope_changed")
        enabled = [rule.release_id for rule in rules if rule.enabled]
        disabled = [
            (row[0], row[1]) for rule, row in zip(rules, rows, strict=True) if not rule.enabled
        ]
        if len(enabled) != EXPECTED_ENABLED or disabled != [(DISABLED_RELEASE, DISABLED_REVISION)]:
            raise CanaryStop("rule_scope_changed")
        samples = dict(db.execute("SELECT release_id,last_success FROM discogs_price_samples"))
        if not set(enabled) <= samples.keys():
            raise CanaryStop("copied_samples_missing")
        # Only this scheduling field is seeded. Provider holds, claims, failures,
        # controls, rules and history are preserved. All later writes are isolated.
        db.executemany(
            "UPDATE discogs_price_samples SET next_check=? WHERE release_id=?",
            [(time.time(), release) for release in enabled],
        )
        return enabled, samples


def safe_headers(response):
    result = {"http_status": response.status_code}
    for name, key in (
        ("X-Discogs-Ratelimit", "limit"),
        ("X-Discogs-Ratelimit-Used", "used"),
        ("X-Discogs-Ratelimit-Remaining", "remaining"),
        ("X-Discogs-Ratelimit-Reset", "reset"),
    ):
        raw = response.headers.get(name)
        result[key] = (
            int(raw) if raw and raw.isascii() and raw.isdigit() and len(raw) <= 12 else None
        )
        if raw is not None and result[key] is None:
            result[key + "_invalid"] = True
    raw = response.headers.get("Retry-After")
    result["retry_after_seconds"] = retry_seconds(raw, time.time()) if raw is not None else None
    return result


class RecordingTransport(httpx.BaseTransport):
    def __init__(self, report, on_headers=None):
        self.inner = None
        self.report, self.on_headers = report, on_headers

    def handle_request(self, request):
        self.report["requests"] += 1
        wall_start, started = time.time(), time.monotonic()
        try:
            if self.inner is None:
                self.inner = httpx.HTTPTransport(trust_env=False)
            response = self.inner.handle_request(request)
        except httpx.HTTPError:
            self.report["responses"].append(
                {"error": "transport_failed", "request_start_at": wall_start}
            )
            raise
        # Never retain a URL, request/authorization headers, response body or ID.
        self.report["responses"].append(
            {
                **safe_headers(response),
                "request_start_at": wall_start,
                "header_latency_seconds": time.monotonic() - started,
            }
        )
        if self.on_headers is not None:
            self.on_headers(response)
        return response

    def close(self):
        if self.inner is not None:
            self.inner.close()
            self.inner = None

    def finish(self):
        self.close()


def intent_fingerprint(db):
    control = db.execute("SELECT enabled,revision FROM discogs_price_control WHERE id=1").fetchone()
    if control is None or control[0] != 1:
        raise CanaryStop("live_intent_changed")
    rules = db.execute(
        "SELECT release_id,revision,data FROM discogs_price_rules ORDER BY release_id"
    ).fetchall()
    return hashlib.sha256(json.dumps([control, rules], separators=(",", ":")).encode()).digest()


def source_authorizer(action, first, second, database, trigger):
    if action == sqlite3.SQLITE_UPDATE:
        return (
            sqlite3.SQLITE_OK
            if database == "main" and trigger is None and (first, second) in SOURCE_WRITES
            else sqlite3.SQLITE_DENY
        )
    if action == sqlite3.SQLITE_PRAGMA:
        # DatabaseReadBudget temporarily makes its own operations nonblocking,
        # then restores the previous connection-local busy_timeout in finally.
        readable = {"application_id", "synchronous", "journal_mode", "busy_timeout"}
        allowed = first in readable and second is None
        allowed |= first == "busy_timeout" and str(second).isascii() and str(second).isdigit()
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
    return (
        sqlite3.SQLITE_OK
        if action
        in {
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_TRANSACTION,
            sqlite3.SQLITE_FUNCTION,
        }
        else sqlite3.SQLITE_DENY
    )


def safety_state(db):
    """Sanitized, read-only diagnostics; even a young pending token blocks rollback."""
    try:
        token, since, known = guard_row(db)
        due = db.execute("SELECT next_request FROM discogs_monitor_control WHERE id=1").fetchone()[
            0
        ]
        if type(due) not in (int, float) or not math.isfinite(due) or due < 0:
            raise ReadSafetyHalt("api_budget_invalid")
        return {
            "state": "pending" if token is not None else "ready",
            "pending_since": since,
            "known_deadline": max(known, due),
            "manual_recovery_required": token is not None,
        }
    except (ReadSafetyHalt, sqlite3.Error, TypeError, IndexError):
        return {
            "state": "unavailable",
            "pending_since": None,
            "known_deadline": None,
            "manual_recovery_required": True,
        }


def inspect_source(source, report):
    previous = report.get("source_guard", {})
    try:
        with closing(
            sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=0)
        ) as db:
            db.set_authorizer(source_authorizer)
            report["source_guard"] = safety_state(db)
            if report["source_guard"]["state"] == "unavailable":
                report["source_guard"]["known_deadline"] = previous.get("known_deadline")
    except (OSError, sqlite3.Error):
        # Preserve the last known deadline if final inspection is unavailable.
        report["source_guard"] = {
            **previous,
            "state": "unavailable",
            "manual_recovery_required": True,
        }


class LiveReadGate:
    """Write only the existing API slot and reviewed write-ahead guard lifecycle."""

    def __init__(self, source, report, expected_intent, checkpoint=None):
        self.source, self.report, self.expected_intent = source, report, expected_intent
        self.checkpoint = checkpoint or (lambda: None)

    def note_hold(self, seconds):
        if seconds > 3:
            deadline = max(
                time.time() + seconds, self.report.get("provider_hold", {}).get("deadline", 0)
            )
            self.report["provider_hold"] = {"deadline": deadline, "state": "pending"}
        # This is a diagnostic checkpoint, not a replacement for either guard.
        # The source pending token remains authoritative if this process is killed.
        self.checkpoint()

    @contextmanager
    def connection(self):
        with closing(
            sqlite3.connect(self.source.resolve().as_uri() + "?mode=rw", uri=True, timeout=0)
        ) as db:
            db.set_authorizer(source_authorizer)
            if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
                raise CanaryStop("source_not_tagged_workspace")
            guard_row(db)  # The operator must initialize the schema separately.
            yield db

    def check_intent(self, db):
        if intent_fingerprint(db) != self.expected_intent:
            raise CanaryStop("live_intent_changed")

    @contextmanager
    def request_budget(self):
        with self.connection() as db:
            budget = DatabaseReadBudget(
                db,
                clock=time.time,
                sleep=time.sleep,
                check=lambda: self.check_intent(db),
            )
            try:
                yield budget
            finally:
                self.report["source_guard"] = safety_state(db)

    def verify_intent(self):
        with self.connection() as db:
            with db:
                db.execute("BEGIN")
                self.check_intent(db)
                state = safety_state(db)
            self.report["source_guard"] = state
            if state["state"] != "ready":
                raise ReadSafetyHalt("api_guard_unresolved")

    def deadline(self):
        inspect_source(self.source, self.report)
        state = self.report["source_guard"]
        if state["state"] != "ready":
            raise ReadSafetyHalt("api_guard_unresolved")
        return state["known_deadline"]


class CoordinatedReadBudget:
    """Commit both guards before HTTP; never compensate by clearing a token.

    Two independent database commits cannot be atomic together. The copy reserves
    first so a copy failure cannot strand a new real-source token. If the source
    then rejects, stop with the copy token intact rather than retrying HTTP.
    """

    def __init__(self, copied, source, gate):
        if not isinstance(copied, DatabaseReadBudget):
            raise ReadSafetyHalt("copied_api_guard_missing")
        self.copied, self.source, self.gate = copied, source, gate

    def reserve(self, *, wait=False):
        self.copied.reserve(wait=wait)
        self._reserve_source()

    def reserve_for(self, local_budget, *, wait=False):
        # The copy checks local eligibility before committing any pending token.
        # Any one permitted wait occurs there, never between the two commits.
        self.copied.reserve_for(local_budget, wait=wait)
        self._reserve_source()

    def _reserve_source(self):
        try:
            self.source.reserve()
        except ReadBudgetBusy:
            raise ReadSafetyHalt("source_api_reservation_incomplete") from None
        finally:
            self.gate.report["copied_guard"] = safety_state(self.copied.db)
            self.gate.report["source_guard"] = safety_state(self.source.db)
        self.gate.report["live_api_gate_updates"] += 1
        self.gate.checkpoint()

    def response(self, seconds):
        failures = []
        try:
            self.gate.note_hold(seconds)
        except OSError:
            failures.append("checkpoint")
        # The underlying reader already preserved its local cooldown. Attempt
        # BOTH durable header finalizations even when either database rejects one.
        # Each reviewed guard owns its absolute five-second persistence deadline.
        for name, budget in (("copied", self.copied), ("source", self.source)):
            try:
                budget.response(seconds)
            except (ReadSafetyHalt, sqlite3.Error, OSError):
                failures.append(name)
            finally:
                self.gate.report[name + "_guard"] = safety_state(budget.db)
        if "source" not in failures:
            self.gate.report["live_api_gate_updates"] += 1
            if self.gate.report.get("provider_hold", {}).get("state") == "pending":
                self.gate.report["provider_hold"]["state"] = "persisted"
        self.gate.report["header_finalization_failures"] = failures
        try:
            self.gate.checkpoint()
        except OSError:
            failures.append("checkpoint")
        if failures:
            raise ReadSafetyHalt("canary_api_guard_finalization_failed")

    def failed_before_headers(self):
        # Neither pending token expires, even when the transport reports an error.
        self.gate.report["response_outcome"] = "unknown"
        self.gate.report["source_guard"] = safety_state(self.source.db)
        self.gate.report["copied_guard"] = safety_state(self.copied.db)
        self.gate.checkpoint()
        raise ReadSafetyHalt("api_guard_response_unknown")


class CoordinatedStatsReader(AuthenticatedReleaseStatsReader):
    def __init__(self, credentials, gate, **kwargs):
        super().__init__(credentials, **kwargs)
        self.live_gate = gate

    def _request(self, path, **kwargs):
        with self.live_gate.request_budget() as source:
            kwargs["budget"] = CoordinatedReadBudget(kwargs.get("budget"), source, self.live_gate)
            return super()._request(path, **kwargs)


def run_copy(path, source, credentials, report, *, checkpoint=None):
    if path.samefile(source):
        raise CanaryStop("copy_is_source")
    enabled, previous_success = scope_and_seed(path)
    report.update(enabled_rules=len(enabled), disabled_rules=EXPECTED_DISABLED)
    started = time.monotonic()
    observed = {release: [] for release in enabled}
    starts = {release: [] for release in enabled}
    report["rules"] = [
        {
            "rule": index + 1,
            "success_at": observed[release],
            "attempt_start_at": starts[release],
            "interval_seconds": [],
        }
        for index, release in enumerate(enabled)
    ]
    alerts = AggregatePriceAlerts(path, clock=time.time, poll_seconds=120)
    with closing(sqlite3.connect(path, timeout=0)) as db:
        expected_intent = intent_fingerprint(db)
    gate = LiveReadGate(source, report, expected_intent, checkpoint=checkpoint)
    gate.verify_intent()
    completed_cycles = 0
    transport = RecordingTransport(report)
    try:
        reader = CoordinatedStatsReader(
            credentials, gate, transport=transport, clock=time.monotonic, wall_clock=time.time
        )
        while time.monotonic() - started < MAX_SECONDS:
            begin = time.monotonic()
            with closing(sqlite3.connect(path, timeout=0)) as db:
                hold = db.execute(
                    "SELECT next_request FROM discogs_monitor_control WHERE id=1"
                ).fetchone()[0]
            hold = max(hold, gate.deadline())
            if hold - time.time() > MAX_SECONDS - (time.monotonic() - started):
                raise CanaryStop("provider_hold_exceeds_canary_window")
            if hold > time.time():
                time.sleep(min(hold - time.time(), MAX_SECONDS - (time.monotonic() - started)))
                continue
            result = alerts.poll_once(reader)
            report["poll_attempts"] += result["requests"]
            report["poll_statuses"][result["status"]] = (
                report["poll_statuses"].get(result["status"], 0) + 1
            )
            with closing(sqlite3.connect(path, timeout=0)) as db:
                for release, stamp, status in db.execute(
                    "SELECT release_id,last_success,status FROM discogs_price_samples"
                ):
                    if (
                        release not in observed
                        or stamp is None
                        or stamp == previous_success.get(release)
                    ):
                        continue
                    previous_success[release] = stamp
                    if status == "ok":
                        observed[release].append(stamp)
                        starts[release].append(report["responses"][-1]["request_start_at"])
            for index, release in enumerate(enabled):
                report["rules"][index]["interval_seconds"] = [
                    b - a for a, b in zip(starts[release], starts[release][1:], strict=False)
                ]
            cycles = min(map(len, observed.values()))
            if cycles > completed_cycles:
                report["cycles"].append(
                    {
                        "cycle": cycles,
                        "completed_at": time.time(),
                        "elapsed_seconds": time.monotonic() - started,
                    }
                )
                completed_cycles = cycles
            if completed_cycles >= CYCLES:
                gate.verify_intent()
                report["live_intent_unchanged_at_finish"] = True
                report["status"] = "three_cycles_observed"
                return
            if report["requests"] >= MAX_REQUESTS:
                raise CanaryStop("request_limit_reached")
            time.sleep(
                min(
                    max(0.5, 3 - (time.monotonic() - begin)),
                    max(0, MAX_SECONDS - (time.monotonic() - started)),
                )
            )
        raise CanaryStop("deadline_reached")
    finally:
        transport.finish()
        report["elapsed_seconds"] = time.monotonic() - started


def new_report():
    return {
        "status": "blocked",
        "requests": 0,
        "responses": [],
        "poll_statuses": {},
        "cycles": [],
        "rules": [],
        "poll_seconds": 120,
        "maximum_seconds": MAX_SECONDS,
        "live_api_gate_updates": 0,
        "poll_attempts": 0,
        "source_write_allowlist": sorted(".".join(item) for item in SOURCE_WRITES),
        "resume_requires_operator_check": True,
        "source_write_allowlist_enforced": True,
        "notifications_attempted": 0,
        "purchase_allowed": False,
        "clock_source": "system",
        "hard_stop_enforced_by": "parent_process",
        "counters_complete": False,
        "request_count_semantics": "recorded_transport_attempts_lower_bound",
    }


def write_report(path, report):
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(json.dumps(report, sort_keys=True, allow_nan=False))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def preserve_pending_hold(report):
    if report.get("provider_hold", {}).get("state") == "pending":
        report.setdefault("stop_reason", report.get("blocker", "child_stopped"))
        report.update(status="blocked", blocker="provider_hold_unpersisted")
        report["manual_recovery_required"] = True
    if report.get("source_guard", {}).get("state") in {"pending", "unavailable"}:
        report["manual_recovery_required"] = True
        report["status"] = "blocked"
        report.setdefault("blocker", "api_guard_unresolved")
    elif report.get("copied_guard", {}).get("state") in {"pending", "unavailable"}:
        report["status"] = "blocked"
        report.setdefault("blocker", "copied_api_guard_unresolved")


def execute_child(source, app_file, directory, result_path):
    report = new_report()
    try:
        with bounded_run() as deadline:
            credentials = AppCredentials(app_file)
            with private_copy(source, directory=directory, deadline=deadline) as path:
                run_copy(
                    path,
                    source,
                    credentials,
                    report,
                    checkpoint=lambda: write_report(result_path, report),
                )
    except CanaryStop as error:
        report.update(status="blocked", blocker=str(error))
    except ReadSafetyHalt as error:
        report.update(status="blocked", blocker=str(error))
    except (
        OSError,
        ValueError,
        RuntimeError,
        sqlite3.Error,
        httpx.HTTPError,
        KeyError,
        TypeError,
        OverflowError,
        RecursionError,
    ):
        report.update(status="blocked", blocker="canary_unavailable")
    inspect_source(source, report)
    preserve_pending_hold(report)
    report["child_final_report"] = True
    report["counters_complete"] = True
    report["request_count_semantics"] = "recorded_transport_attempts"
    write_report(result_path, report)


@contextmanager
def shutdown_signal_shield():
    """A second interruption must not abandon a child during TERM/KILL/join."""
    previous = {}
    try:
        for kind in (signal.SIGTERM, signal.SIGINT):
            previous[kind] = signal.signal(kind, signal.SIG_IGN)
        yield
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)


def stop_child(process):
    with shutdown_signal_shield():
        if process.is_alive():
            process.terminate()
            process.join(1)
        if process.is_alive():
            process.kill()
            process.join(1)
        if process.is_alive():
            raise CanaryStop("child_shutdown_unconfirmed")


def inspect_source_child(source, output):
    # No SQLite or source filesystem operations run in the supervisor itself.
    # Default termination also applies while native SQLite/filesystem I/O blocks.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    try:
        result = {}
        inspect_source(source, result)
        output.send(result["source_guard"])
    finally:
        output.close()


def bounded_inspect_source(source, report):
    """Inspect only in a separately bounded child, retaining checkpoints on failure."""
    previous = report.get("source_guard", {})
    report["source_guard"] = {
        **previous,
        "state": "unavailable",
        "manual_recovery_required": True,
    }
    report["source_inspection"] = "unavailable"
    context = multiprocessing.get_context("fork")
    incoming, outgoing = context.Pipe(duplex=False)
    process = context.Process(target=inspect_source_child, args=(source, outgoing), daemon=True)
    try:
        with shutdown_signal_shield():
            process.start()
            outgoing.close()
        process.join(INSPECT_SECONDS)
        if process.is_alive():
            report["source_inspection"] = "deadline_reached"
        elif process.exitcode == 0 and incoming.poll():
            # The child sends only one small sanitized record and must have exited
            # successfully; never recv a possibly partial message from a killed child.
            inspected = incoming.recv()
            if inspected["state"] == "unavailable":
                inspected["known_deadline"] = previous.get("known_deadline")
            report["source_guard"] = inspected
            report["source_inspection"] = "completed"
    except CanaryStop:
        report["source_inspection"] = "interrupted"
    except (OSError, EOFError, ValueError, RuntimeError, KeyError, TypeError):
        report["source_inspection"] = "unavailable"
    finally:
        stop_child(process)
        incoming.close()
        outgoing.close()
        process.close()


def supervised_run(source, app_file):
    # Independent supervision bounds blocked DNS/C calls too. The parent owns
    # cleanup, so a force-killed child cannot orphan the private database copy.
    if "fork" not in multiprocessing.get_all_start_methods():
        raise CanaryStop("supervised_runtime_unavailable")
    with tempfile.TemporaryDirectory(prefix="sniper-poll-canary-") as directory:
        result_path = Path(directory) / "result.json"
        process = multiprocessing.get_context("fork").Process(
            target=execute_child,
            args=(source, app_file, directory, result_path),
            daemon=True,
        )
        with shutdown_signal_shield():
            process.start()
        timed_out = False
        interruption = None
        try:
            process.join(MAX_SECONDS)
            timed_out = process.is_alive()
        except CanaryStop as error:
            interruption = str(error)
        finally:
            stop_child(process)
        report = new_report()
        if result_path.exists():
            with result_path.open() as stream:
                raw = stream.read(262145)
            if len(raw) > 262144:
                raise CanaryStop("canary_report_unavailable")
            report = json.loads(raw)
        if interruption is not None:
            report.update(status="blocked", blocker=interruption)
        elif timed_out:
            report.update(status="blocked", blocker="deadline_reached")
        elif process.exitcode != 0 or not result_path.exists():
            report.update(status="blocked", blocker="canary_child_unavailable")
        if (
            interruption is not None
            or timed_out
            or process.exitcode != 0
            or not report.get("child_final_report")
        ):
            report["counters_complete"] = False
            report["request_count_semantics"] = "recorded_transport_attempts_lower_bound"
        bounded_inspect_source(source, report)
        preserve_pending_hold(report)
        process.close()
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--discogs-app-file", type=Path, required=True)
    parser.add_argument("--run-live-read-canary", action="store_true")
    args = parser.parse_args(argv)
    if not args.run_live_read_canary:
        parser.error("explicit --run-live-read-canary required after candidate review and CI")
    report = new_report()
    previous = {}

    def interrupted(signum, frame):
        raise CanaryStop("interrupted")

    try:
        for kind in (signal.SIGTERM, signal.SIGINT):
            previous[kind] = signal.signal(kind, interrupted)
        report = supervised_run(args.database, args.discogs_app_file)
    except CanaryStop as error:
        report.update(status="blocked", blocker=str(error))
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError, TypeError, OverflowError):
        report.update(status="blocked", blocker="canary_unavailable")
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)
        print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "three_cycles_observed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
