"""A thin wrapper around the `lxc` command-line client.

Every call to `lxc` goes through LxdClient._run(), so there is exactly one
place that builds argument lists, applies timeouts and turns failures into
Seedcheck errors, and unit tests can swap the whole client for a fake.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from seedcheck.errors import LxdError, TimeoutError_

SETUP_HINT = "run scripts/setup-lxd.sh, or: sudo snap install lxd && sudo lxd init --auto"
GROUP_HINT = "add yourself to the lxd group (sudo usermod -aG lxd $USER), then log out and back in"

# Generous: the first launch of a release downloads its image.
LAUNCH_TIMEOUT = 600
QUICK_TIMEOUT = 30
# How long delete() waits for an interrupted launch (possibly an image download) to end.
DELETE_BUSY_TIMEOUT = 300


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str


class Lxd(Protocol):
    """What the runner and the checks need from LXD; implemented by LxdClient and test fakes."""

    def ensure_available(self) -> None: ...

    def launch(self, image: str, name: str, user_data_text: str) -> None: ...

    def wait_ready(self, name: str, timeout: float) -> None: ...

    def exec(self, name: str, argv: list[str], timeout: float) -> ExecResult: ...

    def pull_file(self, name: str, remote_path: str, local_path: Path) -> None: ...

    def delete(self, name: str) -> None: ...


def _stderr_log(message: str) -> None:
    print(message, file=sys.stderr)


class LxdClient:
    def __init__(
        self,
        lxc: str = "lxc",
        verbose: bool = False,
        log: Callable[[str], None] = _stderr_log,
    ) -> None:
        self.lxc = lxc
        self.verbose = verbose
        self.log = log
        # Replaced in unit tests so polling loops run instantly.
        self.sleep: Callable[[float], None] = time.sleep
        self.clock: Callable[[], float] = time.monotonic

    def _run(
        self, args: list[str], timeout: float, input: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run `lxc ARGS...` and return the finished process, whatever its exit code.

        stdin is always set explicitly: `lxc launch` reads instance configuration
        from stdin whenever it is not a terminal, so it must never inherit ours.
        """
        argv = [self.lxc, *args]
        if self.verbose:
            self.log(f"$ {shlex.join(argv)}")
        try:
            if input is None:
                proc = subprocess.run(
                    argv,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    check=False,
                )
            else:
                proc = subprocess.run(
                    argv,
                    input=input,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    check=False,
                )
        except FileNotFoundError:
            raise LxdError(f"the {self.lxc!r} command was not found; {SETUP_HINT}") from None
        except subprocess.TimeoutExpired:
            raise TimeoutError_(f"'lxc {args[0]}' did not finish within {timeout:g}s") from None
        if self.verbose:
            for line in (proc.stdout + proc.stderr).splitlines():
                self.log(f"  {line}")
            self.log(f"  (exit {proc.returncode})")
        return proc

    def ensure_available(self) -> None:
        """Fail early, with advice, if LXD cannot be used."""
        if shutil.which(self.lxc) is None:
            raise LxdError(f"the {self.lxc!r} command was not found; {SETUP_HINT}")
        info = self._run(["info"], timeout=QUICK_TIMEOUT)
        if info.returncode != 0:
            message = _first_error(info)
            if "permission denied" in message.lower():
                raise LxdError(f"permission denied talking to LXD ({message}); {GROUP_HINT}")
            raise LxdError(f"LXD is not reachable ({message}); {SETUP_HINT}")
        pools = self._run(["storage", "list", "--format", "csv"], timeout=QUICK_TIMEOUT)
        if pools.returncode == 0 and not pools.stdout.strip():
            raise LxdError("LXD has no storage pool, so it is not initialized; " + SETUP_HINT)

    def launch(self, image: str, name: str, user_data_text: str) -> None:
        # The whole key=value pair is one argument, so the user-data never meets a shell.
        proc = self._run(
            ["launch", image, name, "--config", f"cloud-init.user-data={user_data_text}"],
            timeout=LAUNCH_TIMEOUT,
        )
        if proc.returncode != 0:
            raise LxdError(f"could not launch {image}: {_first_error(proc)}")

    def wait_ready(self, name: str, timeout: float) -> None:
        """Poll `lxc exec NAME -- true` once a second until the container answers."""
        deadline = self.clock() + timeout
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise TimeoutError_(f"container {name} was not ready after {timeout:g}s")
            try:
                if self._run(["exec", name, "--", "true"], timeout=remaining).returncode == 0:
                    return
            except TimeoutError_:
                continue  # the deadline check above ends the loop
            self.sleep(1)

    def exec(self, name: str, argv: list[str], timeout: float) -> ExecResult:
        proc = self._run(["exec", name, "--", *argv], timeout=timeout)
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)

    def pull_file(self, name: str, remote_path: str, local_path: Path) -> None:
        proc = self._run(["file", "pull", f"{name}{remote_path}", str(local_path)], QUICK_TIMEOUT)
        if proc.returncode != 0:
            raise LxdError(f"could not copy {remote_path} out of {name}: {_first_error(proc)}")

    def delete(self, name: str) -> None:
        """Force-delete a container; a container that is already gone is not an error.

        If a launch was interrupted (Ctrl+C), LXD keeps creating the container
        server-side and refuses to delete it while it is "busy", so retry once
        a second until that operation ends.
        """
        deadline = self.clock() + DELETE_BUSY_TIMEOUT
        while True:
            proc = self._run(["delete", "--force", name], timeout=QUICK_TIMEOUT * 4)
            stderr = proc.stderr.lower()
            if proc.returncode == 0 or "not found" in stderr:
                return
            if "busy" not in stderr or self.clock() >= deadline:
                raise LxdError(f"could not delete container {name}: {_first_error(proc)}")
            self.sleep(1)


def _first_error(proc: subprocess.CompletedProcess[str]) -> str:
    """The most useful single line of a failed lxc command's output."""
    for line in (proc.stderr + proc.stdout).splitlines():
        if line.startswith("Error:"):
            return line.removeprefix("Error:").strip()
    lines = (proc.stderr + proc.stdout).strip().splitlines()
    return lines[-1] if lines else f"exit code {proc.returncode}"
