"""Synthetic end-to-end AF_UNIX protocol and protected host tests, no provider calls."""

import io
import json
import sqlite3
from contextlib import closing

import pytest
from test_discogs_review_broker import ClientStream

from finder.discogs_phone_review_server import build_app
from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_review_backend import DirectReviewBackend
from finder.discogs_review_broker import BrokerService, ProtocolError, initialize_broker_state
from finder.discogs_rules import RulesStore
from finder.discogs_watch_broker import WatchBrokerClient, project_snapshot
from finder.discogs_watch_manager import WatchBackend


@pytest.fixture
def integration(tmp_path):
    path = tmp_path / "rules.db"
    RulesStore(path, create=True).close()
    alerts = AggregatePriceAlerts(path)
    alerts.set_enabled(True, expected_revision=0)
    for ident in range(1, 22):
        alerts.save_rule(
            PriceLeadRule(
                release_id=ident,
                public_title=f"Fixture {ident}",
                currency="USD",
                item_price_ceiling="40",
                enabled=ident <= 19,
                alert_count_increases=True,
            ),
            expected_revision=0,
        )
    with closing(sqlite3.connect(path)) as db:
        initialize_broker_state(db)
    watch = WatchBackend(AggregatePriceAlerts.for_existing_store(path))
    service = BrokerService(DirectReviewBackend(path), allowed_uid=1234, watch_backend=watch)
    streams = []

    def factory():
        stream = ClientStream(service)
        streams.append(stream)
        return stream

    client = WatchBrokerClient("/run/fixture/broker.sock", broker_uid=4321, socket_factory=factory)
    app = build_app(
        backend=client,
        watch_backend=client,
        enable_watch_management=True,
        origin="https://review.example",
        principal="owner",
    )
    return path, watch, service, client, app, streams


def request(app, path="/watches/state", fields=None, **headers):
    raw = json.dumps(fields).encode() if fields is not None else b""
    env = {
        "HTTP_HOST": "review.example",
        "PATH_INFO": path,
        "REQUEST_METHOD": "POST" if fields is not None else "GET",
        "HTTP_ORIGIN": "https://review.example",
        "HTTP_X_REVIEW_PRINCIPAL": "owner",
        "CONTENT_LENGTH": str(len(raw)),
        "CONTENT_TYPE": "application/json",
        "wsgi.input": io.BytesIO(raw),
    }
    env.update(headers)
    result = []
    body = b"".join(app(env, lambda status, _: result.append(status)))
    return result[0], json.loads(body) if path != "/watches" else body


def fields(csrf="unused", revision=1):
    return dict(
        release="1",
        title="Updated fixture",
        currency="USD",
        price="35.00",
        any_price=False,
        enabled=False,
        revision=revision,
        csrf=csrf,
    )


def test_host_to_broker_preserves_rules_and_replay(integration):
    path, watch, _, client, app, streams = integration
    before = watch.snapshot()
    assert sum(w["enabled"] for w in before["watches"]) == 19
    status, state = request(app)
    assert status == "200 OK"
    assert len(state["watches"]) == 21
    assert request(app, "/watches")[0] == "200 OK"
    data = fields(state["csrf"])
    assert request(app, "/watches/save", data) == ("200 OK", {"saved": True})
    assert request(app, "/watches/save", data)[0] == "403 Forbidden"
    after = client.snapshot()
    assert after["watches"][1:] == before["watches"][1:]
    assert after["monitor_enabled"] is True
    assert after["watches"][0]["alert_count_increases"] is True
    assert after["watches"][0]["revision"] == 2
    assert all(s.connected == "/run/fixture/broker.sock" for s in streams)
    # Existing review route remains mounted with its own protections.
    captured = []
    body = b"".join(
        app(
            {
                "HTTP_HOST": "review.example",
                "PATH_INFO": "/listing-review",
                "REQUEST_METHOD": "GET",
                "HTTP_X_REVIEW_PRINCIPAL": "owner",
            },
            lambda status, _: captured.append(status),
        )
    )
    assert captured == ["200 OK"] and b"listing" in body
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM discogs_review_receipts").fetchone()[0] == 1


