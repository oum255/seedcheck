from __future__ import annotations

import json
from pathlib import Path

import pytest

from seedcheck import __version__, userdata
from seedcheck.cli import EXIT_FAILED, EXIT_INFRA, EXIT_INTERRUPTED, EXIT_OK, EXIT_USAGE, main
from seedcheck.errors import LxdError
from seedcheck.lxd import ExecResult

from .fakes import FakeLxdClient

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONFIG = str(REPO_ROOT / "examples/nginx/seedcheck.yaml")
STATUS_CMD = ("cloud-init", "status", "--long", "--format", "json")
DONE = ExecResult(0, json.dumps({"status": "done", "errors": [], "recoverable_errors": {}}), "")


@pytest.fixture(autouse=True)
def no_host_cloud_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(userdata.shutil, "which", lambda name: None)


@pytest.fixture
def config(tmp_path: Path) -> Path:
    """A config with no extra checks: only schema and cloud-init run."""
    (tmp_path / "user-data.yaml").write_text("#cloud-config\nruncmd: [echo hi]\n")
    path = tmp_path / "seedcheck.yaml"
    path.write_text("user_data: user-data.yaml\n")
    return path


@pytest.fixture
def client() -> FakeLxdClient:
    fake = FakeLxdClient()
    fake.exec_results[STATUS_CMD] = DONE
    return fake


def launched_images(client: FakeLxdClient) -> list[str]:
    return [call[1] for call in client.calls if call[0] == "launch"]


# --- version / help / releases / validate ---------------------------------------


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


# --- run: one scenario per exit code ---------------------------------------------


