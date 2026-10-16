"""A scripted stand-in for LxdClient, so the runner and checks are tested without LXD."""

from __future__ import annotations

from pathlib import Path

from seedcheck.errors import LxdError
from seedcheck.lxd import ExecResult


class FakeLxdClient:
    """Records every call and answers from scripted results.

    - launch_errors / ready_errors: image -> exception raised by launch / wait_ready
    - exec_results: argv tuple -> ExecResult or exception, for any container
    - image_exec_results: (image, argv tuple) -> result for one release only;
      takes precedence over exec_results
    - files: remote path -> content returned by pull_file
    - unavailable: exception raised by ensure_available
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.alive: dict[str, str] = {}  # container name -> image
        self.user_data: dict[str, str] = {}
        self.unavailable: BaseException | None = None
        self.launch_errors: dict[str, BaseException] = {}
        self.ready_errors: dict[str, BaseException] = {}
        self.exec_results: dict[tuple[str, ...], ExecResult | BaseException] = {
            ("cloud-init", "--version"): ExecResult(0, "/usr/bin/cloud-init 26.1-test\n", ""),
        }
        self.image_exec_results: dict[tuple[str, tuple[str, ...]], ExecResult | BaseException] = {}
        self.files: dict[str, str] = {
            "/var/log/cloud-init.log": "cloud-init log\n",
            "/var/log/cloud-init-output.log": "cloud-init output\n",
        }

    def ensure_available(self) -> None:
        self.calls.append(("ensure_available",))
        if self.unavailable is not None:
            raise self.unavailable

    def launch(self, image: str, name: str, user_data_text: str) -> None:
        self.calls.append(("launch", image, name))
        self.user_data[name] = user_data_text
        if image in self.launch_errors:
            raise self.launch_errors[image]
        self.alive[name] = image

    def wait_ready(self, name: str, timeout: float) -> None:
        self.calls.append(("wait_ready", name))
        error = self.ready_errors.get(self.alive.get(name, ""))
        if error is not None:
            raise error

    def exec(self, name: str, argv: list[str], timeout: float) -> ExecResult:
        self.calls.append(("exec", name, *argv))
        key = (self.alive.get(name, ""), tuple(argv))
        result = self.image_exec_results.get(key) or self.exec_results.get(
            tuple(argv), ExecResult(0, "", "")
        )
        if isinstance(result, BaseException):
            raise result
        return result

    def pull_file(self, name: str, remote_path: str, local_path: Path) -> None:
        self.calls.append(("pull_file", name, remote_path))
        if remote_path not in self.files:
            raise LxdError(f"could not copy {remote_path} out of {name}: Not Found")
        local_path.write_text(self.files[remote_path])

    def delete(self, name: str) -> None:
        self.calls.append(("delete", name))
        self.alive.pop(name, None)

    def names(self, action: str) -> list[str]:
        """Container names passed to one kind of call, in order."""
        return [
            call[1] if action != "launch" else call[2] for call in self.calls if call[0] == action
        ]
