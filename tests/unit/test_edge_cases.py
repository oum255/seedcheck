"""Every edge case from the roadmap's list, tested end to end through `seedcheck run`.

Each test drives main() the way a user would, with a FakeLxdClient standing in
for LXD (or the real LxdClient over a faked `lxc` when the case is about LXD
itself), and asserts both the exit code and what the user sees. The unit tests
of each module cover the same rules in more detail.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from seedcheck import lxd, userdata
from seedcheck.cli import EXIT_FAILED, EXIT_INFRA, EXIT_INTERRUPTED, EXIT_OK, EXIT_USAGE, main
from seedcheck.errors import TimeoutError_
from seedcheck.lxd import ExecResult

from .fakes import FakeLxdClient

STATUS_CMD = ("cloud-init", "status", "--long", "--format", "json")
DPKG = ("dpkg-query", "-W", "-f=${Status}\\t${Version}\\n")
USER_DATA = "#cloud-config\npackages: [nginx]\n"


def status(**fields: Any) -> ExecResult:
    data = {"status": "done", "errors": [], "recoverable_errors": {}, **fields}
    return ExecResult(0, json.dumps(data), "")


@pytest.fixture(autouse=True)
def no_host_cloud_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(userdata.shutil, "which", lambda name: None)


@pytest.fixture
def client() -> FakeLxdClient:
    fake = FakeLxdClient()
    fake.exec_results[STATUS_CMD] = status()
    return fake


def write(tmp_path: Path, config: str, user_data: str = USER_DATA) -> str:
    (tmp_path / "user-data.yaml").write_text(user_data)
    path = tmp_path / "seedcheck.yaml"
    path.write_text(config)
    return str(path)


def cells(stdout: str, row: str) -> list[str]:
    """The cells of one row of the printed matrix, e.g. ["PASS (1.0)", "FAIL", "PASS"]."""
    line = next(line for line in stdout.splitlines() if line.startswith(row + "  "))
    return [cell.strip() for cell in line[len(row) :].split("   ") if cell.strip()]


# --- Invalid input: exit 2, before any container starts ------------------------------


def test_missing_config_file_exits_2_naming_the_path(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.yaml"
    assert main(["run", str(missing)], client=client) == EXIT_USAGE
    assert f"error: {missing}: file not found" in capsys.readouterr().err
    assert client.calls == []


def test_invalid_yaml_config_exits_2_with_the_line_number(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, "user_data: user-data.yaml\nchecks:\n  packages: [nginx\n")
    assert main(["run", path], client=client) == EXIT_USAGE
    assert f"{path}:4: invalid YAML" in capsys.readouterr().err


def test_unknown_key_typo_exits_2_naming_the_key(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, "user_data: user-data.yaml\nchecks:\n  package: [nginx]\n")
    assert main(["run", path], client=client) == EXIT_USAGE
    assert "checks.package: unknown key" in capsys.readouterr().err


def test_missing_user_data_file_exits_2_showing_the_resolved_path(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, "user_data: ../elsewhere/user-data.yaml\n")
    assert main(["run", path], client=client) == EXIT_USAGE
    resolved = (tmp_path / "../elsewhere/user-data.yaml").resolve()
    assert f"user_data: file not found: {resolved}" in capsys.readouterr().err


def test_missing_cloud_config_header_is_rejected_with_an_explanation(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, "user_data: user-data.yaml\n", "packages: [nginx]\n")
    assert main(["run", path], client=client) == EXIT_USAGE
    assert "cloud-init would silently ignore this file" in capsys.readouterr().err
    assert client.calls == []


def test_schema_error_found_by_host_cloud_init_exits_2(
    tmp_path: Path,
    client: FakeLxdClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(userdata.shutil, "which", lambda name: "/usr/bin/cloud-init")
    output = "Error: Cloud config schema errors: packages: 42 is not of type 'array'\n"
    monkeypatch.setattr(
        userdata.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, output, ""),
    )
    path = write(tmp_path, "user_data: user-data.yaml\n", "#cloud-config\npackages: 42\n")
    assert main(["run", path], client=client) == EXIT_USAGE
    assert "cloud-init schema: packages: 42 is not of type 'array'" in capsys.readouterr().err


def test_schema_error_found_inside_a_release_fails_its_schema_row(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    error = "  Error: Cloud config schema errors: newkey: not supported on this release\n"
    client.image_exec_results[("ubuntu:22.04", ("cloud-init", "schema", "--system"))] = ExecResult(
        1, error, ""
    )
    path = write(tmp_path, "user_data: user-data.yaml\n")
    assert main(["run", path], client=client) == EXIT_FAILED
    out = capsys.readouterr().out
    assert cells(out, "schema") == ["FAIL", "PASS", "PASS"]
    assert "[22.04] schema: newkey: not supported on this release" in out


@pytest.mark.parametrize(
    ("releases", "message"),
    [
        ("['20.04']", "unknown or unsupported release '20.04'; supported: 22.04 (jammy)"),
        ("['24.04', noble]", "release 24.04 (noble) is listed twice"),
    ],
)
def test_unsupported_or_duplicate_release_exits_2_listing_supported_ones(
    tmp_path: Path,
    client: FakeLxdClient,
    capsys: pytest.CaptureFixture[str],
    releases: str,
    message: str,
) -> None:
    path = write(tmp_path, f"user_data: user-data.yaml\nreleases: {releases}\n")
    assert main(["run", path], client=client) == EXIT_USAGE
    assert message in capsys.readouterr().err


# --- LXD problems: exit 3 with advice, using the real LxdClient over a fake `lxc` ----


@pytest.mark.parametrize(
    ("which", "outcomes", "advice"),
    [
        (None, [], "the 'lxc' command was not found; run scripts/setup-lxd.sh"),
        (
            "/snap/bin/lxc",
            [(0, "config: {}\n", ""), (0, "", "")],
            "LXD has no storage pool, so it is not initialized; run scripts/setup-lxd.sh",
        ),
        (
            "/snap/bin/lxc",
            [
                (
                    1,
                    "",
                    'Error: LXD unix socket "/x/unix.socket" not accessible: permission denied\n',
                )
            ],
            "add yourself to the lxd group (sudo usermod -aG lxd $USER)",
        ),
    ],
    ids=["not installed", "not initialized", "permission denied"],
)
def test_unusable_lxd_exits_3_with_advice_for_each_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    which: str | None,
    outcomes: list[tuple[int, str, str]],
    advice: str,
) -> None:
    # shutil is one shared module: answer only for lxc, so the host still has no cloud-init.
    monkeypatch.setattr(lxd.shutil, "which", lambda name: which if name == "lxc" else None)
    replies = iter(outcomes)
    monkeypatch.setattr(
        lxd.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, *next(replies)),
    )
    path = write(tmp_path, "user_data: user-data.yaml\n")
    assert main(["run", path]) == EXIT_INFRA
    assert advice in capsys.readouterr().err


# --- One release in trouble: ERROR for it, the others still run, exit 3 ------------


def test_image_that_cannot_be_downloaded_errors_only_that_release(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    from seedcheck.errors import LxdError

    client.launch_errors["ubuntu:26.04"] = LxdError(
        "could not launch ubuntu:26.04: image not found"
    )
    path = write(tmp_path, "user_data: user-data.yaml\n")
    assert main(["run", path], client=client) == EXIT_INFRA
    out = capsys.readouterr().out
    assert cells(out, "cloud-init") == ["PASS", "PASS", "SKIP"]
    assert "[26.04] could not launch ubuntu:26.04: image not found" in out


def test_timeout_errors_the_release_skips_its_checks_keeps_logs_and_deletes_it(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.exec_results[("cloud-init", "status", "--wait")] = TimeoutError_("did not finish in 5s")
    path = write(tmp_path, "user_data: user-data.yaml\nchecks:\n  packages: [nginx]\n")
    logs = tmp_path / "logs"
    argv = ["run", path, "--release", "24.04", "--artifacts-dir", str(logs)]
    assert main(argv, client=client) == EXIT_INFRA
    out = capsys.readouterr().out
    assert cells(out, "package: nginx") == ["SKIP"]
    assert "[24.04] timed out: did not finish in 5s" in out
    assert (logs / "24.04" / "cloud-init.log").exists()
    assert not client.alive


def test_unexpected_exception_errors_one_release_and_the_others_continue(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.image_exec_results[("ubuntu:22.04", ("cloud-init", "--version"))] = ValueError("bug")
    path = write(tmp_path, "user_data: user-data.yaml\n")
    assert main(["run", path], client=client) == EXIT_INFRA
    out = capsys.readouterr().out
    assert cells(out, "schema") == ["SKIP", "PASS", "PASS"]
    assert "[22.04] unexpected error: ValueError: bug" in out
    assert not client.alive


# --- What cloud-init reports ----------------------------------------------------------


def test_cloud_init_errors_fail_its_row_but_other_checks_still_run(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.exec_results[STATUS_CMD] = status(status="error", errors=["runcmd failed"])
    client.exec_results[(*DPKG, "nginx")] = ExecResult(0, "install ok installed\t1.24.0\n", "")
    path = write(tmp_path, "user_data: user-data.yaml\nchecks:\n  packages: [nginx]\n")
    assert main(["run", path, "--release", "24.04"], client=client) == EXIT_FAILED
    out = capsys.readouterr().out
    assert cells(out, "cloud-init") == ["FAIL"]
    assert cells(out, "package: nginx") == ["PASS (1.24.0)"]
    assert "[24.04] cloud-init: runcmd failed" in out


def test_recoverable_errors_only_warn_and_the_run_still_exits_0(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.exec_results[STATUS_CMD] = status(recoverable_errors={"DEPRECATED": ["apt_update"]})
    path = write(tmp_path, "user_data: user-data.yaml\n")
    assert main(["run", path, "--release", "24.04"], client=client) == EXIT_OK
    out = capsys.readouterr().out
    assert cells(out, "cloud-init") == ["WARN"]
    assert "Warnings:\n  [24.04] cloud-init: apt_update" in out


def test_older_cloud_init_status_json_with_missing_fields_does_not_crash(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.exec_results[STATUS_CMD] = ExecResult(0, json.dumps({"status": "done"}), "")
    path = write(tmp_path, "user_data: user-data.yaml\n")
    assert main(["run", path, "--release", "22.04"], client=client) == EXIT_OK
    assert cells(capsys.readouterr().out, "cloud-init") == ["PASS"]


def test_no_network_in_the_container_shows_cloud_init_apt_failure(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    apt_error = (
        "('package_update_upgrade_install', PackageInstallerError(\"Failed to install the "
        "following packages: {'nginx'}. See associated package manager logs for more details.\"))"
    )
    client.exec_results[STATUS_CMD] = status(status="error", errors=[apt_error])
    path = write(tmp_path, "user_data: user-data.yaml\n")
    assert main(["run", path, "--release", "24.04"], client=client) == EXIT_FAILED
    out = capsys.readouterr().out
    assert cells(out, "cloud-init") == ["FAIL"]
    assert "Failed to install the following packages: {'nginx'}" in out


# --- Differences between releases: the reason the tool exists -------------------------


def test_package_missing_on_one_release_fails_only_that_column(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.exec_results[(*DPKG, "python3.12")] = ExecResult(1, "", "no packages found")
    client.image_exec_results[("ubuntu:24.04", (*DPKG, "python3.12"))] = ExecResult(
        0, "install ok installed\t3.12.3-1\n", ""
    )
    path = write(tmp_path, "user_data: user-data.yaml\nchecks:\n  packages: [python3.12]\n")
    assert main(["run", path], client=client) == EXIT_FAILED
    assert cells(capsys.readouterr().out, "package: python3.12") == [
        "FAIL",
        "PASS (3.12.3-1)",
        "FAIL",
    ]


def test_same_package_with_different_versions_passes_and_shows_each_version(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    for image, version in (("22.04", "1.18.0"), ("24.04", "1.24.0"), ("26.04", "1.28.3")):
        client.image_exec_results[(f"ubuntu:{image}", (*DPKG, "nginx"))] = ExecResult(
            0, f"install ok installed\t{version}\n", ""
        )
    path = write(tmp_path, "user_data: user-data.yaml\nchecks:\n  packages: [nginx]\n")
    assert main(["run", path], client=client) == EXIT_OK
    assert cells(capsys.readouterr().out, "package: nginx") == [
        "PASS (1.18.0)",
        "PASS (1.24.0)",
        "PASS (1.28.3)",
    ]


# --- Individual checks ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stat", "detail"),
    [
        (
            ExecResult(1, "", "stat: cannot statx '/srv/app': No such file or directory"),
            "not found",
        ),
        (ExecResult(0, "directory|755|root\n", ""), "not a regular file (directory)"),
    ],
    ids=["missing", "directory"],
)
def test_file_check_on_a_missing_path_or_a_directory_fails(
    tmp_path: Path,
    client: FakeLxdClient,
    capsys: pytest.CaptureFixture[str],
    stat: ExecResult,
    detail: str,
) -> None:
    client.exec_results[("stat", "-L", "-c", "%F|%a|%U", "/srv/app")] = stat
    path = write(tmp_path, "user_data: user-data.yaml\nchecks:\n  files: [{path: /srv/app}]\n")
    assert main(["run", path, "--release", "24.04"], client=client) == EXIT_FAILED
    assert f"[24.04] file: /srv/app: {detail}" in capsys.readouterr().out


def test_command_with_huge_output_has_its_detail_truncated(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.exec_results[("sh", "-c", "noisy")] = ExecResult(1, "", "x" * 50_000)
    config = "user_data: user-data.yaml\nchecks:\n  commands: [{name: noisy, run: noisy}]\n"
    path = write(tmp_path, config)
    assert main(["run", path, "--release", "24.04"], client=client) == EXIT_FAILED
    failure = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("  [24.04] command")
    )
    assert failure.endswith("...")
    assert len(failure) < 250


def test_empty_checks_section_shows_only_schema_and_cloud_init(
    tmp_path: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, "user_data: user-data.yaml\nchecks:\n")
    assert main(["run", path], client=client) == EXIT_OK
    rows = capsys.readouterr().out.splitlines()[3:5]
    assert [row.split()[0] for row in rows] == ["schema", "cloud-init"]


# --- Running Seedcheck ------------------------------------------------------------------


def test_ctrl_c_deletes_every_container_and_exits_130(
    tmp_path: Path, client: FakeLxdClient
) -> None:
    client.ready_errors["ubuntu:26.04"] = KeyboardInterrupt()
    path = write(tmp_path, "user_data: user-data.yaml\n")
    assert main(["run", path], client=client) == EXIT_INTERRUPTED
    assert len(client.names("launch")) == 3
    assert not client.alive


def test_two_runs_at_the_same_time_never_reuse_a_container_name(
    tmp_path: Path, client: FakeLxdClient
) -> None:
    path = write(tmp_path, "user_data: user-data.yaml\n")
    for _ in range(20):
        main(["run", path], client=client)
    names = client.names("launch")
    assert len(names) == len(set(names)) == 60


def test_redirected_output_or_no_color_has_no_color_codes(
    tmp_path: Path,
    client: FakeLxdClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import sys

    path = write(tmp_path, "user_data: user-data.yaml\n")
    main(["run", path, "--release", "24.04"], client=client)  # captured, so not a terminal
    assert "\033[" not in capsys.readouterr().out
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    main(["run", path, "--release", "24.04"], client=client)
    assert "\033[" not in capsys.readouterr().out
