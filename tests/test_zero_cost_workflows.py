"""Keep hosted credentials unreachable while the zero-cost transition is in progress."""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PAUSED_JOBS = {
    "watchlist.yml": "scan",
    "deploy-watchlist.yml": "deploy",
    "deploy-ebay-deletion.yml": "deploy",
    "ebay-quota.yml": "quota",
    "private-target-panel.yml": "measure",
    "production-bounded-scan.yml": "scan",
    "production-color-pair-probe.yml": "probe",
    "production-target-match.yml": "match",
    "production-target-scan.yml": "scan",
    "vinyl-catalog-panel.yml": "measure",
    "ebay-smoke.yml": "production",
}
DISABLED = "if: ${{ false }}"


def job_blocks(text):
    """Read this repository's block-style jobs without executing YAML expressions."""
    jobs = text.split("\njobs:\n", 1)[1]
    matches = list(re.finditer(r"^  ([\w-]+):\s*$", jobs, re.MULTILINE))
    return {
        match.group(1): jobs[match.end() : matches[i + 1].start() if i + 1 < len(matches) else None]
        for i, match in enumerate(matches)
    }


def test_all_hosted_jobs_are_unconditionally_paused():
    for filename, job in PAUSED_JOBS.items():
        block = job_blocks((WORKFLOWS / filename).read_text())[job]
        assert re.search(r"^    if: \$\{\{ false \}\}$", block, re.MULTILINE), filename
        # Paused before allocating a runner or executing any credential-using step.
        assert block.index(DISABLED) < block.index("runs-on:")


def test_every_secret_reference_is_in_a_disabled_job_or_step():
    checked = 0
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text()
        assert "secrets." not in text.split("\njobs:\n", 1)[0], path.name
        for name, block in job_blocks(text).items():
            if "secrets." not in block:
                continue
            checked += 1
            if re.search(r"^    if: \$\{\{ false \}\}$", block, re.MULTILINE):
                continue
            parts = re.split(r"^      - ", block, flags=re.MULTILINE)
            assert "secrets." not in parts[0], (path.name, name, "job environment")
            for step in parts[1:]:
                if "secrets." in step:
                    assert re.search(r"^        if: \$\{\{ false \}\}$", step, re.MULTILINE), (
                        path.name,
                        name,
                        step.splitlines()[0],
                    )
    assert checked >= len(PAUSED_JOBS)


def test_offline_and_local_postgres_checks_remain_available():
    ebay = job_blocks((WORKFLOWS / "ebay-smoke.yml").read_text())["validate"]
    postgres = job_blocks((WORKFLOWS / "feedback-postgres.yml").read_text())["feedback-postgres"]
    assert "python -m pytest -q" in ebay
    assert "npm test" in ebay
    assert not re.search(r"^    if: \$\{\{ false \}\}$", ebay, re.MULTILINE)
    assert "127.0.0.1:" in postgres
    assert "check_isolated_postgres.py" in postgres
    assert "secrets." not in postgres
    assert not re.search(r"^    if: \$\{\{ false \}\}$", postgres, re.MULTILINE)


def test_local_ui_checks_are_offline_and_part_of_the_active_gate():
    workflow = (WORKFLOWS / "ebay-smoke.yml").read_text()
    block = job_blocks(workflow)["validate"]
    section = block.split("      - name: Exercise local review UI without services\n", 1)[1]
    section = section.split("      - name:", 1)[0]
    assert "EBAY_ENVIRONMENT: ''" in section
    assert "secrets." not in section
    assert "npm" not in section
    assert "node tests/test_local_ui.cjs" in section
    assert "python scripts/check_local_review_flow.py" in section
    for helper in ("prepare_local_ui_check.py", "check_local_review_flow.py"):
        assert f'      - "scripts/{helper}"' in workflow
