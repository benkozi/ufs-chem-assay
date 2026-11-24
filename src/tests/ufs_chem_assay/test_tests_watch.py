"""The applications' own test runs as `watch`, `report`, `cancel` and
`record-benchmarks` see them: the test job in the same squeue tick as the
sessions' jobs, evaluated in-process when it leaves the queue; the report's
line and issues from the tests root's files; the scancel; the committed
per-application rows. Slurm is mocked; ledgers, reports and rows are real."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from benchmarks import SlurmAccounting, read_test_benchmarks_csv
from cli.main import main
from cli.run_report import IssueKind, SessionState, summarize_tests
from jobs import (
    TestJob,
    TestLedger,
    TestProvenance,
    read_test_ledger,
    write_test_ledger,
)
from logs import LOGGER_NAME
from models.conventions import CONVENTIONS
from platforms import Platform, Runtime
from tests.ufs_chem_assay.run_configs import run_config_file
from tests.ufs_chem_assay.test_watch import _ledger

_CLI_LOGGER = f"{LOGGER_NAME}.cli"
_TESTS_ENABLED: dict[str, object] = {
    "applications.cece.cmake_args": ["-DCMAKE_BUILD_TYPE=Debug", "-DENABLE_ESMF=ON"],
    "applications.cece.targets": ["all"],
    "applications.cece.tests": {"enabled": True, "ntasks": 4},
}
_PASSING = (
    "<testsuite>"
    '<testcase name="CeceCore.Init" time="1.5"/><testcase name="CeceIo.Write" time="0.5"/>'
    '<testcase name="CeceMpi.Four" status="notrun"><skipped/></testcase>'
    "</testsuite>"
)
_FAILING = _PASSING.replace(
    '<testcase name="CeceIo.Write" time="0.5"/>',
    '<testcase name="CeceIo.Write" time="0.5"><failure message=""/></testcase>',
)
_SUBMITTED = datetime.now(UTC)  # within its deadline: nothing is overdue


def _render(
    tmp_path: Path, mocker: MockerFixture, overrides: dict[str, object] | None
) -> Path:
    mocker.patch("cli.commands.run.run_bash")
    config = run_config_file(tmp_path, overrides=overrides, root_dir=tmp_path / "root")
    assert (
        main(["run", f"--config-file={config}", "--application=cece", "--dry-run"]) == 0
    )
    return tmp_path / "root"


@pytest.fixture
def rendered(tmp_path: Path, mocker: MockerFixture) -> Path:
    """A `run --dry-run` of the Ursa template with CECE's tests enabled."""
    mocker.patch("apptests.scancel")
    mocker.patch("cli.commands.watch.time.sleep")
    return _render(tmp_path, mocker, _TESTS_ENABLED)


def _tests_root(root: Path) -> Path:
    return root / "tests" / "cece"


def _submitted(root: Path, job_id: str = "4242", **overrides: object) -> TestLedger:
    """The ledger `submit-tests` leaves."""
    tests_root = _tests_root(root)
    tests_root.mkdir(parents=True, exist_ok=True)
    values: dict[str, object] = {
        "run_id": "01JTESTRUNTESTRUNTESTRUNTE",
        "application": "cece",
        "submitted_by": "me@ufe01",
        "provenance": TestProvenance(
            platform=Platform.URSA,
            runtime=Runtime.SLURM,
            modulefile="cece_ursa.intelllvm",
            build_type="Debug",
            cmake_args="-DCMAKE_BUILD_TYPE=Debug -DENABLE_ESMF=ON",
            application_commit="36da92e0f02ec9aa05d4261596927527350509b8",
            harness_version="0.1.0-rc.11",
            harness_commit="abc123",
        ),
        "job": TestJob(
            job_id=job_id,
            script=root / "scripts" / "04-test-cece.sbatch",
            timeout_s=300,
            queue_wait_s=3600,
            submitted_at=_SUBMITTED,
        ),
        "out_path": tests_root / f"tests-{job_id}.out",
        "junit_path": tests_root / f"tests-{job_id}.xml",
    }
    values.update(overrides)
    ledger = TestLedger.model_validate(values)
    write_test_ledger(ledger, tests_root)
    return ledger


