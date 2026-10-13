"""The checks that run inside each container once cloud-init has finished.

Each check takes (client, container, spec) and returns one CheckResult.
Values from seedcheck.yaml are always passed to `lxc exec` as separate
arguments, never pasted into a shell string, except for the command
check, whose whole purpose is to run the user's shell command.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from seedcheck.config import (
    CommandCheck,
    Config,
    FileCheck,
    PackageCheck,
    ServiceCheck,
    UserCheck,
)
from seedcheck.errors import SeedcheckError, TimeoutError_
from seedcheck.lxd import Lxd
from seedcheck.models import CheckResult, Status
from seedcheck.userdata import schema_errors

CHECK_TIMEOUT = 60
COMMAND_TIMEOUT = 300
DETAIL_LIMIT = 200

DPKG_INSTALLED = "install ok installed"


def run_checks(client: Lxd, container: str, config: Config) -> list[CheckResult]:
    """Run schema and cloud-init first, then the configured checks in file order.

    One check failing, or raising, never prevents the others from running.
    """
    planned: list[tuple[str, Callable[[], CheckResult]]] = [
        ("schema", lambda: check_schema(client, container)),
        ("cloud-init", lambda: check_cloud_init(client, container)),
    ]
    checks = config.checks
    planned += [
        (package_name(s), _bind(check_package, client, container, s)) for s in checks.packages
    ]
    planned += [
        (service_name(s), _bind(check_service, client, container, s)) for s in checks.services
    ]
    planned += [(file_name(s), _bind(check_file, client, container, s)) for s in checks.files]
    planned += [(user_name(s), _bind(check_user, client, container, s)) for s in checks.users]
    planned += [
        (command_name(s), _bind(check_command, client, container, s)) for s in checks.commands
    ]
    return [_guarded(name, run) for name, run in planned]


def check_names(config: Config) -> list[str]:
    """The row names run_checks() produces for this config, in the same order."""
    checks = config.checks
    return [
        "schema",
        "cloud-init",
        *(package_name(s) for s in checks.packages),
        *(service_name(s) for s in checks.services),
        *(file_name(s) for s in checks.files),
        *(user_name(s) for s in checks.users),
        *(command_name(s) for s in checks.commands),
    ]


def skipped(config: Config, reason: str) -> list[CheckResult]:
    """Every planned check marked SKIP, for a release that never got that far."""
    return [CheckResult(name, Status.SKIP, reason) for name in check_names(config)]


def package_name(spec: PackageCheck) -> str:
    return f"package: {spec.name}"


def service_name(spec: ServiceCheck) -> str:
    return f"service: {spec.name}"


def file_name(spec: FileCheck) -> str:
    return f"file: {spec.path}"


def user_name(spec: UserCheck) -> str:
    return f"user: {spec.name}"


def command_name(spec: CommandCheck) -> str:
    return f"command: {spec.name}"


def check_schema(client: Lxd, container: str) -> CheckResult:
    """Validate the user-data with this release's own cloud-init."""
    out = client.exec(container, ["cloud-init", "schema", "--system"], CHECK_TIMEOUT)
    if out.exit_code == 0:
        return CheckResult("schema", Status.PASS)
    return CheckResult(
        "schema",
        Status.FAIL,
        shorten("; ".join(schema_errors(out.stdout + out.stderr, out.exit_code))),
    )


def check_cloud_init(client: Lxd, container: str) -> CheckResult:
    """Read cloud-init's own error report. Its exit code is ignored: it differs per release."""
    name = "cloud-init"
    out = client.exec(
        container, ["cloud-init", "status", "--long", "--format", "json"], CHECK_TIMEOUT
    )
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return CheckResult(
            name, Status.FAIL, shorten(f"unreadable status output: {out.stdout or out.stderr}")
        )
    if not isinstance(data, dict):
        return CheckResult(name, Status.FAIL, shorten(f"unexpected status output: {out.stdout}"))

    status = data.get("status")
    errors = _messages(data.get("errors"))
    if status == "error" or errors:
        return CheckResult(name, Status.FAIL, shorten(errors[0] if errors else "status is 'error'"))
    if status != "done":
        return CheckResult(name, Status.FAIL, f"status is {status!r}, expected 'done'")
    recoverable = _messages(data.get("recoverable_errors"))
    if recoverable:
        more = f" (+{len(recoverable) - 1} more)" if len(recoverable) > 1 else ""
        return CheckResult(
            name, Status.WARN, shorten(recoverable[0], DETAIL_LIMIT - len(more)) + more
        )
    return CheckResult(name, Status.PASS)


def check_package(client: Lxd, container: str, spec: PackageCheck) -> CheckResult:
    name = package_name(spec)
    # No shell is involved, so ${Status} reaches dpkg-query literally; it expands \t and \n itself.
    out = client.exec(
        container, ["dpkg-query", "-W", "-f=${Status}\\t${Version}\\n", spec.name], CHECK_TIMEOUT
    )
    if out.exit_code != 0:
        return CheckResult(name, Status.FAIL, "not installed")
    states = []
    for line in out.stdout.splitlines():
        status, tab, version = line.partition("\t")
        if not tab:
            return CheckResult(
                name, Status.FAIL, shorten(f"unexpected dpkg-query output: {out.stdout}")
            )
        if status == DPKG_INSTALLED:
            return CheckResult(name, Status.PASS, value=version)
        states.append(status)
    detail = f"not installed (dpkg status: {', '.join(states)})" if states else "not installed"
    return CheckResult(name, Status.FAIL, detail)


