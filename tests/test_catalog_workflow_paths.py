"""The catalog gate executes the whole offline suite, including shared fixtures."""

import fnmatch
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "path",
    [
        "tests/conftest.py",
        "tests/test_cli.py",
        "tests/test_target_search.py",
        "tests/test_incomparable_color_confidence.py",
        "tests/fixtures/ebay_search.json",
    ],
)
def test_discogs_gate_runs_when_any_of_its_offline_test_inputs_change(path):
    workflow = Path(".github/workflows/discogs-smoke.yml").read_text()
    # Read this workflow's simple quoted push-path list, without a YAML dependency.
    block = workflow.split("    paths:\n", 1)[1].split("\n\n", 1)[0]
    patterns = [
        json.loads(line.strip()[2:]) for line in block.splitlines() if line.strip().startswith("- ")
    ]
    assert any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
