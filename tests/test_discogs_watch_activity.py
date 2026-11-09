"""Retained activity is bounded, private, read-only evidence, not delivery history."""

import copy
import io
import json
import sqlite3
from contextlib import closing

import pytest
import test_discogs_watch_broker as broker_tests
from test_discogs_watch_broker import fields, request

from finder.discogs_review_broker import ProtocolError


@pytest.fixture
def integration(tmp_path):
    return broker_tests.integration.__wrapped__(tmp_path)


CANARY = "PRIVATE-CANARY-NEVER-PROJECT"
STATES = (
    "accepted",
    "pending",
    "retry_wait",
    "leased",
    "uncertain",
    "failed",
    "expired",
    "revoked",
)


def seed(path, count, *, revision=1, start=0):
    with closing(sqlite3.connect(path)) as db, db:
        db.executemany(
            "INSERT INTO discogs_price_outbox "
            "(event_key,release_id,rule_revision,episode,price,observed_at,state,"
            "next_attempt,provider_id,destination_ref,token,lease_until,last_error) "
            "VALUES (?,1,?,?,?,?,'pending',0,?,?,?,0,?)",
            (
                (
                    f"{CANARY}-{revision}-{i}",
                    revision,
                    i,
                    "",
                    100 + i,
                    CANARY,
                    CANARY,
                    CANARY,
                    CANARY,
                )
                for i in range(start, start + count)
            ),
        )


def test_states_kinds_scopes_and_private_fields(integration):
    path, backend, _, client, _, _ = integration
    backend.save({k: v for k, v in fields().items() if k != "csrf"})
    seed(path, 8)
    with closing(sqlite3.connect(path)) as db, db:
        for i, state in enumerate(STATES):
            db.execute("UPDATE discogs_price_outbox SET state=? WHERE episode=?", (state, i))
        for i, previous_price, previous_count in [(1, "30", None), (2, None, 2), (3, "30", 2)]:
            db.execute("UPDATE discogs_price_outbox SET price='20' WHERE episode=?", (i,))
            db.execute(
                "INSERT INTO discogs_price_changes VALUES (?,?,?,4)",
                (f"{CANARY}-1-{i}", previous_price, previous_count),
            )
    seed(path, 1, revision=2)
    result = client.activity(1)
    assert result == backend.activity(1)
    assert set(result) == {"release_id", "current_revision", "rows", "next_cursor", "has_more"}
    assert result["current_revision"] == 2
    assert result["rows"][0]["settings_scope"] == "current"
    rows = {r["episode"]: r for r in result["rows"] if r["revision"] == 1}
    for i, state in enumerate(STATES):
        assert rows[i]["state"] == state
        assert rows[i]["observed_at"] == 100 + i
        assert rows[i]["settings_scope"] == "earlier"
        assert set(rows[i]) == {
            "revision",
            "episode",
            "observed_at",
            "state",
            "kind",
            "settings_scope",
        }
    assert rows[0]["kind"] == "availability"
    assert rows[1]["kind"] == "price_drop"
    assert rows[2]["kind"] == "count_increase"
    assert rows[3]["kind"] == "price_drop_and_count_increase"
    assert CANARY not in json.dumps(result)
    assert not result["has_more"] and result["next_cursor"] is None


@pytest.mark.parametrize("size", [0, 1, 20, 21, 61])
def test_bounded_keyset_pages(integration, size):
    path, backend, _, _, _, _ = integration
    seed(path, size)
    seen, cursor = [], None
    while True:
        result = backend.activity(1, cursor)
        assert len(result["rows"]) <= 20
        seen.extend(r["episode"] for r in result["rows"])
        if not result["has_more"]:
            assert result["next_cursor"] is None
            break
        last = result["rows"][-1]
        cursor = {"revision": last["revision"], "episode": last["episode"]}
        assert result["next_cursor"] == cursor
    assert seen == list(reversed(range(size)))


def test_cursor_ignores_new_events_and_mutated_observation(integration):
    path, backend, _, _, _, _ = integration
    seed(path, 41)
    first = backend.activity(1)
    seed(path, 1, start=41)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE discogs_price_outbox SET observed_at=999999 WHERE episode=0")
    older = backend.activity(1, first["next_cursor"])
    assert [r["episode"] for r in older["rows"]] == list(range(20, 0, -1))
    assert backend.activity(1, older["next_cursor"])["rows"][0]["episode"] == 0
    assert backend.activity(1)["rows"][0]["episode"] == 41


