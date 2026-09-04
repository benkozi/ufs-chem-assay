"""`ufs-chem-assay run`: rendering, stage selection, dry-run, submission —
through the CLI's main() in-process and through `python -m cli` once. The
CLI speaks through the harness logger (`ufs-chem-assay.cli`), never print."""

import logging
import subprocess
import sys
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from cli.main import main
from logs import LOGGER_NAME
from tests.ufs_chem_assay.run_configs import run_config_file

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLI_LOGGER = f"{LOGGER_NAME}.cli"


def _config(
    tmp_path: Path,
    template: str = "ursa.yaml",
    overrides: dict[str, object] | None = None,
) -> Path:
    return run_config_file(
        tmp_path, template, overrides=overrides, root_dir=tmp_path / "root"
    )


def _scripts(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "root" / "scripts").iterdir())


def test_dry_run_renders_every_script_and_executes_nothing(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["run", f"--config-file={_config(tmp_path)}", "--dry-run"])
    assert code == 0
    assert _scripts(tmp_path) == [
        "01-source.sh",
        "02-build.sh",
        "03-data.sh",
        "05-harness.sh",
    ]
    run_bash.assert_not_called()
    messages = [record.getMessage() for record in caplog.records]
    assert any("dry run" in m for m in messages)
    # Nothing but scripts/ under the root.
    assert sorted(p.name for p in (tmp_path / "root").iterdir()) == ["scripts"]


def test_stage_selection_renders_only_those(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    code = main(
        [
            "run",
            f"--config-file={_config(tmp_path)}",
            "--stage",
            "build",
            "--stage",
            "harness",
            "--dry-run",
        ]
    )
    assert code == 0
    assert _scripts(tmp_path) == ["02-build.sh", "05-harness.sh"]


def test_all_stages_run_with_bash_in_order(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash", return_value=0)
    code = main(["run", f"--config-file={_config(tmp_path)}"])
    assert code == 0
    ran = [call.args[0].name for call in run_bash.call_args_list]
    assert ran == ["01-source.sh", "02-build.sh", "03-data.sh", "05-harness.sh"]


def test_failed_stage_stops_the_run_and_logs_at_error(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash", side_effect=[0, 2])
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["run", f"--config-file={_config(tmp_path)}"])
    assert code == 2
    assert run_bash.call_count == 2
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "02-build" in errors[0].getMessage()


def test_local_config_runs_the_same_stages(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash", return_value=0)
    code = main(["run", f"--config-file={_config(tmp_path, 'local.yaml')}"])
    assert code == 0
    ran = [call.args[0].name for call in run_bash.call_args_list]
    assert ran == ["01-source.sh", "02-build.sh", "03-data.sh", "05-harness.sh"]


def test_cece_tests_stage_only_when_configured(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    path = _config(
        tmp_path, overrides={"cece.run_tests": True, "cece.targets": ["all"]}
    )
    assert main(["run", f"--config-file={path}", "--dry-run"]) == 0
    assert "04-cece-tests.sh" in _scripts(tmp_path)
    tests = (tmp_path / "root" / "scripts" / "04-cece-tests.sh").read_text()
    assert "sbatch --wait" in tests and "ctest" in tests


def test_platform_flag_overrides_file(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch("cli.main.run_bash")
    path = _config(tmp_path)
    assert (
        main(["run", f"--config-file={path}", "--platform", "local", "--dry-run"]) == 0
    )
    # The exports follow the overridden platform (local -> docker runtime).
    harness = tmp_path / "root" / "scripts" / "05-harness.sh"
    assert "export CECE_RUNTIME=docker" in harness.read_text()


def test_python_m_cli_entrypoint(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli",
            "run",
            f"--config-file={_config(tmp_path)}",
            "--dry-run",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(_REPO_ROOT / "src")},
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "root" / "scripts" / "01-source.sh").is_file()


def test_bad_stage_name_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["run", f"--config-file={_config(tmp_path)}", "--stage", "bogus"])
    assert excinfo.value.code == 2


def test_uncreatable_root_dir_is_a_clean_error(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    """An unedited template (placeholder root_dir) must not end in a
    traceback: one ERROR line naming the root, exit 1, nothing executed."""
    mocker.patch("cli.main.run_bash")
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    path = run_config_file(tmp_path, root_dir=blocker / "root")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["run", f"--config-file={path}", "--dry-run"])
    assert code == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and str(blocker / "root") in errors[0]
    assert "root_dir" in errors[0]
