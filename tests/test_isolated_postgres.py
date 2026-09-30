"""The PR PostgreSQL entrypoint must reject nonfixture targets before connecting."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "isolated_postgres", Path(__file__).parents[1] / "scripts/check_isolated_postgres.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

FIXTURE = "postgresql://finder_ci:finder_ci_ephemeral@127.0.0.1:5432/finder_ci"


def test_fixture_accepts_runner_assigned_port():
    module.validate_fixture_url(FIXTURE)
    module.validate_fixture_url(FIXTURE.replace(":5432/", ":49173/"))


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a URL",
        FIXTURE.replace("127.0.0.1", "production.example"),
        FIXTURE.replace("/finder_ci", "/production"),
        FIXTURE.replace("finder_ci_ephemeral", "real-secret"),
        FIXTURE.replace("finder_ci:", "production:"),
        FIXTURE.replace("postgresql:", "sqlite:"),
        FIXTURE.replace(":5432", ""),
        FIXTURE.replace(":5432", ":0"),
        FIXTURE + "?host=production.example",
        FIXTURE + "?options=-csearch_path=public",
    ],
)
def test_nonfixture_targets_rejected_without_leaking_connection_string(url):
    with pytest.raises(ValueError) as error:
        module.validate_fixture_url(url)
    assert str(error.value) == "PostgreSQL rehearsal requires the isolated CI fixture"


def test_entrypoint_never_invokes_rehearsal_for_remote_target(monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = []
    monkeypatch.setitem(
        sys.modules, "check_watch_postgres", SimpleNamespace(main=lambda: calls.append(1))
    )
    monkeypatch.setenv("FINDER_DATABASE_URL", FIXTURE.replace("127.0.0.1", "production.example"))
    with pytest.raises(ValueError):
        module.main()
    assert calls == []
    monkeypatch.setenv("FINDER_DATABASE_URL", FIXTURE)
    module.main()
    assert calls == [1]
