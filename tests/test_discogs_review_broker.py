"""Offline framing + peer identity + durable DB tests. Never contact Discogs."""

import socket
import sqlite3
import struct
import threading
from contextlib import closing

import httpx
import pytest

from finder import discogs_review_broker as module
from finder.discogs_listing_preview import evaluate_preview
from finder.discogs_phone_review import PhoneReviewApp
from finder.discogs_price_alerts import AggregatePriceAlerts
from finder.discogs_read_guard import ReadSafetyHalt, guard_row
from finder.discogs_review_backend import DeadlineTransport, DirectReviewBackend, project_payload
from finder.discogs_review_broker import (
    REQUEST_LIMIT,
    RESPONSE_LIMIT,
    BrokerClient,
    BrokerService,
    ProtocolError,
    decode,
    encode,
    initialize_broker_state,
    receive,
)
from finder.discogs_rules import RulesStore


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "rules.db"
    RulesStore(path, create=True).close()
    AggregatePriceAlerts(path).set_enabled(True, expected_revision=0)
    with closing(sqlite3.connect(path)) as db:
        initialize_broker_state(db)
    now, calls = [1800000000.0], []
    data = {
        "id": 123,
        "release": {"id": 456},
        "condition": "Near Mint (NM or M-)",
        "sleeve_condition": "Very Good Plus (VG+)",
        "price": {"value": "166.666666", "currency": "USD"},
        "original_price": {"value": "125", "curr_abbr": "GBP"},
        "comments": "unplayed copy",
        "status": "For Sale",
        "seller": {"private": "not returned"},
    }

    def provider(req):
        calls.append(req)
        with closing(sqlite3.connect(path)) as db:
            assert guard_row(db)[0] is not None
        return httpx.Response(200, json=data)

    backend = DirectReviewBackend(
        path, transport=httpx.MockTransport(provider), clock=lambda: now[0]
    )
    return BrokerService(backend, allowed_uid=1234), calls, data, now, path


def read_request(ident="a" * 32):
    return {"v": 1, "op": "read", "request_id": ident, "listing_id": 123, "currency": "USD"}


class Stream:
    def __init__(self, incoming=b"", uid=1234, *, chunk=1, send_error=False):
        self.incoming, self.uid, self.chunk = incoming, uid, chunk
        self.sent, self.timeouts = b"", []
        self.send_error = send_error

    def getsockopt(self, *args):
        return struct.pack("3i", 1, self.uid, 1)

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self, size):
        part, self.incoming = (
            self.incoming[: min(size, self.chunk)],
            self.incoming[min(size, self.chunk) :],
        )
        return part

    def sendall(self, data):
        if self.send_error:
            raise BrokenPipeError()
        self.sent += data


class ClientStream(Stream):
    def __init__(self, service, *, broker_uid=4321, response=None, fail=None):
        super().__init__(uid=broker_uid, chunk=7)
        self.service, self.fixed_response, self.fail = service, response, fail
        self.connected = None
        self.processed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def connect(self, path):
        if self.fail == "connect":
            raise OSError()
        self.connected = path

    def sendall(self, data):
        if self.fail == "send":
            raise BrokenPipeError()
        super().sendall(data)

    def recv(self, size):
        if not self.processed:
            self.processed = True
            peer = Stream(self.sent)
            self.service.serve_connection(peer)
            self.incoming = self.fixed_response if self.fixed_response is not None else peer.sent
            if self.fail == "response":
                self.incoming = b""
        return super().recv(size)


def client_for(service, **options):
    streams = []

    def factory():
        stream = ClientStream(service, **options)
        streams.append(stream)
        return stream

    return BrokerClient("/run/broker/broker.sock", broker_uid=4321, socket_factory=factory), streams


def test_full_protocol_backend_to_page_no_db_access(setup):
    service, calls, _, _, _ = setup
    client, streams = client_for(service)
    assert client.status() == "ready"
    page = PhoneReviewApp(
        backend=client, origin="https://review.example", authenticate=lambda _: "owner"
    )
    assert page.database is None
    result = page._review(dict(listing="123", release="456", under="175", media="NM", sleeve="VG+"))
    assert result["item_criteria_match"] and result["purchase_qualified"] is False
    assert result["price"]["display_value"] == "166.67"
    assert result["media_completeness"]["status"] == "unknown"
    assert result["url"] == "https://www.discogs.com/sell/item/123"
    assert len(calls) == 1 and len(streams) == 2
    assert calls[0].method == "GET"
    assert str(calls[0].url) == "https://api.discogs.com/marketplace/listings/123?curr_abbr=USD"
    assert "authorization" not in calls[0].headers
    assert "seller" not in client_for(service)[0].__dict__


