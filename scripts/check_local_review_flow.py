"""Exercise the actual guarded loopback service using an already installed offline jsdom."""

import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from finder.offline import _check_environment, _offline_boundary


@contextmanager
def server(database, *, initialize=False):
    command = [sys.executable, "-m", "finder.local_web", "--database", str(database), "--port", "0"]
    if initialize:
        command.append("--init")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=10):
                raise RuntimeError("Local review server did not start in time.")
            first_line = process.stdout.readline().strip()
        match = re.fullmatch(
            r"Finder local development review: (http://127\.0\.0\.1:[0-9]+)", first_line
        )
        if not match:
            raise RuntimeError("Local review server did not provide its exact loopback address.")
        yield match[1]
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
    if process.returncode != 0:
        raise RuntimeError("Local review server did not shut down cleanly.")


def main():
    _check_environment(os.environ)
    with tempfile.TemporaryDirectory(prefix="finder-local-flow-") as directory:
        database = (Path(directory) / "review.sqlite3").resolve()
        with server(database, initialize=True) as origin:
            subprocess.run(
                ["node", "tests/test_local_ui_service.cjs", origin], check=True, timeout=30
            )
        with _offline_boundary(database):
            from finder.local_workspace import LocalWorkspace

            with LocalWorkspace.open(database) as workspace:
                rows = workspace.snapshot()["rows"]
                assert len(rows) == 3
                assert next(row for row in rows if row["id"] == "example-blue")["verdict"] == "mine"
        with server(database) as origin:
            subprocess.run(
                ["node", "tests/test_local_ui_service.cjs", origin, "--verify"],
                check=True,
                timeout=30,
            )
    print("SQLite persistence verified after clean server stop and restart.")


if __name__ == "__main__":
    main()
