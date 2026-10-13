"""Container lifecycle against a real LXD. Run with: pytest -m integration"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from seedcheck.config import Config, load_config
from seedcheck.lxd import Lxd, LxdClient
from seedcheck.models import CheckResult, Status
from seedcheck.releases import resolve
from seedcheck.runner import RunOptions, run_release

pytestmark = pytest.mark.integration

USER_DATA = """\
#cloud-config
write_files:
  - path: /root/seedcheck-was-here
    content: hello from seedcheck
"""


def lxc_container_names() -> list[str]:
    out = subprocess.run(
        ["lxc", "list", "--columns", "n", "--format", "csv"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


def test_noble_reaches_done_and_the_container_is_deleted(tmp_path: Path) -> None:
    (tmp_path / "user-data.yaml").write_text(USER_DATA)
    (tmp_path / "seedcheck.yaml").write_text("user_data: user-data.yaml\nreleases: ['24.04']\n")
    config = load_config(tmp_path / "seedcheck.yaml")
    client = LxdClient()
    client.ensure_available()
    seen: dict[str, str] = {}

    def probe(client: Lxd, container: str, config: Config) -> list[CheckResult]:
        seen["container"] = container
        status = json.loads(
            client.exec(container, ["cloud-init", "status", "--format", "json"], 60).stdout
        )
        written = client.exec(container, ["cat", "/root/seedcheck-was-here"], 60)
        return [
            CheckResult("cloud-init", Status.PASS if status["status"] == "done" else Status.FAIL),
            CheckResult(
                "user-data applied",
                Status.PASS if written.stdout == "hello from seedcheck" else Status.FAIL,
            ),
        ]

    options = RunOptions(timeout=600, artifacts_dir=tmp_path / "logs")
    result = run_release(client, resolve("24.04"), config, options, check_fn=probe)

    assert result.status is Status.PASS, result.error or result.checks
    assert result.cloud_init_version
    assert (tmp_path / "logs/24.04/cloud-init.log").stat().st_size > 0
    assert seen["container"].startswith("seedcheck-noble-")
    assert seen["container"] not in lxc_container_names()
