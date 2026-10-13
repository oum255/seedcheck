from __future__ import annotations

import re
from pathlib import Path

import pytest

from seedcheck.config import Config, load_config
from seedcheck.errors import LxdError, TimeoutError_
from seedcheck.lxd import ExecResult, Lxd
from seedcheck.models import CheckResult, Status
from seedcheck.releases import resolve
from seedcheck.runner import (
    RunOptions,
    container_name,
    overall_status,
    run_all,
    run_release,
)

from .fakes import FakeLxdClient

USER_DATA = "#cloud-config\npackages: [curl]\n"


@pytest.fixture
def config(tmp_path: Path) -> Config:
    (tmp_path / "user-data.yaml").write_text(USER_DATA)
    path = tmp_path / "seedcheck.yaml"
    path.write_text("user_data: user-data.yaml\n")
    return load_config(path)


@pytest.fixture
def client() -> FakeLxdClient:
    return FakeLxdClient()


def quiet(message: str) -> None:
    pass


def passing_checks(client: Lxd, container: str, config: Config) -> list[CheckResult]:
    return [CheckResult("schema", Status.PASS), CheckResult("cloud-init", Status.PASS)]


OPTIONS = RunOptions(timeout=600)


def test_container_names_are_unique_and_name_the_release() -> None:
    noble = resolve("24.04")
    names = {container_name(noble) for _ in range(50)}
    assert len(names) == 50
    assert all(re.fullmatch(r"seedcheck-noble-[0-9a-f]{6}", name) for name in names)


def test_successful_release_runs_the_full_lifecycle_in_order(
    client: FakeLxdClient, config: Config
) -> None:
    result = run_release(
        client, resolve("24.04"), config, OPTIONS, check_fn=passing_checks, progress=quiet
    )
    name = client.names("launch")[0]
    assert client.calls == [
        ("launch", "ubuntu:24.04", name),
        ("wait_ready", name),
        ("exec", name, "cloud-init", "status", "--wait"),
        ("exec", name, "cloud-init", "--version"),
        ("delete", name),
    ]
    assert client.user_data[name] == USER_DATA
    assert result.status is Status.PASS
    assert result.release == "24.04"
    assert result.codename == "noble"
    assert result.cloud_init_version == "26.1-test"
    assert [check.name for check in result.checks] == ["schema", "cloud-init"]
    assert result.error == ""
    assert not client.alive


def test_cloud_init_status_exit_code_is_not_trusted(client: FakeLxdClient, config: Config) -> None:
    # 24.04 exits 2 for a deprecation that 22.04 reports with exit 0.
    client.exec_results[("cloud-init", "status", "--wait")] = ExecResult(2, "", "")
    result = run_release(
        client, resolve("24.04"), config, OPTIONS, check_fn=passing_checks, progress=quiet
    )
    assert result.status is Status.PASS


def test_failing_check_makes_the_release_fail(client: FakeLxdClient, config: Config) -> None:
    def failing(client: Lxd, container: str, config: Config) -> list[CheckResult]:
        return [CheckResult("package: nginx", Status.FAIL, "not installed")]

    result = run_release(
        client, resolve("22.04"), config, OPTIONS, check_fn=failing, progress=quiet
    )
    assert result.status is Status.FAIL
    assert not client.alive


def test_launch_failure_is_an_error_and_still_cleans_up(
    client: FakeLxdClient, config: Config
) -> None:
    client.launch_errors["ubuntu:26.04"] = LxdError("could not launch ubuntu:26.04: no image")
    result = run_release(client, resolve("26.04"), config, OPTIONS, progress=quiet)
    assert result.status is Status.ERROR
    assert result.error == "could not launch ubuntu:26.04: no image"
    # A failed launch may still leave a stopped container behind, so delete anyway.
    assert client.names("delete") == client.names("launch")


def test_container_never_ready_is_a_timeout_error(client: FakeLxdClient, config: Config) -> None:
    client.ready_errors["ubuntu:24.04"] = TimeoutError_("container was not ready after 600s")
    result = run_release(client, resolve("24.04"), config, OPTIONS, progress=quiet)
    assert result.status is Status.ERROR
    assert result.error == "timed out: container was not ready after 600s"
    assert not client.alive


def test_cloud_init_timeout_is_an_error_and_cleans_up(
    client: FakeLxdClient, config: Config
) -> None:
    client.exec_results[("cloud-init", "status", "--wait")] = TimeoutError_("did not finish")
    result = run_release(client, resolve("24.04"), config, OPTIONS, progress=quiet)
    assert result.status is Status.ERROR
    assert result.error.startswith("timed out")
    assert not client.alive


def test_unexpected_exception_is_an_error_and_cleans_up(
    client: FakeLxdClient, config: Config
) -> None:
    def buggy(client: Lxd, container: str, config: Config) -> list[CheckResult]:
        raise ValueError("boom")

    result = run_release(client, resolve("24.04"), config, OPTIONS, check_fn=buggy, progress=quiet)
    assert result.status is Status.ERROR
    assert result.error == "unexpected error: ValueError: boom"
    assert not client.alive


def test_used_up_timeout_stops_before_waiting(client: FakeLxdClient, config: Config) -> None:
    result = run_release(client, resolve("24.04"), config, RunOptions(timeout=0), progress=quiet)
    assert result.status is Status.ERROR
    assert "timeout was used up" in result.error
    assert ("wait_ready", client.names("launch")[0]) not in client.calls
    assert not client.alive


def test_keep_skips_deletion(client: FakeLxdClient, config: Config) -> None:
    messages: list[str] = []
    options = RunOptions(timeout=600, keep=True)
    run_release(client, resolve("24.04"), config, options, progress=messages.append)
    assert client.names("delete") == []
    assert len(client.alive) == 1
    assert any("keeping container" in message for message in messages)


