"""`ufs-chem-assay run`: rendering, stage and application selection,
overrides, dry-run, the effective-config artifact — through the CLI's
main() in-process and through `python -m cli` once. The CLI speaks through
the harness logger (`ufs-chem-assay.cli`), never print."""

import logging
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from applications.base import Application
from applications.registry import REGISTRY
from cli.main import main
from logs import LOGGER_NAME
from tests.ufs_chem_assay.stubs import stub_application
from tests.ufs_chem_assay.run_configs import run_config_file

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLI_LOGGER = f"{LOGGER_NAME}.cli"
_CECE_SCRIPTS = [
    "01-source-cece.sh",
    "02-build-cece.sh",
    "03-data-cece.sh",
    "05-harness-cece.sh",
]


def _config(
    tmp_path: Path,
    template: str = "ursa.yaml",
    overrides: dict[str, object] | None = None,
) -> Path:
    return run_config_file(
        tmp_path, template, overrides=overrides, root_dir=tmp_path / "root"
    )


def _scripts(tmp_path: Path) -> list[str]:
    return sorted(
        p.name for p in (tmp_path / "root" / "scripts").iterdir() if p.suffix == ".sh"
    )


@pytest.fixture()
def stub(monkeypatch: pytest.MonkeyPatch) -> Application:
    app = stub_application()
    monkeypatch.setitem(REGISTRY, "stub", app)
    return app


def test_dry_run_renders_every_script_and_executes_nothing(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["run", f"--config-file={_config(tmp_path)}", "--dry-run"])
    assert code == 0
    assert _scripts(tmp_path) == _CECE_SCRIPTS
    run_bash.assert_not_called()
    messages = [record.getMessage() for record in caplog.records]
    assert any("dry run" in m for m in messages)
    # Nothing but scripts/ under the root; the effective config beside them.
    assert sorted(p.name for p in (tmp_path / "root").iterdir()) == ["scripts"]
    assert (tmp_path / "root" / "scripts" / "run-config.yaml").is_file()


