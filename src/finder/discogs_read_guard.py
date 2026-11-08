"""Write-ahead shared API guard. Unknown requests never expire automatically."""

import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager

from finder.discogs_listing import PublicReadBudget, ReadBudgetBusy

PENDING_ATTENTION_SECONDS = 30
PERSIST_SECONDS = 5
SAFETY_EXIT = 78


class ReadSafetyHalt(RuntimeError):
    """No more HTTP until the durable guard is inspected and safely recovered."""


def guard_row(db):
    try:
        if [
            tuple(row)
            for row in db.execute("SELECT id,schema_version FROM discogs_api_guard_metadata")
        ] != [(1, 1)]:
            raise ReadSafetyHalt("api_guard_invalid")
        rows = db.execute(
            "SELECT id,schema_version,pending_token,pending_since,known_deadline "
            "FROM discogs_api_read_guard"
        ).fetchall()
        if len(rows) != 1:
            raise ReadSafetyHalt("api_guard_invalid")
        ident, version, token, since, deadline = rows[0]
        if (
            ident != 1
            or version != 1
            or type(deadline) not in (int, float)
            or not math.isfinite(deadline)
            or deadline < 0
            or token is None
            and since is not None
            or token is not None
            and (
                not isinstance(token, str)
                or not re.fullmatch(r"[a-f0-9]{32}", token)
                or type(since) not in (int, float)
                or not math.isfinite(since)
                or since < 0
            )
        ):
            raise ReadSafetyHalt("api_guard_invalid")
        return token, since, deadline
    except sqlite3.Error:
        raise ReadSafetyHalt("api_guard_unavailable") from None


def initialize_guard(db):
    """Add only the guard to an existing workspace; never repair/reset its row."""
    if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
        raise ReadSafetyHalt("api_guard_wrong_workspace")
    with db:
        db.execute("BEGIN IMMEDIATE")
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='discogs_api_read_guard'"
        ).fetchone()
        metadata = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='discogs_api_guard_metadata'"
        ).fetchone()
        if bool(exists) != bool(metadata):
            raise ReadSafetyHalt("api_guard_invalid")
        if not exists:
            db.execute(
                "CREATE TABLE discogs_api_guard_metadata (id INTEGER PRIMARY KEY, "
                "schema_version INTEGER NOT NULL)"
            )
            db.execute("INSERT INTO discogs_api_guard_metadata VALUES(1,1)")
            db.execute(
                "CREATE TABLE discogs_api_read_guard ("
                "id INTEGER PRIMARY KEY CHECK(id=1),schema_version INTEGER NOT NULL,"
                "pending_token TEXT,pending_since REAL,known_deadline REAL NOT NULL)"
            )
            db.execute("INSERT INTO discogs_api_read_guard VALUES(1,1,NULL,NULL,0)")
        guard_row(db)


def guard_pending(db, now):
    token, since, _ = guard_row(db)
    if token is not None:
        if not 0 <= now - since < PENDING_ATTENTION_SECONDS:
            raise ReadSafetyHalt("api_guard_recovery_required")
        return True
    return False


