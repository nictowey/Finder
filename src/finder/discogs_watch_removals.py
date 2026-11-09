"""Additive operator provisioning for reversible aggregate-watch removal.

No request or read-only startup calls this initializer. The old aggregate worker
can still parse every rule; database guards stop old writers editing hidden rows.
"""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = {
    "discogs_price_removed": (
        "CREATE TABLE discogs_price_removed (release_id INTEGER PRIMARY KEY, "
        "removed INTEGER NOT NULL CHECK(removed IN (0,1)))"
    ),
    "discogs_price_removed_insert_guard": (
        "CREATE TRIGGER discogs_price_removed_insert_guard "
        "BEFORE INSERT ON discogs_price_rules "
        "WHEN EXISTS(SELECT 1 FROM discogs_price_removed "
        "WHERE release_id=NEW.release_id AND removed=1) "
        "BEGIN SELECT RAISE(ABORT,'Restore removed watch before editing'); END"
    ),
    "discogs_price_removed_update_guard": (
        "CREATE TRIGGER discogs_price_removed_update_guard "
        "BEFORE UPDATE ON discogs_price_rules "
        "WHEN EXISTS(SELECT 1 FROM discogs_price_removed "
        "WHERE release_id IN (OLD.release_id,NEW.release_id) AND removed=1) "
        "BEGIN SELECT RAISE(ABORT,'Restore removed watch before editing'); END"
    ),
    "discogs_price_removed_delete_guard": (
        "CREATE TRIGGER discogs_price_removed_delete_guard "
        "BEFORE DELETE ON discogs_price_rules "
        "WHEN EXISTS(SELECT 1 FROM discogs_price_removed "
        "WHERE release_id=OLD.release_id AND removed=1) "
        "BEGIN SELECT RAISE(ABORT,'Removed watch history must be retained'); END"
    ),
}


class RemovedWatchError(RuntimeError):
    """A retained watch must be restored before it can be edited or resumed."""


def require_removal_schema(db):
    existing = dict(
        db.execute(
            "SELECT name,sql FROM sqlite_master WHERE name IN (?,?,?,?)", tuple(SCHEMA)
        ).fetchall()
    )
    if existing != SCHEMA:
        raise RuntimeError("Watch removal schema unavailable; operator provisioning required.")


def watch_removed(db, release):
    row = db.execute(
        "SELECT removed FROM discogs_price_removed WHERE release_id=?", (release,)
    ).fetchone()
    if row is not None and (type(row[0]) is not int or row[0] not in (0, 1)):
        raise RuntimeError("Invalid watch removal state.")
    return row is not None and row[0] == 1


def require_retained_watch(db, release):
    if watch_removed(db, release):
        raise RemovedWatchError("Find this watch in Removed and restore it before editing.")


def initialize_removal_state(db):
    """Explicit initializer only; additive transaction, no existing rule/data writes."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
            raise ValueError("Not a Discogs rules workspace.")
        required = {
            "discogs_price_rules": {"release_id", "revision", "data"},
            "discogs_price_samples": {"release_id", "claim", "claim_until"},
            "discogs_price_outbox": {"release_id", "state"},
            "discogs_price_extrema": {"release_id", "continuous"},
        }
        for table, columns in required.items():
            if not columns <= {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
                raise ValueError("Existing aggregate watch schema is required.")
        existing = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE name IN (?,?,?,?)", tuple(SCHEMA)
            )
        }
        if existing:
            require_removal_schema(db)  # Never repair partial or changed schema.
        else:
            for statement in SCHEMA.values():
                db.execute(statement)
            require_removal_schema(db)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args(argv)
    path = args.database
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        parser.error("existing real absolute database required")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)) as db:
        initialize_removal_state(db)


if __name__ == "__main__":
    main()
