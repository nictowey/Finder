"""Deployment migration must not perform optional provider or worker work."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "watchlist_entrypoint", Path(__file__).parents[1] / "scripts/run_watchlist.py"
)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


@pytest.fixture
def worker(monkeypatch):
    repo = SimpleNamespace(engine=object(), close=Mock())
    settings = SimpleNamespace(
        ebay_environment="production",
        database_url=SimpleNamespace(
            get_secret_value=lambda: "postgresql://example:example@database.invalid/finder"
        ),
    )
    factory = Mock(return_value=repo)
    migrate = Mock()
    scan = Mock(return_value={"failed": 0, "quota_paused": 0})
    discogs = Mock(return_value=object())
    store = Mock()
    monkeypatch.setattr(entry, "load_settings", lambda: settings)
    monkeypatch.setattr(entry.SqlAlchemyRepository, "from_url", factory)
    monkeypatch.setattr(entry, "migrate", migrate)
    monkeypatch.setattr(entry, "run_due_watches", scan)
    monkeypatch.setattr(entry, "load_discogs_settings", discogs)
    monkeypatch.setattr(entry, "WatchStore", store)
    monkeypatch.setenv("FINDER_SEED_RELEASES", "123,456")
    return SimpleNamespace(
        repo=repo, factory=factory, migrate=migrate, scan=scan, discogs=discogs, store=store
    )


def test_migration_only_closes_connection_without_seed_catalog_or_scan(worker, monkeypatch, capsys):
    monkeypatch.setattr(entry.sys, "argv", ["run_watchlist.py", "--migrate-only"])
    assert entry.main() == 0
    worker.migrate.assert_called_once_with(worker.repo.engine)
    worker.repo.close.assert_called_once_with()
    worker.store.assert_not_called()
    worker.discogs.assert_not_called()
    worker.scan.assert_not_called()
    assert json.loads(capsys.readouterr().out) == {"status": "migrated"}


def test_migration_failure_closes_connection_and_never_scans(worker, monkeypatch, capsys):
    monkeypatch.setattr(entry.sys, "argv", ["run_watchlist.py", "--migrate-only"])
    worker.migrate.side_effect = RuntimeError("sensitive synthetic failure")
    assert entry.main() == 1
    worker.repo.close.assert_called_once_with()
    worker.scan.assert_not_called()
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "reason": "worker_unavailable",
    }


def test_migration_only_rejects_conflicting_bootstrap_before_connect(worker, monkeypatch, capsys):
    monkeypatch.setattr(entry.sys, "argv", ["run_watchlist.py", "--migrate-only", "--seed"])
    assert entry.main() == 1
    worker.factory.assert_not_called()
    worker.scan.assert_not_called()
    assert "sensitive" not in capsys.readouterr().out


@pytest.mark.parametrize("bootstrap", [False, True])
def test_ordinary_worker_and_explicit_bootstrap_keep_existing_behavior(
    worker, monkeypatch, bootstrap
):
    monkeypatch.setattr(entry.sys, "argv", ["run_watchlist.py", *(["--seed"] if bootstrap else [])])
    assert entry.main() == 0
    worker.migrate.assert_called_once_with(worker.repo.engine)
    worker.scan.assert_called_once()
    worker.discogs.assert_called_once_with()
    worker.repo.close.assert_called_once_with()
    assert worker.store.call_count == int(bootstrap)
    if bootstrap:
        assert worker.store.return_value.add.call_count == 2


@pytest.mark.parametrize(
    "result", [{"failed": 1, "quota_paused": 0}, {"failed": 0, "quota_paused": 1}]
)
def test_scheduled_failures_keep_nonzero_exit(worker, monkeypatch, result):
    monkeypatch.setattr(entry.sys, "argv", ["run_watchlist.py"])
    worker.scan.return_value = result
    assert entry.main() == 1
    worker.repo.close.assert_called_once_with()
