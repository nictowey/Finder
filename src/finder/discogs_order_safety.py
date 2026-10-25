"""Offline order-safety model. No live adapter, credentials, cart or provider I/O.

Grants in this file authorize only the synthetic adapter below. Nothing in the
running Sniper service constructs this store or calls an order method.
"""

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from finder.discogs_rules import Observation, StrictInput, Target, amount, evaluate

APPLICATION_ID = 1397637170
SCHEMA_VERSION = 1
MAX_ID = 9007199254740991
MAX_QUOTE_AGE = 300


class OrderStopped(RuntimeError):
    """A fixed local stop reason, never a provider response or secret."""


class SimulatedCrash(BaseException):
    """Test-only process interruption after an attempt was durably fenced."""


def _uuid(value):
    try:
        parsed = UUID(value) if type(value) is str else None
    except (ValueError, AttributeError):
        parsed = None
    if parsed is None or parsed.int == 0 or str(parsed) != value:
        raise OrderStopped("invalid_opaque_reference")
    return value


def _target_digest(target: Target):
    return hashlib.sha256(
        json.dumps(target.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class SyntheticFinalQuote(StrictInput):
    """Fixture evidence only; never a claim about Discogs' payable total."""

    mode: Literal["synthetic_final_quote"]
    release_id: int = Field(gt=0, le=MAX_ID)
    listing_id: str = Field(pattern=r"^[1-9][0-9]{0,15}$")
    rules_revision: int = Field(ge=0, le=MAX_ID)
    item: str
    shipping: str
    tax: str
    fees: str
    total: str
    currency: Literal["USD", "EUR", "GBP", "CAD", "AUD"]
    destination_country: str = Field(pattern=r"^[A-Z]{2}$")
    destination_postal: str = Field(pattern=r"^[A-Z0-9 -]{1,16}$")
    destination_ref: str
    payment_ref: str
    observed_at: float
    expires_at: float
    quantity: Literal[1]
    final_quote_verified: Literal[True]

    _money = field_validator("item", "shipping", "tax", "fees", "total", mode="before")(amount)

    @field_validator("destination_ref", "payment_ref")
    @classmethod
    def opaque_reference(cls, value):
        return _uuid(value)


@dataclass(frozen=True, slots=True)
class SyntheticReceipt:
    intent_id: str
    provider_order_id: str
    release_id: int
    listing_id: str
    total: str
    currency: str
    quote_fingerprint: str
    destination_ref: str
    payment_ref: str


@dataclass(frozen=True, slots=True)
class SyntheticAbsence:
    """Fixture lookup evidence for one fenced attempt, not permission to try again."""

    intent_id: str
    release_id: int
    listing_id: str
    quote_fingerprint: str
    destination_ref: str
    payment_ref: str
    checked_at: float


class SyntheticOrderAdapter:
    """In-memory fixture; has no browser, HTTP, provider or payment methods."""

    def __init__(self, *, submit_result=None, lookup_result=None):
        self.submit_result, self.lookup_result = submit_result, lookup_result
        self.submit_calls = self.lookup_calls = 0

    def submit(self, intent_id):
        self.submit_calls += 1
        if isinstance(self.submit_result, BaseException):
            raise self.submit_result
        return self.submit_result

    def lookup(self, intent_id):
        self.lookup_calls += 1
        if isinstance(self.lookup_result, BaseException):
            raise self.lookup_result
        return self.lookup_result


class OrderSafetyStore:
    """Separate owner-only ledger; a release slot is never reused automatically."""

    def __init__(self, path: Path, *, create=False):
        self.path = Path(path).absolute()
        if self.path.is_symlink() or self.path.name.startswith(".env"):
            raise ValueError("Use a dedicated regular order-safety file.")
        if create:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        elif not self.path.is_file():
            raise ValueError("Order-safety file does not exist; explicitly initialize it.")
        details = self.path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_uid != os.geteuid()
        ):
            raise ValueError("Order-safety file must be an owner-only regular file.")
        with self._connection() as db:
            if create:
                db.executescript(f"""
                    BEGIN IMMEDIATE;
                    PRAGMA application_id={APPLICATION_ID};
                    PRAGMA user_version={SCHEMA_VERSION};
                    CREATE TABLE grants (
                        grant_id TEXT PRIMARY KEY, release_id INTEGER NOT NULL,
                        rules_revision INTEGER NOT NULL, target_digest TEXT NOT NULL,
                        currency TEXT NOT NULL, max_all_in TEXT NOT NULL,
                        destination_ref TEXT NOT NULL, payment_ref TEXT NOT NULL,
                        issued_at REAL NOT NULL, expires_at REAL NOT NULL,
                        revoked_at REAL, mode TEXT NOT NULL CHECK(mode='simulation_only'),
                        one_copy_limit INTEGER NOT NULL CHECK(one_copy_limit=1)
                    );
                    CREATE TABLE intents (
                        release_id INTEGER PRIMARY KEY, intent_id TEXT NOT NULL UNIQUE,
                        grant_id TEXT NOT NULL, listing_id TEXT NOT NULL,
                        rules_revision INTEGER NOT NULL, quote_fingerprint TEXT NOT NULL,
                        quoted_total TEXT NOT NULL, currency TEXT NOT NULL,
                        destination_ref TEXT NOT NULL, payment_ref TEXT NOT NULL,
                        prepared_at REAL NOT NULL, quote_expires_at REAL NOT NULL,
                        state TEXT NOT NULL CHECK(state IN
                            ('prepared','submitting','uncertain','confirmed','absent')),
                        provider_order_id TEXT UNIQUE,
                        FOREIGN KEY(grant_id) REFERENCES grants(grant_id)
                    );
                    CREATE TRIGGER no_grant_delete BEFORE DELETE ON grants
                      BEGIN SELECT RAISE(ABORT,'grants cannot be deleted'); END;
                    CREATE TRIGGER no_intent_delete BEFORE DELETE ON intents
                      BEGIN SELECT RAISE(ABORT,'intents cannot be deleted'); END;
                    COMMIT;
                """)
            elif (
                db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            ):
                raise ValueError("Not a supported order-safety file.")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(
            f"{self.path.as_uri()}?mode=rw", timeout=5, isolation_level=None, uri=True
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _grant(db, grant_id, target, revision, now):
        row = db.execute("SELECT * FROM grants WHERE grant_id=?", (grant_id,)).fetchone()
        if (
            row is None
            or row["mode"] != "simulation_only"
            or row["one_copy_limit"] != 1
            or row["revoked_at"] is not None
            or not row["issued_at"] <= now < row["expires_at"]
            or row["release_id"] != target.release_id
            or row["rules_revision"] != revision
            or row["target_digest"] != _target_digest(target)
            or row["currency"] != target.currency
            or not target.enabled
        ):
            raise OrderStopped("grant_missing_stale_revoked_or_expired")
        return row

    def issue_simulated_grant(
        self,
        grant_id,
        target: Target,
        rules_revision,
        max_all_in,
        destination_ref,
        payment_ref,
        expires_at,
        *,
        now,
    ):
        _uuid(grant_id), _uuid(destination_ref), _uuid(payment_ref)
        if (
            not isinstance(target, Target)
            or not target.enabled
            or type(rules_revision) is not int
            or not 0 <= rules_revision <= MAX_ID
            or type(now) not in {int, float}
            or type(expires_at) not in {int, float}
            or not now < expires_at <= now + 30 * 24 * 3600
        ):
            raise OrderStopped("invalid_simulated_grant")
        try:
            ceiling = amount(max_all_in)
        except ValueError:
            raise OrderStopped("invalid_simulated_grant") from None
        if not 0 < Decimal(ceiling) <= Decimal(target.max_total):
            raise OrderStopped("grant_ceiling_exceeds_target")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO grants VALUES (?,?,?,?,?,?,?,?,?,?,NULL,'simulation_only',1)",
                    (
                        grant_id,
                        target.release_id,
                        rules_revision,
                        _target_digest(target),
                        target.currency,
                        ceiling,
                        destination_ref,
                        payment_ref,
                        now,
                        expires_at,
                    ),
                )
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
                raise OrderStopped("grant_already_exists") from None
        return {"grant_id": grant_id, "mode": "simulation_only", "purchase_allowed": False}

    def revoke(self, grant_id, *, now):
        _uuid(grant_id)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE grants SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL",
                (now, grant_id),
            ).rowcount
            db.commit()
        if changed != 1:
            raise OrderStopped("grant_missing_or_already_revoked")

    @staticmethod
    def _quote(grant, target, revision, observation, quote, now):
        if not isinstance(quote, SyntheticFinalQuote) or not isinstance(observation, Observation):
            raise OrderStopped("synthetic_quote_and_observation_required")
        if (
            observation.source != "synthetic"
            or quote.release_id != target.release_id
            or quote.listing_id != observation.listing_id
            or quote.rules_revision != revision
            or quote.currency != grant["currency"]
            or quote.destination_ref != grant["destination_ref"]
            or quote.payment_ref != grant["payment_ref"]
            or quote.destination_country != target.destination_country
            or quote.destination_postal != target.destination_postal
            or not quote.observed_at <= now < quote.expires_at
            or now - quote.observed_at > MAX_QUOTE_AGE
            or quote.expires_at - quote.observed_at > MAX_QUOTE_AGE
            or any(
                getattr(quote, field) != getattr(observation, field)
                for field in ("item", "shipping", "tax", "fees")
            )
        ):
            raise OrderStopped("quote_identity_destination_or_freshness_unverified")
        total = sum(
            (Decimal(getattr(quote, field)) for field in ("item", "shipping", "tax", "fees")),
            Decimal(0),
        )
        if (
            total != Decimal(quote.total)
            or total > Decimal(grant["max_all_in"])
            or total > Decimal(target.max_total)
        ):
            raise OrderStopped("quote_changed_or_above_ceiling")
        review = evaluate(target, observation, now=datetime.fromtimestamp(now, UTC))
        if not review["eligible_for_review"] or review["total"] != quote.total:
            raise OrderStopped("candidate_not_eligible_for_simulation")
        return _fingerprint(quote.model_dump())

    def prepare(self, grant_id, intent_id, target, revision, observation, quote, *, now):
        _uuid(grant_id), _uuid(intent_id)
        if not isinstance(target, Target) or type(revision) is not int:
            raise OrderStopped("invalid_target_revision")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            grant = self._grant(db, grant_id, target, revision, now)
            fingerprint = self._quote(grant, target, revision, observation, quote, now)
            existing = db.execute(
                "SELECT * FROM intents WHERE release_id=? OR intent_id=?",
                (target.release_id, intent_id),
            ).fetchone()
            if existing:
                if (
                    existing["intent_id"] == intent_id
                    and existing["release_id"] == target.release_id
                    and existing["grant_id"] == grant_id
                    and existing["quote_fingerprint"] == fingerprint
                ):
                    return self._status(existing)
                raise OrderStopped("one_copy_or_idempotency_conflict")
            db.execute(
                "INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'prepared',NULL)",
                (
                    target.release_id,
                    intent_id,
                    grant_id,
                    quote.listing_id,
                    revision,
                    fingerprint,
                    quote.total,
                    quote.currency,
                    quote.destination_ref,
                    quote.payment_ref,
                    now,
                    quote.expires_at,
                ),
            )
            db.commit()
        return {"intent_id": intent_id, "state": "prepared", "purchase_allowed": False}

    @staticmethod
    def _status(row):
        return {
            "intent_id": row["intent_id"],
            "release_id": row["release_id"],
            "state": row["state"],
            "purchase_allowed": False,
            "requires_reconciliation": row["state"] in {"submitting", "uncertain"},
        }

    def status(self, intent_id):
        _uuid(intent_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            return self._status(row) if row else None

    @staticmethod
    def _receipt(row, receipt):
        return (
            isinstance(receipt, SyntheticReceipt)
            and _uuid(receipt.intent_id) == row["intent_id"]
            and _uuid(receipt.provider_order_id)
            and receipt.release_id == row["release_id"]
            and receipt.listing_id == row["listing_id"]
            and receipt.total == row["quoted_total"]
            and receipt.currency == row["currency"]
            and receipt.quote_fingerprint == row["quote_fingerprint"]
            and receipt.destination_ref == row["destination_ref"]
            and receipt.payment_ref == row["payment_ref"]
        )

    def simulate_attempt(self, intent_id, target, revision, quote, adapter, *, now):
        """Fence before a synthetic callback; interruption never permits resubmission."""
        _uuid(intent_id)
        if type(adapter) is not SyntheticOrderAdapter or not isinstance(quote, SyntheticFinalQuote):
            raise OrderStopped("synthetic_adapter_only")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            if not row or row["state"] != "prepared":
                raise OrderStopped("attempt_already_started_or_missing")
            grant = self._grant(db, row["grant_id"], target, revision, now)
            if (
                row["rules_revision"] != revision
                or row["quote_fingerprint"] != _fingerprint(quote.model_dump())
                or not now < row["quote_expires_at"]
                or quote.currency != grant["currency"]
            ):
                raise OrderStopped("attempt_state_or_quote_changed")
            db.execute("UPDATE intents SET state='submitting' WHERE intent_id=?", (intent_id,))
            db.commit()
        try:
            receipt = adapter.submit(intent_id)
        except Exception:
            receipt = None
        return self._finish(intent_id, receipt)

    def _finish(self, intent_id, receipt):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            if not row or row["state"] not in {"submitting", "uncertain"}:
                raise OrderStopped("reconciliation_state_changed")
            try:
                valid = self._receipt(row, receipt)
            except OrderStopped:
                valid = False
            if valid:
                db.execute(
                    "UPDATE intents SET state='confirmed',provider_order_id=? WHERE intent_id=?",
                    (receipt.provider_order_id, intent_id),
                )
                state = "confirmed"
            else:
                db.execute("UPDATE intents SET state='uncertain' WHERE intent_id=?", (intent_id,))
                state = "uncertain"
            db.commit()
        return {
            "intent_id": intent_id,
            "state": state,
            "purchase_allowed": False,
            "requires_reconciliation": state == "uncertain",
        }

    def reconcile(self, intent_id, adapter, *, now):
        """Read-only synthetic lookup. Unknown/absent evidence never triggers submit."""
        _uuid(intent_id)
        if type(adapter) is not SyntheticOrderAdapter:
            raise OrderStopped("synthetic_adapter_only")
        with self._connection() as db:
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            if not row or row["state"] not in {"submitting", "uncertain"}:
                raise OrderStopped("reconciliation_state_changed")
        try:
            result = adapter.lookup(intent_id)
        except Exception:
            result = None
        try:
            absent = (
                isinstance(result, SyntheticAbsence)
                and _uuid(result.intent_id) == row["intent_id"]
                and result.release_id == row["release_id"]
                and result.listing_id == row["listing_id"]
                and result.quote_fingerprint == row["quote_fingerprint"]
                and result.destination_ref == row["destination_ref"]
                and result.payment_ref == row["payment_ref"]
                and type(result.checked_at) in {int, float}
                and type(now) in {int, float}
                and row["prepared_at"] <= result.checked_at <= now
            )
        except OrderStopped:
            absent = False
        if absent:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                changed = db.execute(
                    "UPDATE intents SET state='absent' WHERE intent_id=? "
                    "AND state IN ('submitting','uncertain')",
                    (intent_id,),
                ).rowcount
                db.commit()
            if changed != 1:
                raise OrderStopped("reconciliation_state_changed")
            return {
                "intent_id": intent_id,
                "state": "absent",
                "purchase_allowed": False,
                "requires_reconciliation": False,
            }
        return self._finish(intent_id, result)
