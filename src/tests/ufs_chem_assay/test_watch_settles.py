"""`watch` never waits forever for something that will not be submitted: it
watches only the applications the last `run` selected, settles a test run or
session at once when the stage record says why it was never submitted, and
gives up on any other one after a number of checks — then ends with the
report, exit 1."""

import logging
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from pytest_mock import MockerFixture

import apptests
from cli.main import main
from logs import LOGGER_NAME
from tests.ufs_chem_assay.run_configs import (
    TEMPLATES_DIR,
    load_run_config,
    run_config_file,
)
from tests.ufs_chem_assay.test_stage_outcomes import _PROVENANCE, _run

_CLI_LOGGER = f"{LOGGER_NAME}.cli"
_KOKKOS = TEMPLATES_DIR / "ursa-kokkos.yaml"


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _CLI_LOGGER]


def _watch(
    root: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture, *args: str
) -> tuple[int, list[str], int]:
    """watch under root with squeue answering nothing queued and the sleep
    stubbed; the exit code, the CLI's messages and how often it slept."""
    mocker.patch("cli.sessions.queue_states", return_value={})
    sleep = mocker.patch("cli.commands.watch.time.sleep")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["watch", f"--root-dir={root}", *args])
    return code, _messages(caplog), sleep.call_count


# ── 1. the applications the last run selected ────────────────────────────────


def test_run_records_its_applications_and_watch_and_report_follow(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    mocker.patch("cli.commands.run.run_bash", return_value=0)
    command = [
        "run",
        f"--config-file={_KOKKOS}",
        f"--root-dir={root}",
        "--toolchain=oneapi",
    ]
    assert (
        main(
            [
                *command,
                "--application=catchem",
                "--stage=build",
                "--stage=test",
                "--dry-run",
            ]
        )
        == 0
    )
    effective = yaml.safe_load((root / "scripts" / "run-config.yaml").read_text())
    assert effective["selected_applications"] == ["catchem"]
    # watch covers CATChem's test runs only: CECE's was never part of the run.
    _code, lines, _ = _watch(
        root, mocker, caplog, "--only", "tests", "--not-submitted-ticks", "1"
    )
    watching = next(line for line in lines if line.startswith("watching "))
    assert "tests catchem/oneapi/compute" in watching
    assert "cece" not in watching
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        main(["report", f"--root-dir={root}", "--only", "tests"])
    assert not any("tests cece" in line for line in _messages(caplog))
    # --application still widens or narrows it.
    _code, lines, _ = _watch(
        root,
        mocker,
        caplog,
        "--only",
        "tests",
        "--application",
        "cece",
        "--not-submitted-ticks",
        "1",
    )
    assert "tests cece/oneapi" in next(
        line for line in lines if line.startswith("watching ")
    )


def test_selected_applications_must_be_configured(tmp_path: Path) -> None:
    path = run_config_file(tmp_path, overrides={"selected_applications": ["nope"]})
    with pytest.raises(ValidationError, match=r"selected_applications: \['nope'\]"):
        load_run_config(path)


# ── 2. settled at once when the stage record explains it ────────────────────


def test_a_test_run_whose_build_failed_settles_on_the_first_check(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    _run(root, mocker, {"02-build-cece-oneapi.sh": 2}, "--stage=build", "--stage=test")
    code, lines, slept = _watch(
        root, mocker, caplog, "--only", "tests", "--application", "cece"
    )
    assert code == 1
    assert slept == 0  # nothing to wait for
    assert (
        "tests cece/oneapi: not submitted (02-build-cece-oneapi.sh failed (exit 2))"
    ) in lines
    text = "\n".join(lines)
    assert (
        "[stage-failed] 02-build-cece-oneapi.sh failed (exit 2)" in text
    )  # the report
    assert any(line.startswith("not submitted: tests cece/oneapi") for line in lines)


def test_a_session_whose_build_failed_settles_on_the_first_check(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    path = run_config_file(tmp_path, root_dir=root)
    mocker.patch(
        "cli.commands.run.run_bash",
        side_effect=lambda p, _l: 2 if "02-build" in p.name else 0,
    )
    main(["run", f"--config-file={path}", "--application=cece"])
    code, lines, slept = _watch(root, mocker, caplog, "--only", "sessions")
    assert code == 1
    assert slept == 0
    assert "simple-maccity: not submitted (02-build-cece.sh failed (exit 2))" in lines


# ── 3. a limit on plain "not submitted" ─────────────────────────────────────


def test_an_unexplained_test_run_is_given_up_after_the_limit(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    _run(
        root, mocker, {}, "--stage=build", "--stage=test", "--dry-run"
    )  # rendered, never run
    code, lines, slept = _watch(
        root,
        mocker,
        caplog,
        "--only",
        "tests",
        "--application",
        "cece",
        "--not-submitted-ticks",
        "3",
    )
    assert code == 1
    assert slept == 2  # three checks, two waits between them
    assert "tests cece/oneapi: not submitted" in lines
    assert (
        "tests cece/oneapi: not submitted (still not submitted after 3 checks)" in lines
    )
    assert any(line.startswith("report: ") for line in lines)


def test_a_test_run_submitted_before_the_limit_is_followed(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    _run(root, mocker, {}, "--stage=build", "--stage=test", "--dry-run")
    tests_root = root / "oneapi" / "tests" / "cece"
    calls = {"n": 0}

    def wait(_seconds: float) -> None:
        # The test stage of a run started alongside records its run during
        # the second wait: watch evaluates it instead of giving up. (With no
        # job to ask squeue about, the sleep is where time passes.)
        calls["n"] += 1
        if calls["n"] == 2:
            apptests.record_local(
                tests_root,
                application="cece",
                toolchain="oneapi",
                provenance=_PROVENANCE,
            )
            (tests_root / "tests.out").write_text("tic: x\ntoc: y\n")
            apptests.evaluate(tests_root, exit_status=0)

    sleep = mocker.patch("cli.commands.watch.time.sleep", side_effect=wait)
    mocker.patch("cli.sessions.queue_states", return_value={})
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        main(
            [
                "watch",
                f"--root-dir={root}",
                "--only",
                "tests",
                "--application",
                "cece",
                "--not-submitted-ticks",
                "5",
            ]
        )
    lines = _messages(caplog)
    assert not any("still not submitted" in line for line in lines)
    assert any(
        line.startswith("tests cece/oneapi: evaluated (exit 0)") for line in lines
    )
    assert sleep.call_count == 2


def test_the_limit_must_be_positive(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    _run(root, mocker, {}, "--stage=build", "--stage=test", "--dry-run")
    code, lines, _ = _watch(
        root, mocker, caplog, "--only", "tests", "--not-submitted-ticks", "0"
    )
    assert code == 1
    assert "--not-submitted-ticks must be a positive number of checks; got 0" in lines