def test_activity_never_writes_reconciles_or_claims(integration, monkeypatch):
    path, backend, service, client, _, _ = integration
    seed(path, 8)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE discogs_price_outbox SET state='leased',lease_until=0")

    def forbidden(*args, **kwargs):
        raise AssertionError("Read invoked a writer or whole-history snapshot")

    for name in ("_reconcile", "claim", "finish", "status"):
        monkeypatch.setattr(backend.alerts, name, forbidden)
    monkeypatch.setattr(backend, "snapshot", forbidden)
    monkeypatch.setattr(service, "_claim", forbidden)
    before = path.read_bytes()
    assert all(r["state"] == "leased" for r in client.activity(1)["rows"])
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "release,cursor",
    [
        (True, None),
        (0, None),
        (-1, None),
        (2**63, None),
        ("1", None),
        (1, {}),
        (1, {"revision": True, "episode": 0}),
        (1, {"revision": 0, "episode": 0}),
        (1, {"revision": 1, "episode": -1}),
        (1, {"revision": 1, "episode": 2**63}),
        (1, {"revision": 1, "episode": 0, "secret": CANARY}),
    ],
)
def test_strict_selectors(integration, release, cursor):
    _, backend, service, client, _, streams = integration
    with pytest.raises((ValueError, RuntimeError)):
        backend.activity(release, cursor)
    with pytest.raises((ValueError, RuntimeError)):
        client.activity(release, cursor)
    assert not streams
    response = service.handle(
        {"v": 1, "op": "watch_activity", "release_id": release, "cursor": cursor}, 1234
    )
    assert not response["ok"] and CANARY not in json.dumps(response)


def test_exact_ipc_envelope_and_unknown_watch(integration):
    _, _, service, client, _, _ = integration
    base = {"v": 1, "op": "watch_activity", "release_id": 1, "cursor": None}
    assert set(service.handle(base, 1234)) == {"v", "ok", "activity"}
    for extra in ({"limit": 100}, {"database": CANARY}, {"request_id": "a" * 32}):
        assert not service.handle({**base, **extra}, 1234)["ok"]
    for key in ("cursor", "release_id"):
        invalid = dict(base)
        del invalid[key]
        assert not service.handle(invalid, 1234)["ok"]
    with pytest.raises(ProtocolError):
        service.handle(base, 9999)
    with pytest.raises(RuntimeError):
        client.activity(999999)


def test_http_read_preserves_csrf_and_receipt_ledger(integration):
    path, backend, _, client, app, _ = integration
    seed(path, 21)
    _, state = request(app)
    before = path.read_bytes()
    status, result = request(
        app, "/watches/state", {"action": "activity", "release_id": 1, "cursor": None}
    )
    assert status == "200 OK"
    assert result["activity"] == client.activity(1)
    assert path.read_bytes() == before and CANARY not in json.dumps(result)
    assert request(app, "/watches/save", fields(state["csrf"]))[0] == "200 OK"
    assert backend.snapshot()["watches"][0]["revision"] == 2


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_X_REVIEW_PRINCIPAL": "other"},
        {"HTTP_HOST": "evil.example"},
        {"HTTP_ORIGIN": "https://evil.example"},
        {"HTTP_ORIGIN": ""},
        {"HTTP_SEC_FETCH_SITE": "cross-site"},
        {"CONTENT_TYPE": "text/plain"},
        {"CONTENT_LENGTH": "1025"},
        {"QUERY_STRING": "release_id=1"},
    ],
)
def test_http_boundary_before_ipc(integration, headers):
    _, _, _, _, app, streams = integration
    assert not request(
        app, "/watches/state", {"action": "activity", "release_id": 1, "cursor": None}, **headers
    )[0].startswith("200")
    assert not streams


@pytest.mark.parametrize(
    "raw",
    [
        b'{"action":"activity","release_id":1,"release_id":2,"cursor":null}',
        b'{"action":"activity","release_id":1,"cursor":{"revision":1,"episode":NaN}}',
        b'{"action":"activity","release_id":1,"cursor":null,"include_internal":true}',
    ],
)
def test_http_rejects_duplicate_nonfinite_and_extra_fields(integration, raw):
    _, _, _, _, app, streams = integration
    assert not request(
        app, "/watches/state", {}, CONTENT_LENGTH=str(len(raw)), **{"wsgi.input": io.BytesIO(raw)}
    )[0].startswith("200")
    assert not streams


def test_removed_watch_retains_readable_activity(integration):
    path, backend, _, client, _, _ = integration
    seed(path, 1)
    backend.save({"action": "remove", "release": "1", "revision": 1})
    result = client.activity(1)
    assert result["current_revision"] == 2
    assert result["rows"][0]["settings_scope"] == "earlier"


