"""Durable execution *simulation*, not a Discogs purchasing implementation.

This isolated ledger has no provider, browser, credential, cart, order or payment
capability. Enabling a live configuration flag fails closed. Only the exact built-in
mock adapter is accepted, not arbitrary objects claiming to be synthetic.

A release keeps its one-copy slot after every outcome, including decline or
uncertainty. There is deliberately no retry, expiry, release, or reconciliation API.
An interrupted ``attempting`` record remains blocked across process restarts.
Callers supply server-validated policy flags; these flags authorize a simulation
only, never a purchase. A new ledger starts killed; that switch is durable.
"""

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

APPLICATION_ID = 1178882648
SCHEMA_VERSION = 1


class GuardBlocked(RuntimeError):
    """A bounded, machine-readable reason with no external exception payload."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ExecutionRequest(BaseModel):
    """Synthetic input only. No free-form metadata or seller text is accepted."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    release_id: int = Field(gt=0, le=9007199254740991)
    idempotency_key: str = Field(min_length=36, max_length=36)
    target_active: bool
    eligible_for_review: bool = False
    already_owned: bool = False
    source: Literal["synthetic"] = "synthetic"
    live_requested: bool = False

    @field_validator("idempotency_key")
    @classmethod
    def canonical_uuid(cls, value):
        parsed = UUID(value)
        if str(parsed) != value or parsed.int == 0:
            raise ValueError("Use a canonical nonzero UUID idempotency key.")
        return value


class SyntheticOutcome(StrEnum):
    CONFIRMED = "synthetic_confirmed"
    DECLINED = "synthetic_declined"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: str
    release_id: int
    state: str
    mode: Literal["synthetic_only"] = "synthetic_only"
    purchase_allowed: Literal[False] = False


class SyntheticCheckoutAdapter(Protocol):
    """A simulation-only interface, explicitly NOT a live checkout extension point."""

    synthetic_only: Literal[True]

    def simulate(self, attempt: Attempt) -> SyntheticOutcome: ...


@dataclass(frozen=True, slots=True)
class MockCheckoutAdapter:
    """Deterministic in-memory fixture. No side effects or external dependencies."""

    outcome: SyntheticOutcome = SyntheticOutcome.CONFIRMED
    synthetic_only: Literal[True] = True

    def __post_init__(self):
        if type(self.outcome) is not SyntheticOutcome or self.synthetic_only is not True:
            raise ValueError("Only an explicit synthetic outcome is supported.")

    def simulate(self, attempt: Attempt) -> SyntheticOutcome:
        return self.outcome


