"""Opt-in exact-release observation scheduler. No checkout or background thread.

The caller supplies a private fixed-action reader and explicitly calls ``tick``. This
module does not mount a browser socket or start polling when imported. A durable
reservation is made before each read, including across worker restarts.
"""

import json
import os
import socket
import stat
import time
import uuid
from pathlib import Path

from finder.discogs_candidate_pipeline import CandidateStopped
from finder.discogs_release_snapshots import (
    ReleaseSnapshots,
    RenderedReleaseSnapshot,
    assemble_release_pages,
)
from finder.discogs_rules import RulesStore

MIN_INTERVAL = 15 * 60
LEASE_SECONDS = 120
MAX_BACKOFF = 24 * 60 * 60
MAX_RESPONSE = 262_144
SHARED_SOCKET = Path("/var/lib/sniper/browser-bridge/sniper-read-only.sock")
SHARED_OWNER = 10001


def _cloud_control(store):
    if not store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_control'"
    ).fetchone():
        return None
    return store.db.execute("SELECT killed,revision FROM cloud_control WHERE id=1").fetchone()


class HostedReadError(Exception):
    def __init__(self, code, cooldown=0, *, stop=False):
        super().__init__(code)
        self.code, self.cooldown, self.stop = code, cooldown, stop


class HostedSocketReader:
    """Send only the fixed observe_release action over an owner-only local socket."""

    def __init__(self, path):
        self.path = Path(path)

    def ready(self):
        try:
            info = self.path.lstat()
            if not stat.S_ISSOCK(info.st_mode):
                return False
            if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600:
                return True
            if self.path != SHARED_SOCKET:
                return False
            parent = self.path.parent.lstat()
            return (
                stat.S_ISDIR(parent.st_mode)
                and parent.st_uid == info.st_uid == SHARED_OWNER
                and parent.st_gid == info.st_gid == os.getgid()
                and stat.S_IMODE(parent.st_mode) == 0o2710
                and stat.S_IMODE(info.st_mode) == 0o660
            )
        except OSError:
            return False

    def __call__(self, release):
        if type(release) is not int or not 0 < release <= 9007199254740991:
            raise ValueError("Invalid exact release ID.")
        if not self.ready():
            raise HostedReadError("socket_not_private", stop=True)
        request = json.dumps({"action": "observe_release", "release_id": str(release)}) + "\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(95)
            connection.connect(str(self.path))
            connection.sendall(request.encode())
            with connection.makefile("rb") as stream:
                raw = stream.readline(MAX_RESPONSE + 2)
        if not raw.endswith(b"\n") or len(raw) > MAX_RESPONSE + 1:
            raise HostedReadError("invalid_reader_response", stop=True)
        try:
            response = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise HostedReadError("invalid_reader_response", stop=True) from None
        if (
            not isinstance(response, dict)
            or response.get("source") != "hosted_browser_read_only"
            or response.get("purchase_allowed") is not False
            or response.get("stop_required") is not True
        ):
            raise HostedReadError("invalid_reader_response", stop=True)
        if response.get("status") != "observed":
            code = response.get("code", "reader_incomplete")
            if not isinstance(code, str) or not code.isidentifier() or len(code) > 48:
                code = "reader_incomplete"
            cooldown = response.get("cooldown_seconds", 0)
            cooldown = cooldown if type(cooldown) is int and 0 <= cooldown <= MAX_BACKOFF else 0
            raise HostedReadError(
                code,
                cooldown,
                stop=code in {"auth_required", "access_restricted", "unsupported_read_request"},
            )
        if "pages" in response:
            if "observation" in response or not isinstance(response["pages"], list):
                raise HostedReadError("ambiguous_page_response", stop=True)
            try:
                observation = RenderedReleaseSnapshot.model_validate(
                    assemble_release_pages(release, response["pages"])
                )
            except ValueError:
                raise HostedReadError("unverified_page_coverage", stop=True) from None
        else:
            observation = RenderedReleaseSnapshot.model_validate(response.get("observation"))
        if observation.release_id != release or not observation.complete:
            raise HostedReadError("incomplete_or_wrong_release", stop=True)
        return observation.model_dump()


