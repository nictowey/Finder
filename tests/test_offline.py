import json
import os
import socket
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from finder import offline
from finder.persistence import SqlAlchemyRepository


def test_demo_uses_existing_pressing_policy_and_persists_evidence(tmp_path):
    database = tmp_path / "demo.sqlite3"
    result = offline.run_demo(str(database))

    assert result["mode"] == "offline-synthetic"
    assert result["stored_listings"] == 3
    assert [row["review"]["status"] for row in result["reviews"]] == [
        "possible_pressing",
        "family_review",
        "conflicting",
    ]
    assert [row["review"]["notify"] for row in result["reviews"]] == [True, True, False]
    assert [row["observations"] for row in result["reviews"]] == [2, 1, 1]
    assert [row["stored_candidates"] for row in result["reviews"]] == [2, 2, 2]
    assert result["reviews"][0]["review"]["subtotal"] == "23.00"
    assert result["reviews"][1]["review"]["alternatives_not_ruled_out"] == 1

    # Inspect the saved file independently; no outbox or scheduled watch was created.
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert "finder_outbox" not in tables
        assert "finder_watches" not in tables
        assert connection.execute("SELECT count(*) FROM listing_observations").fetchone() == (4,)
        assert connection.execute("SELECT count(*) FROM variants").fetchone() == (2,)
        documents = [json.loads(row[0]) for row in connection.execute("SELECT data FROM listings")]
        assert all(row["marketplace"] == "synthetic" for row in documents)
        assert all(row["listing_url"] is None and row["primary_image"] is None for row in documents)
        claimed = next(row for row in documents if row["marketplace_item_id"] == "claimed-blue")
        assert claimed["current_price"] == "19.00"
        assert datetime.fromisoformat(claimed["first_observed_at"]) == datetime(
            2026, 1, 1, 12, tzinfo=UTC
        )
        assert datetime.fromisoformat(claimed["last_observed_at"]) == datetime(
            2026, 1, 1, 12, 5, tzinfo=UTC
        )


@pytest.mark.parametrize(
    "key",
    [
        "FINDER_DATABASE_URL",
        "DATABASE_URL",
        "DATABASE_URL_UNPOOLED",
        "PGHOST",
        "PGSERVICE",
        "NEON_API_KEY",
        "EBAY_ENVIRONMENT",
        "EBAY_PRODUCTION_CLIENT_SECRET",
        "DISCOGS_TOKEN",
    ],
)
def test_exported_live_configuration_fails_before_creating_database(
    monkeypatch, tmp_path, capsys, key
):
    database = tmp_path / "must-not-exist.sqlite3"
    monkeypatch.setenv(key, "sensitive-placeholder-do-not-print")
    assert offline.main(["--database", str(database)]) == 2
    captured = capsys.readouterr()
    assert key in captured.err
    assert "sensitive-placeholder" not in captured.err
    assert not captured.out
    assert not database.exists()


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://someone:secret@database.invalid/owner",
        "sqlite:///owner.db",
        "file:owner.db?mode=rw",
        ":memory:",
        "//server/share/demo.sqlite3",
        r"\\server\share\demo.sqlite3",
    ],
)
def test_database_urls_and_remote_paths_are_not_accepted(tmp_path, monkeypatch, capsys, value):
    monkeypatch.chdir(tmp_path)
    assert offline.main(["--database", value]) == 2
    assert "local file path" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_existing_owner_database_is_unchanged(tmp_path):
    database = tmp_path / "owner.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE owner_data (value TEXT)")
        connection.execute("INSERT INTO owner_data VALUES ('preserve me')")
    before = database.read_bytes()
    with pytest.raises(offline.OfflineSafetyError, match="never overwrites"):
        offline.run_demo(str(database))
    assert database.read_bytes() == before


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
@pytest.mark.parametrize("kind", ["file", "symlink", "dangling_symlink"])
def test_existing_sqlite_sidecar_is_untouched_and_prevents_database_creation(
    tmp_path, suffix, kind
):
    database = tmp_path / "fresh.sqlite3"
    sidecar = tmp_path / f"fresh.sqlite3{suffix}"
    target = tmp_path / "existing-owner-data"
    contents = b"existing SQLite sidecar bytes must remain unchanged\x00\xff"
    if kind == "file":
        sidecar.write_bytes(contents)
    else:
        if kind == "symlink":
            target.write_bytes(contents)
        sidecar.symlink_to(target)

    with pytest.raises(offline.OfflineSafetyError, match="sidecar"):
        offline.run_demo(str(database))

    assert not database.exists()
    if kind == "file":
        assert sidecar.read_bytes() == contents
    else:
        assert sidecar.is_symlink()
        assert sidecar.readlink() == target
        if kind == "symlink":
            assert target.read_bytes() == contents
        else:
            assert not target.exists()


