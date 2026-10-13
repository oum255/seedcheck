from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from seedcheck import userdata
from seedcheck.userdata import Problem, Severity, has_errors, validate_userdata

REPO_ROOT = Path(__file__).resolve().parents[2]
VALID = "#cloud-config\npackages:\n  - nginx\n"


@pytest.fixture
def no_host_cloud_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(userdata.shutil, "which", lambda name: None)


class FakeRun:
    """Stands in for subprocess.run and records the argv it was called with."""

    def __init__(self, returncode: int = 0, stdout: str = "", raises: Exception | None = None):
        self.returncode = returncode
        self.stdout = stdout
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        assert kwargs["stdin"] is subprocess.DEVNULL
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


@pytest.fixture
def host_cloud_init(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    fake = FakeRun()
    monkeypatch.setattr(userdata.shutil, "which", lambda name: "/usr/bin/cloud-init")
    monkeypatch.setattr(userdata.subprocess, "run", fake)
    return fake


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "user-data.yaml"
    path.write_text(text)
    return path


def errors(problems: list[Problem]) -> list[str]:
    return [p.message for p in problems if p.severity is Severity.ERROR]


def test_nginx_example_is_valid_and_notes_skipped_host_schema(no_host_cloud_init: None) -> None:
    problems = validate_userdata(REPO_ROOT / "examples/nginx/user-data.yaml")
    assert not has_errors(problems)
    assert len(problems) == 1
    assert problems[0].severity is Severity.NOTE
    assert "host schema validation was skipped" in problems[0].message


def test_header_with_trailing_whitespace_is_accepted(
    tmp_path: Path, no_host_cloud_init: None
) -> None:
    assert not has_errors(validate_userdata(write(tmp_path, "#cloud-config  \r\nruncmd: []\n")))


def test_host_schema_is_run_when_cloud_init_is_installed(
    tmp_path: Path, host_cloud_init: FakeRun
) -> None:
    path = write(tmp_path, VALID)
    assert validate_userdata(path) == []
    assert host_cloud_init.calls == [["/usr/bin/cloud-init", "schema", "--config-file", str(path)]]


def test_host_schema_errors_are_reported_without_log_noise(
    tmp_path: Path, host_cloud_init: FakeRun
) -> None:
    host_cloud_init.returncode = 1
    host_cloud_init.stdout = (
        "2026-10-13 15:10:16,130 - schema.py[WARNING]: datasource not detected\n"
        "Error: Cloud config schema errors: packages: 42 is not of type 'array'\n"
        "\n"
        "Error: Invalid schema: user-data\n"
    )
    problems = validate_userdata(write(tmp_path, "#cloud-config\npackages: 42\n"))
    assert errors(problems) == ["cloud-init schema: packages: 42 is not of type 'array'"]


def test_host_schema_failure_without_parsable_output_still_fails(
    tmp_path: Path, host_cloud_init: FakeRun
) -> None:
    host_cloud_init.returncode = 2
    problems = validate_userdata(write(tmp_path, VALID))
    assert errors(problems) == ["cloud-init schema: failed with exit code 2"]


@pytest.mark.parametrize(
    "exc", [OSError("exec format error"), subprocess.TimeoutExpired("cloud-init", 60)]
)
def test_host_schema_that_cannot_run_is_only_a_note(
    tmp_path: Path, host_cloud_init: FakeRun, exc: Exception
) -> None:
    host_cloud_init.raises = exc
    problems = validate_userdata(write(tmp_path, VALID))
    assert not has_errors(problems)
    assert "could not run host cloud-init schema" in problems[0].message


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    assert errors(validate_userdata(tmp_path / "nope.yaml")) == ["file not found"]


def test_directory_is_an_error(tmp_path: Path) -> None:
    assert errors(validate_userdata(tmp_path))[0].startswith("cannot read file")


def test_non_utf8_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "user-data.yaml"
    path.write_bytes(b"#cloud-config\n\xff\xfe\n")
    assert errors(validate_userdata(path)) == ["file is not valid UTF-8 text"]


@pytest.mark.parametrize("text", ["", "   \n\n"])
def test_empty_file_is_an_error(tmp_path: Path, text: str) -> None:
    assert errors(validate_userdata(write(tmp_path, text))) == ["file is empty"]


@pytest.mark.parametrize(
    "text",
    ["packages:\n  - nginx\n", " #cloud-config\n", "#cloud-config: true\n", "#cloudconfig\n"],
)
def test_missing_cloud_config_header_is_rejected(tmp_path: Path, text: str) -> None:
    problems = validate_userdata(write(tmp_path, text))
    assert has_errors(problems)
    assert problems[0].line == 1
    assert "first line must be exactly '#cloud-config'" in problems[0].message
    assert "silently ignore" in problems[0].message


@pytest.mark.parametrize(
    ("first_line", "kind"),
    [
        ("#!/bin/bash", "shell script"),
        ("#include", "include list"),
        ("#include-once", "include-once list"),
        ("#cloud-boothook", "boothook"),
        ("#cloud-config-archive", "cloud-config archive"),
        ("#cloud-config-jsonp", "cloud-config JSON patch"),
        ("#part-handler", "part handler"),
        ("## template: jinja", "Jinja template"),
        ("Content-Type: multipart/mixed; boundary=x", "MIME multi-part archive"),
    ],
)
def test_other_cloud_init_formats_are_accepted_with_a_note(
    tmp_path: Path, host_cloud_init: FakeRun, first_line: str, kind: str
) -> None:
    problems = validate_userdata(write(tmp_path, f"{first_line}\nwhatever: [\n"))
    assert not has_errors(problems)
    assert problems[0].message.startswith(f"recognized as a {kind};")
    assert host_cloud_init.calls == []


def test_invalid_yaml_reports_the_line_number(tmp_path: Path) -> None:
    problems = validate_userdata(write(tmp_path, "#cloud-config\npackages:\n  - [nginx\n"))
    assert errors(problems)[0].startswith("invalid YAML")
    assert problems[0].line == 4


def test_header_only_file_is_an_error(tmp_path: Path) -> None:
    problems = validate_userdata(write(tmp_path, "#cloud-config\n"))
    assert errors(problems) == ["contains no configuration after the #cloud-config header"]


def test_body_that_is_not_a_mapping_is_an_error(tmp_path: Path) -> None:
    problems = validate_userdata(write(tmp_path, "#cloud-config\n- nginx\n"))
    assert errors(problems) == ["top level must be a mapping of cloud-init modules, got list"]


def test_problem_formatting_includes_severity_path_and_line() -> None:
    path = Path("/tmp/u.yaml")
    assert str(Problem(path, Severity.ERROR, "bad", 3)) == "error: /tmp/u.yaml:3: bad"
    assert str(Problem(path, Severity.NOTE, "fyi")) == "note: /tmp/u.yaml: fyi"
