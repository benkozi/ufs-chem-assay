"""`report` and `watch` cover only what a run started: `run` records the
stages it selected, and a session or test run is covered when the last run
selected its stage, the stage record holds its script from any run, or its
root already has a ledger. A C7 run of source, build and test then reports
no driver session it never meant to start, and a later build-only run keeps
the test runs an earlier one started (and their staleness) in view."""

import logging
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

import apptests
from cli.main import main
from logs import LOGGER_NAME
from tests.ufs_chem_assay.run_configs import (
    TEMPLATES_DIR,
    load_run_config,
    run_config_file,
)
from tests.ufs_chem_assay.test_stage_outcomes import _PROVENANCE

_CLI_LOGGER = f"{LOGGER_NAME}.cli"
_GAEA_C7 = TEMPLATES_DIR / "gaea-c7.yaml"
_C7_RUN = ("--stage=source", "--stage=build", "--stage=test")


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _CLI_LOGGER]


def _run_c7(
    root: Path, mocker: MockerFixture, *args: str, application: str = "all"
) -> int:
    """`run` of the C7 template (CECE and CATChem, no sessions: section, so
    CECE has its nameless session, without harness.submit_only) with bash
    stubbed to pass."""
    mocker.patch("cli.commands.run.run_bash", return_value=0)
    return main(
        [
            "run",
            f"--config-file={_GAEA_C7}",
            f"--root-dir={root}",
            f"--application={application}",
            *args,
        ]
    )


def _report(
    root: Path, caplog: pytest.LogCaptureFixture, *args: str
) -> tuple[int, list[str]]:
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["report", f"--root-dir={root}", *args])
    return code, _messages(caplog)


def _watch(
    root: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture, *args: str
) -> tuple[int, list[str]]:
    mocker.patch("cli.sessions.queue_states", return_value={})
    mocker.patch("cli.commands.watch.time.sleep")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["watch", f"--root-dir={root}", *args])
    return code, _messages(caplog)


def _effective(root: Path) -> dict[str, object]:
    data = yaml.safe_load((root / "scripts" / "run-config.yaml").read_text())
    assert isinstance(data, dict)
    return data


def _evaluated(tests_root: Path, application: str, build: str | None = None) -> None:
    """A test run recorded and evaluated (exit 0) at tests_root."""
    apptests.record_local(
        tests_root,
        application=application,
        toolchain="oneapi",
        build=build,
        provenance=_PROVENANCE,
    )
    (tests_root / "tests.out").write_text("tic: x\ntoc: y\n")
    apptests.evaluate(tests_root, exit_status=0)


# ── the recorded selection ──────────────────────────────────────────────────


