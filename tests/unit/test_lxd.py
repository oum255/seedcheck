from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from seedcheck import lxd
from seedcheck.errors import LxdError, TimeoutError_
from seedcheck.lxd import ExecResult, LxdClient


class FakeSubprocess:
    """Stands in for subprocess.run: records calls and replays scripted outcomes in order."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outcomes: list[tuple[int, str, str] | BaseException] = []

    def add(self, code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.outcomes.append((code, stdout, stderr))

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append({"argv": argv, **kwargs})
        outcome = self.outcomes.pop(0) if self.outcomes else (0, "", "")
        if isinstance(outcome, BaseException):
            raise outcome
        code, stdout, stderr = outcome
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    @property
    def argvs(self) -> list[list[str]]:
        return [call["argv"] for call in self.calls]


@pytest.fixture
def proc(monkeypatch: pytest.MonkeyPatch) -> FakeSubprocess:
    fake = FakeSubprocess()
    monkeypatch.setattr(lxd.subprocess, "run", fake)
    monkeypatch.setattr(lxd.shutil, "which", lambda name: f"/snap/bin/{name}")
    return fake


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def client() -> LxdClient:
    client = LxdClient()
    clock = FakeClock()
    client.clock = clock
    client.sleep = clock.sleep
    return client


def test_stdin_is_never_inherited(proc: FakeSubprocess, client: LxdClient) -> None:
    client.exec("c1", ["true"], timeout=5)
    assert proc.calls[0]["stdin"] is subprocess.DEVNULL
    assert proc.calls[0]["timeout"] == 5
    assert "shell" not in proc.calls[0]


def test_input_is_passed_on_stdin_when_given(proc: FakeSubprocess, client: LxdClient) -> None:
    client._run(["launch", "ubuntu:24.04", "c1"], timeout=5, input="config: {}\n")
    assert proc.calls[0]["input"] == "config: {}\n"
    assert "stdin" not in proc.calls[0]


def test_missing_lxc_binary_is_an_lxd_error(proc: FakeSubprocess, client: LxdClient) -> None:
    proc.outcomes.append(FileNotFoundError())
    with pytest.raises(LxdError, match=r"'lxc' command was not found; run scripts/setup-lxd\.sh"):
        client.exec("c1", ["true"], timeout=5)


def test_lxc_timeout_is_a_seedcheck_timeout(proc: FakeSubprocess, client: LxdClient) -> None:
    proc.outcomes.append(subprocess.TimeoutExpired(["lxc"], 5))
    with pytest.raises(TimeoutError_, match="'lxc exec' did not finish within 5s"):
        client.exec("c1", ["sleep", "100"], timeout=5)


def test_launch_passes_user_data_as_one_argument(proc: FakeSubprocess, client: LxdClient) -> None:
    user_data = "#cloud-config\nruncmd:\n  - echo 'hi; rm -rf /' $(whoami)\n"
    client.launch("ubuntu:24.04", "seedcheck-noble-abc123", user_data)
    assert proc.argvs == [
        [
            "lxc",
            "launch",
            "ubuntu:24.04",
            "seedcheck-noble-abc123",
            "--config",
            f"cloud-init.user-data={user_data}",
        ]
    ]


def test_launch_failure_reports_lxc_error(proc: FakeSubprocess, client: LxdClient) -> None:
    proc.add(
        1,
        "Launching x\n",
        "Error: Failed instance creation: The requested image couldn't be found\n",
    )
    with pytest.raises(LxdError, match=r"could not launch ubuntu:99\.99: Failed instance creation"):
        client.launch("ubuntu:99.99", "x", "#cloud-config\n")


def test_exec_returns_exit_code_and_output(proc: FakeSubprocess, client: LxdClient) -> None:
    proc.add(7, "out\n", "err\n")
    assert client.exec("c1", ["sh", "-c", "exit 7"], timeout=5) == ExecResult(7, "out\n", "err\n")
    assert proc.argvs == [["lxc", "exec", "c1", "--", "sh", "-c", "exit 7"]]


def test_wait_ready_polls_until_the_container_answers(
    proc: FakeSubprocess, client: LxdClient
) -> None:
    proc.add(1, stderr="Error: Instance is not running\n")
    proc.add(1)
    proc.add(0)
    client.wait_ready("c1", timeout=60)
    assert proc.argvs == [["lxc", "exec", "c1", "--", "true"]] * 3


def test_wait_ready_times_out(proc: FakeSubprocess, client: LxdClient) -> None:
    proc.outcomes = [(1, "", "")] * 100
    with pytest.raises(TimeoutError_, match="container c1 was not ready after 5s"):
        client.wait_ready("c1", timeout=5)
    assert len(proc.calls) == 5  # one poll per second


def test_wait_ready_survives_a_hanging_poll(
    proc: FakeSubprocess, client: LxdClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = client.clock

    def hang(argv: list[str], **kwargs: Any) -> None:
        clock.now += kwargs["timeout"]  # type: ignore[attr-defined]
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(lxd.subprocess, "run", hang)
    with pytest.raises(TimeoutError_, match="not ready after 10s"):
        client.wait_ready("c1", timeout=10)


def test_pull_file(proc: FakeSubprocess, client: LxdClient, tmp_path: Path) -> None:
    client.pull_file("c1", "/var/log/cloud-init.log", tmp_path / "cloud-init.log")
    assert proc.argvs == [
        ["lxc", "file", "pull", "c1/var/log/cloud-init.log", str(tmp_path / "cloud-init.log")]
    ]


def test_pull_missing_file_is_an_error(proc: FakeSubprocess, client: LxdClient) -> None:
    proc.add(1, stderr="Error: Not Found\n")
    with pytest.raises(LxdError, match="could not copy /nope out of c1: Not Found"):
        client.pull_file("c1", "/nope", Path("/tmp/x"))


def test_delete_forces(proc: FakeSubprocess, client: LxdClient) -> None:
    client.delete("c1")
    assert proc.argvs == [["lxc", "delete", "--force", "c1"]]


def test_delete_of_a_container_already_gone_is_not_an_error(
    proc: FakeSubprocess, client: LxdClient
) -> None:
    proc.add(1, stderr='Error: Failed checking instance exists "local:c1": Instance not found\n')
    client.delete("c1")


def test_delete_failing_for_another_reason_is_an_error(
    proc: FakeSubprocess, client: LxdClient
) -> None:
    proc.add(1, stderr="Error: Failed deleting storage volume: permission denied\n")
    with pytest.raises(LxdError, match="c1: Failed deleting storage volume"):
        client.delete("c1")


def test_ensure_available_succeeds_when_lxd_works(proc: FakeSubprocess, client: LxdClient) -> None:
    proc.add(0, "config: {}\n")
    proc.add(0, "default,dir,/var/snap/lxd/common/lxd/storage-pools/default,,1,CREATED\n")
    client.ensure_available()
    assert proc.argvs == [["lxc", "info"], ["lxc", "storage", "list", "--format", "csv"]]


def test_ensure_available_without_lxc_installed(
    proc: FakeSubprocess, client: LxdClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lxd.shutil, "which", lambda name: None)
    with pytest.raises(LxdError, match=r"not found; run scripts/setup-lxd\.sh"):
        client.ensure_available()
    assert proc.calls == []


def test_ensure_available_without_group_membership(proc: FakeSubprocess, client: LxdClient) -> None:
    proc.add(
        1,
        stderr='Error: LXD unix socket "/var/snap/lxd/common/lxd/unix.socket" not accessible: '
        "permission denied\n",
    )
    with pytest.raises(LxdError, match=r"permission denied .* add yourself to the lxd group"):
        client.ensure_available()


def test_ensure_available_when_the_daemon_is_down(proc: FakeSubprocess, client: LxdClient) -> None:
    proc.add(1, stderr="Error: connect: no such file or directory\n")
    with pytest.raises(LxdError, match=r"LXD is not reachable \(connect: no such file"):
        client.ensure_available()


def test_ensure_available_when_lxd_is_not_initialized(
    proc: FakeSubprocess, client: LxdClient
) -> None:
    proc.add(0, "config: {}\n")
    proc.add(0, "")
    with pytest.raises(LxdError, match="no storage pool, so it is not initialized"):
        client.ensure_available()


def test_verbose_mode_logs_commands_and_output(proc: FakeSubprocess) -> None:
    lines: list[str] = []
    client = LxdClient(verbose=True, log=lines.append)
    proc.add(0, "hello\n")
    client.exec("c1", ["echo", "hello world"], timeout=5)
    assert lines == ["$ lxc exec c1 -- echo 'hello world'", "  hello", "  (exit 0)"]


def test_error_message_falls_back_to_last_line_or_exit_code(
    proc: FakeSubprocess, client: LxdClient
) -> None:
    proc.add(1, stderr="something odd\nlast line\n")
    with pytest.raises(LxdError, match="could not delete container c1: last line"):
        client.delete("c1")
    proc.add(1, stderr="")
    with pytest.raises(LxdError, match="could not launch img: exit code 1"):
        client.launch("img", "c1", "")


BUSY = (
    'Error: Failed deleting instance "c1" in project "default": Failed creating instance '
    'delete operation: Instance is busy running a "create" operation\n'
)


def test_delete_waits_for_an_interrupted_launch_to_finish(
    proc: FakeSubprocess, client: LxdClient
) -> None:
    # Regression: Ctrl+C during `lxc launch` left LXD still creating the
    # container, so the cleanup delete failed and the container leaked.
    proc.add(1, stderr=BUSY)
    proc.add(1, stderr=BUSY)
    proc.add(0)
    client.delete("c1")
    assert proc.argvs == [["lxc", "delete", "--force", "c1"]] * 3


def test_delete_gives_up_if_the_container_stays_busy(
    proc: FakeSubprocess, client: LxdClient
) -> None:
    proc.outcomes = [(1, "", BUSY)] * 1000
    with pytest.raises(LxdError, match="Instance is busy"):
        client.delete("c1")
