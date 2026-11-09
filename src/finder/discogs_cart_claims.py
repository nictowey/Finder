"""Durable one-copy cart-add rehearsal claims, with no browser or order capability.

This is a storage primitive, not an eligibility or authorization decision. A later
owner-authenticated adapter must recheck current rules, listing and cart state before
using it. The current private service does not expose that adapter.
"""

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

APPLICATION_ID = 1397637169
SCHEMA_VERSION = 1
MAX_ID = 9007199254740991


class CartClaimBlocked(RuntimeError):
    """A sanitized stop code; never a provider or database payload."""


@dataclass(frozen=True, slots=True)
class CartClaim:
    release_id: int
    listing_id: str
    rules_revision: int
    idempotency_key: str
    state: str
    mode: str = "durable_cart_rehearsal"
    purchase_allowed: bool = False

    def response(self):
        return asdict(self)


def _identity(release_id, listing_id, rules_revision, idempotency_key):
    if type(release_id) is not int or not 0 < release_id <= MAX_ID:
        raise CartClaimBlocked("invalid_release_id")
    if (
        type(listing_id) is not str
        or not listing_id.isascii()
        or not listing_id.isdecimal()
        or listing_id[0] == "0"
        or int(listing_id) > MAX_ID
    ):
        raise CartClaimBlocked("invalid_listing_id")
    if type(rules_revision) is not int or not 0 <= rules_revision <= MAX_ID:
        raise CartClaimBlocked("invalid_rules_revision")
    try:
        parsed = UUID(idempotency_key) if type(idempotency_key) is str else None
    except (ValueError, AttributeError):
        parsed = None
    if parsed is None or parsed.int == 0 or str(parsed) != idempotency_key:
        raise CartClaimBlocked("invalid_idempotency_key")


class CartClaimStore:
    """A release has one immutable slot across process restarts and competing callers.

    An interrupted `attempting` claim remains blocked. There is deliberately no
    retry, expiry, deletion, release, order or payment method.
    """

    def __init__(self, path: Path, *, create=False):
        if type(create) is not bool:
            raise ValueError("Creation must be explicit.")
        self.path = Path(path).absolute()
        if self.path.is_symlink() or self.path.name.startswith(".env"):
            raise ValueError("Use a dedicated regular claim file.")
        if create:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        elif not self.path.is_file():
            raise ValueError("Claim file does not exist; explicitly initialize it.")
        with self._connection() as db:
            if create:
                db.executescript(f"""
                    BEGIN IMMEDIATE;
                    PRAGMA application_id={APPLICATION_ID};
                    PRAGMA user_version={SCHEMA_VERSION};
                    CREATE TABLE claims (
                        release_id INTEGER PRIMARY KEY CHECK(release_id>0),
                        listing_id TEXT NOT NULL,
                        rules_revision INTEGER NOT NULL CHECK(rules_revision>=0),
                        idempotency_key TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL CHECK(state IN
                            ('reserved','attempting','cart_observed','uncertain'))
                    );
                    CREATE TRIGGER claims_no_delete BEFORE DELETE ON claims
                        BEGIN SELECT RAISE(ABORT,'claims cannot be deleted'); END;
                    CREATE TRIGGER claims_no_replace BEFORE INSERT ON claims
                    WHEN EXISTS(SELECT 1 FROM claims WHERE release_id=NEW.release_id
                        OR idempotency_key=NEW.idempotency_key)
                        BEGIN SELECT RAISE(ABORT,'claims cannot be replaced'); END;
                    COMMIT;
                """)
            elif (
                db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            ):
                raise ValueError("Not a supported cart rehearsal claim file.")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _claim(row):
        return CartClaim(**dict(row))

    def get(self, idempotency_key):
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM claims WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            return self._claim(row) if row else None

    def reserve(self, release_id, listing_id, rules_revision, idempotency_key):
        _identity(release_id, listing_id, rules_revision, idempotency_key)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM claims WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if row:
                    claim = self._claim(row)
                    if (claim.release_id, claim.listing_id, claim.rules_revision) != (
                        release_id,
                        listing_id,
                        rules_revision,
                    ):
                        raise CartClaimBlocked("idempotency_conflict")
                    return claim
                if db.execute("SELECT 1 FROM claims WHERE release_id=?", (release_id,)).fetchone():
                    raise CartClaimBlocked("one_copy_reserved")
                db.execute(
                    "INSERT INTO claims VALUES (?,?,?,?,?)",
                    (release_id, listing_id, rules_revision, idempotency_key, "reserved"),
                )
                claim = CartClaim(
                    release_id, listing_id, rules_revision, idempotency_key, "reserved"
                )
                db.commit()
                return claim
            except BaseException:
                db.rollback()
                raise

    def transition(self, idempotency_key, state):
        allowed = {
            "attempting": "reserved",
            "cart_observed": "attempting",
            "uncertain": "attempting",
        }
        if state not in allowed or type(idempotency_key) is not str:
            raise CartClaimBlocked("invalid_transition")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM claims WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if row is None:
                    raise CartClaimBlocked("claim_missing")
                if row["state"] != allowed[state]:
                    raise CartClaimBlocked("claim_already_used_or_uncertain")
                db.execute(
                    "UPDATE claims SET state=? WHERE idempotency_key=?", (state, idempotency_key)
                )
                claim = CartClaim(
                    row["release_id"],
                    row["listing_id"],
                    row["rules_revision"],
                    idempotency_key,
                    state,
                )
                db.commit()
                return claim
            except BaseException:
                db.rollback()
                raise