def test_replay_persistent_even_after_client_response_lost(setup):
    service, calls, _, now, path = setup
    peer = Stream(encode(read_request(), REQUEST_LIMIT), send_error=True)
    service.serve_connection(peer)
    assert len(calls) == 1
    now[0] += 10000
    restarted = BrokerService(DirectReviewBackend(path), allowed_uid=1234)
    assert restarted.handle(read_request(), 1234)["error"] == "replay"
    assert len(calls) == 1
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is None


@pytest.mark.parametrize("cut", [0, 2, 4, 12])
def test_disconnect_before_full_frame_no_request(setup, cut):
    service, calls, *_ = setup
    service.serve_connection(Stream(encode(read_request(), REQUEST_LIMIT)[:cut]))
    assert not calls


@pytest.mark.parametrize("fail", ["connect", "send", "response"])
def test_client_never_retries_uncertain_transport(setup, fail):
    service, calls, *_ = setup
    client, streams = client_for(service, fail=fail)
    with pytest.raises(ReadSafetyHalt):
        client.read(123)
    assert len(streams) == 1
    assert len(calls) == (1 if fail == "response" else 0)


def test_crash_after_reservation_keeps_pending_and_receipt(setup):
    service, calls, _, now, path = setup

    def crash(req):
        calls.append(req)
        raise SystemExit("simulated abrupt process exit")

    service.backend.transport = httpx.MockTransport(crash)
    with pytest.raises(SystemExit):
        service.handle(read_request(), 1234)
    now[0] += 100000
    assert service.handle(read_request(), 1234)["error"] == "replay"
    assert service.handle(read_request("b" * 32), 1234)["error"] == "unavailable"
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is not None
    assert len(calls) == 1


@pytest.mark.parametrize("code", [401, 403])
def test_refusal_stops_page_not_aggregate(setup, code):
    service, calls, _, now, path = setup

    def refuse(req):
        calls.append(req)
        return httpx.Response(code)

    service.backend.transport = httpx.MockTransport(refuse)
    assert service.handle(read_request(), 1234)["error"] == "denied"
    now[0] += 10000
    assert service.handle(read_request("b" * 32), 1234)["error"] == "unavailable"
    assert service.handle({"v": 1, "op": "status"}, 1234)["status"] == "denied"
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is None
        assert db.execute("SELECT enabled FROM discogs_price_control").fetchone()[0] == 1
    assert len(calls) == 1


def test_cooldown_and_consumed_busy_id(setup):
    service, calls, _, now, _ = setup
    assert service.handle(read_request(), 1234)["ok"]
    assert service.handle(read_request("b" * 32), 1234)["error"] == "busy"
    now[0] += 4
    assert service.handle(read_request("b" * 32), 1234)["error"] == "replay"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"op": "query"},
        {"url": "https://evil.example"},
        {"database": "/tmp/other"},
        {"currency": "GBP"},
        {"listing_id": True},
        {"listing_id": "123"},
        {"listing_id": 0},
        {"listing_id": 9007199254740992},
        {"request_id": "not-a-token"},
        {"v": True},
        {"request_id": ["a"]},
    ],
)
def test_hostile_schema_no_read(setup, change):
    service, calls, *_ = setup
    request = dict(read_request(), **change)
    assert not service.handle(request, 1234)["ok"]
    assert not calls


@pytest.mark.parametrize(
    "raw",
    [
        b'{"v":1,"v":1,"op":"status"}',
        b'{"v":NaN}',
        b"[]",
        b"null",
        b"\xff",
        b"{" + b'"x":[' * 1100,
    ],
)
def test_strict_json(raw):
    with pytest.raises(ProtocolError):
        decode(raw)


def test_peer_identity_checked_before_request_and_response(setup):
    service, calls, *_ = setup
    peer = Stream(encode(read_request(), REQUEST_LIMIT), uid=9999)
    service.serve_connection(peer)
    assert peer.incoming and not peer.sent and not calls
    client, streams = client_for(service, broker_uid=9999)
    with pytest.raises(ReadSafetyHalt):
        client.read(123)
    assert not streams[0].sent and not calls


@pytest.mark.parametrize("length", [0, REQUEST_LIMIT + 1, 2**32 - 1])
def test_request_length_rejected_before_payload(setup, length):
    service, calls, *_ = setup
    peer = Stream(struct.pack("!I", length) + b"X" * 8)
    service.serve_connection(peer)
    assert not calls and not peer.sent


def test_absolute_frame_deadline():
    ticks = iter([0, 1, 3])
    with pytest.raises(ProtocolError):
        receive(Stream(encode({"v": 1}, 100)), 100, timeout=2, clock=lambda: next(ticks))


def test_response_size_limit_no_second_request(setup):
    service, calls, *_ = setup
    client, streams = client_for(service, response=struct.pack("!I", RESPONSE_LIMIT + 1))
    with pytest.raises(ReadSafetyHalt):
        client.read(123)
    assert len(calls) == len(streams) == 1