@pytest.mark.parametrize(
    "column,value",
    [
        ("state", "sent"),
        ("observed_at", -1),
        ("observed_at", float("inf")),
        ("rule_revision", 99),
        ("episode", -1),
    ],
)
def test_corrupt_record_fails_closed_without_leaking(integration, column, value):
    path, _, _, client, _, _ = integration
    seed(path, 1)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"UPDATE discogs_price_outbox SET {column}=?", (value,))
    before = path.read_bytes()
    with pytest.raises(RuntimeError) as error:
        client.activity(1)
    assert CANARY not in str(error.value)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "previous_price,previous_count,count",
    [
        (None, None, 4),
        ("NaN", None, 4),
        ("19", None, 4),
        (None, -1, 4),
        (None, 4, 4),
        (None, 2, 10000001),
    ],
)
def test_corrupt_change_metadata_fails_closed(integration, previous_price, previous_count, count):
    path, _, _, client, _, _ = integration
    seed(path, 1)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE discogs_price_outbox SET price='20'")
        db.execute(
            "INSERT INTO discogs_price_changes VALUES (?,?,?,?)",
            (f"{CANARY}-1-0", previous_price, previous_count, count),
        )
    with pytest.raises(RuntimeError):
        client.activity(1)


@pytest.mark.parametrize("damage", ["application", "table", "index"])
def test_missing_capability_fails_without_repair(integration, damage):
    path, _, _, client, _, _ = integration
    with closing(sqlite3.connect(path)) as db, db:
        if damage == "application":
            db.execute("PRAGMA application_id=1")
        elif damage == "table":
            db.execute("DROP TABLE discogs_price_changes")
        else:
            db.execute("ALTER TABLE discogs_price_outbox RENAME TO old_outbox")
            db.execute("CREATE TABLE discogs_price_outbox AS SELECT * FROM old_outbox")
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        client.activity(1)
    assert path.read_bytes() == before


def test_large_history_uses_indexed_bounded_seek(integration):
    from finder.discogs_watch_activity import ACTIVITY_NEXT_QUERY, ACTIVITY_QUERY

    path, backend, _, _, _, _ = integration
    seed(path, 100001)
    with closing(sqlite3.connect(path)) as db:
        for query, parameters in [(ACTIVITY_QUERY, (1,)), (ACTIVITY_NEXT_QUERY, (1, 1, 40000))]:
            plan = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + query, parameters)]
            assert any("SEARCH o USING INDEX" in line and "release_id=?" in line for line in plan)
            assert any("SEARCH c USING INDEX" in line and "event_key=?" in line for line in plan)
            assert not any("TEMP B-TREE" in line or "SCAN " in line for line in plan)
            if len(parameters) > 1:
                assert any("(rule_revision,episode)<(?,?)" in line for line in plan)
            instructions = 0

            def bound():
                nonlocal instructions
                instructions += 1000
                return instructions > 100000

            db.set_progress_handler(bound, 1000)
            assert len(db.execute(query, parameters).fetchall()) == 21
            assert instructions <= 100000
    assert backend.activity(1)["rows"][0]["episode"] == 100000
    assert backend.activity(1, {"revision": 1, "episode": 40000})["rows"][0]["episode"] == 39999


def test_response_projection_rejects_malformed_fields(integration):
    from finder.discogs_watch_activity import project_activity

    path, backend, _, _, _, _ = integration
    seed(path, 21)
    valid = backend.activity(1)
    variants = []
    for key, value in [
        ("release_id", True),
        ("current_revision", True),
        ("has_more", 1),
        ("next_cursor", None),
        ("secret", CANARY),
        ("rows", valid["rows"] * 2),
    ]:
        value_copy = copy.deepcopy(valid)
        value_copy[key] = value
        variants.append(value_copy)
    for key, value in [
        ("revision", True),
        ("episode", -1),
        ("observed_at", float("nan")),
        ("observed_at", True),
        ("state", "delivered"),
        ("kind", "new_copy"),
        ("settings_scope", "earlier"),
        ("token", CANARY),
    ]:
        value_copy = copy.deepcopy(valid)
        value_copy["rows"][0][key] = value
        variants.append(value_copy)
    duplicate = copy.deepcopy(valid)
    duplicate["rows"][1] = duplicate["rows"][0]
    variants.append(duplicate)
    unordered = copy.deepcopy(valid)
    unordered["rows"][0], unordered["rows"][1] = unordered["rows"][1], unordered["rows"][0]
    variants.append(unordered)
    for invalid in variants:
        with pytest.raises(ValueError):
            project_activity(invalid, 1)
    with pytest.raises(ValueError):
        project_activity(valid, 2)
    with pytest.raises(ValueError):
        project_activity(valid, 1, {"revision": 1, "episode": 20})