def test_default_off_and_peer_denial(integration):
    _, watch, service, client, _, _ = integration
    disabled = BrokerService(service.backend, allowed_uid=1234)
    assert not disabled.handle({"v": 1, "op": "watch_snapshot"}, 1234)["ok"]
    with pytest.raises(ProtocolError):
        service.handle({"v": 1, "op": "watch_snapshot"}, 9999)
    app = build_app(backend=client, origin="https://review.example", principal="owner")
    status = []
    app(
        {
            "HTTP_HOST": "review.example",
            "PATH_INFO": "/watches",
            "REQUEST_METHOD": "GET",
            "HTTP_X_REVIEW_PRINCIPAL": "owner",
        },
        lambda s, _: status.append(s),
    )
    assert not status[0].startswith("200")
    assert len(watch.snapshot()["watches"]) == 21


@pytest.mark.parametrize(
    "header,value",
    [
        ("HTTP_X_REVIEW_PRINCIPAL", "other"),
        ("HTTP_AUTHORIZATION", "Basic untrusted"),
        ("HTTP_COOKIE", "untrusted"),
        ("HTTP_FORWARDED", "for=127.0.0.1"),
        ("HTTP_HOST", "evil.example"),
    ],
)
def test_auth_boundary(integration, header, value):
    *_, app, streams = integration
    assert not request(app, **{header: value})[0].startswith("200")
    assert not streams


def test_durable_replay_conflict_and_exact_fields(integration):
    _, watch, service, _, _, _ = integration
    data = fields()
    del data["csrf"]
    req = {"v": 1, "op": "watch_save", "request_id": "a" * 32, "fields": data}
    assert service.handle(req, 1234)["ok"]
    assert service.handle(req, 1234)["error"] == "replay"
    # Even a new ID cannot overwrite the saved revision.
    assert not service.handle({**req, "request_id": "b" * 32}, 1234)["ok"]
    assert watch.snapshot()["watches"][0]["revision"] == 2
    assert not service.handle({**req, "fields": {**data, "database": "/tmp/other"}}, 1234)["ok"]
    assert not service.handle({"v": 1, "op": []}, 1234)["ok"]


def test_response_lost_never_retries(integration):
    _, watch, service, _, _, _ = integration
    streams = []

    def factory():
        stream = ClientStream(service, fail="response")
        streams.append(stream)
        return stream

    client = WatchBrokerClient("/run/fixture/broker.sock", broker_uid=4321, socket_factory=factory)
    with pytest.raises(RuntimeError):
        client.save(fields())
    assert len(streams) == 1
    assert watch.snapshot()["watches"][0]["revision"] == 2


def test_projection_excludes_internal_data(integration):
    _, watch, _, _, _, _ = integration
    data = watch.snapshot()
    data["secret"] = "never-return"
    data["watches"][0]["internal"] = "never-return"
    projected = project_snapshot(data)
    assert "never-return" not in json.dumps(projected)


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_ORIGIN": "https://evil.example"},
        {"HTTP_SEC_FETCH_SITE": "cross-site"},
        {"CONTENT_TYPE": "text/plain"},
        {"CONTENT_LENGTH": "4097"},
    ],
)
def test_save_origin_and_size_before_ipc(integration, headers):
    _, watch, _, _, app, streams = integration
    _, state = request(app)
    count = len(streams)
    assert not request(app, "/watches/save", fields(state["csrf"]), **headers)[0].startswith("200")
    assert len(streams) == count
    assert watch.snapshot()["watches"][0]["revision"] == 1


def test_client_rejects_wrong_broker_uid(integration):
    _, watch, service, _, _, _ = integration
    client = WatchBrokerClient(
        "/run/fixture/broker.sock",
        broker_uid=4321,
        socket_factory=lambda: ClientStream(service, broker_uid=4322),
    )
    with pytest.raises(RuntimeError):
        client.save(fields())
    assert watch.snapshot()["watches"][0]["revision"] == 1


@pytest.mark.parametrize(
    "counts",
    [
        {
            "accepted": True,
            "accepted_current_revision": 0,
            "pending": 0,
            "in_flight": 0,
            "uncertain": 0,
        },
        {
            "accepted": 1,
            "accepted_current_revision": 2,
            "pending": 0,
            "in_flight": 0,
            "uncertain": 0,
        },
        {
            "accepted": -1,
            "accepted_current_revision": 0,
            "pending": 0,
            "in_flight": 0,
            "uncertain": 0,
        },
        {"accepted": 1},
    ],
)
def test_invalid_notification_projection_fails_closed(integration, counts):
    _, backend, _, _, _, _ = integration
    state = backend.snapshot()
    state["watches"][0]["notifications"] = counts
    with pytest.raises(ProtocolError):
        project_snapshot(state)
