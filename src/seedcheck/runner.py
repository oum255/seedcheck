"""Run a configuration on each release: launch, wait, check, collect logs, clean up."""

from __future__ import annotations

import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from seedcheck import __version__
from seedcheck.config import Config
from seedcheck.errors import SeedcheckError, TimeoutError_
from seedcheck.lxd import Lxd
from seedcheck.models import CheckResult, ReleaseResult, RunResult, Status
from seedcheck.releases import Release

# Runs the checks inside a ready container (see checks.run_checks).
CheckFn = Callable[[Lxd, str, Config], list[CheckResult]]
ProgressFn = Callable[[str], None]

LOG_FILES = ("/var/log/cloud-init.log", "/var/log/cloud-init-output.log")
VERSION_TIMEOUT = 30


@dataclass(frozen=True)
class RunOptions:
    timeout: int
    keep: bool = False
    artifacts_dir: Path | None = None


def _stderr_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _no_checks(client: Lxd, container: str, config: Config) -> list[CheckResult]:
    return []


def container_name(release: Release) -> str:
    """A unique name, so two runs at the same time never collide."""
    return f"seedcheck-{release.codename}-{secrets.token_hex(3)}"


def run_release(
    client: Lxd,
    release: Release,
    config: Config,
    options: RunOptions,
    *,
    check_fn: CheckFn = _no_checks,
    progress: ProgressFn = _stderr_progress,
    created: list[str] | None = None,
) -> ReleaseResult:
    """Run the whole lifecycle for one release. Never raises SeedcheckError or Exception.

    Infrastructure problems (launch failure, timeout, unexpected exceptions)
    become an ERROR result. The container is deleted in all cases unless
    options.keep is set. `created` lists the containers that may still exist:
    a name is added before launch, so a caller interrupted mid-launch still
    knows what to clean up, and removed once the container is deleted.
    """

    def say(message: str) -> None:
        progress(f"[{release.version}] {message}")

    name = container_name(release)
    result = ReleaseResult(release.version, release.codename, Status.ERROR)
    started = time.monotonic()
    deadline = started + options.timeout
    launched = False
    if created is not None:
        created.append(name)
    try:
        user_data = config.user_data.read_text(encoding="utf-8")
        say(f"launching {release.image} as {name}...")
        client.launch(release.image, name, user_data)
        launched = True

        say("waiting for the container to start...")
        client.wait_ready(name, timeout=_remaining(deadline))

        say("waiting for cloud-init...")
        # The exit code is deliberately ignored: its meaning differs between
        # releases (see docs/design.md). The checks read the JSON status instead.
        client.exec(name, ["cloud-init", "status", "--wait"], timeout=_remaining(deadline))
        result.cloud_init_version = _cloud_init_version(client, name)

        say("running checks...")
        result.checks = check_fn(client, name, config)
        result.status = overall_status(result.checks)
    except TimeoutError_ as exc:
        result.error = f"timed out: {exc}"
    except SeedcheckError as exc:
        result.error = str(exc)
    except Exception as exc:  # a bug must not stop the other releases
        result.error = f"unexpected error: {type(exc).__name__}: {exc}"
    finally:
        if launched and options.artifacts_dir is not None:
            _collect_logs(client, name, options.artifacts_dir / release.version, say)
        if options.keep:
            say(f"keeping container {name} (--keep)")
        elif _delete(client, name, say) and created is not None:
            created.remove(name)
        result.duration_s = round(time.monotonic() - started, 1)

    say(f"{result.status.value}" + (f": {result.error}" if result.error else ""))
    return result


def run_all(
    client: Lxd,
    config: Config,
    options: RunOptions,
    *,
    check_fn: CheckFn = _no_checks,
    progress: ProgressFn = _stderr_progress,
) -> RunResult:
    """Run every release in turn; one release failing never stops the next."""
    run = RunResult(
        tool_version=__version__,
        user_data=str(config.user_data),
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    created: list[str] = []
    try:
        for release in config.releases:
            run.releases.append(
                run_release(
                    client,
                    release,
                    config,
                    options,
                    check_fn=check_fn,
                    progress=progress,
                    created=created,
                )
            )
    except KeyboardInterrupt:
        if options.keep:
            progress(f"interrupted; keeping containers (--keep): {', '.join(created)}")
        else:
            progress("interrupted; deleting containers...")
            for name in list(created):
                _delete(client, name, progress)
        raise
    return run


def overall_status(checks: list[CheckResult]) -> Status:
    """A release fails if any check fails; WARN only if nothing worse happened."""
    statuses = {check.status for check in checks}
    for status in (Status.ERROR, Status.FAIL, Status.WARN):
        if status in statuses:
            return status
    return Status.PASS


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError_("the release timeout was used up")
    return remaining


def _cloud_init_version(client: Lxd, name: str) -> str:
    # Prints e.g. "/usr/bin/cloud-init 26.1-0ubuntu1~24.04.1".
    out = client.exec(name, ["cloud-init", "--version"], timeout=VERSION_TIMEOUT)
    words = (out.stdout or out.stderr).split()
    return words[-1] if words else ""


def _collect_logs(client: Lxd, name: str, directory: Path, say: ProgressFn) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for remote in LOG_FILES:
        try:
            client.pull_file(name, remote, directory / Path(remote).name)
        except SeedcheckError as exc:
            say(f"warning: {exc}")


def _delete(client: Lxd, name: str, say: ProgressFn) -> bool:
    """Delete a container, reporting (not raising) a failure. Returns whether it worked."""
    try:
        client.delete(name)
    except SeedcheckError as exc:
        say(f"warning: {exc}; remove it with: lxc delete --force {name}")
        return False
    return True
