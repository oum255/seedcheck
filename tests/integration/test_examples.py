"""The shipped examples, run end to end on 22.04, 24.04 and 26.04 against a real LXD.

Run with: pytest -m integration  (takes several minutes)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from seedcheck.cli import EXIT_FAILED, EXIT_INFRA, EXIT_OK, main

pytestmark = pytest.mark.integration

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def seedcheck_containers() -> set[str]:
    out = subprocess.run(
        ["lxc", "list", "--columns", "n", "--format", "csv"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return {name for name in out.stdout.split() if name.startswith("seedcheck-")}


@pytest.fixture(autouse=True)
def leaves_no_containers() -> object:
    before = seedcheck_containers()
    yield
    assert seedcheck_containers() <= before, "a seedcheck container was left behind"


def run_json(example: str, tmp_path: Path, *flags: str) -> tuple[int, dict[str, object]]:
    report = tmp_path / "report.json"
    code = main(["run", str(EXAMPLES / example / "seedcheck.yaml"), "--json", str(report), *flags])
    return code, json.loads(report.read_text())


def test_nginx_passes_on_all_three_releases(tmp_path: Path) -> None:
    code, report = run_json("nginx", tmp_path)
    assert code == EXIT_OK, report
    assert [r["status"] for r in report["releases"]] == ["PASS", "PASS", "PASS"]  # type: ignore[index]


def test_apt_source_passes_on_all_three_releases(tmp_path: Path) -> None:
    code, report = run_json("apt-source", tmp_path)
    assert code == EXIT_OK, report


def test_broken_fails_with_the_expected_cells(tmp_path: Path) -> None:
    code, report = run_json("broken", tmp_path)
    assert code == EXIT_FAILED
    python = {
        r["release"]: next(
            c["status"] for c in r["checks"] if c["name"] == "command: python3 is 3.12"
        )
        for r in report["releases"]  # type: ignore[union-attr]
    }
    assert python == {"22.04": "FAIL", "24.04": "PASS", "26.04": "FAIL"}


def test_tiny_timeout_exits_3_and_leaves_nothing_behind(tmp_path: Path) -> None:
    code, report = run_json("nginx", tmp_path, "--timeout", "5", "--release", "24.04")
    assert code == EXIT_INFRA
    assert report["releases"][0]["status"] == "ERROR"  # type: ignore[index]
