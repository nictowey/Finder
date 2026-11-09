"""Synthetic real-IPC gate. No production paths, provider calls or notifications."""

import json
import os
import socket
import sqlite3
import tempfile
import threading
from pathlib import Path

import httpx
from waitress import create_server

from finder.discogs_phone_review_server import build_app
from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_review_backend import DirectReviewBackend
from finder.discogs_review_broker import BrokerClient, BrokerService, initialize_broker_state
from finder.discogs_rules import RulesStore
from finder.discogs_watch_broker import WatchBrokerClient
from finder.discogs_watch_manager import WatchBackend


def main(*, extra_gate=None):
    assert os.getuid() > 0, "Run as an existing unprivileged user, not root"
    with tempfile.TemporaryDirectory(prefix="watch-isolated-") as directory:
        database = Path(directory) / "synthetic.db"
        store = RulesStore(database, create=True)
        store.close()
        alerts = AggregatePriceAlerts(database)
        for ident in range(1, 22):
            alerts.save_rule(
                PriceLeadRule(
                    release_id=ident,
                    public_title="Synthetic fixture",
                    currency="USD",
                    item_price_ceiling="39.99",
                    enabled=ident <= 19,
                ),
                expected_revision=0,
            )
        store = RulesStore(database)
        initialize_broker_state(store.db)
        store.close()
        backend = WatchBackend(AggregatePriceAlerts.for_existing_store(database))
        baseline = backend.snapshot()
        service = BrokerService(
            DirectReviewBackend(database), allowed_uid=os.getuid(), watch_backend=backend
        )
        endpoint = Path(directory) / "fixture.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(endpoint))
        listener.listen(2)
        listener.settimeout(0.1)
        stop = threading.Event()
        errors = []

        def serve():
            while not stop.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                try:
                    with connection:
                        service.serve_connection(connection)
                except Exception as error:
                    errors.append(type(error).__name__)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        http_server = http_thread = http = None
        try:
            client = WatchBrokerClient(endpoint, broker_uid=os.getuid())
            assert client.snapshot() == baseline
            app = build_app(
                backend=BrokerClient(endpoint, broker_uid=os.getuid()),
                origin="https://watch.example",
                principal="fixture-owner",
                enable_watch_management=True,
                watch_backend=client,
            )

            # Real HTTP parsing over a private Unix socket, never TCP or external DNS.
            http_endpoint = Path(directory) / "http.sock"
            http_server = create_server(
                app,
                unix_socket=str(http_endpoint),
                threads=2,
                clear_untrusted_proxy_headers=True,
                asyncore_loop_timeout=0.1,
                max_request_body_size=4096,
                max_request_header_size=8192,
            )
            http_thread = threading.Thread(target=http_server.run, daemon=True)
            http_thread.start()
            transport = httpx.HTTPTransport(uds=str(http_endpoint))
            http = httpx.Client(transport=transport, base_url="http://watch.example", timeout=5)

            def call(path, fields=None, **overrides):
                headers = {
                    "X-Review-Principal": "fixture-owner",
                    "Origin": "https://watch.example",
                    **overrides,
                }
                response = http.request(
                    "POST" if fields is not None else "GET",
                    path,
                    headers=headers,
                    **({"json": fields} if fields is not None else {}),
                )
                return response.status_code, response.content

            def token():
                code, raw = call("/watches/state")
                assert code == 200
                return json.loads(raw)["csrf"]

            def saved(revision, enabled, price):
                snapshot = client.snapshot()
                assert snapshot["watches"][:-1] == baseline["watches"]
                assert len(snapshot["watches"]) == 22
                watch = snapshot["watches"][-1]
                assert watch["revision"] == revision and watch["enabled"] is enabled
                assert watch["item_price_ceiling"] == price
                assert watch["public_title"] == "Discogs release 999"
                assert all(value == 0 for value in watch["notifications"].values())
                assert snapshot["monitor_enabled"] is False
                with sqlite3.connect(database) as db:
                    assert (
                        db.execute("SELECT COUNT(*) FROM discogs_price_outbox").fetchone()[0] == 0
                    )
                return snapshot

            assert call("/watches")[0] == 200
            fields = {
                "release": "https://www.discogs.com/release/999-Synthetic",
                "title": "",
                "currency": "USD",
                "price": "7.99",
                "any_price": False,
                "enabled": True,
                "revision": 0,
                "csrf": token(),
            }
            assert call("/watches/save", fields, Origin="https://wrong.example")[0] == 403
            assert call("/watches/save", fields)[0] == 200
            assert call("/watches/save", fields)[0] == 403
            saved(1, True, "7.99")
            fields.update(release="999", price="6.99", revision=1, csrf=token())
            assert call("/watches/save", fields)[0] == 200
            saved(2, True, "6.99")
            fields.update(revision=2, csrf=token())
            assert call("/watches/save", fields)[0] == 200
            saved(2, True, "6.99")  # Semantic no-op must preserve the revision.
            fields.update(revision=1, csrf=token())
            assert call("/watches/save", fields)[0] == 409
            saved(2, True, "6.99")
            fields.update(enabled=False, revision=2, csrf=token())
            assert call("/watches/save", fields)[0] == 200
            after = saved(3, False, "6.99")
            assert call("/watches/state", **{"X-Review-Principal": "wrong"})[0] == 401
            assert call("/nope")[0] == 404
            wrong_peer = WatchBrokerClient(endpoint, broker_uid=os.getuid() + 1)
            try:
                wrong_peer.snapshot()
                raise AssertionError("wrong broker UID accepted")
            except RuntimeError:
                pass
            replay_fields = {key: value for key, value in fields.items() if key != "csrf"}
            replay_fields["revision"] = 3
            request = {"v": 1, "op": "watch_save", "request_id": "ab" * 16, "fields": replay_fields}
            assert client._watch_call(request)["revision"] == 3
            try:
                client._watch_call(request)
                raise AssertionError("durable request ID replay accepted")
            except RuntimeError:
                pass
            service.allowed_uid = os.getuid() + 1
            try:
                client.snapshot()
                raise AssertionError("server accepted a disallowed client UID")
            except RuntimeError:
                pass
            finally:
                service.allowed_uid = os.getuid()
            assert client.snapshot() == after
            assert not errors
            if extra_gate is not None:
                extra_gate(directory, endpoint, client, database, after)
                assert not errors
            print(
                "PASS real HTTP over Unix socket and broker IPC, both peer UID checks, "
                "owner boundary, CSRF/origin/durable replay, add/edit/no-op/stale/pause, "
                "zero notification counts/outbox, 21 preserved rules; "
                "no production or provider access; not a TLS/login/iOS persistence test"
            )
        finally:
            if http is not None:
                http.close()
            if http_server is not None:
                http_server.task_dispatcher.shutdown()
                http_server.close()
                http_server.asyncore.close_all(map=http_server._map)
            if http_thread is not None:
                http_thread.join(timeout=3)
                assert not http_thread.is_alive()
            stop.set()
            thread.join(timeout=2)
            listener.close()
            assert not thread.is_alive()


if __name__ == "__main__":
    main()