def test_effective_config_records_overrides(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(
            [
                "run",
                f"--config-file={_config(tmp_path)}",
                "--dry-run",
                "-o",
                "applications:cece:ref=develop",
                "slurm:qos=batch",
                "--override",
                "harness:suite_config=ex3-suite.yaml",
            ]
        )
    assert code == 0
    effective = yaml.safe_load(
        (tmp_path / "root" / "scripts" / "run-config.yaml").read_text()
    )
    assert effective["applications"]["cece"]["ref"] == "develop"
    assert effective["slurm"]["qos"] == "batch"
    assert effective["harness"]["suite_config"] == "ex3-suite.yaml"
    assert effective["root_dir"] == str(tmp_path / "root")
    harness = (tmp_path / "root" / "scripts" / "05-harness-cece.sh").read_text()
    assert "--suite-config=ex3-suite.yaml" in harness
    assert "-q batch" in harness
    messages = [record.getMessage() for record in caplog.records]
    assert any("overrides:" in m and "slurm:qos=batch" in m for m in messages)


def test_bad_override_is_a_clean_error(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(
            ["run", f"--config-file={_config(tmp_path)}", "--dry-run", "-o", "nonsense"]
        )
    assert code == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "nonsense" in errors[0]


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
    assert _scripts(tmp_path) == ["02-build-cece.sh", "05-harness-cece.sh"]


def test_all_stages_run_with_bash_in_order(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash", return_value=0)
    code = main(["run", f"--config-file={_config(tmp_path)}"])
    assert code == 0
    ran = [call.args[0].name for call in run_bash.call_args_list]
    assert ran == _CECE_SCRIPTS


def test_failed_stage_stops_the_run_and_logs_at_error(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash", side_effect=[0, 2])
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["run", f"--config-file={_config(tmp_path)}"])
    assert code == 2
    assert run_bash.call_count == 2
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "02-build-cece" in errors[0].getMessage()


def test_local_config_runs_the_same_stages(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash", return_value=0)
    code = main(["run", f"--config-file={_config(tmp_path, 'local.yaml')}"])
    assert code == 0
    ran = [call.args[0].name for call in run_bash.call_args_list]
    assert ran == _CECE_SCRIPTS


def test_harness_keeps_slot_05_without_a_04(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    # Slot 04 is held for the application-tests stage of issue #9.
    mocker.patch("cli.main.run_bash")
    assert main(["run", f"--config-file={_config(tmp_path)}", "--dry-run"]) == 0
    assert _scripts(tmp_path) == _CECE_SCRIPTS


def test_platform_flag_overrides_file(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch("cli.main.run_bash")
    path = _config(tmp_path)
    assert (
        main(["run", f"--config-file={path}", "--platform", "local", "--dry-run"]) == 0
    )
    # The exports follow the overridden platform (local -> docker runtime).
    harness = tmp_path / "root" / "scripts" / "05-harness-cece.sh"
    assert "export ASSAY_RUNTIME=docker" in harness.read_text()


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
    assert (tmp_path / "root" / "scripts" / "01-source-cece.sh").is_file()


def test_bad_stage_name_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["run", f"--config-file={_config(tmp_path)}", "--stage", "bogus"])
    assert excinfo.value.code == 2


def test_uncreatable_root_dir_is_a_clean_error(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    """A bad root_dir must not end in a traceback: one ERROR line naming
    the root, exit 1, nothing executed."""
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


def test_root_dir_flag_overrides_the_derived_root(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    config = run_config_file(tmp_path)  # no root_dir in the template
    root = tmp_path / "elsewhere"
    code = main(["run", f"--config-file={config}", f"--root-dir={root}", "--dry-run"])
    assert code == 0
    harness = root / "scripts" / "05-harness-cece.sh"
    assert harness.is_file()
    assert f"export CECE_ROOT_DIR={root}/CECE" in harness.read_text()


# ── Several applications in one run ──────────────────────────────────────────


def test_comprehensive_run_renders_stage_major_per_application(
    tmp_path: Path, mocker: MockerFixture, stub: Application
) -> None:
    run_bash = mocker.patch("cli.main.run_bash", return_value=0)
    path = _config(
        tmp_path, overrides={"applications.stub": {"git_url": "u", "ref": "main"}}
    )
    assert main(["run", f"--config-file={path}"]) == 0
    ran = [call.args[0].name for call in run_bash.call_args_list]
    assert ran == [
        "01-source-cece.sh",
        "01-source-stub.sh",
        "02-build-cece.sh",
        "02-build-stub.sh",
        "03-data-cece.sh",
        "03-data-stub.sh",
        "05-harness-cece.sh",
        "05-harness-stub.sh",
    ]
    scripts = tmp_path / "root" / "scripts"
    assert "natural_earth" in (scripts / "03-data-cece.sh").read_text()
    assert "natural_earth" not in (scripts / "03-data-stub.sh").read_text()
    assert (
        "--combo-output-root=ufs-chem-assay-output/cece"
        in (scripts / "05-harness-cece.sh").read_text()
    )


def test_application_flag_narrows_a_comprehensive_run(
    tmp_path: Path, mocker: MockerFixture, stub: Application
) -> None:
    mocker.patch("cli.main.run_bash")
    path = _config(
        tmp_path, overrides={"applications.stub": {"git_url": "u", "ref": "main"}}
    )
    assert (
        main(["run", f"--config-file={path}", "--application", "stub", "--dry-run"])
        == 0
    )
    assert _scripts(tmp_path) == [
        "01-source-stub.sh",
        "02-build-stub.sh",
        "03-data-stub.sh",
        "05-harness-stub.sh",
    ]


def test_unconfigured_application_is_a_clean_error(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(
            [
                "run",
                f"--config-file={_config(tmp_path)}",
                "--application",
                "catchem",
                "--dry-run",
            ]
        )
    assert code == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "catchem" in errors[0] and "['cece']" in errors[0]