class ExecutionGuard:
    """A separate SQLite file with atomic claims and append-only lifecycle audit.

    Each operation owns a connection, so instances can be used concurrently by
    threads or separate processes. BEGIN IMMEDIATE serializes competing claims.
    The kill switch is checked atomically when reserving and starting a mock; it
    cannot undo an attempt already started. Audit records contain generated IDs,
    enumerated events and timestamps, never submitted keys or exception text.
    """

    def __init__(self, path: Path, *, create=False, live_enabled=False):
        if type(create) is not bool or type(live_enabled) is not bool:
            raise ValueError("Execution configuration must use booleans.")
        if live_enabled:
            raise GuardBlocked("live_capability_unsupported")
        self.path = Path(path).absolute()
        if self.path.is_symlink() or self.path.name.startswith(".env"):
            raise ValueError("Use a dedicated regular execution ledger file.")
        if create:
            with self.path.open("x"):
                pass
        elif not self.path.is_file():
            raise ValueError("Execution ledger does not exist; explicitly initialize it.")
        with self._connection() as db:
            if create:
                db.executescript(f"""
                    BEGIN IMMEDIATE;
                    PRAGMA application_id={APPLICATION_ID};
                    PRAGMA user_version={SCHEMA_VERSION};
                    CREATE TABLE control (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        killed INTEGER NOT NULL CHECK(killed IN (0,1))
                    );
                    INSERT INTO control VALUES (1,1);
                    CREATE TABLE attempts (
                        attempt_id TEXT PRIMARY KEY,
                        request_hash TEXT NOT NULL UNIQUE,
                        release_id INTEGER NOT NULL UNIQUE CHECK(release_id>0),
                        state TEXT NOT NULL CHECK(state IN (
                            'reserved','attempting','synthetic_confirmed',
                            'synthetic_declined','uncertain'
                        ))
                    );
                    CREATE TABLE audit (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at TEXT NOT NULL,
                        attempt_id TEXT REFERENCES attempts(attempt_id),
                        event TEXT NOT NULL CHECK(event IN (
                            'initialized_killed','kill_enabled','kill_disabled',
                            'reserved','mock_started','synthetic_confirmed',
                            'synthetic_declined','uncertain'
                        ))
                    );
                    CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit
                        BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
                    CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit
                        BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
                    CREATE TRIGGER audit_no_replace BEFORE INSERT ON audit
                    WHEN EXISTS(SELECT 1 FROM audit WHERE sequence=NEW.sequence)
                        BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
                    CREATE TRIGGER attempt_no_delete BEFORE DELETE ON attempts
                        BEGIN SELECT RAISE(ABORT,'one-copy slots cannot be deleted'); END;
                    CREATE TRIGGER attempt_no_replace BEFORE INSERT ON attempts
                    WHEN EXISTS(SELECT 1 FROM attempts WHERE
                        attempt_id=NEW.attempt_id OR request_hash=NEW.request_hash OR
                        release_id=NEW.release_id)
                        BEGIN SELECT RAISE(ABORT,'one-copy slots cannot be replaced'); END;
                    CREATE TRIGGER attempt_valid_transition BEFORE UPDATE ON attempts
                    WHEN NOT (
                        NEW.attempt_id=OLD.attempt_id AND
                        NEW.request_hash=OLD.request_hash AND
                        NEW.release_id=OLD.release_id AND (
                            (OLD.state='reserved' AND NEW.state='attempting') OR
                            (OLD.state='attempting' AND NEW.state IN (
                                'synthetic_confirmed','synthetic_declined','uncertain'
                            ))
                        )
                    ) BEGIN SELECT RAISE(ABORT,'invalid attempt transition'); END;
                    COMMIT;
                """)
                db.execute("BEGIN IMMEDIATE")
                self._audit(db, "initialized_killed")
                db.commit()
            if (
                db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            ):
                raise ValueError("Not an execution ledger. Existing data was not changed.")
            self._killed(db)

    @contextmanager
    def _connection(self):
        # mode=rw prevents accidental creation if a file disappears after initialization.
        db = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=rw",
            uri=True,
            timeout=5,
            isolation_level=None,
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _audit(db, event, attempt_id=None):
        db.execute(
            "INSERT INTO audit(occurred_at,attempt_id,event) VALUES (?,?,?)",
            (datetime.now(UTC).isoformat(), attempt_id, event),
        )

    @staticmethod
    def _killed(db):
        rows = db.execute("SELECT id,killed FROM control").fetchall()
        if len(rows) != 1 or rows[0]["id"] != 1 or rows[0]["killed"] not in (0, 1):
            raise GuardBlocked("invalid_kill_switch_state")
        return bool(rows[0]["killed"])

    @staticmethod
    def _request(value):
        # Revalidate even a model built using an unsafe Pydantic construction helper.
        if isinstance(value, ExecutionRequest):
            value = value.model_dump(warnings=False)
        request = ExecutionRequest.model_validate(value)
        if request.live_requested:
            raise GuardBlocked("live_capability_unsupported")
        return request

    @staticmethod
    def _policy(request):
        if not request.target_active:
            raise GuardBlocked("target_inactive")
        if request.already_owned:
            raise GuardBlocked("already_owned")
        if not request.eligible_for_review:
            raise GuardBlocked("not_eligible_for_review")

    @staticmethod
    def _hash(request):
        return hashlib.sha256(request.idempotency_key.encode("ascii")).hexdigest()

    @staticmethod
    def _attempt(row):
        return Attempt(row["attempt_id"], row["release_id"], row["state"])

    def _reserve(self, db, request):
        self._policy(request)
        if self._killed(db):
            raise GuardBlocked("kill_switch_on")
        row = db.execute(
            "SELECT * FROM attempts WHERE request_hash=?", (self._hash(request),)
        ).fetchone()
        if row is not None:
            if row["release_id"] != request.release_id:
                raise GuardBlocked("idempotency_conflict")
            return self._attempt(row)
        if db.execute(
            "SELECT 1 FROM attempts WHERE release_id=?", (request.release_id,)
        ).fetchone():
            raise GuardBlocked("one_copy_reserved")
        attempt_id = str(uuid4())
        db.execute(
            "INSERT INTO attempts VALUES (?,?,?,'reserved')",
            (attempt_id, self._hash(request), request.release_id),
        )
        self._audit(db, "reserved", attempt_id)
        return Attempt(attempt_id, request.release_id, "reserved")

    def reserve(self, value):
        """Reserve one synthetic slot. An identical request returns its existing state."""
        request = self._request(value)
        with self._transaction() as db:
            return self._reserve(db, request)

    def execute(self, value, adapter: SyntheticCheckoutAdapter):
        """Run the built-in mock at most once; uncertain/in-flight attempts never retry."""
        request = self._request(value)
        if type(adapter) is not MockCheckoutAdapter or adapter.synthetic_only is not True:
            raise GuardBlocked("adapter_capability_unsupported")
        if type(adapter.outcome) is not SyntheticOutcome:
            raise GuardBlocked("invalid_mock_outcome")
        with self._transaction() as db:
            attempt = self._reserve(db, request)
            if attempt.state != "reserved":
                return attempt
            db.execute(
                "UPDATE attempts SET state='attempting' WHERE attempt_id=?", (attempt.attempt_id,)
            )
            self._audit(db, "mock_started", attempt.attempt_id)
        # Commit the consumed attempt before invoking even the synthetic adapter.
        # A process crash here leaves 'attempting', which cannot be executed again.
        try:
            outcome = adapter.simulate(
                Attempt(attempt.attempt_id, attempt.release_id, "attempting")
            )
            if type(outcome) is not SyntheticOutcome:
                outcome = SyntheticOutcome.UNCERTAIN
        except Exception:
            # No exception message, seller payload or credential is logged or returned.
            outcome = SyntheticOutcome.UNCERTAIN
        with self._transaction() as db:
            db.execute(
                "UPDATE attempts SET state=? WHERE attempt_id=?",
                (outcome.value, attempt.attempt_id),
            )
            self._audit(db, outcome.value, attempt.attempt_id)
        return Attempt(attempt.attempt_id, attempt.release_id, outcome.value)

    def set_kill_switch(self, killed: bool):
        if type(killed) is not bool:
            raise ValueError("Kill switch must be a boolean.")
        with self._transaction() as db:
            if self._killed(db) != killed:
                db.execute("UPDATE control SET killed=? WHERE id=1", (int(killed),))
                self._audit(db, "kill_enabled" if killed else "kill_disabled")

    def get_attempt(self, idempotency_key: str):
        request = ExecutionRequest(
            release_id=1, idempotency_key=idempotency_key, target_active=False
        )
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM attempts WHERE request_hash=?", (self._hash(request),)
            ).fetchone()
            return None if row is None else self._attempt(row)

    def snapshot(self):
        with self._transaction() as db:
            return {
                "mode": "synthetic_only",
                "purchase_allowed": False,
                "kill_switch_on": self._killed(db),
                "attempts": [
                    self._attempt(row)
                    for row in db.execute("SELECT * FROM attempts ORDER BY rowid")
                ],
                "audit": [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY sequence")],
            }
