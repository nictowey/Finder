"""Private Linux Unix-socket known-listing broker. No arbitrary URLs/SQL or credentials."""

import argparse
import json
import logging
import os
import pwd
import re
import secrets
import signal
import socket
import sqlite3
import stat
import struct
import time
from contextlib import closing
from pathlib import Path

from finder.discogs_listing import ListingReadError, ReadBudgetBusy, listing_id
from finder.discogs_read_guard import ReadSafetyHalt
from finder.discogs_review_backend import (
    DirectReviewBackend,
    initialize_review_state,
    project_payload,
)
from finder.discogs_watch_removals import RemovedWatchError

REQUEST_LIMIT = 512
RESPONSE_LIMIT = 32768
LEDGER_LIMIT = 4096
ERRORS = {"invalid", "unavailable", "busy", "provider", "denied", "replay", "capacity", "removed"}


class ProtocolError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate_key")
        result[key] = value
    return result


def decode(data):
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ProtocolError("constant")),
        )
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError("invalid_json") from None
    if type(value) is not dict:
        raise ProtocolError("object_required")
    return value


def encode(value, limit):
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    if not 0 < len(data) <= limit:
        raise ProtocolError("frame_size")
    return struct.pack("!I", len(data)) + data


def receive(sock, limit, *, timeout, clock=time.monotonic, operation_limits=None):
    deadline = clock() + timeout

    def exact(size):
        chunks = bytearray()
        while len(chunks) < size:
            remaining = deadline - clock()
            if remaining <= 0:
                raise ProtocolError("deadline")
            sock.settimeout(remaining)
            part = sock.recv(size - len(chunks))
            if not part:
                raise ProtocolError("incomplete_frame")
            chunks.extend(part)
        return bytes(chunks)

    length = struct.unpack("!I", exact(4))[0]
    if not 0 < length <= limit:
        raise ProtocolError("frame_size")
    value = decode(exact(length))
    if operation_limits is not None:
        for operation, operation_limit in operation_limits.items():
            if value.get("op") == operation and length > operation_limit:
                raise ProtocolError("frame_size")
    return value


def peer_uid(sock):
    if not hasattr(socket, "SO_PEERCRED"):
        raise ProtocolError("linux_peer_credentials_required")
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _, uid, _ = struct.unpack("3i", raw)
    return uid


def initialize_broker_state(db):
    """Operator-only additive provisioning. No request path creates/repairs schema."""
    initialize_review_state(db)
    with db:
        db.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        names = {"discogs_review_receipts", "discogs_review_broker_metadata"}
        if bool(tables & names) and not names <= tables:
            raise ReadSafetyHalt("broker_schema_invalid")
        if not tables & names:
            db.execute(
                "CREATE TABLE discogs_review_broker_metadata (id INTEGER PRIMARY KEY, "
                "version INTEGER NOT NULL)"
            )
            db.execute("INSERT INTO discogs_review_broker_metadata VALUES(1,1)")
            db.execute(
                "CREATE TABLE discogs_review_receipts (request_id TEXT PRIMARY KEY NOT NULL)"
            )
        _ledger_count(db)


def _ledger_count(db):
    if db.execute("SELECT id,version FROM discogs_review_broker_metadata").fetchall() != [(1, 1)]:
        raise ReadSafetyHalt("broker_schema_invalid")
    # Bounded ledger is deliberately never automatically pruned: old IDs cannot replay.
    rows = db.execute(
        "SELECT request_id FROM discogs_review_receipts LIMIT ?", (LEDGER_LIMIT + 1,)
    ).fetchall()
    if len(rows) > LEDGER_LIMIT or any(
        not isinstance(row[0], str) or not re.fullmatch(r"[a-f0-9]{32}", row[0]) for row in rows
    ):
        raise ReadSafetyHalt("broker_ledger_invalid")
    return len(rows)