def test_failed_delete_is_reported_not_raised(client: FakeLxdClient, config: Config) -> None:
    def failing_delete(name: str) -> None:
        raise LxdError(f"could not delete container {name}: busy")

    client.delete = failing_delete  # type: ignore[method-assign]
    messages: list[str] = []
    result = run_release(
        client, resolve("24.04"), config, OPTIONS, check_fn=passing_checks, progress=messages.append
    )
    assert result.status is Status.PASS
    assert any("remove it with: lxc delete --force seedcheck-noble-" in m for m in messages)


def test_logs_are_collected_into_the_artifacts_dir(
    client: FakeLxdClient, config: Config, tmp_path: Path
) -> None:
    options = RunOptions(timeout=600, artifacts_dir=tmp_path / "logs")
    run_release(client, resolve("22.04"), config, options, progress=quiet)
    assert (tmp_path / "logs/22.04/cloud-init.log").read_text() == "cloud-init log\n"
    assert (tmp_path / "logs/22.04/cloud-init-output.log").read_text() == "cloud-init output\n"


def test_logs_are_collected_even_after_a_timeout(
    client: FakeLxdClient, config: Config, tmp_path: Path
) -> None:
    client.exec_results[("cloud-init", "status", "--wait")] = TimeoutError_("did not finish")
    del client.files["/var/log/cloud-init-output.log"]
    messages: list[str] = []
    options = RunOptions(timeout=600, artifacts_dir=tmp_path / "logs")
    result = run_release(client, resolve("24.04"), config, options, progress=messages.append)
    assert result.status is Status.ERROR
    assert (tmp_path / "logs/24.04/cloud-init.log").exists()
    assert any("could not copy /var/log/cloud-init-output.log" in m for m in messages)
    assert not client.alive


def test_no_logs_are_collected_when_launch_failed(
    client: FakeLxdClient, config: Config, tmp_path: Path
) -> None:
    client.launch_errors["ubuntu:24.04"] = LxdError("no image")
    options = RunOptions(timeout=600, artifacts_dir=tmp_path / "logs")
    run_release(client, resolve("24.04"), config, options, progress=quiet)
    assert client.names("pull_file") == []


def test_progress_lines_are_prefixed_with_the_release(
    client: FakeLxdClient, config: Config
) -> None:
    messages: list[str] = []
    run_release(client, resolve("24.04"), config, OPTIONS, progress=messages.append)
    assert "[24.04] waiting for cloud-init..." in messages
    assert all(message.startswith("[24.04] ") for message in messages)


def test_run_all_runs_every_release_in_order(client: FakeLxdClient, config: Config) -> None:
    run = run_all(client, config, OPTIONS, check_fn=passing_checks, progress=quiet)
    assert [r.release for r in run.releases] == ["22.04", "24.04", "26.04"]
    assert [image for _, image, _ in (c for c in client.calls if c[0] == "launch")] == [
        "ubuntu:22.04",
        "ubuntu:24.04",
        "ubuntu:26.04",
    ]
    assert run.tool_version == "0.1.0"
    assert run.user_data == str(config.user_data)
    assert not client.alive


def test_an_erroring_release_does_not_stop_the_next(client: FakeLxdClient, config: Config) -> None:
    client.launch_errors["ubuntu:22.04"] = LxdError("no image")
    run = run_all(client, config, OPTIONS, check_fn=passing_checks, progress=quiet)
    assert [r.status for r in run.releases] == [Status.ERROR, Status.PASS, Status.PASS]


def test_ctrl_c_deletes_every_container_and_reraises(client: FakeLxdClient, config: Config) -> None:
    client.ready_errors["ubuntu:24.04"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run_all(client, config, OPTIONS, check_fn=passing_checks, progress=quiet)
    launched = client.names("launch")
    assert len(launched) == 2  # 26.04 never started
    assert set(client.names("delete")) == set(launched)
    assert not client.alive


def test_ctrl_c_during_launch_still_deletes_that_container(
    client: FakeLxdClient, config: Config
) -> None:
    client.launch_errors["ubuntu:22.04"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run_all(client, config, OPTIONS, progress=quiet)
    assert client.names("delete") == client.names("launch")


def test_ctrl_c_with_keep_leaves_containers(client: FakeLxdClient, config: Config) -> None:
    client.ready_errors["ubuntu:24.04"] = KeyboardInterrupt()
    messages: list[str] = []
    with pytest.raises(KeyboardInterrupt):
        run_all(client, config, RunOptions(timeout=600, keep=True), progress=messages.append)
    assert client.names("delete") == []
    assert any(m.startswith("interrupted; keeping containers") for m in messages)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], Status.PASS),
        ([Status.PASS, Status.SKIP], Status.PASS),
        ([Status.PASS, Status.WARN], Status.WARN),
        ([Status.WARN, Status.FAIL], Status.FAIL),
        ([Status.FAIL, Status.ERROR], Status.ERROR),
    ],
)
def test_overall_status_is_the_worst_check(statuses: list[Status], expected: Status) -> None:
    checks = [CheckResult(f"c{i}", status) for i, status in enumerate(statuses)]
    assert overall_status(checks) is expected


def test_ctrl_c_during_cleanup_is_retried_by_run_all(client: FakeLxdClient, config: Config) -> None:
    # The user presses Ctrl+C while run_release is deleting its container.
    real_delete = client.delete
    interrupted = []

    def delete(name: str) -> None:
        if not interrupted:
            interrupted.append(name)
            raise KeyboardInterrupt
        real_delete(name)

    client.delete = delete  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        run_all(client, config, OPTIONS, progress=quiet)
    assert interrupted == client.names("launch")
    assert not client.alive