@pytest.mark.parametrize("filename", [".env", ".env.local", ".env.example"])
def test_database_cannot_create_a_dotenv_file(tmp_path, filename):
    database = tmp_path / filename
    with pytest.raises(offline.OfflineSafetyError, match="dotenv"):
        offline.run_demo(str(database))
    assert not database.exists()


@pytest.mark.parametrize("target_exists", [True, False])
def test_symlink_database_is_rejected_without_touching_target(tmp_path, target_exists):
    target = tmp_path / "owner.sqlite3"
    if target_exists:
        target.write_bytes(b"untouched owner data")
    link = tmp_path / "demo.sqlite3"
    link.symlink_to(target)
    with pytest.raises(offline.OfflineSafetyError, match="symlink"):
        offline.run_demo(str(link))
    assert target.exists() is target_exists
    if target_exists:
        assert target.read_bytes() == b"untouched owner data"


def test_dotenv_is_not_loaded_even_when_it_contains_live_configuration(tmp_path, monkeypatch):
    import dotenv

    monkeypatch.chdir(tmp_path)
    contents = "FINDER_DATABASE_URL=postgresql://placeholder.invalid/owner\nDISCOGS_TOKEN=fake\n"
    (tmp_path / ".env").write_text(contents)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline development must not load environment settings")

    monkeypatch.setattr(dotenv, "load_dotenv", forbidden)
    assert offline.run_demo("demo.sqlite3")["stored_listings"] == 3
    assert (tmp_path / ".env").read_text() == contents
    assert "DISCOGS_TOKEN" not in os.environ


@pytest.mark.parametrize("operation", ["socket", "dns", "subprocess", "dotenv", "other_sqlite"])
def test_accidental_external_io_is_blocked_at_runtime(tmp_path, monkeypatch, operation):
    other_database = tmp_path / "owner.sqlite3"
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text("FINDER_DATABASE_URL=do-not-read\n")

    def accidental_io(database):
        if operation == "socket":
            socket.socket()
        elif operation == "dns":
            socket.getaddrinfo("must-not-be-resolved.invalid", 443)
        elif operation == "subprocess":
            subprocess.run([sys.executable, "-c", "raise SystemExit(99)"], check=True)
        elif operation == "dotenv":
            dotenv_file.read_text()
        else:
            sqlite3.connect(other_database)
        pytest.fail("The offline boundary allowed an unexpected I/O operation")

    monkeypatch.setattr(offline, "_populate_and_review", accidental_io)
    with pytest.raises(offline.OfflineSafetyError):
        offline.run_demo(str(tmp_path / "demo.sqlite3"))
    assert not other_database.exists()
    # The command's guard must not change unrelated tests or the normal runtime afterward.
    with sqlite3.connect(other_database) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)


def test_repository_always_receives_explicit_sqlite_url(tmp_path, monkeypatch):
    factory = SqlAlchemyRepository.from_url
    urls = []

    def capture(url):
        urls.append(url)
        assert url.startswith("sqlite:///")
        return factory(url)

    monkeypatch.setattr(SqlAlchemyRepository, "from_url", capture)
    offline.run_demo(str(tmp_path / "demo.sqlite3"))
    assert len(urls) == 2
    assert urls[0] == urls[1]


def test_module_entrypoint_runs_without_live_cli_or_provider_imports(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src"
    environment = {**os.environ, "PYTHONPATH": str(source)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from finder.offline import main; "
            "assert main([]) == 0; "
            "assert 'finder.cli' not in sys.modules; "
            "assert 'finder.config' not in sys.modules; "
            "assert not any(name.startswith('finder.adapters.') for name in sys.modules)",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["stored_listings"] == 3
    assert not result.stderr
    assert (tmp_path / "finder-offline-demo.sqlite3").is_file()


def test_module_entrypoint_rejects_inherited_credentials(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-m", "finder.offline"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(source),
            "FINDER_DATABASE_URL": "postgresql://fake:do-not-print@invalid/owner",
            "DISCOGS_TOKEN": "also-do-not-print",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "FINDER_DATABASE_URL" in result.stderr
    assert "DISCOGS_TOKEN" in result.stderr
    assert "do-not-print" not in result.stderr
    assert not result.stdout
    assert not list(tmp_path.iterdir())