class BrokerService:
    def __init__(self, backend, *, allowed_uid, watch_backend=None):
        if type(allowed_uid) is not int or allowed_uid <= 0:
            raise ValueError("non-root review UID required")
        self.backend, self.allowed_uid = backend, allowed_uid
        self.watch_backend = watch_backend

    def _claim(self, request_id):
        with closing(
            sqlite3.connect(self.backend.database.as_uri() + "?mode=rw", uri=True, timeout=2)
        ) as db:
            if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
                raise ReadSafetyHalt("broker_wrong_workspace")
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA journal_mode").fetchone()[0].lower() in {"off", "memory"}:
                raise ReadSafetyHalt("broker_durability_unavailable")
            with db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("PRAGMA application_id").fetchone()[0] != 1178882643:
                    raise ReadSafetyHalt("broker_wrong_workspace")
                count = _ledger_count(db)
                if db.execute(
                    "SELECT 1 FROM discogs_review_receipts WHERE request_id=?", (request_id,)
                ).fetchone():
                    return "replay"
                if count >= LEDGER_LIMIT:
                    return "capacity"
                db.execute("INSERT INTO discogs_review_receipts VALUES(?)", (request_id,))
        return None

    def handle(self, request, uid):
        if uid != self.allowed_uid:
            raise ProtocolError("wrong_peer")
        response = {"v": 1, "ok": False, "error": "invalid"}
        if type(request) is not dict or type(request.get("v")) is not int or request["v"] != 1:
            return response
        try:
            if request.get("op") in ("watch_snapshot", "watch_save", "watch_activity"):
                return self._watch_request(request)
            if request == {"v": 1, "op": "status"}:
                with closing(
                    sqlite3.connect(self.backend.database.as_uri() + "?mode=ro", uri=True)
                ) as db:
                    full = _ledger_count(db) >= LEDGER_LIMIT
                state = self.backend.status()
                return {"v": 1, "ok": True, "status": "capacity" if full else state}
            if (
                set(request) != {"v", "op", "request_id", "listing_id", "currency"}
                or request["op"] != "read"
                or request["currency"] != "USD"
            ):
                return response
            ident, request_id = request["listing_id"], request["request_id"]
            if (
                type(ident) is not int
                or listing_id(ident) != ident
                or not isinstance(request_id, str)
                or not re.fullmatch(r"[a-f0-9]{32}", request_id)
            ):
                return response
            failure = self._claim(request_id)
            if failure:
                return {"v": 1, "ok": False, "error": failure}
            # A consumed ID never retries, including busy, provider failure and client disconnect.
            payload = self.backend.read(ident)
            return {"v": 1, "ok": True, "request_id": request_id, "payload": payload}
        except ReadBudgetBusy:
            return {"v": 1, "ok": False, "error": "busy"}
        except ListingReadError as error:
            return {
                "v": 1,
                "ok": False,
                "error": "denied" if error.access_restricted else "provider",
            }
        except (ValueError, sqlite3.Error, OSError, ReadSafetyHalt):
            return {"v": 1, "ok": False, "error": "unavailable"}

    def _watch_request(self, request):
        from finder.discogs_watch_broker import mutation_fields, project_snapshot

        if self.watch_backend is None:
            return {"v": 1, "ok": False, "error": "invalid"}
        try:
            if request.get("op") == "watch_activity":
                from finder.discogs_watch_activity import (
                    ACTIVITY_REQUEST_LIMIT,
                    ACTIVITY_RESPONSE_LIMIT,
                    activity_fields,
                    project_activity,
                )

                if set(request) != {"v", "op", "release_id", "cursor"}:
                    raise ValueError("Invalid activity")
                encode(request, ACTIVITY_REQUEST_LIMIT)
                fields = activity_fields({key: request[key] for key in ("release_id", "cursor")})
                result = {
                    "v": 1,
                    "ok": True,
                    "activity": project_activity(
                        self.watch_backend.activity(fields["release_id"], fields["cursor"]),
                        fields["release_id"],
                        fields["cursor"],
                    ),
                }
                encode(result, ACTIVITY_RESPONSE_LIMIT)
                return result
            if request == {"v": 1, "op": "watch_snapshot"}:
                return {
                    "v": 1,
                    "ok": True,
                    "snapshot": project_snapshot(self.watch_backend.snapshot()),
                }
            if (
                set(request) != {"v", "op", "request_id", "fields"}
                or request["op"] != "watch_save"
                or type(request["request_id"]) is not str
                or not re.fullmatch(r"[a-f0-9]{32}", request["request_id"])
            ):
                return {"v": 1, "ok": False, "error": "invalid"}
            fields = mutation_fields(request["fields"])
            failure = self._claim(request["request_id"])
            if failure:
                return {"v": 1, "ok": False, "error": failure}
            revision = self.watch_backend.save(fields)
            return {"v": 1, "ok": True, "request_id": request["request_id"], "revision": revision}
        except RemovedWatchError:
            return {"v": 1, "ok": False, "error": "removed"}
        except (ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error, OSError):
            return {"v": 1, "ok": False, "error": "unavailable"}

    def serve_connection(self, connection):
        # Authenticate before consuming any attacker-supplied frame; one operation per socket.
        try:
            uid = peer_uid(connection)
            if uid != self.allowed_uid:
                return
            from finder.discogs_watch_activity import ACTIVITY_REQUEST_LIMIT
            from finder.discogs_watch_broker import WATCH_REQUEST_LIMIT, WATCH_RESPONSE_LIMIT

            request = receive(
                connection,
                WATCH_REQUEST_LIMIT if self.watch_backend else REQUEST_LIMIT,
                timeout=2,
                operation_limits={"watch_activity": ACTIVITY_REQUEST_LIMIT},
            )
            response = self.handle(request, uid)
            limit = WATCH_RESPONSE_LIMIT if self.watch_backend else RESPONSE_LIMIT
            if request.get("op") == "watch_activity":
                from finder.discogs_watch_activity import ACTIVITY_RESPONSE_LIMIT

                limit = ACTIVITY_RESPONSE_LIMIT
            framed = encode(response, limit)
            connection.settimeout(2)
            connection.sendall(framed)
        except (OSError, ValueError, ReadSafetyHalt, sqlite3.Error):
            # No logs, no response retry, no second provider attempt.
            return


