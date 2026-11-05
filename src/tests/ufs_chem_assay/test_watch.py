"""`ufs-chem-assay watch`, `cancel` and `report`: the monitor loop over the
effective config's sessions — one squeue per tick, the resume scripts run
serially as sessions' jobs finish, exit = the sessions' pytest statuses,
the run report at the end — the scancel of a session's live jobs, and the
report command over the same roots. Slurm (queue_states) and the resume
scripts (run_bash) are mocked; the ledgers and the roots are real files."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from cli.main import main
from cli.run_report import RunReport
from cli.sessions import PendingState, SessionState, WatchedSession, tick
from jobs import JobLedger, SubmittedJob, read_ledger, write_ledger
from logs import LOGGER_NAME
from models.conventions import CONVENTIONS
from tests.ufs_chem_assay.report_roots import (
    COMBOS,
    RUN_ID,
    benchmark,
    clean_root,
    manifest,
    report_rows,
    write_log,
    write_outcomes,
    write_run,
    write_session_ledger,
)
from tests.ufs_chem_assay.run_configs import run_config_file

_CLI_LOGGER = f"{LOGGER_NAME}.cli"


def _job(combo: str, combo_id: str, job_id: str, root: Path) -> SubmittedJob:
    return SubmittedJob(
        suite="s",
        combo=combo,
        combo_id=combo_id,
        job_id=job_id,
        script=root / combo_id / f"{combo_id}.sbatch",
        out_path=root / combo_id / f"{combo_id}.out",
        timeout_s=300,
        queue_wait_s=3600,
        submitted_at=datetime(2026, 11, 4, 10, tzinfo=UTC),
    )


@pytest.fixture
def rendered(tmp_path: Path, mocker: MockerFixture) -> Path:
    """A `run --dry-run` of the Ursa template at tmp_path/root: the
    effective config and the scripts are on disk, nothing executed."""
    mocker.patch("cli.commands.run.run_bash")
    config = run_config_file(tmp_path, root_dir=tmp_path / "root")
    assert main(["run", f"--config-file={config}", "--dry-run"]) == 0
    return tmp_path / "root"


def _session_root(root: Path, session: str) -> Path:
    # <checkout>/<harness.output_root>/<session>: the template's relative
    # output root resolves against the CECE checkout under root_dir.
    return root / "CECE" / "ufs-chem-assay-output" / session


def _ledger(root: Path, session: str, job_ids: list[str]) -> Path:
    session_root = _session_root(root, session)
    session_root.mkdir(parents=True, exist_ok=True)
    ledger = JobLedger(
        run_id=f"RUN-{session}",
        application="cece",
        submitted_by="t@h",
        jobs=[
            _job(f"c{i}", f"ID{session}{i}", job_id, session_root)
            for i, job_id in enumerate(job_ids)
        ],
    )
    return write_ledger(ledger, session_root)


def _evaluating(exit_status: int = 0) -> Callable[[Path, Path], int]:
    """A run_bash stand-in for a resume script: marks the session's ledger
    evaluated, as the resume session's sessionfinish does."""

    def run(path: Path, _logs_dir: Path) -> int:
        session = path.stem.split("-cece-", 1)[1]
        root = _session_root(path.parents[1], session)
        ledger = read_ledger(root)
        ledger.evaluated_at = datetime.now(UTC)
        ledger.exit_status = exit_status
        write_ledger(ledger, root)
        return 0

    return run


def _watch(root: Path, *args: str) -> int:
    return main(["watch", f"--root-dir={root}", *args])


