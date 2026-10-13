from __future__ import annotations

from pathlib import Path

import pytest

from seedcheck import __version__, userdata
from seedcheck.cli import EXIT_OK, EXIT_USAGE, main

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONFIG = str(REPO_ROOT / "examples/nginx/seedcheck.yaml")


@pytest.fixture(autouse=True)
def no_host_cloud_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(userdata.shutil, "which", lambda name: None)


def test_version_flag_prints_version_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"seedcheck {__version__}"


def test_no_command_prints_help_and_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_USAGE
    assert "usage: seedcheck" in capsys.readouterr().err


def test_unknown_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == EXIT_USAGE


def test_releases_lists_versions_codenames_and_images(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["releases"]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["VERSION", "CODENAME", "IMAGE"]
    assert [line.split() for line in lines[1:]] == [
        ["22.04", "jammy", "ubuntu:22.04"],
        ["24.04", "noble", "ubuntu:24.04"],
        ["26.04", "resolute", "ubuntu:26.04"],
    ]


def test_validate_accepts_the_nginx_example(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", NGINX_CONFIG]) == EXIT_OK
    out = capsys.readouterr()
    assert "valid" in out.out
    assert "releases: 22.04, 24.04, 26.04; 6 checks" in out.out
    assert out.err.startswith("note: ")


def test_validate_rejects_an_invalid_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "seedcheck.yaml"
    config.write_text("user_data: missing.yaml\n")
    assert main(["validate", str(config)]) == EXIT_USAGE
    assert capsys.readouterr().err.startswith(f"error: {config}: user_data: file not found")


def test_validate_rejects_invalid_user_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "user-data.yaml").write_text("packages: [nginx]\n")
    config = tmp_path / "seedcheck.yaml"
    config.write_text("user_data: user-data.yaml\n")
    assert main(["validate", str(config)]) == EXIT_USAGE
    out = capsys.readouterr()
    assert out.out == ""
    assert "user-data.yaml:1: first line must be exactly '#cloud-config'" in out.err