def _finish(root: Path, junit: str | None = _PASSING, job_id: str = "4242") -> None:
    tests_root = _tests_root(root)
    (tests_root / f"tests-{job_id}.out").write_text(
        "tic: 2026-11-16T12:00:00Z\n"
        + "".join(f"line {i}\n" for i in range(30))
        + "toc: 2026-11-16T12:01:00Z\n"
    )
    if junit is not None:
        (tests_root / f"tests-{job_id}.xml").write_text(junit)


def _accounting(
    mocker: MockerFixture, state: str = "COMPLETED", exit_code: int = 0
) -> None:
    mocker.patch(
        "apptests.slurm_accounting",
        return_value=SlurmAccounting(
            job_id="4242",
            submit=None,
            start=None,
            end=None,
            elapsed_s=62.0,
            queue_s=5.0,
            max_rss_kb=1000,
            state=state,
            exit_code=exit_code,
        ),
    )


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _CLI_LOGGER]


def _watch(root: Path, *args: str) -> int:
    return main(["watch", f"--root-dir={root}", *args])


def _report(root: Path, *args: str) -> int:
    return main(["report", f"--root-dir={root}", *args])


# ── watch ────────────────────────────────────────────────────────────────────


def test_watch_once_reports_an_unsubmitted_test_run(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    states = mocker.patch("cli.sessions.queue_states", return_value={})
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--only", "tests", "--once") == 0
    states.assert_not_called()
    assert "tests cece: not submitted" in _messages(caplog)


def test_watch_waits_for_the_test_job_then_evaluates_it_in_process(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _submitted(rendered)
    _finish(rendered)
    _accounting(mocker)
    states = mocker.patch(
        "cli.sessions.queue_states",
        side_effect=[{"4242": "PENDING"}, {"4242": "RUNNING"}, {}],
    )
    again = mocker.patch("apptests.queue_states")
    run_bash = mocker.patch("cli.sessions.run_bash")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--only", "tests") == 0
    assert [call.args[0] for call in states.call_args_list] == [["4242"]] * 3
    again.assert_not_called()  # the tick's poll is the evidence: no second squeue
    run_bash.assert_not_called()  # no resume script: the evaluation is in-process
    messages = _messages(caplog)
    assert "tests cece: job 4242 queued (PENDING); not evaluated" in messages
    assert "tests cece: job 4242 running (RUNNING); not evaluated" in messages
    assert "tests cece: job 4242 done; evaluating" in messages
    assert "tests cece: evaluated (exit 0), 2 passed / 0 failed / 1 skipped" in messages
    assert messages[-1] == "report: no issue in 0 session(s) and 1 test run(s); exit 0"
    ledger = read_test_ledger(_tests_root(rendered))
    assert ledger.exit_status == 0
    (row,) = read_test_benchmarks_csv(_tests_root(rendered) / CONVENTIONS.benchmarks)
    assert (row.wall_s, row.elapsed_s) == (60.0, 62.0)


def test_watch_exit_follows_the_tests_verdict(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _submitted(rendered)
    _finish(rendered, _FAILING)
    _accounting(mocker, state="FAILED", exit_code=8)
    mocker.patch("cli.sessions.queue_states", return_value={})
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--only", "tests") == 1
    messages = _messages(caplog)
    assert "tests cece: evaluated (exit 8), 1 passed / 1 failed / 1 skipped" in messages
    assert any(m.startswith("  [tests-failed] 1 of 3 tests failed") for m in messages)
    assert "    failed (1): CeceIo.Write" in messages
    assert messages[-1] == (
        "report: 1 issue(s) in 1 of 0 session(s) and 1 test run(s); exit 1"
    )


def test_watch_polls_the_sessions_and_the_tests_in_one_squeue(
    rendered: Path, mocker: MockerFixture
) -> None:
    _ledger(rendered, "simple-maccity", ["10"])
    _ledger(rendered, "gcafs-anthro", ["20"])
    _submitted(rendered)
    states = mocker.patch(
        "cli.sessions.queue_states",
        return_value={"10": "RUNNING", "20": "PENDING", "4242": "PENDING"},
    )
    assert _watch(rendered, "--once") == 0
    states.assert_called_once()
    assert sorted(states.call_args.args[0]) == ["10", "20", "4242"]


def test_watch_leaves_an_evaluated_test_run_alone(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _submitted(rendered)
    _finish(rendered)
    _accounting(mocker)
    mocker.patch("apptests.queue_states", return_value={})
    assert main(["evaluate-tests", f"--root-dir={rendered}", "--application=cece"]) == 0
    states = mocker.patch("cli.sessions.queue_states")
    evaluate = mocker.patch("cli.sessions.apptests.evaluate")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--only", "tests") == 0
    states.assert_not_called()  # nothing unevaluated to ask about
    evaluate.assert_not_called()
    assert (
        "tests cece: evaluated (exit 0), 2 passed / 0 failed / 1 skipped"
        in _messages(caplog)
    )


def test_watch_polls_each_cluster_once(rendered: Path, mocker: MockerFixture) -> None:
    # Sessions' jobs recorded without a cluster and a test job on c7: one
    # squeue per cluster per tick, each naming its own.
    _ledger(rendered, "simple-maccity", ["10"])
    _ledger(rendered, "gcafs-anthro", ["20"])
    ledger = _submitted(rendered)
    assert ledger.job is not None
    write_test_ledger(
        ledger.model_copy(
            update={"job": ledger.job.model_copy(update={"cluster": "c7"})}
        ),
        _tests_root(rendered),
    )
    states = mocker.patch(
        "cli.sessions.queue_states",
        side_effect=[{"10": "RUNNING", "20": "PENDING"}, {"4242": "PENDING"}],
    )
    assert _watch(rendered, "--once") == 0
    assert [(sorted(c.args[0]), c.kwargs) for c in states.call_args_list] == [
        (["10", "20"], {"clusters": None}),
        (["4242"], {"clusters": "c7"}),
    ]


def test_watch_only_narrows_to_one_kind(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _ledger(rendered, "simple-maccity", ["10"])
    _submitted(rendered)
    states = mocker.patch("cli.sessions.queue_states", return_value={"10": "RUNNING"})
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--only", "sessions", "--once") == 0
    assert states.call_args.args[0] == ["10"]
    assert not any(m.startswith("tests cece") for m in _messages(caplog))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--only", "tests", "--once") == 0
    assert states.call_args.args[0] == ["4242"]
    assert not any("simple-maccity" in m for m in _messages(caplog))


def test_watch_reports_an_evaluation_that_failed(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _submitted(rendered)
    mocker.patch("cli.sessions.queue_states", return_value={})
    mocker.patch("cli.sessions.apptests.evaluate", side_effect=ValueError("no row"))
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--only", "tests") == 1
    messages = _messages(caplog)
    assert any("tests cece: evaluation failed (no row)" in m for m in messages)
    assert "evaluation failed: tests cece" in messages


def test_watch_reports_an_unreadable_ledger_instead_of_raising(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # A ledger that is there but does not parse is not "not submitted": it
    # is final at once, with the reason, and the watch goes on to its end.
    root = _tests_root(rendered)
    root.mkdir(parents=True)
    (root / CONVENTIONS.test_ledger).write_text("jobs: [\n")
    queue = mocker.patch("cli.sessions.queue_states", return_value={})
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--only", "tests") == 1
    messages = _messages(caplog)
    assert any(
        m.startswith("tests cece: evaluation failed (") and "not a test ledger" in m
        for m in messages
    )
    assert "evaluation failed: tests cece" in messages
    queue.assert_not_called()  # nothing to ask the queue about


def test_report_survives_an_unreadable_ledger(rendered: Path) -> None:
    root = _tests_root(rendered)
    root.mkdir(parents=True)
    (root / CONVENTIONS.test_ledger).write_text("jobs: []\n")
    summary = summarize_tests(root, application="cece")
    assert summary.state is SessionState.NOT_EVALUATED
    assert (summary.run_id, summary.exit_status, summary.job_id) == (None, None, None)
    (issue,) = summary.issues
    assert issue.kind is IssueKind.NOT_EVALUATED
    assert "not a test ledger" in issue.detail
    assert _report(rendered, "--only", "tests") == 1


def test_watch_gives_up_on_a_test_job_queued_past_its_deadline(
    rendered: Path, mocker: MockerFixture
) -> None:
    # Submitted long ago, still pending: cancelled at the tick, evaluated at
    # the next one (Slurm says CANCELLED).
    _submitted(
        rendered,
        job=TestJob(
            job_id="4242",
            script=rendered / "x.sbatch",
            timeout_s=300,
            queue_wait_s=60,
            submitted_at=datetime.now(UTC) - timedelta(hours=1),
        ),
    )
    mocker.patch("cli.sessions.queue_states", side_effect=[{"4242": "PENDING"}, {}])
    mocker.patch("apptests.queue_states", return_value={"4242": "PENDING"})
    scancel = mocker.patch("apptests.scancel")
    _accounting(mocker, state="CANCELLED by 1234")
    assert _watch(rendered, "--only", "tests") == 1
    scancel.assert_called_once_with(["4242"], clusters=None)
    assert read_test_ledger(_tests_root(rendered)).exit_status == 1


def test_watch_clean_errors_for_tests(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    plain = _render(tmp_path, mocker, None)  # tests off, as shipped
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert _watch(plain, "--only", "tests") == 1
    assert "nothing to watch: no application has tests enabled" in caplog.text
    # Sessions that evaluate in place leave nothing to watch but the tests.
    (tmp_path / "inplace").mkdir()
    inplace = _render(
        tmp_path / "inplace",
        mocker,
        {**_TESTS_ENABLED, "harness.submit_only": False},
    )
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert _watch(inplace) == 1
    assert "--only tests" in caplog.text


# ── report ───────────────────────────────────────────────────────────────────


def test_summarize_tests_reads_the_roots_files(
    rendered: Path, mocker: MockerFixture
) -> None:
    root = _tests_root(rendered)
    none = summarize_tests(root, application="cece")
    assert none.state is SessionState.NOT_SUBMITTED
    assert [issue.kind for issue in none.issues] == [IssueKind.NOT_SUBMITTED]
    assert not none.ok

    _submitted(rendered)
    waiting = summarize_tests(root, application="cece")
    assert waiting.state is SessionState.NOT_EVALUATED
    assert waiting.job_id == "4242"
    assert [issue.kind for issue in waiting.issues] == [IssueKind.NOT_EVALUATED]
    assert "ufs-chem-assay watch" in waiting.issues[0].detail

    _finish(rendered)
    _accounting(mocker)
    mocker.patch("apptests.queue_states", return_value={})
    assert main(["evaluate-tests", f"--root-dir={rendered}", "--application=cece"]) == 0
    done = summarize_tests(root, application="cece")
    assert done.state is SessionState.EVALUATED
    assert done.ok
    assert done.issues == []
    assert done.tests is not None
    assert (done.tests.passed, done.tests.failed, done.tests.skipped) == (2, 0, 1)
    assert done.wall_s == 60.0
    assert done.out == root / "tests-4242.out"
    assert done.junit == root / "tests-4242.xml"
    assert done.label == "tests cece"


def test_report_names_the_failed_tests_and_tails_the_output(
    rendered: Path,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    _submitted(rendered)
    _finish(rendered, _FAILING)
    _accounting(mocker, state="FAILED", exit_code=8)
    mocker.patch("apptests.queue_states", return_value={})
    assert main(["evaluate-tests", f"--root-dir={rendered}", "--application=cece"]) == 0
    written = tmp_path / "report.yaml"
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert (
            _report(rendered, "--only", "tests", "--tail", "3", f"--write={written}")
            == 1
        )
    messages = _messages(caplog)
    root = _tests_root(rendered)
    assert messages[0].startswith(
        f"report: 0 session(s), 1 test run(s) under {rendered}"
    )
    assert (
        "tests cece  run 01JTESTRUNTESTRUNTESTRUNTE  evaluated (exit 8)  "
        "1 passed / 1 failed / 1 skipped  60 s"
    ) in messages
    assert "  [tests-failed] 1 of 3 tests failed  job 4242 FAILED exit 8" in messages
    assert "    failed (1): CeceIo.Write" in messages
    assert "    .out tail (3):" in messages
    assert "      | toc: 2026-11-16T12:01:00Z" in messages
    assert f"    see: {root}/tests-4242.out, {root}/tests-4242.xml" in messages
    assert messages[-2] == (
        "report: 1 issue(s) in 1 of 0 session(s) and 1 test run(s); exit 1"
    )
    dumped = yaml.safe_load(written.read_text())
    assert dumped["sessions"] == []
    (tests,) = dumped["tests"]
    assert tests["application"] == "cece"
    assert tests["issues"][0]["kind"] == "tests-failed"
    assert tests["issues"][0]["failed_tests"] == ["CeceIo.Write"]


def test_report_tells_a_dead_job_from_failing_tests(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # Killed by its time limit: no junit report, nothing to name.
    _submitted(rendered)
    _finish(rendered, None)
    _accounting(mocker, state="TIMEOUT")
    mocker.patch("apptests.queue_states", return_value={})
    assert main(["evaluate-tests", f"--root-dir={rendered}", "--application=cece"]) == 0
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _report(rendered, "--only", "tests", "--quiet") == 1
    messages = _messages(caplog)
    assert (
        "tests cece  run 01JTESTRUNTESTRUNTESTRUNTE  evaluated (exit 1)  "
        "no junit report  60 s"
    ) in messages
    assert not any(m.startswith("  [") for m in messages)  # --quiet
    summary = summarize_tests(_tests_root(rendered), application="cece")
    (issue,) = summary.issues
    assert issue.kind is IssueKind.TESTS_JOB_FAILED
    assert issue.detail == "the test job ended TIMEOUT (exit 1) with no junit report"
    assert (issue.job_id, issue.slurm_state, issue.exit_code) == ("4242", "TIMEOUT", 1)
    assert issue.out_tail[-1] == "toc: 2026-11-16T12:01:00Z"


def test_report_survives_files_changed_since_the_evaluation(
    rendered: Path, mocker: MockerFixture
) -> None:
    # The ledger's verdict and counts stand; what decorates them is read
    # best-effort: an unreadable rows file gives no wall time or Slurm
    # state, an unreadable junit report no names.
    _submitted(rendered)
    _finish(rendered, _FAILING)
    _accounting(mocker, state="FAILED", exit_code=8)
    mocker.patch("apptests.queue_states", return_value={})
    assert main(["evaluate-tests", f"--root-dir={rendered}", "--application=cece"]) == 0
    root = _tests_root(rendered)
    (root / CONVENTIONS.benchmarks).write_text("not,the,columns\n")
    (root / "tests-4242.xml").write_text("<testsuite><testcase")
    summary = summarize_tests(root, application="cece")
    (issue,) = summary.issues
    assert issue.kind is IssueKind.TESTS_FAILED
    assert issue.detail == "1 of 3 tests failed"
    assert issue.failed_tests == []
    assert (summary.wall_s, issue.slurm_state) == (None, None)
    assert summary.exit_status == 8


def test_report_covers_sessions_and_tests_by_default(
    rendered: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _report(rendered) == 1  # nothing ran
    messages = _messages(caplog)
    assert messages[0].startswith(
        f"report: 2 session(s), 1 test run(s) under {rendered}"
    )
    assert any(m.startswith("tests cece") and "not submitted" in m for m in messages)
    assert messages[-1] == (
        "report: 3 issue(s) in 3 of 2 session(s) and 1 test run(s); exit 1"
    )
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _report(rendered, "--only", "sessions") == 1
    assert _messages(caplog)[-1] == "report: 2 issue(s) in 2 of 2 session(s); exit 1"


# ── cancel ───────────────────────────────────────────────────────────────────


def test_cancel_scancels_a_queued_test_job(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = _tests_root(rendered)
    _submitted(rendered)
    mocker.patch("apptests.queue_states", return_value={"4242": "PENDING"})
    scancel = mocker.patch("apptests.scancel")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["cancel", f"--tests-root={root}", "--dry-run"]) == 0
    scancel.assert_not_called()
    assert "dry run: would cancel 1 job(s): 4242" in _messages(caplog)[-1]
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["cancel", f"--tests-root={root}"]) == 0
    scancel.assert_called_once_with(["4242"], clusters=None)
    assert "cancelled 1 job(s): 4242" in _messages(caplog)[-1]
    mocker.patch("apptests.queue_states", return_value={})
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["cancel", f"--tests-root={root}"]) == 0
    assert "no job in the queue" in _messages(caplog)[-1]


def test_cancel_of_a_root_without_a_readable_ledger_is_a_no_op(
    rendered: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = _tests_root(rendered)
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["cancel", f"--tests-root={root}"]) == 0  # no ledger at all
    assert "no job in the queue" in _messages(caplog)[-1]
    root.mkdir(parents=True)
    (root / CONVENTIONS.test_ledger).write_text("jobs: []\n")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["cancel", f"--tests-root={root}"]) == 0
    assert "not a test ledger" in _messages(caplog)[-1]
    assert "nothing to cancel" in _messages(caplog)[-1]


def test_cancel_takes_one_kind_of_root() -> None:
    for argv in (["cancel"], ["cancel", "--output-root=a", "--tests-root=b"]):
        with pytest.raises(SystemExit) as usage:
            main(argv)
        assert usage.value.code == 2


# ── record-benchmarks ────────────────────────────────────────────────────────


def test_record_benchmarks_appends_a_tests_roots_rows_per_application(
    rendered: Path,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    committed = tmp_path / "config"
    mocker.patch("cli.commands.record_benchmarks.BUILTIN_CONFIG_DIR", committed)
    root = _tests_root(rendered)
    target = committed / "cece" / "tests-benchmarks.csv"
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert main(["record-benchmarks", f"--tests-root={root}"]) == 1
    assert "no benchmarks.csv" in caplog.text  # nothing evaluated there yet
    _submitted(rendered)
    _finish(rendered)
    _accounting(mocker)
    mocker.patch("apptests.queue_states", return_value={})
    assert main(["evaluate-tests", f"--root-dir={rendered}", "--application=cece"]) == 0
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["record-benchmarks", f"--tests-root={root}", "--dry-run"]) == 0
    assert not target.exists()
    assert (
        f"dry run: would append test run 01JTESTRUNTESTRUNTESTRUNTE to {target}"
        in (_messages(caplog)[-1])
    )
    assert main(["record-benchmarks", f"--tests-root={root}"]) == 0
    assert read_test_benchmarks_csv(target) == read_test_benchmarks_csv(
        root / CONVENTIONS.benchmarks
    )
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["record-benchmarks", f"--tests-root={root}"]) == 0  # idempotent
    assert "0 appended, 1 already recorded" in _messages(caplog)[-1]
    assert len(read_test_benchmarks_csv(target)) == 1


def test_record_benchmarks_reports_unreadable_and_unappendable_rows(
    rendered: Path,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    mocker.patch(
        "cli.commands.record_benchmarks.BUILTIN_CONFIG_DIR", tmp_path / "config"
    )
    root = _tests_root(rendered)
    root.mkdir(parents=True)
    (root / CONVENTIONS.benchmarks).write_text("not,the,columns\n")
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert main(["record-benchmarks", f"--tests-root={root}"]) == 1
    assert "is not the test benchmark columns" in caplog.text
    (root / CONVENTIONS.benchmarks).unlink()
    _submitted(rendered)
    _finish(rendered)
    _accounting(mocker)
    mocker.patch("apptests.queue_states", return_value={})
    assert main(["evaluate-tests", f"--root-dir={rendered}", "--application=cece"]) == 0
    mocker.patch(
        "cli.commands.record_benchmarks.append_test_benchmarks",
        side_effect=ValueError("reload after the append failed; restored"),
    )
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert main(["record-benchmarks", f"--tests-root={root}"]) == 1
    assert "restored" in caplog.text


def test_record_benchmarks_takes_one_kind_of_root() -> None:
    for argv in (
        ["record-benchmarks"],
        ["record-benchmarks", "--output-root=a", "--tests-root=b"],
    ):
        with pytest.raises(SystemExit) as usage:
            main(argv)
        assert usage.value.code == 2
