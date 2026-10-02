"""Real subprocess lifecycle checks; no database or provider connections."""

import importlib.util
import json
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "manual_refresh_gate", Path(__file__).parents[1] / "scripts/check_manual_refresh.py"
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

NODE_LOOP = """
import { createInterface } from 'node:readline';
console.log(JSON.stringify({phase:'ready'}));
for await (const line of createInterface({ input: process.stdin })) {
  if (JSON.parse(line).action === 'close') break;
}
"""


@pytest.fixture
def endpoint():
    if not shutil.which("node"):
        pytest.skip("Subprocess lifecycle checks require Node (required in PostgreSQL PR CI)")
    children = []

    def launch(source=NODE_LOOP, *, args=(), env=None):
        child = gate.RefreshEndpoint.__new__(gate.RefreshEndpoint)
        child.buffer = b""
        child.process = subprocess.Popen(
            ["node", *args] if args else ["node", "--input-type=module", "-e", source],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        children.append(child.process)
        return child

    yield launch
    for process in children:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()


def assert_closed(endpoint):
    assert endpoint.process.poll() is not None
    assert endpoint.process.stdin.closed
    assert endpoint.process.stdout.closed


def test_close_sends_eof_and_reaps_node_after_its_readline_loop_finishes(endpoint):
    child = endpoint()
    child.receive("ready")
    child.close(timeout=1)
    assert child.process.returncode == 0
    assert_closed(child)


def test_already_failed_child_is_reaped_and_remains_a_gate_failure(endpoint):
    child = endpoint("process.exit(7)")
    assert child.process.wait(timeout=5) == 7
    with pytest.raises(subprocess.CalledProcessError) as raised:
        child.close(timeout=1)
    assert raised.value.returncode == 7
    assert_closed(child)


def test_stubborn_child_is_killed_and_timeout_remains_a_gate_failure(endpoint):
    child = endpoint("process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);" + NODE_LOOP)
    child.receive("ready")
    with pytest.raises(subprocess.TimeoutExpired):
        child.close(timeout=0.2)
    assert child.process.returncode == -signal.SIGKILL
    assert_closed(child)


def test_cleanup_timeout_does_not_replace_original_gate_failure(endpoint, monkeypatch):
    child = endpoint("process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);" + NODE_LOOP)
    child.receive("ready")
    close = child.close
    monkeypatch.setattr(child, "close", lambda: close(timeout=0.2))
    with pytest.raises(AssertionError, match="synthetic primary failure") as raised, child:
        raise AssertionError("synthetic primary failure")
    assert raised.value.__notes__ == ["Refresh endpoint cleanup also failed: TimeoutExpired"]
    assert child.process.returncode == -signal.SIGKILL
    assert_closed(child)


def test_cleanup_failure_without_primary_error_still_fails(endpoint):
    child = endpoint("process.exit(7)")
    assert child.process.wait(timeout=5) == 7
    with pytest.raises(subprocess.CalledProcessError) as raised, child:
        pass
    assert raised.value.returncode == 7
    assert_closed(child)


@pytest.mark.parametrize(
    ("cleanup_failure", "delay_setup"),
    [("", False), ("rollback", False), ("release", False), ("pool_end", False), ("", True)],
    ids=["normal", "rollback_failure", "release_failure", "pool_end_failure", "delayed_setup"],
)
def test_actual_driver_releases_database_resources_and_exits(
    endpoint, tmp_path, cleanup_failure, delay_setup
):
    # Run the actual TS protocol helper. Only pg is replaced; lifecycle cleanup,
    # stdin/readline, handler dispatch and process exit are the real code.
    import os

    if not Path("node_modules/tsx").exists():
        pytest.skip("Driver lifecycle checks require npm ci (required in PostgreSQL PR CI)")
    trace = tmp_path / "lifecycle.jsonl"
    child = endpoint(
        args=(
            "--import",
            "tsx",
            "--import",
            "./tests/support/refresh_shutdown_loader.mjs",
            "scripts/check_manual_refresh.ts",
        ),
        env={
            **os.environ,
            "FINDER_SHUTDOWN_TRACE": str(trace),
            "FINDER_SHUTDOWN_FAIL_AT": cleanup_failure,
            "FINDER_SHUTDOWN_DELAY_SETUP": str(int(delay_setup)),
        },
    )
    child.start({"id": "synthetic-watch"}, "synthetic-item", hold=True)
    child.finish()
    # Keep Python's stdin deliberately open: Node must explicitly close its own
    # readline resource and complete every database cleanup operation.
    child.send(action="close")
    assert child.process.wait(timeout=5) == int(bool(cleanup_failure))
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    assert events == ["rollback", "release", "pool_end"]
    if cleanup_failure:
        with pytest.raises(subprocess.CalledProcessError):
            child.close(timeout=1)
    else:
        child.close(timeout=1)
    assert_closed(child)