class HostedReleaseScheduler:
    """One serialized read per tick; disabled until explicitly resumed and wired."""

    def __init__(self, database, reader, *, clock=time.time, snapshots=None, candidates=None):
        self.database, self.reader, self.clock = database, reader, clock
        self.snapshots = snapshots or ReleaseSnapshots(database, clock=clock)
        self.candidates = candidates
        store = RulesStore(database)
        try:
            with store.db:
                store.db.executescript("""
                    CREATE TABLE IF NOT EXISTS discogs_hosted_schedule (
                        id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 1,
                        revision INTEGER NOT NULL DEFAULT 0, next_request REAL NOT NULL DEFAULT 0,
                        lease_token TEXT, lease_until REAL NOT NULL DEFAULT 0,
                        last_error TEXT);
                    INSERT OR IGNORE INTO discogs_hosted_schedule(id) VALUES(1);
                    CREATE TABLE IF NOT EXISTS discogs_hosted_due (
                        release_id INTEGER PRIMARY KEY, next_due REAL NOT NULL DEFAULT 0,
                        failures INTEGER NOT NULL DEFAULT 0,
                        last_status TEXT NOT NULL DEFAULT 'waiting',
                        last_attempt REAL);
                """)
        finally:
            store.close()

    def set_paused(self, paused, *, expected_revision):
        if type(paused) is not bool or type(expected_revision) is not int:
            raise ValueError("Explicit pause and revision required.")
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                result = store.db.execute(
                    "UPDATE discogs_hosted_schedule SET paused=?,revision=revision+1 "
                    "WHERE id=1 AND revision=?",
                    (int(paused), expected_revision),
                )
                if result.rowcount != 1:
                    raise RuntimeError("Scheduler state changed.")
            return self.health()
        finally:
            store.close()

    def health(self):
        store = RulesStore(self.database)
        try:
            row = store.db.execute(
                "SELECT paused,revision,next_request,lease_until,last_error "
                "FROM discogs_hosted_schedule WHERE id=1"
            ).fetchone()
            due = store.db.execute(
                "SELECT release_id,next_due,failures,last_status,last_attempt "
                "FROM discogs_hosted_due ORDER BY release_id"
            ).fetchall()
            control = _cloud_control(store)
            return {
                "paused": bool(row[0]),
                "revision": row[1],
                "next_request": row[2],
                "lease_until": row[3],
                "last_error": row[4],
                "kill_switch": not control or bool(control[0]),
                "reader_ready": bool(getattr(self.reader, "ready", lambda: False)()),
                "targets": [
                    dict(
                        zip(
                            ("release_id", "next_due", "failures", "last_status", "last_attempt"),
                            item,
                            strict=True,
                        )
                    )
                    for item in due
                ],
                "purchase_allowed": False,
            }
        finally:
            store.close()

    def _claim(self):
        now = self.clock()
        store = RulesStore(self.database)
        try:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                state = store.db.execute(
                    "SELECT paused,revision,next_request,lease_until "
                    "FROM discogs_hosted_schedule WHERE id=1"
                ).fetchone()
                control = _cloud_control(store)
                if state[0] or not control or control[0] or now < max(state[2], state[3]):
                    return None
                rules = store.snapshot()
                enabled = sorted(
                    t["release_id"]
                    for t in rules["targets"]
                    if t["enabled"] and t["release_id"] not in rules["owned"]
                )
                store.db.execute(
                    "DELETE FROM discogs_hosted_due WHERE release_id NOT IN "
                    "(SELECT release_id FROM targets)"
                )
                for release in enabled:
                    store.db.execute(
                        "INSERT OR IGNORE INTO discogs_hosted_due(release_id) VALUES(?)", (release,)
                    )
                enabled_set = set(enabled)
                selected = next(
                    (
                        row[0]
                        for row in store.db.execute(
                            "SELECT release_id FROM discogs_hosted_due WHERE next_due<=? "
                            "ORDER BY next_due,release_id",
                            (now,),
                        )
                        if row[0] in enabled_set
                    ),
                    None,
                )
                if selected is None:
                    return None
                release = selected
                baseline = store.db.execute(
                    "SELECT revision FROM discogs_release_baselines WHERE release_id=?", (release,)
                ).fetchone()
                token = uuid.uuid4().hex
                store.db.execute(
                    "UPDATE discogs_hosted_schedule SET lease_token=?,lease_until=?,"
                    "next_request=? WHERE id=1",
                    (token, now + LEASE_SECONDS, now + MIN_INTERVAL),
                )
                store.db.execute(
                    "UPDATE discogs_hosted_due SET next_due=?,last_attempt=? WHERE release_id=?",
                    (now + MIN_INTERVAL, now, release),
                )
                return (
                    release,
                    token,
                    state[1],
                    rules["revision"],
                    control[1],
                    baseline[0] if baseline else 0,
                )
        finally:
            store.close()

    def tick(self):
        """Perform at most one due read; never return purchase eligibility."""
        claim = self._claim()
        if claim is None:
            return {"status": "idle", "purchase_allowed": False}
        release, token, schedule_revision, rules_revision, control_revision, baseline_revision = (
            claim
        )
        error = None
        result = None
        try:
            if self.reader is None:
                raise HostedReadError("reader_unavailable", stop=True)
            observation = self.reader(release)
            parsed = RenderedReleaseSnapshot.model_validate(observation)
            if not parsed.complete or parsed.status != "observed" or parsed.release_id != release:
                raise HostedReadError("incomplete_or_wrong_release")
            result = self.snapshots.ingest(
                parsed.model_dump(),
                expected_rules_revision=rules_revision,
                expected_control_revision=control_revision,
                expected_baseline_revision=baseline_revision,
                expected_schedule_revision=schedule_revision,
                expected_schedule_token=token,
            )
            if self.candidates is not None:
                self.candidates.capture(
                    release,
                    rules_revision=rules_revision,
                    control_revision=control_revision,
                    schedule_revision=schedule_revision,
                    lease_token=token,
                )
        except HostedReadError as exc:
            error = exc
        except CandidateStopped:
            error = HostedReadError("candidate_capture_stopped", stop=True)
        except (OSError, TimeoutError, ValueError, RuntimeError):
            error = HostedReadError("read_or_revision_failed")
        except Exception:
            # Unknown reader failures must stop, without logging the session or payload.
            error = HostedReadError("unexpected_reader_failure", stop=True)
        finally:
            store = RulesStore(self.database)
            try:
                with store.db:
                    store.db.execute("BEGIN IMMEDIATE")
                    state = store.db.execute(
                        "SELECT lease_token FROM discogs_hosted_schedule WHERE id=1"
                    ).fetchone()
                    if state and state[0] == token:
                        now = self.clock()
                        if error:
                            row = store.db.execute(
                                "SELECT failures FROM discogs_hosted_due WHERE release_id=?",
                                (release,),
                            ).fetchone()
                            failures = min((row[0] if row else 0) + 1, 8)
                            delay = max(
                                MIN_INTERVAL,
                                error.cooldown,
                                min(MAX_BACKOFF, MIN_INTERVAL * 2**failures),
                            )
                            store.db.execute(
                                "UPDATE discogs_hosted_due SET failures=?,next_due=?,"
                                "last_status=? WHERE release_id=?",
                                (failures, now + delay, error.code, release),
                            )
                            store.db.execute(
                                "UPDATE discogs_hosted_schedule SET last_error=?,"
                                "paused=max(paused,?) WHERE id=1",
                                (error.code, int(error.stop)),
                            )
                        else:
                            store.db.execute(
                                "UPDATE discogs_hosted_due SET failures=0,last_status=?,"
                                "next_due=? WHERE release_id=?",
                                (result["status"], now + MIN_INTERVAL, release),
                            )
                            store.db.execute(
                                "UPDATE discogs_hosted_schedule SET last_error=NULL WHERE id=1"
                            )
                        store.db.execute(
                            "UPDATE discogs_hosted_schedule SET lease_token=NULL,"
                            "lease_until=0 WHERE id=1"
                        )
            finally:
                store.close()
        return {
            "status": "blocked" if error else result["status"],
            "release_id": release,
            "code": error.code if error else None,
            "purchase_allowed": False,
        }
