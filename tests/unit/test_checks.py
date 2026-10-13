"""Checks, fed with outputs captured from real 22.04/24.04/26.04 containers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from seedcheck.checks import (
    DETAIL_LIMIT,
    check_cloud_init,
    check_command,
    check_file,
    check_names,
    check_package,
    check_schema,
    check_service,
    check_user,
    run_checks,
    shorten,
    skipped,
)
from seedcheck.config import (
    CommandCheck,
    Config,
    FileCheck,
    PackageCheck,
    ServiceCheck,
    UserCheck,
    load_config,
)
from seedcheck.errors import LxdError, TimeoutError_
from seedcheck.lxd import ExecResult
from seedcheck.models import CheckResult, Status

from .fakes import FakeLxdClient

C = "seedcheck-noble-abc123"
REPO_ROOT = Path(__file__).resolve().parents[2]

STATUS_CMD = ("cloud-init", "status", "--long", "--format", "json")
SCHEMA_CMD = ("cloud-init", "schema", "--system")
DPKG = ("dpkg-query", "-W", "-f=${Status}\\t${Version}\\n")
STAT = ("stat", "-L", "-c", "%F|%a|%U")


@pytest.fixture
def client() -> FakeLxdClient:
    return FakeLxdClient()


def answer(
    client: FakeLxdClient, argv: tuple[str, ...], code: int, out: str = "", err: str = ""
) -> None:
    client.exec_results[argv] = ExecResult(code, out, err)


def status_json(**fields: Any) -> str:
    base: dict[str, Any] = {"status": "done", "errors": [], "recoverable_errors": {}}
    base.update(fields)
    return json.dumps({k: v for k, v in base.items() if v is not ...})


# --- schema -----------------------------------------------------------------


def test_schema_passes_when_cloud_init_accepts_the_user_data(client: FakeLxdClient) -> None:
    answer(client, SCHEMA_CMD, 0, "Valid schema user-data\n")
    assert check_schema(client, C) == CheckResult("schema", Status.PASS)


def test_schema_fails_with_the_indented_error_from_schema_system(client: FakeLxdClient) -> None:
    out = (
        "Found cloud-config data types: user-data, network-config\n\n"
        "1. user-data at /var/lib/cloud/instances/x/cloud-config.txt:\n"
        "  Error: Cloud config schema errors: runcmd.0.0: False is not of type 'string'\n\n"
        "  Error: Invalid schema: user-data\n"
    )
    answer(client, SCHEMA_CMD, 1, out)
    assert check_schema(client, C) == CheckResult(
        "schema", Status.FAIL, "runcmd.0.0: False is not of type 'string'"
    )


def test_schema_failure_without_parsable_output(client: FakeLxdClient) -> None:
    answer(client, SCHEMA_CMD, 1, "garbage\n")
    assert check_schema(client, C).detail == "failed with exit code 1"


# --- cloud-init --------------------------------------------------------------


def test_cloud_init_done_without_errors_passes(client: FakeLxdClient) -> None:
    answer(client, STATUS_CMD, 0, status_json())
    assert check_cloud_init(client, C) == CheckResult("cloud-init", Status.PASS)


def test_cloud_init_exit_code_is_ignored(client: FakeLxdClient) -> None:
    answer(client, STATUS_CMD, 2, status_json())
    assert check_cloud_init(client, C).status is Status.PASS


def test_cloud_init_error_fails_with_the_first_error(client: FakeLxdClient) -> None:
    error = (
        "('scripts_user', RuntimeError('Runparts: 1 failures (runcmd) in 1 attempted commands'))"
    )
    answer(client, STATUS_CMD, 1, status_json(status="error", errors=[error, "second"]))
    assert check_cloud_init(client, C) == CheckResult("cloud-init", Status.FAIL, error)


def test_cloud_init_errors_fail_even_if_status_says_done(client: FakeLxdClient) -> None:
    answer(client, STATUS_CMD, 1, status_json(errors=["boom"]))
    assert check_cloud_init(client, C) == CheckResult("cloud-init", Status.FAIL, "boom")


def test_cloud_init_error_status_without_messages(client: FakeLxdClient) -> None:
    answer(client, STATUS_CMD, 1, status_json(status="error"))
    assert check_cloud_init(client, C).detail == "status is 'error'"


def test_cloud_init_recoverable_errors_only_warn(client: FakeLxdClient) -> None:
    deprecated = "Deprecated cloud-config provided: apt_update: Use **package_update** instead."
    # 26.04 repeats the same message; it must be counted once.
    recoverable = {"DEPRECATED": [deprecated, deprecated], "WARNING": ["other"]}
    answer(client, STATUS_CMD, 2, status_json(recoverable_errors=recoverable))
    assert check_cloud_init(client, C) == CheckResult(
        "cloud-init", Status.WARN, f"{deprecated} (+1 more)"
    )


def test_cloud_init_single_recoverable_error(client: FakeLxdClient) -> None:
    answer(client, STATUS_CMD, 2, status_json(recoverable_errors={"WARNING": ["careful"]}))
    assert check_cloud_init(client, C) == CheckResult("cloud-init", Status.WARN, "careful")


def test_cloud_init_missing_error_fields_are_treated_as_empty(client: FakeLxdClient) -> None:
    answer(client, STATUS_CMD, 0, json.dumps({"status": "done"}))
    assert check_cloud_init(client, C).status is Status.PASS


def test_cloud_init_22_04_shape_with_schemas_copy_passes(client: FakeLxdClient) -> None:
    jammy = status_json(_schema_version="1", schemas={"1": {"status": "done"}}, stage=None)
    answer(client, STATUS_CMD, 0, jammy)
    assert check_cloud_init(client, C).status is Status.PASS


@pytest.mark.parametrize("status", ["running", "disabled", "not started", None])
def test_cloud_init_not_done_fails(client: FakeLxdClient, status: str | None) -> None:
    answer(client, STATUS_CMD, 0, status_json(status=status))
    assert check_cloud_init(client, C) == CheckResult(
        "cloud-init", Status.FAIL, f"status is {status!r}, expected 'done'"
    )


def test_cloud_init_invalid_json_fails_without_crashing(client: FakeLxdClient) -> None:
    answer(client, STATUS_CMD, 1, "", "usage: cloud-init status [-h] [-w] [-l]\n")
    result = check_cloud_init(client, C)
    assert result.status is Status.FAIL
    assert result.detail.startswith("unreadable status output: usage: cloud-init status")


def test_cloud_init_json_that_is_not_an_object_fails(client: FakeLxdClient) -> None:
    answer(client, STATUS_CMD, 0, "[1, 2]")
    assert check_cloud_init(client, C).detail == "unexpected status output: [1, 2]"


def test_cloud_init_errors_given_as_a_plain_string(client: FakeLxdClient) -> None:
    answer(client, STATUS_CMD, 1, status_json(errors="single error"))
    assert check_cloud_init(client, C).detail == "single error"


# --- package -----------------------------------------------------------------


def test_installed_package_passes_and_reports_its_version(client: FakeLxdClient) -> None:
    answer(client, (*DPKG, "curl"), 0, "install ok installed\t8.5.0-2ubuntu10.15\n")
    assert check_package(client, C, PackageCheck("curl")) == CheckResult(
        "package: curl", Status.PASS, value="8.5.0-2ubuntu10.15"
    )


def test_missing_package_fails(client: FakeLxdClient) -> None:
    answer(client, (*DPKG, "nope"), 1, "", "dpkg-query: no packages found matching nope\n")
    assert check_package(client, C, PackageCheck("nope")) == CheckResult(
        "package: nope", Status.FAIL, "not installed"
    )


def test_removed_package_with_leftover_config_fails(client: FakeLxdClient) -> None:
    answer(client, (*DPKG, "nginx"), 0, "deinstall ok config-files\t1.24.0-2ubuntu7\n")
    assert check_package(client, C, PackageCheck("nginx")).detail == (
        "not installed (dpkg status: deinstall ok config-files)"
    )


def test_multiarch_package_passes_if_any_architecture_is_installed(client: FakeLxdClient) -> None:
    out = "deinstall ok config-files\t2.39\ninstall ok installed\t2.39-0ubuntu8\n"
    answer(client, (*DPKG, "libc6"), 0, out)
    assert check_package(client, C, PackageCheck("libc6")).value == "2.39-0ubuntu8"


@pytest.mark.parametrize("out", ["install ok installed 1.0\n", ""])
def test_malformed_dpkg_output_fails(client: FakeLxdClient, out: str) -> None:
    answer(client, (*DPKG, "curl"), 0, out)
    result = check_package(client, C, PackageCheck("curl"))
    assert result.status is Status.FAIL


# --- service -----------------------------------------------------------------


@pytest.mark.parametrize("unit", ["nginx", "nginx.service"])
def test_active_service_passes_with_or_without_suffix(client: FakeLxdClient, unit: str) -> None:
    answer(client, ("systemctl", "is-active", unit), 0, "active\n")
    assert check_service(client, C, ServiceCheck(unit)).status is Status.PASS


def test_service_in_another_state_fails(client: FakeLxdClient) -> None:
    # On 24.04, ssh is socket-activated and reports inactive until first use.
    answer(client, ("systemctl", "is-active", "ssh"), 3, "inactive\n")
    assert check_service(client, C, ServiceCheck("ssh")) == CheckResult(
        "service: ssh", Status.FAIL, "expected active, found inactive"
    )


def test_service_expected_inactive_passes(client: FakeLxdClient) -> None:
    answer(client, ("systemctl", "is-active", "ssh"), 3, "inactive\n")
    assert check_service(client, C, ServiceCheck("ssh", "inactive")).status is Status.PASS


def test_service_with_no_output_fails(client: FakeLxdClient) -> None:
    answer(client, ("systemctl", "is-active", "x"), 1, "", "Failed to connect to bus\n")
    assert check_service(client, C, ServiceCheck("x")).detail == (
        "no state reported: Failed to connect to bus"
    )


# --- file --------------------------------------------------------------------

MOTD = FileCheck("/etc/motd", contains="Provisioned by cloud-init", mode="0644", owner="root")


def test_file_matching_everything_passes(client: FakeLxdClient) -> None:
    answer(client, (*STAT, "/etc/motd"), 0, "regular file|644|root\n")
    answer(client, ("grep", "-q", "-F", "--", "Provisioned by cloud-init", "/etc/motd"), 0)
    assert check_file(client, C, MOTD) == CheckResult("file: /etc/motd", Status.PASS)


def test_empty_regular_file_counts_as_a_file(client: FakeLxdClient) -> None:
    answer(client, (*STAT, "/etc/empty"), 0, "regular empty file|600|root\n")
    assert check_file(client, C, FileCheck("/etc/empty", mode="600")).status is Status.PASS


def test_file_with_wrong_mode_owner_and_content_lists_every_problem(client: FakeLxdClient) -> None:
    answer(client, (*STAT, "/etc/motd"), 0, "regular file|600|ubuntu\n")
    answer(client, ("grep", "-q", "-F", "--", "Provisioned by cloud-init", "/etc/motd"), 1)
    assert check_file(client, C, MOTD).detail == (
        "expected mode 0644, found 0600; expected owner root, found ubuntu; "
        "does not contain 'Provisioned by cloud-init'"
    )


def test_mode_is_compared_as_an_octal_number(client: FakeLxdClient) -> None:
    answer(client, (*STAT, "/usr/bin/sudo"), 0, "regular file|4755|root\n")
    assert check_file(client, C, FileCheck("/usr/bin/sudo", mode="4755")).status is Status.PASS


def test_missing_file_fails_as_not_found(client: FakeLxdClient) -> None:
    answer(
        client,
        (*STAT, "/etc/motd"),
        1,
        "",
        "stat: cannot statx '/etc/motd': No such file or directory\n",
    )
    assert check_file(client, C, MOTD) == CheckResult("file: /etc/motd", Status.FAIL, "not found")


def test_directory_is_not_a_regular_file(client: FakeLxdClient) -> None:
    answer(client, (*STAT, "/etc"), 0, "directory|755|root\n")
    assert check_file(client, C, FileCheck("/etc")).detail == "not a regular file (directory)"


def test_stat_failing_for_another_reason(client: FakeLxdClient) -> None:
    answer(client, (*STAT, "/root/x"), 1, "", "stat: cannot statx '/root/x': Permission denied\n")
    assert check_file(client, C, FileCheck("/root/x")).detail.startswith("cannot stat:")


def test_malformed_stat_output_fails(client: FakeLxdClient) -> None:
    answer(client, (*STAT, "/x"), 0, "weird\n")
    assert check_file(client, C, FileCheck("/x")).detail == "unexpected stat output: weird"


def test_contains_text_starting_with_a_dash_is_not_an_option(client: FakeLxdClient) -> None:
    answer(client, (*STAT, "/x"), 0, "regular file|644|root\n")
    answer(client, ("grep", "-q", "-F", "--", "-v", "/x"), 0)
    assert check_file(client, C, FileCheck("/x", contains="-v")).status is Status.PASS


def test_multi_line_contains_is_compared_on_full_content(client: FakeLxdClient) -> None:
    answer(client, (*STAT, "/x"), 0, "regular file|644|root\n")
    answer(client, ("cat", "--", "/x"), 0, "header\nline one\nline two\nfooter\n")
    assert (
        check_file(client, C, FileCheck("/x", contains="line one\nline two\n")).status
        is Status.PASS
    )
    assert (
        check_file(client, C, FileCheck("/x", contains="line two\nline one")).status is Status.FAIL
    )


# --- user ----------------------------------------------------------------------


def test_user_in_every_group_passes(client: FakeLxdClient) -> None:
    answer(client, ("id", "-nG", "deploy"), 0, "deploy sudo users\n")
    assert check_user(client, C, UserCheck("deploy", ("sudo", "users"))).status is Status.PASS


def test_user_missing_a_group_fails(client: FakeLxdClient) -> None:
    answer(client, ("id", "-nG", "deploy"), 0, "deploy\n")
    assert check_user(client, C, UserCheck("deploy", ("sudo", "docker"))).detail == (
        "not in group(s): sudo, docker"
    )


def test_missing_user_fails(client: FakeLxdClient) -> None:
    answer(client, ("id", "-nG", "deploy"), 1, "", "id: 'deploy': no such user\n")
    assert check_user(client, C, UserCheck("deploy")).detail == "user does not exist"


# --- command -------------------------------------------------------------------

CURL = CommandCheck("nginx answers", "curl -fsS http://localhost", 0, "nginx")


def test_command_with_expected_exit_and_output_passes(client: FakeLxdClient) -> None:
    answer(client, ("sh", "-c", CURL.run), 0, "<h1>Welcome to nginx!</h1>\n")
    assert check_command(client, C, CURL) == CheckResult("command: nginx answers", Status.PASS)


def test_command_failure_reports_exit_code_and_output(client: FakeLxdClient) -> None:
    answer(
        client, ("sh", "-c", CURL.run), 7, "", "curl: (7) Failed to connect to localhost port 80\n"
    )
    assert check_command(client, C, CURL).detail == (
        "exit code 7, expected 0; stdout does not contain 'nginx'; "
        "output: curl: (7) Failed to connect to localhost port 80"
    )


def test_command_expected_to_fail_passes_when_it_fails(client: FakeLxdClient) -> None:
    spec = CommandCheck("no telnet", "command -v telnet", expect_exit=1)
    answer(client, ("sh", "-c", spec.run), 1)
    assert check_command(client, C, spec).status is Status.PASS


def test_command_with_huge_output_has_its_detail_truncated(client: FakeLxdClient) -> None:
    spec = CommandCheck("noisy", "yes | head -n 100000")
    answer(client, ("sh", "-c", spec.run), 1, "y\n" * 100_000)
    detail = check_command(client, C, spec).detail
    assert len(detail) == DETAIL_LIMIT
    assert detail.endswith("...")


def test_command_timeout_is_a_failure_not_an_infrastructure_error(client: FakeLxdClient) -> None:
    spec = CommandCheck("hangs", "sleep 1000")
    client.exec_results[("sh", "-c", "sleep 1000")] = TimeoutError_("did not finish")
    assert check_command(client, C, spec) == CheckResult(
        "command: hangs", Status.FAIL, "did not finish within 300s"
    )


# --- run_checks / skipped --------------------------------------------------------


@pytest.fixture
def nginx() -> Config:
    return load_config(REPO_ROOT / "examples/nginx/seedcheck.yaml")


def test_run_checks_runs_schema_and_cloud_init_first_then_config_order(
    client: FakeLxdClient, nginx: Config
) -> None:
    names = [result.name for result in run_checks(client, C, nginx)]
    assert (
        names
        == check_names(nginx)
        == [
            "schema",
            "cloud-init",
            "package: nginx",
            "package: curl",
            "service: nginx",
            "file: /etc/motd",
            "user: deploy",
            "command: nginx answers on port 80",
        ]
    )


def test_run_checks_with_empty_checks_runs_only_schema_and_cloud_init(
    client: FakeLxdClient, tmp_path: Path
) -> None:
    (tmp_path / "u.yaml").write_text("#cloud-config\nruncmd: []\n")
    (tmp_path / "s.yaml").write_text("user_data: u.yaml\nchecks: {}\n")
    config = load_config(tmp_path / "s.yaml")
    answer(client, STATUS_CMD, 0, status_json())
    results = run_checks(client, C, config)
    assert [(r.name, r.status) for r in results] == [
        ("schema", Status.PASS),
        ("cloud-init", Status.PASS),
    ]


def test_an_infrastructure_error_in_one_check_does_not_stop_the_rest(
    client: FakeLxdClient, nginx: Config
) -> None:
    client.exec_results[(*DPKG, "nginx")] = LxdError("container stopped responding")
    results = {r.name: r for r in run_checks(client, C, nginx)}
    assert results["package: nginx"] == CheckResult(
        "package: nginx", Status.ERROR, "container stopped responding"
    )
    assert len(results) == 8


def test_skipped_marks_every_planned_check_with_the_reason(nginx: Config) -> None:
    results = skipped(nginx, "not run: timed out")
    assert [r.name for r in results] == check_names(nginx)
    assert all(r.status is Status.SKIP and r.detail == "not run: timed out" for r in results)


def test_shorten_collapses_whitespace_and_truncates() -> None:
    assert shorten("a\n  b\tc") == "a b c"
    assert shorten("x" * 300, limit=10) == "xxxxxxx..."