def check_service(client: Lxd, container: str, spec: ServiceCheck) -> CheckResult:
    name = service_name(spec)
    # `systemctl is-active` accepts both "nginx" and "nginx.service"; it exits
    # non-zero for any state but active, so only its output matters.
    out = client.exec(container, ["systemctl", "is-active", spec.name], CHECK_TIMEOUT)
    state = out.stdout.strip()
    if not state:
        return CheckResult(name, Status.FAIL, shorten(f"no state reported: {out.stderr}"))
    if state == spec.state:
        return CheckResult(name, Status.PASS)
    return CheckResult(name, Status.FAIL, f"expected {spec.state}, found {state}")


def check_file(client: Lxd, container: str, spec: FileCheck) -> CheckResult:
    name = file_name(spec)
    # -L follows symlinks, so a link to a regular file counts as that file.
    out = client.exec(container, ["stat", "-L", "-c", "%F|%a|%U", spec.path], CHECK_TIMEOUT)
    if out.exit_code != 0:
        if "No such file" in out.stderr:
            return CheckResult(name, Status.FAIL, "not found")
        return CheckResult(name, Status.FAIL, shorten(f"cannot stat: {out.stderr}"))
    parts = out.stdout.strip().split("|")
    if len(parts) != 3:
        return CheckResult(name, Status.FAIL, shorten(f"unexpected stat output: {out.stdout}"))
    kind, mode, owner = parts
    if not kind.startswith("regular"):  # "regular file" or "regular empty file"
        return CheckResult(name, Status.FAIL, f"not a regular file ({kind})")

    problems = []
    if spec.mode is not None and int(mode, 8) != int(spec.mode, 8):
        problems.append(f"expected mode {spec.mode}, found {int(mode, 8):04o}")
    if spec.owner is not None and owner != spec.owner:
        problems.append(f"expected owner {spec.owner}, found {owner}")
    if spec.contains is not None and not _file_contains(
        client, container, spec.path, spec.contains
    ):
        problems.append(f"does not contain {spec.contains!r}")
    if problems:
        return CheckResult(name, Status.FAIL, shorten("; ".join(problems)))
    return CheckResult(name, Status.PASS)


def _file_contains(client: Lxd, container: str, path: str, text: str) -> bool:
    if "\n" not in text.strip("\n"):
        # grep -F: fixed string, not a regex; "--" so text starting with "-" is not an option.
        out = client.exec(
            container, ["grep", "-q", "-F", "--", text.strip("\n"), path], CHECK_TIMEOUT
        )
        return out.exit_code == 0
    # grep matches one line at a time, so multi-line text is compared on the full content.
    out = client.exec(container, ["cat", "--", path], CHECK_TIMEOUT)
    return out.exit_code == 0 and text in out.stdout


def check_user(client: Lxd, container: str, spec: UserCheck) -> CheckResult:
    name = user_name(spec)
    out = client.exec(container, ["id", "-nG", spec.name], CHECK_TIMEOUT)
    if out.exit_code != 0:
        return CheckResult(name, Status.FAIL, "user does not exist")
    groups = set(out.stdout.split())
    missing = [group for group in spec.groups if group not in groups]
    if missing:
        return CheckResult(name, Status.FAIL, f"not in group(s): {', '.join(missing)}")
    return CheckResult(name, Status.PASS)


def check_command(client: Lxd, container: str, spec: CommandCheck) -> CheckResult:
    name = command_name(spec)
    try:
        out = client.exec(container, ["sh", "-c", spec.run], COMMAND_TIMEOUT)
    except TimeoutError_:
        return CheckResult(name, Status.FAIL, f"did not finish within {COMMAND_TIMEOUT}s")
    problems = []
    if out.exit_code != spec.expect_exit:
        problems.append(f"exit code {out.exit_code}, expected {spec.expect_exit}")
    if spec.expect_stdout_contains is not None and spec.expect_stdout_contains not in out.stdout:
        problems.append(f"stdout does not contain {spec.expect_stdout_contains!r}")
    if problems:
        output = (out.stderr or out.stdout).strip()
        if output:
            problems.append(f"output: {output}")
        return CheckResult(name, Status.FAIL, shorten("; ".join(problems)))
    return CheckResult(name, Status.PASS)


def shorten(text: str, limit: int = DETAIL_LIMIT) -> str:
    """Collapse whitespace to one line and cut it to `limit` characters."""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 3] + "..."


def _messages(value: Any) -> list[str]:
    """Flatten cloud-init's errors: a list, or a mapping of level -> list. Missing is empty."""
    if not value:
        return []
    if isinstance(value, dict):
        items = [item for messages in value.values() for item in _messages(messages)]
    elif isinstance(value, list):
        items = [str(item) for item in value]
    else:
        items = [str(value)]
    return list(dict.fromkeys(items))  # 26.04 can repeat a message; keep the first of each


def _bind(
    fn: Callable[[Lxd, str, Any], CheckResult], client: Lxd, container: str, spec: Any
) -> Callable[[], CheckResult]:
    return lambda: fn(client, container, spec)


def _guarded(name: str, run: Callable[[], CheckResult]) -> CheckResult:
    """Turn an infrastructure failure inside one check into an ERROR cell."""
    try:
        return run()
    except SeedcheckError as exc:
        return CheckResult(name, Status.ERROR, shorten(str(exc)))
