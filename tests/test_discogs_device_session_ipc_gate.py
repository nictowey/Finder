"""Socket-free validation of gate assertions, not a substitute for real IPC/TLS."""

import importlib
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from finder.discogs_price_alerts import AggregatePriceAlerts, PriceLeadRule
from finder.discogs_rules import RulesStore
from finder.discogs_watch_manager import WatchBackend


@pytest.mark.parametrize("ip_origin", [False, True], ids=["dns", "ipv4"])
def test_session_gate_assertions_without_sockets(tmp_path, monkeypatch, capsys, ip_origin):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    gate = importlib.import_module("discogs_device_session_ipc_selftest")
    host = gate.IP_FIXTURE if ip_origin else "watch.example"
    monkeypatch.setattr(gate, "HOST", host)
    monkeypatch.setattr(gate, "ORIGIN", "https://" + host)
    monkeypatch.setattr(gate, "IP_ORIGIN", ip_origin)

    def no_network(*args, **kwargs):
        raise AssertionError("Socket/DNS access is forbidden in the unit gate")

    monkeypatch.setattr("socket.getaddrinfo", no_network)
    monkeypatch.setattr("socket.socket", no_network)
    database = tmp_path / "synthetic.db"
    store = RulesStore(database, create=True)
    store.close()
    alerts = AggregatePriceAlerts(database)
    alerts.save_rule(
        PriceLeadRule(
            release_id=1,
            public_title="Preserved fixture",
            currency="USD",
            item_price_ceiling="39.99",
            enabled=False,
        ),
        expected_revision=0,
    )
    backend = WatchBackend(AggregatePriceAlerts.for_existing_store(database))
    baseline = backend.snapshot()

    @contextmanager
    def wsgi_app(directory, endpoint, client, session_path, now, generation):
        app, rate_clock = gate.session_app(endpoint, client, session_path, now)
        with httpx.Client(
            transport=httpx.WSGITransport(app),
            base_url="http://" + gate.HOST,
            follow_redirects=False,
        ) as http:
            yield gate.requester(http, rate_clock)

    monkeypatch.setattr(gate, "http_app", wsgi_app)
    gate.session_gate(tmp_path, tmp_path / "never-opened.sock", backend, database, baseline)
    assert "NOT Caddy Basic password validation" in capsys.readouterr().out