class BrokerClient:
    """No fallback, reconnect or retry. Socket factory exists only for offline tests."""

    request_limit = REQUEST_LIMIT
    response_limit = RESPONSE_LIMIT

    def __init__(self, path, *, broker_uid, socket_factory=None):
        path = Path(path)
        if not path.is_absolute() or type(broker_uid) is not int or broker_uid <= 0:
            raise ValueError("absolute socket path and non-root broker UID required")
        self.path, self.broker_uid = path, broker_uid
        self.socket_factory = socket_factory or (
            lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        )

    def _call(self, request):
        request_limit, response_limit = self.request_limit, self.response_limit
        if request.get("op") == "watch_activity":
            from finder.discogs_watch_activity import (
                ACTIVITY_REQUEST_LIMIT,
                ACTIVITY_RESPONSE_LIMIT,
            )

            request_limit, response_limit = ACTIVITY_REQUEST_LIMIT, ACTIVITY_RESPONSE_LIMIT
        try:
            with self.socket_factory() as connection:
                connection.settimeout(2)
                connection.connect(str(self.path))
                if peer_uid(connection) != self.broker_uid:
                    raise ProtocolError("wrong_broker")
                connection.sendall(encode(request, request_limit))
                response = receive(connection, response_limit, timeout=30)
            if (
                type(response.get("v")) is not int
                or response["v"] != 1
                or type(response.get("ok")) is not bool
            ):
                raise ProtocolError("invalid_response")
            if response["ok"] is False:
                if set(response) != {"v", "ok", "error"} or response["error"] not in ERRORS:
                    raise ProtocolError("invalid_response")
                if response["error"] == "removed" and request.get("op") == "watch_save":
                    raise RemovedWatchError("Find this watch in Removed and restore it first.")
                if response["error"] == "busy":
                    raise ReadBudgetBusy("Broker busy.")
                if response["error"] in {"provider", "denied"}:
                    raise ListingReadError(
                        "Public read unavailable.", access_restricted=response["error"] == "denied"
                    )
                raise ReadSafetyHalt("broker_unavailable")
            return response
        except (OSError, ValueError, TypeError, KeyError) as error:
            if isinstance(error, ListingReadError):
                raise
            raise ReadSafetyHalt("broker_response_unknown") from None

    def status(self):
        response = self._call({"v": 1, "op": "status"})
        if set(response) != {"v", "ok", "status"} or response["status"] not in {
            "ready",
            "denied",
            "capacity",
        }:
            raise ReadSafetyHalt("broker_invalid_response")
        if response["status"] == "capacity":
            raise ReadSafetyHalt("broker_capacity_operator_required")
        return response["status"]

    def read(self, ident):
        ident = listing_id(ident)
        request_id = secrets.token_hex(16)
        response = self._call(
            {"v": 1, "op": "read", "request_id": request_id, "listing_id": ident, "currency": "USD"}
        )
        if (
            set(response) != {"v", "ok", "request_id", "payload"}
            or response["request_id"] != request_id
        ):
            raise ReadSafetyHalt("broker_invalid_response")
        try:
            payload = response["payload"]
            if (
                type(payload) is not dict
                or type(payload.get("shipping_is_blocked")) is not bool
                or payload != project_payload(payload, ident)
            ):
                raise ValueError()
            return payload
        except (ValueError, TypeError, KeyError):
            raise ReadSafetyHalt("broker_invalid_payload") from None