def test_run_all_passing_exits_0_and_prints_the_matrix(
    config: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", str(config)], client=client) == EXIT_OK
    out = capsys.readouterr()
    assert out.out.splitlines()[2].split() == ["Check", "22.04", "24.04", "26.04"]
    assert "Result: 3 of 3 releases passed (6 checks, 0 failed, 0 warnings)" in out.out
    assert "[24.04] waiting for cloud-init..." in out.err  # progress stays off stdout
    assert "waiting" not in out.out
    assert ("ensure_available",) in client.calls


def test_run_with_a_failing_check_exits_1(
    config: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.exec_results[STATUS_CMD] = ExecResult(
        1, json.dumps({"status": "error", "errors": ["boom"]}), ""
    )
    assert main(["run", str(config)], client=client) == EXIT_FAILED
    assert "[22.04] cloud-init: boom" in capsys.readouterr().out


def test_run_with_an_invalid_config_exits_2_before_touching_lxd(
    tmp_path: Path, client: FakeLxdClient
) -> None:
    config = tmp_path / "seedcheck.yaml"
    config.write_text("user_data: missing.yaml\n")
    assert main(["run", str(config)], client=client) == EXIT_USAGE
    assert client.calls == []


def test_run_with_invalid_user_data_exits_2_before_touching_lxd(
    config: Path, client: FakeLxdClient
) -> None:
    (config.parent / "user-data.yaml").write_text("runcmd: []\n")
    assert main(["run", str(config)], client=client) == EXIT_USAGE
    assert client.calls == []


def test_run_without_lxd_exits_3_with_advice(
    config: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.unavailable = LxdError("the 'lxc' command was not found; run scripts/setup-lxd.sh")
    assert main(["run", str(config)], client=client) == EXIT_INFRA
    assert "error: the 'lxc' command was not found" in capsys.readouterr().err
    assert launched_images(client) == []


def test_run_with_an_erroring_release_exits_3_but_runs_the_others(
    config: Path, client: FakeLxdClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.launch_errors["ubuntu:24.04"] = LxdError("could not launch ubuntu:24.04: no image")
    assert main(["run", str(config)], client=client) == EXIT_INFRA
    assert launched_images(client) == ["ubuntu:22.04", "ubuntu:24.04", "ubuntu:26.04"]
    assert "[24.04] could not launch ubuntu:24.04: no image" in capsys.readouterr().out


def test_ctrl_c_exits_130_and_cleans_up(config: Path, client: FakeLxdClient) -> None:
    client.ready_errors["ubuntu:24.04"] = KeyboardInterrupt()
    assert main(["run", str(config)], client=client) == EXIT_INTERRUPTED
    assert not client.alive


# --- run: options ------------------------------------------------------------------


def test_release_flag_limits_and_orders_the_releases(config: Path, client: FakeLxdClient) -> None:
    assert (
        main(["run", str(config), "--release", "resolute", "--release", "22.04"], client=client)
        == 0
    )
    assert launched_images(client) == ["ubuntu:26.04", "ubuntu:22.04"]


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--release", "20.04"], "unknown or unsupported release '20.04'"),
        (["--release", "jammy", "--release", "22.04"], "release 22.04 (jammy) is given twice"),
    ],
)
def test_bad_release_flag_exits_2(
    config: Path,
    client: FakeLxdClient,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    message: str,
) -> None:
    assert main(["run", str(config), *flags], client=client) == EXIT_USAGE
    assert message in capsys.readouterr().err
    assert client.calls == []


@pytest.mark.parametrize("value", ["0", "-5", "soon"])
def test_bad_timeout_flag_is_a_usage_error(config: Path, value: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", str(config), "--timeout", value])
    assert exc.value.code == EXIT_USAGE


def test_timeout_flag_overrides_the_config(
    config: Path, client: FakeLxdClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeouts: list[float] = []
    real_wait = client.wait_ready

    def wait_ready(name: str, timeout: float) -> None:
        timeouts.append(timeout)
        real_wait(name, timeout)

    monkeypatch.setattr(client, "wait_ready", wait_ready)
    main(["run", str(config), "--timeout", "42", "--release", "24.04"], client=client)
    assert 40 < timeouts[0] <= 42


def test_markdown_and_json_reports_are_written(
    config: Path, client: FakeLxdClient, tmp_path: Path
) -> None:
    md, js = tmp_path / "report.md", tmp_path / "report.json"
    assert main(["run", str(config), "--markdown", str(md), "--json", str(js)], client=client) == 0
    assert md.read_text().startswith("## Seedcheck results")
    data = json.loads(js.read_text())
    assert [r["release"] for r in data["releases"]] == ["22.04", "24.04", "26.04"]
    assert data["exit_code"] == 0


def test_report_in_a_missing_directory_exits_2_before_running(
    config: Path, client: FakeLxdClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "nope" / "report.md"
    assert main(["run", str(config), "--markdown", str(target)], client=client) == EXIT_USAGE
    assert "--markdown: directory does not exist" in capsys.readouterr().err
    assert client.calls == []


def test_report_that_cannot_be_written_exits_3(
    config: Path, client: FakeLxdClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "out.json").mkdir()  # a directory where the file should go
    assert main(["run", str(config), "--json", str(tmp_path / "out.json")], client=client) == 3
    assert "could not write report" in capsys.readouterr().err


def test_artifacts_dir_and_keep_are_passed_to_the_runner(
    config: Path, client: FakeLxdClient, tmp_path: Path
) -> None:
    logs = tmp_path / "logs"
    argv = ["run", str(config), "--release", "24.04", "--artifacts-dir", str(logs), "--keep"]
    assert main(argv, client=client) == 0
    assert (logs / "24.04" / "cloud-init.log").exists()
    assert [c for c in client.calls if c[0] == "delete"] == []


def test_user_data_path_is_shown_relative_to_the_current_directory(
    config: Path,
    client: FakeLxdClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(config.parent)
    main(["run", "seedcheck.yaml", "--release", "24.04"], client=client)
    assert capsys.readouterr().out.startswith("Seedcheck 0.1.0   user-data: user-data.yaml\n")


def test_user_data_outside_the_current_directory_is_shown_absolute(
    config: Path,
    client: FakeLxdClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    elsewhere = config.parent / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    main(["run", str(config), "--release", "24.04"], client=client)
    assert f"user-data: {config.parent / 'user-data.yaml'}" in capsys.readouterr().out


# --- color -------------------------------------------------------------------------


class Tty:
    """Makes sys.stdout look like a terminal while capsys still captures it."""

    @staticmethod
    def isatty() -> bool:
        return True


def run_with_tty(config: Path, client: FakeLxdClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(sys.stdout, "isatty", Tty.isatty)
    main(["run", str(config), "--release", "24.04"], client=client)


def test_color_is_used_on_a_terminal(
    config: Path,
    client: FakeLxdClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    run_with_tty(config, client, monkeypatch)
    assert "\033[32mPASS\033[0m" in capsys.readouterr().out


def test_no_color_env_disables_color(
    config: Path,
    client: FakeLxdClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    run_with_tty(config, client, monkeypatch)
    assert "\033[" not in capsys.readouterr().out


def test_redirected_output_has_no_color(
    config: Path,
    client: FakeLxdClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    main(["run", str(config), "--release", "24.04"], client=client)
    assert "\033[" not in capsys.readouterr().out