def test_ledger_capacity_fail_closed_without_affecting_aggregate(setup, monkeypatch):
    service, calls, _, _, path = setup
    monkeypatch.setattr(module, "LEDGER_LIMIT", 1)
    assert service.handle(read_request(), 1234)["ok"]
    assert service.handle(read_request("b" * 32), 1234)["error"] == "capacity"
    client, _ = client_for(service)
    with pytest.raises(ReadSafetyHalt):
        client.status()
    with closing(sqlite3.connect(path)) as db:
        initialize_broker_state(db)
        assert db.execute("SELECT COUNT(*) FROM discogs_review_receipts").fetchone()[0] == 1
        assert db.execute("SELECT enabled FROM discogs_price_control").fetchone()[0] == 1
        assert guard_row(db)[0] is None
    assert len(calls) == 1


@pytest.mark.parametrize(
    "comments", ["<b>unplayed</b>", "x" * 5000, "unplayed\x00copy", "très bon", None]
)
def test_projection_never_upgrades_unsupported_description(setup, comments):
    _, _, data, *_ = setup
    data["comments"] = comments
    projected = project_payload(data, 123)
    assert "seller" not in projected
    assert (
        evaluate_preview(projected, 123, 456)["checks"]
        == evaluate_preview(data, 123, 456)["checks"]
    )
    assert len(encode({"payload": projected}, RESPONSE_LIMIT)) <= RESPONSE_LIMIT + 4


def test_slow_drip_provider_has_elapsed_deadline():
    now = [0]

    class Slow(httpx.SyncByteStream):
        def __iter__(self):
            now[0] = 13
            yield b"{}"

    transport = DeadlineTransport(
        httpx.MockTransport(lambda _: httpx.Response(200, stream=Slow())), clock=lambda: now[0]
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.get("https://fixture.invalid")


def test_real_unix_socket_protocol_if_permitted(setup, tmp_path):
    """Explicit skipped gate on socket-denied executors; never escalate/bypass."""
    service, _, *_ = setup
    try:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except PermissionError:
        pytest.skip("Environment denies sockets; actual IPC/peer-UID gate remains unverified")
    import os

    path = tmp_path / "broker.sock"
    service.allowed_uid = os.getuid()
    with listener:
        try:
            listener.bind(str(path))
            listener.listen(1)
        except PermissionError:
            pytest.skip("Environment denies socket binding; real IPC gate remains unverified")

        def serve():
            connection, _ = listener.accept()
            with connection:
                service.serve_connection(connection)

        worker = threading.Thread(target=serve)
        worker.start()
        client = BrokerClient(path, broker_uid=os.getuid())
        assert client.status() == "ready"
        worker.join(5)
        assert not worker.is_alive()


def test_second_frame_on_same_connection_ignored(setup):
    service, calls, *_ = setup
    peer = Stream(
        encode(read_request(), REQUEST_LIMIT) + encode(read_request("b" * 32), REQUEST_LIMIT)
    )
    service.serve_connection(peer)
    assert len(calls) == 1
    assert peer.incoming  # No second command is parsed or acted on.


@pytest.mark.parametrize(
    "response",
    [
        {"v": True, "ok": True, "status": "ready"},
        {"v": 1, "ok": 1, "status": "ready"},
        {"v": 1, "ok": False, "error": []},
        {"v": 1, "ok": False, "error": "private secret message"},
        {"v": 1, "ok": True, "status": "ready", "other": "unexpected"},
    ],
)
def test_hostile_broker_response_rejected(setup, response):
    service, calls, *_ = setup
    client, streams = client_for(service, response=encode(response, RESPONSE_LIMIT))
    with pytest.raises(ReadSafetyHalt):
        client.status()
    assert len(streams) == 1 and not calls


def test_missing_ledger_and_wrong_workspace_fail_before_receipt_or_http(setup):
    service, calls, _, _, path = setup
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA application_id=0")
    assert service.handle(read_request(), 1234)["error"] == "unavailable"
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM discogs_review_receipts").fetchone()[0] == 0
        db.execute("PRAGMA application_id=1178882643")
        db.execute("DROP TABLE discogs_review_receipts")
    assert service.handle(read_request(), 1234)["error"] == "unavailable"
    assert not calls


def test_broker_does_not_modify_watches_send_controls_or_lock(setup):
    service, _, _, _, path = setup
    lock = path.with_name(path.name + ".ntfy-send.lock")
    lock.write_text("fixture lock")
    before_inode = lock.stat().st_ino
    with closing(sqlite3.connect(path)) as db:
        before = [
            db.execute(f"SELECT * FROM {table}").fetchall()
            for table in (
                "targets",
                "owned",
                "state",
                "discogs_price_control",
                "discogs_price_rules",
                "discogs_price_outbox",
            )
        ]
    assert service.handle(read_request(), 1234)["ok"]
    with closing(sqlite3.connect(path)) as db:
        after = [
            db.execute(f"SELECT * FROM {table}").fetchall()
            for table in (
                "targets",
                "owned",
                "state",
                "discogs_price_control",
                "discogs_price_rules",
                "discogs_price_outbox",
            )
        ]
    assert after == before
    assert lock.stat().st_ino == before_inode and lock.read_text() == "fixture lock"


def test_production_templates_no_page_db_or_inet_and_private_broker():
    from pathlib import Path

    root = Path(__file__).parents[1] / "deploy" / "phone-review"
    page = (root / "sniper-review.service").read_text()
    broker = (root / "sniper-review-broker.service").read_text()
    assert "--database" not in page
    assert "--broker-socket /run/sniper-review-broker/broker.sock --broker-user sniper" in page
    assert "RestrictAddressFamilies=AF_UNIX\n" in page
    assert "ReadWritePaths=/run/sniper-review\n" in page
    assert "sniper-shared-db" not in page
    assert "--database /var/lib/sniper/rules.db" in broker
    assert "Restart=no" in broker
    assert " /proc" in broker and "pidfd_getfd" in broker and "RestrictNamespaces=yes" in broker
    for private in (
        "/var/lib/sniper/private-auth",
        "/var/lib/sniper/private-ntfy.json",
        "/etc/ntfy",
        "/var/lib/ntfy",
    ):
        assert private in broker


def test_page_production_launcher_rejects_database_argument():
    from finder.discogs_phone_review_server import main

    with pytest.raises(SystemExit):
        main(["--database", "/tmp/never-open-this"])


def test_receipt_commit_failure_prevents_provider_read(setup):
    service, calls, _, _, path = setup
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TRIGGER deny_receipt BEFORE INSERT ON discogs_review_receipts "
            "BEGIN SELECT RAISE(FAIL,'private fixture storage error'); END"
        )
    result = service.handle(read_request(), 1234)
    assert result == {"v": 1, "ok": False, "error": "unavailable"}
    assert not calls
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is None
        assert db.execute("SELECT COUNT(*) FROM discogs_review_receipts").fetchone()[0] == 0