class DatabaseReadBudget(PublicReadBudget):
    """Each actual HTTP request commits an exclusive pending token first.

    The transaction ends before I/O. A pending token blocks all shared readers;
    its age affects diagnostics only, never permission to clear it. SQLite FULL
    durability is required; no network runs after a failed pre-request commit.
    """

    def __init__(self, db, *, clock=time.time, sleep=time.sleep, check=None):
        self.db, self.check = db, check
        self.token = None
        self.started_at = None
        self._local_budget = None
        if db.in_transaction:
            raise ReadSafetyHalt("api_guard_transaction_active")
        if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
            raise ReadSafetyHalt("api_guard_wrong_workspace")
        if db.execute("PRAGMA synchronous").fetchone()[0] < 2 or db.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0].lower() in {"off", "memory"}:
            raise ReadSafetyHalt("api_guard_durability_unavailable")
        super().__init__(clock=clock, sleep=sleep, claim=self._reserve_slot)

    def reserve_for(self, local_budget, *, wait=False):
        # Callback/subclass-backed budgets could perform I/O inside the transaction.
        if (
            type(local_budget) is not PublicReadBudget
            or local_budget.claim_callback is not None
            or local_budget.defer_callback is not None
        ):
            raise ReadSafetyHalt("api_guard_local_budget_unsupported")
        # Check shared eligibility before waiting; reserve the local slot inside
        # the short transaction before committing the pending HTTP token.
        self._local_budget = local_budget
        try:
            super().reserve(wait=wait)
        finally:
            self._local_budget = None

    @contextmanager
    def _nonblocking(self):
        timeout = self.db.execute("PRAGMA busy_timeout").fetchone()[0]
        self.db.execute("PRAGMA busy_timeout=0")
        try:
            yield
        finally:
            self.db.execute(f"PRAGMA busy_timeout={int(timeout)}")

    def _reserve_slot(self):
        with self._nonblocking():
            return self._reserve_now()

    def _reserve_now(self):
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                if self.check is not None:
                    self.check()
                now = self.clock()
                if guard_pending(self.db, now):
                    return 3.0
                _, _, known = guard_row(self.db)
                due = self.db.execute(
                    "SELECT next_request FROM discogs_monitor_control WHERE id=1"
                ).fetchone()[0]
                if type(due) not in (int, float) or not math.isfinite(due) or due < 0:
                    raise ReadSafetyHalt("api_budget_invalid")
                due = max(due, known)
                if due > now:
                    return due - now
                if self._local_budget is not None:
                    self._local_budget.reserve()
                token = uuid.uuid4().hex
                self.db.execute(
                    "UPDATE discogs_api_read_guard SET pending_token=?,pending_since=? WHERE id=1",
                    (token, now),
                )
                self.db.execute(
                    "UPDATE discogs_monitor_control SET next_request=max(next_request,?) "
                    "WHERE id=1",
                    (now + 3,),
                )
            # Only publish the token after commit returns successfully.
            self.token, self.started_at = token, now
            return 0.0
        except ReadBudgetBusy as error:
            return error.retry_after
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise ReadBudgetBusy("Shared API slot busy.", retry_after=3) from None
            raise ReadSafetyHalt("api_guard_reservation_failed") from None
        except sqlite3.Error:
            raise ReadSafetyHalt("api_guard_reservation_failed") from None

    def response(self, seconds):
        with self._nonblocking():
            self._finish_response(seconds)

    def _finish_response(self, seconds):
        if self.token is None:
            raise ReadSafetyHalt("api_guard_token_missing")
        deadline = max(self.started_at + 3, self.clock() + seconds if seconds > 3 else 0)
        until = time.monotonic() + PERSIST_SECONDS
        while True:
            try:
                with self.db:
                    self.db.execute("BEGIN IMMEDIATE")
                    token, _, known = guard_row(self.db)
                    if token != self.token:
                        raise ReadSafetyHalt("api_guard_token_changed")
                    self.db.execute(
                        "UPDATE discogs_monitor_control SET next_request=max(next_request,?) "
                        "WHERE id=1",
                        (max(known, deadline),),
                    )
                    self.db.execute(
                        "UPDATE discogs_api_read_guard SET pending_token=NULL,pending_since=NULL,"
                        "known_deadline=max(known_deadline,?) WHERE id=1 AND pending_token=?",
                        (deadline, self.token),
                    )
                self.token = None
                return
            except sqlite3.OperationalError as error:
                if (
                    "locked" not in str(error).lower() and "busy" not in str(error).lower()
                ) or time.monotonic() >= until:
                    raise ReadSafetyHalt("api_guard_hold_unpersisted") from None
                time.sleep(min(0.05, max(0, until - time.monotonic())))
            except sqlite3.Error:
                raise ReadSafetyHalt("api_guard_hold_unpersisted") from None

    def failed_before_headers(self):
        if self.token is not None:
            raise ReadSafetyHalt("api_guard_response_unknown")


def guard_diagnostics(db, now):
    """Read-only state, without publishing pending tokens or changing recovery state."""
    try:
        token, since, known = guard_row(db)
        pending = token is not None
        return {
            "state": "pending" if pending else "ready",
            "pending_since": since,
            "known_deadline": known,
            "manual_recovery_required": pending
            and not 0 <= now - since < PENDING_ATTENTION_SECONDS,
        }
    except ReadSafetyHalt:
        return {
            "state": "unavailable",
            "pending_since": None,
            "known_deadline": None,
            "manual_recovery_required": True,
        }
