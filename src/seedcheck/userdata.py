"""Static validation of a cloud-init user-data file, before any container is launched."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

CLOUD_CONFIG_HEADER = "#cloud-config"

# First-line markers of the other user-data formats cloud-init accepts. Longer
# markers come first so "#include-once" is not mistaken for "#include".
OTHER_FORMATS: tuple[tuple[str, str], ...] = (
    ("#cloud-config-archive", "cloud-config archive"),
    ("#cloud-config-jsonp", "cloud-config JSON patch"),
    ("#cloud-boothook", "boothook"),
    ("#include-once", "include-once list"),
    ("#include", "include list"),
    ("#part-handler", "part handler"),
    ("## template: jinja", "Jinja template"),
    ("#!", "shell script"),
    ("Content-Type: multipart/", "MIME multi-part archive"),
)

HOST_SCHEMA_TIMEOUT = 60

_SCHEMA_ERROR_PREFIX = "Error: Cloud config schema errors: "


class Severity(str, Enum):
    ERROR = "error"
    NOTE = "note"


@dataclass(frozen=True)
class Problem:
    """Something found in the user-data file. Only errors make it invalid."""

    path: Path
    severity: Severity
    message: str
    line: int | None = None

    def __str__(self) -> str:
        location = f"{self.path}:{self.line}" if self.line is not None else str(self.path)
        return f"{self.severity.value}: {location}: {self.message}"


def has_errors(problems: list[Problem]) -> bool:
    return any(problem.severity is Severity.ERROR for problem in problems)


def validate_userdata(path: Path) -> list[Problem]:
    """Check a user-data file and return every problem found (empty when clean)."""

    def error(message: str, line: int | None = None) -> list[Problem]:
        return [Problem(path, Severity.ERROR, message, line)]

    def note(message: str) -> list[Problem]:
        return [Problem(path, Severity.NOTE, message)]

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return error("file not found")
    except UnicodeDecodeError:
        return error("file is not valid UTF-8 text")
    except OSError as exc:
        return error(f"cannot read file: {exc.strerror}")

    if not text.strip():
        return error("file is empty")

    first_line = text.splitlines()[0].rstrip()
    if first_line != CLOUD_CONFIG_HEADER:
        for marker, kind in OTHER_FORMATS:
            if first_line.startswith(marker):
                return note(
                    f"recognized as a {kind}; schema validation only applies to "
                    f"{CLOUD_CONFIG_HEADER} files"
                )
        return error(
            f"first line must be exactly {CLOUD_CONFIG_HEADER!r}, found {first_line!r}; "
            "cloud-init would silently ignore this file",
            line=1,
        )

    try:
        body = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        problem = getattr(exc, "problem", None) or str(exc)
        return error(f"invalid YAML: {problem}", line=mark.line + 1 if mark else None)

    if body is None:
        return error("contains no configuration after the #cloud-config header")
    if not isinstance(body, dict):
        return error(
            f"top level must be a mapping of cloud-init modules, got {type(body).__name__}"
        )

    return _host_schema(path)


def _host_schema(path: Path) -> list[Problem]:
    """Run the host's own `cloud-init schema` on the file, if cloud-init is installed."""
    executable = shutil.which("cloud-init")
    if executable is None:
        return [
            Problem(
                path,
                Severity.NOTE,
                "cloud-init is not installed on this host, so host schema validation was "
                "skipped; each release is still schema-checked inside its own container",
            )
        ]
    try:
        proc = subprocess.run(
            [executable, "schema", "--config-file", str(path)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=HOST_SCHEMA_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [Problem(path, Severity.NOTE, f"could not run host cloud-init schema: {exc}")]
    if proc.returncode == 0:
        return []
    return [
        Problem(path, Severity.ERROR, f"cloud-init schema: {message}")
        for message in schema_errors(proc.stdout + proc.stderr, proc.returncode)
    ]


def schema_errors(output: str, returncode: int) -> list[str]:
    """Pick the error messages out of `cloud-init schema` output, skipping its log lines.

    `schema --config-file` prints them flush left; `schema --system` indents them.
    """
    lines = (line.strip() for line in output.splitlines())
    messages = [
        line.removeprefix(_SCHEMA_ERROR_PREFIX).strip()
        for line in lines
        if line.startswith(_SCHEMA_ERROR_PREFIX)
    ]
    return messages or [f"failed with exit code {returncode}"]