@pytest.mark.parametrize("damage", ["secret", "identity", "bool_version", "row", "oversize"])
def test_client_revalidates_forged_wire_response(integration, damage):
    from test_discogs_review_broker import ClientStream

    from finder.discogs_review_broker import encode
    from finder.discogs_watch_broker import WatchBrokerClient

    path, backend, service, _, _, _ = integration
    seed(path, 1)
    response = {"v": 1, "ok": True, "activity": backend.activity(1)}
    if damage == "secret":
        response["secret"] = CANARY
    elif damage == "identity":
        response["activity"]["release_id"] = 2
    elif damage == "bool_version":
        response["v"] = True
    elif damage == "row":
        response["activity"]["rows"][0]["destination_ref"] = CANARY
    else:
        response["secret"] = CANARY * 2000
    wire = encode(response, 1000000)
    streams = []

    def factory():
        stream = ClientStream(service, response=wire)
        streams.append(stream)
        return stream

    client = WatchBrokerClient("/run/fixture/broker.sock", broker_uid=4321, socket_factory=factory)
    with pytest.raises(RuntimeError) as error:
        client.activity(1)
    assert CANARY not in str(error.value)
    assert len(streams) == 1


def test_http_security_headers_and_explicit_action(integration):
    _, _, _, _, app, streams = integration
    raw = b'{"action":"activity","release_id":1,"cursor":null}'
    env = {
        "HTTP_HOST": "review.example",
        "PATH_INFO": "/watches/state",
        "REQUEST_METHOD": "POST",
        "HTTP_ORIGIN": "https://review.example",
        "HTTP_X_REVIEW_PRINCIPAL": "owner",
        "CONTENT_LENGTH": str(len(raw)),
        "CONTENT_TYPE": "application/json",
        "wsgi.input": io.BytesIO(raw),
    }
    captured = {}
    result = b"".join(
        app(env, lambda status, headers: captured.update(status=status, headers=dict(headers)))
    )
    assert captured["status"] == "200 OK" and json.loads(result)["activity"]["rows"] == []
    headers = captured["headers"]
    assert headers["Cache-Control"] == "no-store"
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"
    streams.clear()
    for payload in (
        {"release_id": 1, "cursor": None},
        {"action": "send", "release_id": 1, "cursor": None},
        {"action": "activity", "release_id": 1, "cursor": None, "csrf": CANARY},
    ):
        assert not request(app, "/watches/state", payload)[0].startswith("200")
    assert not streams


@pytest.mark.parametrize("timestamp", [10**500, -1, True, float("nan"), float("inf")])
def test_client_rejects_forged_timestamp(integration, timestamp):
    import struct

    from test_discogs_review_broker import ClientStream

    from finder.discogs_watch_activity import project_activity
    from finder.discogs_watch_broker import WatchBrokerClient

    path, backend, service, _, _, _ = integration
    seed(path, 1)
    activity = backend.activity(1)
    activity["rows"][0]["observed_at"] = timestamp
    with pytest.raises(ValueError):
        project_activity(activity, 1)
    # Construct deliberately invalid wire JSON outside the production encoder,
    # which itself correctly refuses NaN and infinity.
    raw = json.dumps({"v": 1, "ok": True, "activity": activity}).encode()
    wire = struct.pack("!I", len(raw)) + raw
    streams = []

    def factory():
        stream = ClientStream(service, response=wire)
        streams.append(stream)
        return stream

    client = WatchBrokerClient("/run/fixture/broker.sock", broker_uid=4321, socket_factory=factory)
    with pytest.raises(RuntimeError):
        client.activity(1)
    assert len(streams) == 1


def test_raw_activity_frame_limit_includes_whitespace():
    import struct

    from finder.discogs_review_broker import receive

    class Socket:
        def __init__(self, data):
            self.data = data

        def settimeout(self, value):
            pass

        def recv(self, count):
            chunk, self.data = self.data[:count], self.data[count:]
            return chunk

    request = b'{"v":1,"op":"watch_activity","release_id":1,"cursor":null}'
    for size in (1024, 1025, 4096):
        data = request.ljust(size, b" ")
        socket = Socket(struct.pack("!I", len(data)) + data)
        if size == 1024:
            assert receive(
                socket, 4096, timeout=2, operation_limits={"watch_activity": 1024}
            ) == json.loads(request)
        else:
            with pytest.raises(ProtocolError, match="frame_size"):
                receive(socket, 4096, timeout=2, operation_limits={"watch_activity": 1024})