def test_refusal_persistence_failure_keeps_receipt_and_guard(setup):
    service, calls, _, _, path = setup
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TRIGGER deny_latch BEFORE UPDATE ON discogs_phone_review_state "
            "BEGIN SELECT RAISE(FAIL,'private fixture storage error'); END"
        )

    def refuse(req):
        calls.append(req)
        return httpx.Response(403)

    service.backend.transport = httpx.MockTransport(refuse)
    assert service.handle(read_request(), 1234)["error"] == "unavailable"
    assert service.handle(read_request(), 1234)["error"] == "replay"
    with closing(sqlite3.connect(path)) as db:
        assert guard_row(db)[0] is not None
        assert db.execute("SELECT COUNT(*) FROM discogs_review_receipts").fetchone()[0] == 1
    assert len(calls) == 1


def test_production_launcher_arms_and_disarms_hard_deadline(setup, tmp_path, monkeypatch):
    import types

    service, _, _, _, path = setup
    directory = tmp_path / "runtime"
    directory.mkdir(mode=0o750)
    events = []
    peer = Stream(encode({"v": 1, "op": "status"}, REQUEST_LIMIT))
    peer.__class__ = type(
        "ContextStream",
        (Stream,),
        {
            "__enter__": lambda self: self,
            "__exit__": lambda *args: None,
        },
    )

    class Listener:
        accepted = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, value):
            from pathlib import Path

            Path(value).touch()

        def listen(self, value):
            assert value == 2

        def accept(self):
            if self.accepted:
                raise KeyboardInterrupt()
            self.accepted = True
            return peer, None

    monkeypatch.setattr(module.socket, "socket", lambda *args: Listener())
    monkeypatch.setattr(module.pwd, "getpwnam", lambda _: types.SimpleNamespace(pw_uid=1234))
    monkeypatch.setattr(module.signal, "signal", lambda *args: events.append(("signal", args)))
    monkeypatch.setattr(module.signal, "alarm", lambda seconds: events.append(("alarm", seconds)))
    monkeypatch.setattr(module.os.environ, "clear", lambda: events.append("env_clear"))
    monkeypatch.setattr(module.logging, "disable", lambda _: None)
    with pytest.raises(KeyboardInterrupt):
        module.main(
            [
                "--database",
                str(path),
                "--socket-directory",
                str(directory),
                "--review-user",
                "fixture",
            ]
        )
    assert ("alarm", 25) in events and events[-1] == ("alarm", 0)
    assert "env_clear" in events
    assert peer.sent