def validate_directory(path):
    path = Path(path)
    info = path.lstat()
    if (
        not path.is_absolute()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o750
    ):
        raise ValueError("broker-owned real directory mode0750 required")
    endpoint = path / "broker.sock"
    if endpoint.exists() or endpoint.is_symlink():
        # Never unlink an existing socket: its listener/lifecycle may be uncertain.
        raise ValueError("socket already exists; operator inspection required")
    return endpoint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--socket-directory", required=True, type=Path)
    parser.add_argument("--review-user", required=True)
    parser.add_argument("--enable-watch-management", action="store_true")
    args = parser.parse_args(argv)
    if not args.database.is_absolute() or not args.database.is_file() or args.database.is_symlink():
        parser.error("existing real absolute database required")
    uid = pwd.getpwnam(args.review_user).pw_uid
    path = validate_directory(args.socket_directory)
    watch_backend = None
    if args.enable_watch_management:
        from finder.discogs_price_alerts import AggregatePriceAlerts
        from finder.discogs_watch_manager import WatchBackend

        watch_backend = WatchBackend(AggregatePriceAlerts.for_existing_store(args.database))
        watch_backend.snapshot()  # Fail closed before binding; never initialize or migrate.
    service = BrokerService(
        DirectReviewBackend(args.database), allowed_uid=uid, watch_backend=watch_backend
    )
    # Verify schema/status without provider traffic before binding.
    if not service.handle({"v": 1, "op": "status"}, uid).get("ok"):
        parser.error("broker state unavailable; operator provisioning required")
    logging.disable(logging.CRITICAL)
    # Hard production wall bound, including resolver/slow-drip/body/DB stalls.
    # Default SIGALRM terminates, leaving durable pending/receipt state intact.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    os.environ.clear()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        path.chmod(0o660)
        listener.listen(2)
        while True:
            connection, _ = listener.accept()
            with connection:
                signal.alarm(25)
                try:
                    service.serve_connection(connection)
                finally:
                    signal.alarm(0)


if __name__ == "__main__":
    main()
