"""Run a config on each release: launch, wait, check, grab logs, clean up."""

from __future__ import annotations

import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from seedcheck import __version__
from seedcheck.checks import run_checks, skipped
from seedcheck.config import Config
from seedcheck.errors import SeedcheckError, TimeoutError_
from seedcheck.lxd import Lxd
from seedcheck.models import CheckResult, ReleaseResult, RunResult, Status
from seedcheck.releases import Release

# Same shape as checks.run_checks, so tests can pass their own.
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


def container_name(release: Release) -> str:
    """Random suffix so two runs at once don't clash."""
    return f"seedcheck-{release.codename}-{secrets.token_hex(3)}"


def run_release(
    client: Lxd,
    release: Release,
    config: Config,
    options: RunOptions,
    *,
    check_fn: CheckFn = run_checks,
    progress: ProgressFn = _stderr_progress,
    created: list[str] | None = None,
) -> ReleaseResult:
    """Full lifecycle for one release. Infra problems don't raise.

    Launch failures, timeouts and unexpected exceptions become an ERROR
    result. The container is always deleted unless options.keep is set.

    `created` tracks containers that might still exist. A name goes in
    before launch (so a Ctrl+C mid-launch still cleans it up) and comes
    out once it's deleted.
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
        # Don't trust the exit code here, it differs between releases
        # (see docs/design.md). The cloud-init check reads the JSON instead.
        client.exec(name, ["cloud-init", "status", "--wait"], timeout=_remaining(deadline))
        result.cloud_init_version = _cloud_init_version(client, name)

        say("running checks...")
        result.checks = check_fn(client, name, config)
        result.status = overall_status(result.checks)
    except TimeoutError_ as exc:
        result.error = f"timed out: {exc}"
    except SeedcheckError as exc:
        result.error = str(exc)
    except Exception as exc:  # a bug shouldn't stop the other releases
        result.error = f"unexpected error: {type(exc).__name__}: {exc}"
    finally:
        if launched and options.artifacts_dir is not None:
            _collect_logs(client, name, options.artifacts_dir / release.version, say)
        if options.keep:
            say(f"keeping container {name} (--keep)")
        elif _delete(client, name, say) and created is not None:
            created.remove(name)
        result.duration_s = round(time.monotonic() - started, 1)

    if result.error:
        # Never got to the checks, so show them all as SKIP.
        result.checks = skipped(config, f"not run: {result.error}")

    say(f"{result.status.value}" + (f": {result.error}" if result.error else ""))
    return result


def run_all(
    client: Lxd,
    config: Config,
    options: RunOptions,
    *,
    check_fn: CheckFn = run_checks,
    progress: ProgressFn = _stderr_progress,
) -> RunResult:
    """Run each release in turn. One failing doesn't stop the rest."""
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
    """Worst status wins: ERROR, then FAIL, then WARN, otherwise PASS."""
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
    """Delete a container. Prints a warning instead of raising. Returns True if it worked."""
    try:
        client.delete(name)
    except SeedcheckError as exc:
        say(f"warning: {exc}; remove it with: lxc delete --force {name}")
        return False
    return True
