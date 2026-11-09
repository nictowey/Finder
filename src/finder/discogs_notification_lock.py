"""Single-host notification dispatch exclusion and bounded crash recovery.

Hold the advisory lock across claim/check/HTTP/finish, never a database write
transaction across HTTP. All supported publishers must use the same database
path and this protocol. External clients and mixed-version workers are excluded.
"""

import fcntl
import math
import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path

UNKNOWN_RECOVERY_SECONDS = 900


@contextmanager
def notification_lock(database):
    path = Path(database).absolute()
    if path.is_symlink() or not path.is_file() or path.name.startswith(".env"):
        raise ValueError("Existing aggregate database required.")
    fd = os.open(
        str(path) + ".ntfy-send.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        0o600,
    )
    locked = False
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ValueError("Notification dispatch lock unavailable.")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            pass
        yield locked
    finally:
        if locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    # Never unlink: another process could already hold/wait on this inode.


def recover_abandoned(database, now):
    """Only call while holding notification_lock. Recovery itself never sends.

    An unfinished lease with no live lock holder is an interrupted unknown
    attempt, even before its old expiry. Consume it once and preserve all known
    provider deadlines. The 15-minute hold is bounded conservatism, not knowledge
    of a completely unseen Retry-After response.
    """
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ValueError("A finite current time is required.")
    db = sqlite3.connect(Path(database).absolute().as_uri() + "?mode=rw", uri=True, timeout=2)
    try:
        db.execute("PRAGMA trusted_schema=OFF")
        with db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
                raise ValueError("Existing aggregate database required.")
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            aggregate = db.execute(
                "UPDATE discogs_price_outbox SET state='uncertain',lease_until=NULL,"
                "last_error='interrupted_unknown' WHERE state IN ('leased','uncertain') "
                "AND lease_until IS NOT NULL"
            ).rowcount
            health = 0
            if "discogs_health_outbox" in tables:
                version = db.execute(
                    "SELECT schema_version FROM discogs_health_control WHERE id=1"
                ).fetchone()
                if version is None or version[0] != 1:
                    raise ValueError("Compatible health watcher state required.")
                health = db.execute(
                    "UPDATE discogs_health_outbox SET state='uncertain',lease_until=NULL "
                    "WHERE state IN ('leased','uncertain') AND lease_until IS NOT NULL"
                ).rowcount
            if aggregate or health:
                changed = db.execute(
                    "UPDATE discogs_price_control SET next_send=max(next_send,?) WHERE id=1",
                    (now + UNKNOWN_RECOVERY_SECONDS,),
                ).rowcount
                if changed != 1:
                    raise ValueError("Shared notification budget unavailable.")
                if health:
                    db.execute(
                        "UPDATE discogs_health_control SET next_send=max(next_send,?) WHERE id=1",
                        (now + UNKNOWN_RECOVERY_SECONDS,),
                    )
            return aggregate + health
    finally:
        db.close()
