"""Which harness command wrote a log: every harness log line carries the
command in brackets (`ufs-chem-assay.cli [watch]: ...`, `[pytest]` for the
pytest session), and every stage log `run` or `watch` writes under
<root>/logs starts with a header line naming the command, the script, the
run and the harness."""

import logging
import re
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

import logs
from cli.main import main
from cli.shell import run_bash, write_script
from cli.stages import ShellScript
from identity import harness_commit, harness_version
from logs import LOGGER_NAME
from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR

_GAEA_C7 = TEMPLATES_DIR / "gaea-c7.yaml"


def _formatted(message: str, name: str = "cli") -> str:
    """A record as the harness's own handler renders it."""
    (handler,) = [
        h
        for h in logging.getLogger(LOGGER_NAME).handlers
        if not isinstance(h, logging.FileHandler)
    ]
    record = logging.getLogger(f"{LOGGER_NAME}.{name}").makeRecord(
        f"{LOGGER_NAME}.{name}", logging.INFO, __file__, 1, message, (), None
    )
    assert handler.filter(record)
    return handler.format(record)


def _probe(tmp_path: Path) -> Path:
    return write_script(
        ShellScript(name="probe-stub", text="#!/bin/bash\necho hello\n"),
        tmp_path / "scripts",
        9,
    )


# ── log lines ───────────────────────────────────────────────────────────────


def test_the_pytest_session_tags_its_lines() -> None:
    assert logs.current_command() == "pytest"
    assert _formatted("hello").endswith(" INFO ufs-chem-assay.cli [pytest]: hello")


def test_each_command_tags_its_lines(tmp_path: Path) -> None:
    # report on a root with no effective config: it fails at once, but the
    # command is set before anything is logged.
    assert main(["report", f"--root-dir={tmp_path}"]) == 1
    assert logs.current_command() == "report"
    assert _formatted("x").endswith(" ufs-chem-assay.cli [report]: x")
    # Any logger in the namespace, not only the CLI's: submit-tests runs
    # inside a test stage script, and its apptests line says so.
    logs.set_command("submit-tests")
    assert _formatted("submitted", name="apptests").endswith(
        " ufs-chem-assay.apptests [submit-tests]: submitted"
    )


# ── stage log headers ───────────────────────────────────────────────────────


def test_a_stage_log_starts_with_the_command_and_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs.set_command("run")
    assert run_bash(_probe(tmp_path), tmp_path / "logs", run_id="01ABC") == 0
    (log,) = (tmp_path / "logs").glob("09-probe-stub-*.log")
    header, *output = log.read_text().splitlines()
    commit = harness_commit() or "unknown"
    assert re.fullmatch(
        rf"# ufs-chem-assay run: 09-probe-stub\.sh \(run 01ABC, harness "
        rf"{re.escape(harness_version())} {re.escape(commit)}, started "
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}\)",
        header,
    ), header
    assert output == ["hello"]
    # The header is the log file's alone: the terminal shows the script's
    # output and the CLI's "running ..." line.
    assert "# ufs-chem-assay" not in capsys.readouterr().out


def test_a_resume_log_names_watch(tmp_path: Path) -> None:
    logs.set_command("watch")
    run_bash(_probe(tmp_path), tmp_path / "logs")
    (log,) = (tmp_path / "logs").glob("09-probe-stub-*.log")
    header = log.read_text().splitlines()[0]
    assert header.startswith("# ufs-chem-assay watch: 09-probe-stub.sh (harness ")


def test_run_passes_its_run_id_to_every_stage_log(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.commands.run.run_bash", return_value=0)
    root = tmp_path / "root"
    code = main(
        [
            "run",
            f"--config-file={_GAEA_C7}",
            f"--root-dir={root}",
            "--application=all",
            "--stage=source",
            "--stage=build",
        ]
    )
    assert code == 0
    record = yaml.safe_load((root / "scripts" / "stages.yaml").read_text())
    (run_id,) = {o["run_id"] for o in record["outcomes"].values()}
    assert run_bash.call_count == 5
    assert {call.kwargs["run_id"] for call in run_bash.call_args_list} == {run_id}