def test_watch_once_reports_unsubmitted_sessions(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    run_bash = mocker.patch("cli.sessions.run_bash")
    states = mocker.patch("cli.sessions.queue_states", return_value={})
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--once") == 0
    run_bash.assert_not_called()
    states.assert_not_called()  # nothing to ask about
    messages = [r.getMessage() for r in caplog.records]
    assert any("simple-maccity: not submitted" in m for m in messages)
    assert any("gcafs-anthro: not submitted" in m for m in messages)


def test_watch_evaluates_each_session_when_its_jobs_are_gone(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _ledger(rendered, "simple-maccity", ["10", "11", "12"])
    _ledger(rendered, "gcafs-anthro", ["20"])
    # Tick 1: gcafs-anthro's job is still running, the maccity jobs are
    # gone; tick 2: everything gone.
    states = mocker.patch(
        "cli.sessions.queue_states", side_effect=[{"20": "RUNNING"}, {}]
    )
    run_bash = mocker.patch("cli.sessions.run_bash", side_effect=_evaluating())
    sleep = mocker.patch("cli.commands.watch.time.sleep")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--interval", "7") == 0
    # One squeue per tick, every session's ids together.
    assert states.call_count == 2
    assert sorted(states.call_args_list[0].args[0]) == ["10", "11", "12", "20"]
    assert sorted(states.call_args_list[1].args[0]) == ["20"]  # evaluated ones drop out
    ran = [call.args[0].name for call in run_bash.call_args_list]
    assert ran == ["06-resume-cece-simple-maccity.sh", "06-resume-cece-gcafs-anthro.sh"]
    assert all(call.args[1] == rendered / "logs" for call in run_bash.call_args_list)
    sleep.assert_called_once_with(7)
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "gcafs-anthro: 1 job(s): 0 queued, 1 running, 0 done; not evaluated" in m
        for m in messages
    )
    assert any(
        "simple-maccity: 3 job(s): 0 queued, 0 running, 3 done; not evaluated" in m
        for m in messages
    )
    assert any("simple-maccity: evaluated (exit 0)" in m for m in messages)
    # The ending is the run report over the watched roots (here: ledgers
    # without run.yaml, so every session reads as not submitted).
    assert not any("every session evaluated" in m for m in messages)
    assert messages[-1] == "report: 2 issue(s) in 2 of 2 session(s); exit 1"
    assert any(m.startswith(f"report: 2 session(s) under {rendered}") for m in messages)
    assert any(m.startswith("simple-maccity  not submitted") for m in messages)
    for session in ("simple-maccity", "gcafs-anthro"):
        assert read_ledger(_session_root(rendered, session)).evaluated_at is not None


def test_watch_exit_follows_the_sessions_statuses_and_skips_evaluated_ones(
    rendered: Path, mocker: MockerFixture
) -> None:
    _ledger(rendered, "simple-maccity", ["10"])
    _ledger(rendered, "gcafs-anthro", ["20"])
    mocker.patch("cli.sessions.queue_states", return_value={})
    run_bash = mocker.patch("cli.sessions.run_bash", side_effect=_evaluating(1))
    mocker.patch("cli.commands.watch.time.sleep")
    assert _watch(rendered) == 1  # a failed test somewhere
    assert run_bash.call_count == 2
    # Already evaluated: nothing to do, the statuses are reported.
    run_bash.reset_mock()
    assert _watch(rendered) == 1
    run_bash.assert_not_called()


def test_watch_reports_a_resume_that_died_and_moves_on(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _ledger(rendered, "simple-maccity", ["10"])
    _ledger(rendered, "gcafs-anthro", ["20"])
    mocker.patch("cli.sessions.queue_states", return_value={})
    # The maccity resume dies (ledger untouched); gcafs-anthro evaluates.
    evaluating = _evaluating()

    def flaky(path: Path, logs_dir: Path) -> int:
        if "simple-maccity" in path.name:
            return 3
        return evaluating(path, logs_dir)

    run_bash = mocker.patch("cli.sessions.run_bash", side_effect=flaky)
    mocker.patch("cli.commands.watch.time.sleep")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered) == 1
    assert run_bash.call_count == 2  # one attempt per session per watch
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("simple-maccity: evaluation failed (exit 3)" in m for m in errors)
    assert any("06-resume-cece-simple-maccity" in m for m in errors)
    assert read_ledger(_session_root(rendered, "simple-maccity")).evaluated_at is None


def test_watch_narrows_to_sessions_and_applications(
    rendered: Path, mocker: MockerFixture
) -> None:
    _ledger(rendered, "gcafs-anthro", ["20"])
    mocker.patch("cli.sessions.queue_states", return_value={})
    run_bash = mocker.patch("cli.sessions.run_bash", side_effect=_evaluating())
    mocker.patch("cli.commands.watch.time.sleep")
    assert _watch(rendered, "--session", "gcafs-anthro", "--application", "cece") == 0
    ran = [call.args[0].name for call in run_bash.call_args_list]
    assert ran == ["06-resume-cece-gcafs-anthro.sh"]


def test_watch_waits_for_an_unsubmitted_session(
    rendered: Path, mocker: MockerFixture
) -> None:
    # The harness stage may be about to submit it: wait, like a queued job.
    _ledger(rendered, "gcafs-anthro", ["20"])
    mocker.patch("cli.sessions.queue_states", return_value={})
    run_bash = mocker.patch("cli.sessions.run_bash", side_effect=_evaluating())
    ticks = 0

    def submit_later(_seconds: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            _ledger(rendered, "simple-maccity", ["10"])

    mocker.patch("cli.commands.watch.time.sleep", side_effect=submit_later)
    assert _watch(rendered) == 0
    assert ticks == 2
    ran = [call.args[0].name for call in run_bash.call_args_list]
    assert ran == ["06-resume-cece-gcafs-anthro.sh", "06-resume-cece-simple-maccity.sh"]


def test_watch_clean_errors(
    tmp_path: Path,
    rendered: Path,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mocker.patch("cli.sessions.queue_states", return_value={})
    mocker.patch("cli.commands.watch.time.sleep")
    # No effective config: `run` writes it.
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(tmp_path / "elsewhere") == 1
    assert "ufs-chem-assay run" in caplog.records[-1].getMessage()
    assert CONVENTIONS.effective_config in caplog.records[-1].getMessage()
    # An unconfigured session or application.
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--session", "nope") == 1
    assert "nope" in caplog.records[-1].getMessage()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--application", "catchem") == 1
    assert "catchem" in caplog.records[-1].getMessage()
    # A session whose resume script is missing: `run --dry-run` renders it.
    _ledger(rendered, "simple-maccity", ["10"])
    (rendered / "scripts" / "06-resume-cece-simple-maccity.sh").unlink()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--session", "simple-maccity") == 1
    assert "run --dry-run" in caplog.records[-1].getMessage()
    # A config without submit_only has nothing to watch.
    local = run_config_file(tmp_path, "local.yaml", root_dir=tmp_path / "local-root")
    mocker.patch("cli.commands.run.run_bash")
    assert main(["run", f"--config-file={local}", "--dry-run"]) == 0
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(tmp_path / "local-root") == 1
    assert "submit_only" in caplog.records[-1].getMessage()


def test_tick_counts_every_pending_state_as_queued(tmp_path: Path) -> None:
    # squeue's not-started states — and the poll's own UNKNOWN — are queued;
    # anything else still in the queue is running; an id gone is done.
    pending = [
        PendingState.PENDING,
        PendingState.CONFIGURING,
        PendingState.REQUEUED,
        PendingState.REQUEUE_HOLD,
        PendingState.SUSPENDED,
        PendingState.UNKNOWN,
    ]
    assert set(pending) == set(PendingState)
    root = tmp_path / "root"
    root.mkdir()
    ids = [str(i) for i in range(len(pending) + 3)]
    ledger = JobLedger(
        run_id="R",
        application="cece",
        submitted_by="t@h",
        jobs=[_job(f"c{i}", f"ID{i}", job_id, root) for i, job_id in enumerate(ids)],
    )
    watched = WatchedSession(
        application="cece", session="s", root=root, script=root / "resume.sh"
    )
    states = dict(zip(ids, [*pending, "RUNNING", "COMPLETING"], strict=False))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("cli.sessions.queue_states", lambda _ids: states)
        report = tick([watched], {root: ledger})[root]
    assert report.state is SessionState.WAITING
    assert (report.queued, report.running, report.done) == (6, 2, 1)


def test_watch_refuses_a_non_positive_interval(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    states = mocker.patch("cli.sessions.queue_states", return_value={})
    sleep = mocker.patch("cli.commands.watch.time.sleep")
    for interval in ("0", "-5"):
        with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
            assert _watch(rendered, "--interval", interval) == 1
        assert "--interval must be a positive" in caplog.records[-1].getMessage()
    states.assert_not_called()
    sleep.assert_not_called()


def test_watch_interrupt_ends_the_tick(rendered: Path, mocker: MockerFixture) -> None:
    _ledger(rendered, "gcafs-anthro", ["20"])
    mocker.patch("cli.sessions.queue_states", return_value={"20": "PENDING"})
    mocker.patch("cli.commands.watch.time.sleep", side_effect=KeyboardInterrupt)
    assert _watch(rendered) == 130


def test_watch_ends_with_the_report_of_the_evaluated_roots(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # A resume that leaves a clean root behind: the report says so.
    _ledger(rendered, "gcafs-anthro", ["20"])
    mocker.patch("cli.sessions.queue_states", return_value={})

    def resume(_path: Path, _logs_dir: Path) -> int:
        clean_root(_session_root(rendered, "gcafs-anthro"))
        return 0

    mocker.patch("cli.sessions.run_bash", side_effect=resume)
    mocker.patch("cli.commands.watch.time.sleep")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _watch(rendered, "--session", "gcafs-anthro") == 0
    messages = [r.getMessage() for r in caplog.records]
    assert messages[-1] == "report: no issue in 1 session(s); exit 0"
    assert any(
        m.startswith(f"gcafs-anthro  run {RUN_ID}  evaluated (exit 0)  tests 12 passed")
        for m in messages
    )


# ── report ───────────────────────────────────────────────────────────────────


def _report(root: Path, *args: str) -> int:
    return main(["report", f"--root-dir={root}", *args])


def _cli_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The CLI's own lines (the fabricated roots' writers log too)."""
    return [r.getMessage() for r in caplog.records if r.name == _CLI_LOGGER]


def test_report_over_the_effective_config(
    rendered: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Nothing submitted yet: two not-submitted sessions, exit 1.
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _report(rendered) == 1
    messages = [r.getMessage() for r in caplog.records]
    assert messages[0].startswith(f"report: 2 session(s) under {rendered}")
    assert messages[1] == "simple-maccity  not submitted"
    assert messages[2].startswith("  [not-submitted] no run.yaml at ")
    assert messages[3] == "gcafs-anthro    not submitted"
    assert messages[-1] == "report: 2 issue(s) in 2 of 2 session(s); exit 1"
    # One clean session, narrowed to: exit 0; the other still counts without it.
    clean_root(_session_root(rendered, "simple-maccity"))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert (
            _report(rendered, "--session", "simple-maccity", "--application", "cece")
            == 0
        )
    messages = [r.getMessage() for r in caplog.records]
    assert messages[-1] == "report: no issue in 1 session(s); exit 0"
    assert _report(rendered) == 1


def test_report_reads_the_logs_and_writes_yaml(
    rendered: Path, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    root = _session_root(rendered, "simple-maccity")
    write_run(root, manifest(), COMBOS[:1])
    write_session_ledger(root, COMBOS[:1], exit_status=1)
    bilinear = COMBOS[0]
    rows = report_rows(bilinear, failed=("test_nc_file_count",))
    write_outcomes(root, rows, [benchmark(bilinear, "100")])
    log = write_log(
        rendered / "logs",
        "06-resume-cece-simple-maccity-20261105-073000.log",
        f"FAILED test_driver_combos.py::test_nc_file_count[{bilinear.name}] - "
        "AssertionError: 2 != 3\n",
    )
    out = tmp_path / "keep" / "report.yaml"
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _report(rendered, "--session", "simple-maccity", f"--write={out}") == 1
    messages = [r.getMessage() for r in caplog.records]
    assert "    test_nc_file_count: AssertionError: 2 != 3" in messages
    assert f"    see: {log}" in messages
    assert messages[-2] == "report: 1 issue(s) in 1 of 1 session(s); exit 1"
    assert messages[-1] == f"wrote {out}"
    with open(out) as f:
        loaded = RunReport.model_validate(yaml.safe_load(f))
    assert loaded.root_dir == rendered
    assert loaded.sessions[0].log == log
    assert [i.kind for i in loaded.sessions[0].issues] == ["test-failed"]
    # --quiet keeps the session lines and the verdict only.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _report(rendered, "--session", "simple-maccity", "--quiet") == 1
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 3
    assert not any(m.startswith("  [") for m in messages)


def test_report_output_root(
    rendered: Path, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    root = _session_root(rendered, "simple-maccity")
    clean_root(root)
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["report", f"--output-root={root}", "--tail", "0"]) == 0
    messages = _cli_messages(caplog)
    assert messages[0] == "logs: none (output root given directly)"
    assert (
        messages[1]
        == "report: 1 session(s) (generated " + messages[1].split("(generated ")[1]
    )
    # The directory name stands in for the session; the application is run.yaml's.
    assert messages[2].startswith(f"simple-maccity  run {RUN_ID}  evaluated (exit 0)")
    assert messages[-1] == "report: no issue in 1 session(s); exit 0"
    # A root that is not a run: unknown application, not submitted, exit 1.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["report", f"--output-root={tmp_path / 'nothing'}"]) == 1
    messages = _cli_messages(caplog)
    assert messages[2] == "nothing  not submitted"
    # The selection flags belong to --root-dir.
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["report", f"--output-root={root}", "--session", "x"]) == 1
    assert "--root-dir" in caplog.records[-1].getMessage()
    with pytest.raises(SystemExit):
        main(["report", f"--output-root={root}", f"--root-dir={rendered}"])


def test_report_clean_errors(
    tmp_path: Path,
    rendered: Path,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _report(tmp_path / "elsewhere") == 1
    assert CONVENTIONS.effective_config in caplog.records[-1].getMessage()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _report(rendered, "--session", "nope") == 1
    assert "nope" in caplog.records[-1].getMessage()
    # A config without submit_only is reportable (unlike watchable): its
    # nameless session renders as the application.
    local = run_config_file(tmp_path, "local.yaml", root_dir=tmp_path / "local-root")
    mocker.patch("cli.commands.run.run_bash")
    assert main(["run", f"--config-file={local}", "--dry-run"]) == 0
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _report(tmp_path / "local-root") == 1
    messages = [r.getMessage() for r in caplog.records]
    assert "cece  not submitted" in messages


# ── cancel ───────────────────────────────────────────────────────────────────


def test_cancel_scancels_the_live_jobs(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = _session_root(rendered, "gcafs-anthro")
    _ledger(rendered, "gcafs-anthro", ["20", "21"])
    cancel = mocker.patch("cli.commands.cancel.cancel_jobs", return_value=["21"])
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["cancel", f"--output-root={root}"]) == 0
    assert cancel.call_args.kwargs == {"dry_run": False}
    assert [job.job_id for job in cancel.call_args.args[0].jobs] == ["20", "21"]
    messages = [r.getMessage() for r in caplog.records]
    assert any("cancelled 1 job(s): 21" in m for m in messages)
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["cancel", f"--output-root={root}", "--dry-run"]) == 0
    assert cancel.call_args.kwargs == {"dry_run": True}
    assert "dry run: would cancel 1 job(s): 21" in caplog.records[-1].getMessage()


def test_cancel_without_a_ledger_or_live_jobs_is_a_no_op(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = _session_root(rendered, "simple-maccity")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["cancel", f"--output-root={root}"]) == 0
    assert "nothing submitted" in caplog.records[-1].getMessage()
    _ledger(rendered, "simple-maccity", ["10"])
    mocker.patch("cli.commands.cancel.cancel_jobs", return_value=[])
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["cancel", f"--output-root={root}"]) == 0
    assert "no job in the queue" in caplog.records[-1].getMessage()