def test_run_records_the_stages_it_selected(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    root = tmp_path / "root"
    assert _run_c7(root, mocker, *_C7_RUN, "--dry-run") == 0
    assert _effective(root)["selected_stages"] == ["source", "build", "test"]
    assert _run_c7(root, mocker, "--stage=test", "--stage=build", "--dry-run") == 0
    assert _effective(root)["selected_stages"] == ["build", "test"]  # canonical order
    # A plain run selects every stage but resume (CECE alone: CATChem's
    # variants take no driver stage).
    assert _run_c7(root, mocker, "--dry-run", application="cece") == 0
    assert _effective(root)["selected_stages"] == [
        "source",
        "build",
        "test",
        "data",
        "harness",
    ]


def test_an_effective_config_without_the_selection_covers_every_stage(
    tmp_path: Path,
) -> None:
    # Written before the field existed (or by hand): empty, so everything is
    # covered as before.
    config = load_run_config(run_config_file(tmp_path))
    assert config.selected_stages == []


# ── report ──────────────────────────────────────────────────────────────────


def test_report_leaves_out_a_session_no_run_started(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # The C7 run: source, build and test. CECE's nameless session was never
    # part of it, so the report does not flag it as not submitted.
    root = tmp_path / "root"
    assert _run_c7(root, mocker, *_C7_RUN) == 0
    for name, build in (("cece", None), ("catchem", "compute"), ("catchem", "musica")):
        tests_root = root / "oneapi" / "tests" / name
        _evaluated(tests_root / build if build else tests_root, name, build)
    code, lines = _report(root, caplog)
    assert code == 0
    assert lines[0].startswith("report: 0 session(s), 3 test run(s), 5 stage(s) ")
    assert not any("oneapi/cece" in line for line in lines)
    assert lines[-1] == (
        "report: no issue in 0 session(s) and 3 test run(s) and 5 stage(s); exit 0"
    )


def test_report_keeps_the_test_runs_an_earlier_run_started(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    assert _run_c7(root, mocker, *_C7_RUN) == 0
    _evaluated(root / "oneapi" / "tests" / "cece", "cece")
    # A later rebuild of CECE alone selects only the build stage; the stage
    # record still holds the test stage, so the test run stays in the report,
    # now older than the build it stands on.
    assert _run_c7(root, mocker, "--stage=build", application="cece") == 0
    assert _effective(root)["selected_stages"] == ["build"]
    _code, lines = _report(root, caplog, "--only", "tests")
    assert any(line.startswith("tests cece/oneapi ") for line in lines)
    assert any("[tests-stale]" in line for line in lines)


def test_report_covers_a_root_that_has_a_ledger(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # A build-only run (no test stage selected or recorded), and a test run
    # made by hand (submit-tests / evaluate-tests): its ledger puts it in.
    root = tmp_path / "root"
    assert _run_c7(root, mocker, "--stage=source", "--stage=build") == 0
    _evaluated(root / "oneapi" / "tests" / "cece", "cece")
    _code, lines = _report(root, caplog, "--only", "tests")
    assert lines[0].startswith("report: 0 session(s), 1 test run(s), ")
    assert any(line.startswith("tests cece/oneapi ") for line in lines)


def test_report_covers_a_dry_run_selection(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # A dry run records nothing, but its selection says what a run of the
    # same scripts would start: the test runs, not the session.
    root = tmp_path / "root"
    assert _run_c7(root, mocker, *_C7_RUN, "--dry-run") == 0
    code, lines = _report(root, caplog)
    assert code == 1
    assert lines[0].startswith("report: 0 session(s), 3 test run(s) under ")


# ── watch ───────────────────────────────────────────────────────────────────


def test_watch_follows_the_test_runs_of_a_run_without_sessions(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # Before: C7's nameless session (no harness.submit_only) made a plain
    # `watch` refuse ("nothing to watch"). It was never started, so `watch`
    # follows the test runs alone.
    root = tmp_path / "root"
    assert _run_c7(root, mocker, *_C7_RUN) == 0
    for name, build in (("cece", None), ("catchem", "compute"), ("catchem", "musica")):
        tests_root = root / "oneapi" / "tests" / name
        _evaluated(tests_root / build if build else tests_root, name, build)
    code, lines = _watch(root, mocker, caplog)
    assert code == 0
    watching = next(line for line in lines if line.startswith("watching "))
    assert watching.startswith("watching 0 session(s) and 3 test run(s) ")


def test_watch_with_nothing_started_says_so(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    assert _run_c7(root, mocker, "--stage=source", "--stage=build") == 0
    code, lines = _watch(root, mocker, caplog)
    assert code == 1
    assert lines[-1] == (
        "nothing to watch: the last run (stages source, build) started no "
        "session or test run, and the stage record holds none"
    )


def test_watch_still_refuses_a_started_session_without_submit_only(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # A run that selects the harness stage does start the nameless session,
    # which evaluates in place: watch has nothing to finish, as before.
    root = tmp_path / "root"
    assert _run_c7(root, mocker, "--dry-run", application="cece") == 0
    code, lines = _watch(root, mocker, caplog)
    assert code == 1
    assert lines[-1].startswith(
        "nothing to watch: session(s) ['(nameless)'] do not set harness.submit_only"
    )
