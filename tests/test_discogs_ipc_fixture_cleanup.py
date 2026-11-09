"""No sockets: deterministic fake Waitress lifecycle and real bounded threads."""

import errno
import importlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def runner_type(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    return importlib.import_module("discogs_watch_ipc_selftest").FixtureHTTPRunner


class FakeServer:
    def __init__(self, *, error=None, immediate=False, workers=False, descriptors=False):
        self.released = threading.Event()
        self.error = error
        self.immediate = immediate
        self.workers = workers
        self.descriptors = descriptors
        self._map = {1: object()}
        self.task_dispatcher = SimpleNamespace(threads={1}, shutdown=self.shutdown)
        self.asyncore = SimpleNamespace(close_all=self.close_all)

    def run(self):
        if not self.immediate:
            assert self.released.wait(timeout=3), "Test release not signaled"
        if self.error is not None:
            raise self.error

    def shutdown(self, *, timeout):
        assert timeout == 3
        if not self.workers:
            self.task_dispatcher.threads.clear()

    def close(self):
        self.released.set()

    def close_all(self, *, map):
        assert map is self._map
        if not self.descriptors:
            map.clear()


@pytest.mark.parametrize("error", [None, OSError(errno.EBADF, "synthetic close race")])
def test_clean_or_expected_closing_ebadf(runner_type, error):
    server = FakeServer(error=error)
    runner = runner_type(server)
    runner.stop()
    assert not runner.thread.is_alive()
    assert not runner.errors
    assert runner.close_ebadf == (1 if error else 0)


@pytest.mark.parametrize("error", [OSError(errno.EIO, "wrong error"), RuntimeError("bug")])
def test_other_errors_during_close_fail(runner_type, error):
    runner = runner_type(FakeServer(error=error))
    with pytest.raises(AssertionError, match="Unexpected") as result:
        runner.stop()
    assert result.value.__cause__ is error
    assert not runner.thread.is_alive()


def test_ebadf_before_shutdown_is_not_hidden(runner_type):
    error = OSError(errno.EBADF, "service failure")
    runner = runner_type(FakeServer(error=error, immediate=True))
    runner.thread.join(timeout=3)
    assert not runner.thread.is_alive()
    with pytest.raises(AssertionError, match="Unexpected") as result:
        runner.stop()
    assert result.value.__cause__ is error


@pytest.mark.parametrize("leak", ["workers", "descriptors"])
def test_resource_leaks_fail(runner_type, leak):
    runner = runner_type(FakeServer(**{leak: True}))
    with pytest.raises(AssertionError, match="did not stop|remain open"):
        runner.stop()
    assert not runner.thread.is_alive()


def test_join_timeout_is_bounded_and_fails(runner_type):
    runner = runner_type(FakeServer(immediate=True))
    runner.thread.join(timeout=3)
    assert not runner.thread.is_alive()
    joins = []
    runner.thread = SimpleNamespace(
        join=lambda *, timeout: joins.append(timeout), is_alive=lambda: True
    )
    with pytest.raises(AssertionError, match="listener did not stop"):
        runner.stop()
    assert joins == [3]


def test_cleanup_failure_still_closes_and_joins(runner_type):
    server = FakeServer()
    runner = runner_type(server)
    error = RuntimeError("synthetic shutdown failure")

    def fail(*, timeout):
        server.task_dispatcher.threads.clear()
        raise error

    server.task_dispatcher.shutdown = fail
    with pytest.raises(AssertionError, match="Unexpected") as result:
        runner.stop()
    assert result.value.__cause__ is error
    assert not runner.thread.is_alive() and not server._map
