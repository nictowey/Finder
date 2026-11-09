"""Synthetic real-IPC gate. No production paths, provider calls or notifications."""

import io
import json
import os
import socket
import tempfile
import threading
from pathlib import Path

from finder.discogs_phone_review_server import build_app
from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_review_backend import DirectReviewBackend
from finder.discogs_review_broker import BrokerClient, BrokerService, initialize_broker_state
from finder.discogs_rules import RulesStore
from finder.discogs_watch_broker import WatchBrokerClient
from finder.discogs_watch_manager import WatchBackend


def main():
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

            def call(path, fields=None, **overrides):
                raw = json.dumps(fields).encode() if fields is not None else b""
                env = {
                    "HTTP_HOST": "watch.example",
                    "PATH_INFO": path,
                    "REQUEST_METHOD": "POST" if fields is not None else "GET",
                    "HTTP_X_REVIEW_PRINCIPAL": "fixture-owner",
                    "HTTP_ORIGIN": "https://watch.example",
                    "CONTENT_TYPE": "application/json",
                    "CONTENT_LENGTH": str(len(raw)),
                    "wsgi.input": io.BytesIO(raw),
                    **overrides,
                }
                response = []
                body = b"".join(app(env, lambda status, headers: response.append(status)))
                return int(response[0].split()[0]), body

            code, raw = call("/watches/state")
            assert code == 200
            fields = {
                "release": "999",
                "title": "TEST SYNTHETIC ONLY",
                "currency": "USD",
                "price": "7.99",
                "any_price": False,
                "enabled": False,
                "revision": 0,
                "csrf": json.loads(raw)["csrf"],
            }
            assert call("/watches/save", fields, HTTP_ORIGIN="https://wrong.example")[0] == 403
            assert call("/watches/save", fields)[0] == 200
            assert call("/watches/save", fields)[0] == 403
            after = client.snapshot()
            assert after["watches"][:-1] == baseline["watches"]
            assert len(after["watches"]) == 22 and not after["watches"][-1]["enabled"]
            assert after["monitor_enabled"] is False
            assert call("/watches/state", HTTP_X_REVIEW_PRINCIPAL="wrong")[0] == 401
            wrong_peer = WatchBrokerClient(endpoint, broker_uid=os.getuid() + 1)
            try:
                wrong_peer.snapshot()
                raise AssertionError("wrong broker UID accepted")
            except RuntimeError:
                pass
            replay_fields = {key: value for key, value in fields.items() if key != "csrf"}
            replay_fields["revision"] = 1
            request = {"v": 1, "op": "watch_save", "request_id": "ab" * 16, "fields": replay_fields}
            assert client._watch_call(request)["revision"] == 1
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
            print(
                "PASS real Unix IPC, both peer UID checks, owner auth, CSRF/origin/durable replay, "
                "synthetic add, 21 preserved rules; no production or provider access"
            )
        finally:
            stop.set()
            thread.join(timeout=2)
            listener.close()
            assert not thread.is_alive()


if __name__ == "__main__":
    main()
