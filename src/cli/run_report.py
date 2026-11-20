"""The run report: one rollup of what a run's sessions left behind — from
the files alone, after `watch` or any time later.

A failure is recorded truthfully but in four places: the session root's
`test-report.csv` and `benchmarks.csv`, the ledger's exit status, the
resume log's pytest summary, and the combination's `.out`. This module
joins them per session root: `summarize_session` classifies a root into one
`SessionSummary` (its state, the test counts, how the comparisons went, and
one `Issue` per thing that went wrong, from a small closed taxonomy) and
`render` turns a `RunReport` into the lines the CLI logs. `ufs-chem-assay
report` and the end of `ufs-chem-assay watch` are the two entry points
(cli/main.py, cli/watch.py); nothing here imports either.

Nothing but files are read — no `squeue`, no `sacct` — so the report works
on a downloaded root and after the jobs are long gone. Logs are a bonus,
never a requirement: with a logs directory the newest resume (or harness)
log of the session supplies pytest's `FAILED … - message` lines and the
session-end pipeline's `ERROR` lines; the classification itself comes from
the files, and a log line decorates an issue only when it matches a
combination the files already judged (files win, logs decorate).
"""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum, unique
from pathlib import Path

import pandas as pd
import yaml
from pydantic import Field
from ulid import ULID

from applications.base import Stage
from apptests import parse_junit
from baselines.models import BASELINE_CONVENTIONS
from baselines.plan import read_run, read_test_report
from benchmarks import (
    BenchmarkRow,
    TestBenchmarkRow,
    read_benchmarks_csv,
    read_test_benchmarks_csv,
)
from cli.stage_outcomes import StageOutcome, StageRecord, StageState
from jobs import (
    JobLedger,
    TestCounts,
    ledger_path,
    path_of_test_ledger,
    read_ledger,
    read_test_ledger,
)
from models.base import StrictModel
from models.conventions import CONVENTIONS
from outputs import OutputUploadRecord
from platforms import Runtime
from plotting import BIAS_PLOTS_DIRNAME
from report import TestReportRow, TestResult


@unique
class IssueKind(StrEnum):
    """The closed taxonomy: a combination's worst problem (driver before
    tests before comparison), or a session-level one."""

    NOT_SUBMITTED = "not-submitted"
    SUBMISSION_INCOMPLETE = "submission-incomplete"
    NOT_EVALUATED = "not-evaluated"
    DRIVER_FAILED = "driver-failed"
    DRIVER_TIMEOUT = "driver-timeout"
    ACCOUNTING_MISSING = "accounting-missing"
    TEST_FAILED = "test-failed"
    COMPARISON_FAILED = "comparison-failed"
    PIPELINE_ERROR = "pipeline-error"
    # An application's own test run (a tests root, not a session).
    TESTS_FAILED = "tests-failed"  # the junit report names failed tests
    TESTS_JOB_FAILED = "tests-job-failed"  # nonzero without a failed test to name
    TESTS_STALE = "tests-stale"  # the result predates the latest build or test stage
    # A stage script `run` executed (the stage record, not a root).
    STAGE_FAILED = "stage-failed"  # exited nonzero
    STAGE_SKIPPED = "stage-skipped"  # not run: an earlier stage it needs failed


@unique
class SessionState(StrEnum):
    """Where a session root stands, from its files."""

    NOT_SUBMITTED = "not submitted"  # no run.yaml, or a slurm root without jobs.yaml
    SUBMISSION_INCOMPLETE = "submission incomplete"  # jobs.yaml lacks combinations
    NOT_EVALUATED = "not evaluated"  # no verdicts yet
    EVALUATED = "evaluated"  # the test report is on disk


DEFAULT_TAIL = 15
NO_OUT = "(no .out: the job never wrote and no session judged it)"

# The two log anchors (Decision 4 of the design): pytest's short-summary
# lines, and the harness logger's ERROR lines from the session-end pipeline
# (the only ERRORs the session logs outside a test's captured output).
_FAILED_LINE = re.compile(
    r"^FAILED \S*::(?P<test>\w+)\[(?P<id>[^\]]*)\](?: - (?P<message>.*))?$"
)
_ERROR_LINE = re.compile(r" ERROR ufs-chem-assay\.\S+: (?P<message>.*)$")
_PIPELINE_PREFIXES = (
    "benchmarks failed:",
    "publishing baselines failed:",
    "uploading output failed:",
)
_ERRORISH = re.compile(r"\bERROR\b|\bError\b|\berror:|Traceback")
_COMPARISON_ORDER = ("passed", "failed", "unpublished", "off")
_DRIVER_KINDS = frozenset(
    {IssueKind.DRIVER_FAILED, IssueKind.DRIVER_TIMEOUT, IssueKind.ACCOUNTING_MISSING}
)
_TEST_RESULTS: tuple[TestResult, ...] = ("passed", "failed", "skipped")
_TESTS_KINDS = frozenset({IssueKind.TESTS_FAILED, IssueKind.TESTS_JOB_FAILED})
_STAGE_KINDS = frozenset({IssueKind.STAGE_FAILED, IssueKind.STAGE_SKIPPED})
# The stages a test run stands on, in the order a missing run's cause is sought.
_TEST_LANE = (Stage.SOURCE, Stage.BUILD, Stage.TEST)
_STAMP = "%Y-%m-%d %H:%M UTC"


class Issue(StrictModel):
    """One thing that went wrong in a session: a combination's (the driver,
    its tests, its comparison) or the session's own (never submitted,
    submitted half-way, never evaluated, a pipeline error)."""

    kind: IssueKind = Field(description="The issue's kind, from the closed taxonomy")
    suite: str | None = Field(
        default=None,
        description="The combination's suite; None for a session-level issue",
    )
    combo: str | None = Field(
        default=None,
        description="The combination's canonical name; None at session level",
    )
    combo_id: str | None = Field(
        default=None,
        description="The combination's runtime ULID; None at session level",
    )
    detail: str = Field(
        description=(
            "One sentence: the pytest FAILED message(s), the pipeline ERROR line, "
            "the Slurm state and exit, or what is missing"
        )
    )
    job_id: str | None = Field(
        default=None, description="The Slurm job id, when there was a job"
    )
    slurm_state: str | None = Field(
        default=None, description="Slurm's State as the benchmark row recorded it"
    )
    exit_code: int | None = Field(
        default=None, description="The driver's exit code (sacct's under slurm)"
    )
    duration_s: float | None = Field(
        default=None,
        description="The harness's clock around the driver (slurm: from submission)",
    )
    failed_tests: list[str] = Field(
        default_factory=list, description="Bare names of the combination's failed tests"
    )
    out_tail: list[str] = Field(
        default_factory=list,
        description="The last lines of the combination's .out (or one line saying it is absent)",
    )
    paths: list[Path] = Field(
        default_factory=list,
        description="Where to look next: the .out, cece.log, the combination directory, the log — whichever exist",
    )


class SessionSummary(StrictModel):
    """One (application, session) root as the report sees it."""

    application: str = Field(description="The application's registry name")
    session: str | None = Field(description="The sessions: key; None when nameless")
    toolchain: str | None = Field(
        default=None,
        description="The toolchain the session's build used; None without one",
    )
    root: Path = Field(description="The session's output root (host path)")
    run_id: str | None = Field(description="run.yaml's run_id; None without run.yaml")
    state: SessionState = Field(description="Where the root stands, from its files")
    exit_status: int | None = Field(
        description="The evaluating session's exit status from the ledger; None when unknown"
    )
    evaluated_at: datetime | None = Field(
        description="When the ledger says the jobs were evaluated; None without one"
    )
    submitted_by: str | None = Field(
        description="user@host of the submitting session, from the ledger"
    )
    combinations: int = Field(description="Combinations enumerated (combos.csv)")
    jobs: int = Field(description="Jobs the ledger records")
    tests: dict[TestResult, int] = Field(
        description="Test outcomes by result (test-report.csv); empty when not evaluated"
    )
    clean: int = Field(
        description="Combinations whose driver passed and no test but a skipped comparison did anything else"
    )
    not_run: int = Field(
        description="Combinations enumerated but absent from the test report (deselected with -k)"
    )
    uploaded_to: str | None = Field(
        description="upload.yaml's destination; None when the root was not uploaded"
    )
    comparisons: dict[str, int] = Field(
        description=(
            "How the configured comparisons went for the combinations whose driver "
            "passed: passed, failed, unpublished (the placeholder pin), off (no entry "
            "or comparisons disabled)"
        )
    )
    log: Path | None = Field(
        description="The newest resume (or harness) log read for the details; None without one"
    )
    issues: list[Issue] = Field(
        description="Session-level issues first, then the combinations' in enumeration order"
    )

    @property
    def label(self) -> str:
        label = self.session or self.application
        return label if self.toolchain is None else f"{self.toolchain}/{label}"

    @property
    def ok(self) -> bool:
        """Evaluated, nothing wrong, and the session did not exit nonzero (an
        unknown exit — a root without a ledger — is judged by the files)."""
        return (
            self.state is SessionState.EVALUATED
            and not self.issues
            and self.exit_status in (0, None)
        )


class TestsSummary(StrictModel):
    """One application's own test run (a tests root) as the report sees it."""

    __test__ = False  # a model, not a pytest class

    application: str = Field(description="The application's registry name")
    toolchain: str | None = Field(description="The build's toolchain; None without one")
    build: str | None = Field(
        default=None, description="The build variant; None for the unnamed build"
    )
    root: Path = Field(description="The tests root")
    run_id: str | None = Field(description="The test ledger's run id; None without one")
    state: SessionState = Field(
        description="Where the root stands: not submitted, not evaluated, evaluated"
    )
    exit_status: int | None = Field(
        description="The run's verdict (0: every test passed); None until evaluated"
    )
    evaluated_at: datetime | None = Field(
        description="When the run was evaluated; None until then"
    )
    submitted_by: str | None = Field(description="user@host that launched the run")
    job_id: str | None = Field(
        description="The run's Slurm job; None for a run in place"
    )
    tests: TestCounts | None = Field(
        description="The junit report's counts; None without a report"
    )
    wall_s: float | None = Field(
        description="toc - tic around the tests, from the run's benchmark row"
    )
    out: Path | None = Field(description="The run's output; None before a run")
    junit: Path | None = Field(
        description="The run's junit report path; None before a run"
    )
    issues: list[Issue] = Field(description="What is wrong with the run, if anything")

    @property
    def label(self) -> str:
        parts = [self.application, self.toolchain, self.build]
        return "tests " + "/".join(part for part in parts if part)

    @property
    def ok(self) -> bool:
        return (
            self.state is SessionState.EVALUATED
            and self.exit_status == 0
            and not self.issues
        )


class StageSummary(StrictModel):
    """One stage script's latest outcome as the report sees it."""

    outcome: StageOutcome = Field(description="The script's entry in the stage record")
    issues: list[Issue] = Field(
        description="A failed or skipped stage's issue; empty when it passed"
    )

    @property
    def label(self) -> str:
        o = self.outcome
        parts = [o.application, o.toolchain, o.build]
        return f"{o.stage.value} " + "/".join(part for part in parts if part)

    @property
    def ok(self) -> bool:
        return not self.issues


class RunReport(StrictModel):
    """Every selected session's summary, in config order, then every
    selected application's own test run."""

    root_dir: Path | None = Field(
        description="The run root the effective config came from; None for one output root"
    )
    generated_at: datetime = Field(description="When the report was built (UTC)")
    sessions: list[SessionSummary] = Field(description="One summary per session")
    tests: list[TestsSummary] = Field(
        default_factory=list,
        description="One summary per application whose own tests are enabled",
    )
    stages: list[StageSummary] = Field(
        default_factory=list,
        description=(
            "The latest outcome of the selected source, build and data stages, from "
            "the stage record; empty without one"
        ),
    )

    @property
    def ok(self) -> bool:
        return (
            all(session.ok for session in self.sessions)
            and all(tests.ok for tests in self.tests)
            and all(stage.ok for stage in self.stages)
        )


# ── files ────────────────────────────────────────────────────────────────────


def tail_lines(path: Path, n: int) -> list[str] | None:
    """The last `n` lines of a file, read from its end (a huge .out costs
    nothing), decoded as UTF-8 with replacement; None when the file does
    not exist, [] for n <= 0 or an empty file."""
    if not path.is_file():
        return None
    if n <= 0:
        return []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        position = f.tell()
        data = b""
        while position > 0 and data.count(b"\n") <= n:
            step = min(8192, position)
            position -= step
            f.seek(position)
            data = f.read(step) + data
    return data.decode("utf-8", errors="replace").splitlines()[-n:]


def _combinations(root: Path) -> list[tuple[str, str, str]]:
    """(combo_id, suite, name) of every enumerated combination, in
    combos.csv order; [] without the file."""
    path = root / CONVENTIONS.combos_csv
    if not path.is_file():
        return []
    try:
        frame = pd.read_csv(path, dtype=str).drop_duplicates("combo_id")
    except (pd.errors.EmptyDataError, pd.errors.ParserError, KeyError):
        # A 0-byte or garbled file (a session interrupted while writing
        # it): nothing enumerated, as far as the report can tell.
        return []
    return [
        (str(row["combo_id"]), str(row["suite"]), str(row["name"]))
        for row in frame.to_dict("records")
    ]


def _upload_destination(root: Path) -> str | None:
    path = root / CONVENTIONS.upload_record
    if not path.is_file():
        return None
    with open(path) as f:
        return OutputUploadRecord.model_validate(yaml.safe_load(f)).destination


def newest_log(
    logs_dir: Path | None,
    application: str,
    session: str | None,
    toolchain: str | None = None,
) -> Path | None:
    """The newest `06-resume-<app>[-<toolchain>][-<session>]-<stamp>.log`
    under logs_dir, else the newest `05-harness-…` one (a session evaluated
    in place); None without a logs directory or a matching file. Newest by
    name: the stamp is lexicographic."""
    if logs_dir is None or not logs_dir.is_dir():
        return None
    suffix = "-".join(
        re.escape(part) for part in (application, toolchain, session) if part
    )
    for stage in ("06-resume", "05-harness"):
        pattern = re.compile(rf"^{stage}-{suffix}-\d{{8}}-\d{{6}}\.log$")
        matches = sorted(p for p in logs_dir.iterdir() if pattern.match(p.name))
        if matches:
            return matches[-1]
    return None


class _LogLines(StrictModel):
    """What the two anchors found in a log."""

    failed: list[tuple[str, str, str]] = Field(
        default_factory=list,
        description="(test, parameter id, message) per FAILED line",
    )
    pipeline: list[str] = Field(
        default_factory=list, description="The session-end pipeline's ERROR messages"
    )
    last_error: str | None = Field(
        default=None, description="The last error-looking line, for a dead session"
    )


def _parse_log(log: Path | None) -> _LogLines:
    lines = _LogLines()
    if log is None:
        return lines
    for raw in log.read_text(errors="replace").splitlines():
        line = raw.rstrip()
        failed = _FAILED_LINE.match(line)
        if failed is not None:
            lines.failed.append((failed["test"], failed["id"], failed["message"] or ""))
            continue
        error = _ERROR_LINE.search(line)
        if error is not None and error["message"].startswith(_PIPELINE_PREFIXES):
            lines.pipeline.append(error["message"])
        if _ERRORISH.search(line):
            lines.last_error = line
    return lines


# ── one combination ──────────────────────────────────────────────────────────


def _test_name(row: TestReportRow) -> str:
    return row.pytest_name.split("[", 1)[0]


def _messages(
    log: _LogLines, test: str, suite: str, combo: str, known: frozenset[str]
) -> list[str]:
    """The FAILED messages of one test of one combination: the parameter id
    is the combination's name (suite-qualified in multi-suite sessions),
    optionally followed by `-<species>`. The species form must be exactly
    this combination plus one dash-joined token, and never another
    combination's own id (`known`: every combination's id forms), so a
    name that is a prefix of another's cannot borrow its lines; lines
    naming a combination the files do not know are never joined."""
    parts = (combo, f"{suite}/{combo}")
    exact = [m for t, i, m in log.failed if t == test and i in parts]
    if exact:
        return exact
    return [
        m
        for t, i, m in log.failed
        if t == test and i not in known and i.rsplit("-", 1)[0] in parts
    ]


def _driver_detail(
    result: str, bench: BenchmarkRow | None, job_id: str | None, missing: bool
) -> str:
    if missing:
        return (
            f"driver failed; sacct has no record of job {job_id} "
            f"(check by hand: sacct -j {job_id})"
        )
    parts: list[str] = []
    if bench is not None and bench.slurm_state is not None:
        parts.append(f"Slurm {bench.slurm_state}")
    if bench is not None and bench.exit_code is not None:
        parts.append(f"exit {bench.exit_code}")
    return f"driver {result}" + (f" ({', '.join(parts)})" if parts else "")


def _combination_issue(
    root: Path,
    combo_id: str,
    suite: str,
    combo: str,
    rows: list[TestReportRow],
    bench: BenchmarkRow | None,
    ledger_job_id: str | None,
    log: _LogLines,
    log_path: Path | None,
    tail: int,
    known: frozenset[str],
) -> Issue | None:
    """The combination's worst issue — driver before tests before
    comparison — or None when it is clean."""
    failed = [_test_name(r) for r in rows if r.result == "failed"]
    others = [t for t in failed if t != BASELINE_CONVENTIONS.comparison_test]
    driver_failed = BASELINE_CONVENTIONS.driver_test in failed
    job_id = bench.slurm_job_id if bench is not None else ledger_job_id
    kind: IssueKind
    detail: str
    directory = root / combo_id
    paths = [directory / f"{combo_id}.out", directory / "cece.log", directory]
    if (bench is not None and bench.driver_result != "passed") or driver_failed:
        result = bench.driver_result if bench is not None else "failed"
        missing = (
            result == "failed"
            and bench is not None
            and bench.slurm_job_id is not None
            and bench.slurm_state is None
        )
        messages = _messages(log, BASELINE_CONVENTIONS.driver_test, suite, combo, known)
        if any("sacct -j" in m for m in messages):
            missing = True
        kind = (
            IssueKind.DRIVER_TIMEOUT
            if result == "timeout"
            else IssueKind.ACCOUNTING_MISSING
            if missing
            else IssueKind.DRIVER_FAILED
        )
        detail = (
            f"{BASELINE_CONVENTIONS.driver_test}: {'; '.join(messages)}"
            if messages
            else _driver_detail(result, bench, job_id, missing)
        )
        decorated = bool(messages)
    elif others:
        kind = IssueKind.TEST_FAILED
        pieces: list[str] = []
        decorated = False
        for test in others:
            messages = _messages(log, test, suite, combo, known)
            decorated = decorated or bool(messages)
            if messages:
                pieces.extend(f"{test}: {m}" for m in messages)
            else:
                pieces.append(test)
        detail = "; ".join(pieces)
    elif BASELINE_CONVENTIONS.comparison_test in failed or (
        bench is not None and bench.comparison_result == "failed"
    ):
        kind = IssueKind.COMPARISON_FAILED
        messages = _messages(
            log, BASELINE_CONVENTIONS.comparison_test, suite, combo, known
        )
        decorated = bool(messages)
        if messages:
            detail = f"{BASELINE_CONVENTIONS.comparison_test}: {'; '.join(messages)}"
        elif bench is not None and bench.baseline_ulid is not None:
            detail = f"comparison against {bench.baseline_ulid} failed"
        else:
            detail = "comparison failed"
        paths = [
            directory / f"{combo_id}-{CONVENTIONS.stats_comparison}",
            directory / BIAS_PLOTS_DIRNAME,
            directory,
        ]
    else:
        return None
    existing = [p for p in paths if p.exists()]
    if decorated and log_path is not None:
        existing.append(log_path)
    out_tail: list[str] = []
    if kind is not IssueKind.COMPARISON_FAILED:
        read = tail_lines(directory / f"{combo_id}.out", tail)
        out_tail = [NO_OUT] if read is None else read
    return Issue(
        kind=kind,
        suite=suite,
        combo=combo,
        combo_id=combo_id,
        detail=detail,
        job_id=job_id,
        slurm_state=bench.slurm_state if bench is not None else None,
        exit_code=bench.exit_code if bench is not None else None,
        duration_s=bench.duration_s if bench is not None else None,
        failed_tests=failed,
        out_tail=out_tail,
        paths=existing,
    )


def _comparison_bucket(bench: BenchmarkRow) -> str:
    if bench.comparison_result in ("passed", "failed"):
        return bench.comparison_result
    if bench.baseline_state == "unpublished":
        return "unpublished"
    return "off"


# ── one session ──────────────────────────────────────────────────────────────


def _summary(
    root: Path, application: str, session: str | None, **values: object
) -> SessionSummary:
    """A summary with the fields a stopped-early classification leaves
    empty filled in."""
    defaults: dict[str, object] = {
        "run_id": None,
        "state": SessionState.NOT_SUBMITTED,
        "exit_status": None,
        "evaluated_at": None,
        "submitted_by": None,
        "combinations": 0,
        "jobs": 0,
        "tests": {},
        "clean": 0,
        "not_run": 0,
        "uploaded_to": None,
        "comparisons": {},
        "log": None,
        "issues": [],
    }
    defaults.update(values)
    return SessionSummary.model_validate(
        {"application": application, "session": session, "root": root, **defaults}
    )


def summarize_session(
    root: Path,
    *,
    application: str,
    session: str | None,
    toolchain: str | None = None,
    logs_dir: Path | None = None,
    tail: int = DEFAULT_TAIL,
) -> SessionSummary:
    """Classify one session root from its files (and, with `logs_dir`, the
    session's newest log — the toolchain is part of its name), in the order
    of the design: no run.yaml; the ledger (slurm) against the enumerated
    set; the verdicts; the pipeline errors; the upload record."""
    summary = _classify_session(
        root,
        application=application,
        session=session,
        log_path=newest_log(logs_dir, application, session, toolchain),
        tail=tail,
    )
    summary.toolchain = toolchain
    return summary


def _classify_session(
    root: Path,
    *,
    application: str,
    session: str | None,
    log_path: Path | None,
    tail: int,
) -> SessionSummary:
    log = _parse_log(log_path)
    issues: list[Issue] = []
    if not (root / CONVENTIONS.run_manifest).is_file():
        issues.append(
            Issue(
                kind=IssueKind.NOT_SUBMITTED,
                detail=f"no {CONVENTIONS.run_manifest} at {root}; the harness stage has not run",
            )
        )
        return _summary(root, application, session, log=log_path, issues=issues)
    run = read_run(root)
    combinations = _combinations(root)
    common: dict[str, object] = {
        "run_id": run.run_id,
        "combinations": len(combinations),
        "log": log_path,
        "uploaded_to": _upload_destination(root),
    }

    ledger: JobLedger | None = None
    state: SessionState = SessionState.EVALUATED
    if ledger_path(root).is_file():
        try:
            ledger = read_ledger(root)
        except ValueError as exc:
            issues.append(Issue(kind=IssueKind.NOT_EVALUATED, detail=str(exc)))
            return _summary(
                root,
                application,
                session,
                state=SessionState.NOT_EVALUATED,
                issues=issues,
                **common,
            )
        common.update(
            jobs=len(ledger.jobs),
            submitted_by=ledger.submitted_by,
            evaluated_at=ledger.evaluated_at,
            exit_status=ledger.exit_status,
        )
        recorded = {job.combo_id for job in ledger.jobs}
        missing = [
            f"{suite}/{name}"
            for combo_id, suite, name in combinations
            if combo_id not in recorded
        ]
        if missing:
            state = SessionState.SUBMISSION_INCOMPLETE
            issues.append(
                Issue(
                    kind=IssueKind.SUBMISSION_INCOMPLETE,
                    detail=(
                        f"{len(missing)} combination(s) have no recorded job: "
                        f"{', '.join(missing)}"
                    ),
                )
            )
        if ledger.evaluated_at is None:
            detail = f"no evaluation yet ({CONVENTIONS.job_ledger} has no evaluated_at)"
            if log.last_error is not None and log_path is not None:
                detail = f"resume log {log_path.name}: {log.last_error}"
            issues.append(
                Issue(
                    kind=IssueKind.NOT_EVALUATED,
                    detail=detail,
                    paths=[log_path] if log_path is not None else [],
                )
            )
            return _summary(
                root,
                application,
                session,
                state=SessionState.NOT_EVALUATED
                if state is SessionState.EVALUATED
                else state,
                issues=issues,
                **common,
            )
    elif run.runtime is Runtime.SLURM:
        issues.append(
            Issue(
                kind=IssueKind.NOT_SUBMITTED,
                detail=(
                    f"{CONVENTIONS.run_manifest} without {CONVENTIONS.job_ledger}: "
                    "a dry run, or a submit that never reached sbatch"
                ),
            )
        )
        return _summary(root, application, session, issues=issues, **common)
    elif not (root / CONVENTIONS.test_report).is_file():
        issues.append(
            Issue(
                kind=IssueKind.NOT_EVALUATED,
                detail=(
                    f"no {CONVENTIONS.test_report} and no {CONVENTIONS.job_ledger}: "
                    "the session died before writing its verdicts"
                ),
            )
        )
        return _summary(
            root,
            application,
            session,
            state=SessionState.NOT_EVALUATED,
            issues=issues,
            **common,
        )

    # Evaluated: the verdicts.
    rows = read_test_report(root) if (root / CONVENTIONS.test_report).is_file() else []
    benchmarks = (
        read_benchmarks_csv(root / CONVENTIONS.benchmarks)
        if (root / CONVENTIONS.benchmarks).is_file()
        else []
    )
    by_combo_id = {row.combo_id: row for row in benchmarks}
    job_ids = {job.combo_id: job.job_id for job in ledger.jobs} if ledger else {}
    counts = Counter(row.result for row in rows)
    tests = {result: counts.get(result, 0) for result in _TEST_RESULTS}
    issues.extend(
        Issue(
            kind=IssueKind.PIPELINE_ERROR,
            detail=message,
            paths=[log_path] if log_path else [],
        )
        for message in log.pipeline
    )
    known = frozenset(
        form
        for _combo_id, suite, name in combinations
        for form in (name, f"{suite}/{name}")
    )
    clean = not_run = 0
    comparisons: Counter[str] = Counter()
    for combo_id, suite, name in combinations:
        mine = [row for row in rows if row.combo_id == combo_id]
        if not mine:
            not_run += 1
            continue
        bench = by_combo_id.get(combo_id)
        issue = _combination_issue(
            root,
            combo_id,
            suite,
            name,
            mine,
            bench,
            job_ids.get(combo_id),
            log,
            log_path,
            tail,
            known,
        )
        if issue is None:
            clean += 1
        else:
            issues.append(issue)
        # The comparison ran whenever the driver passed: count it then.
        if bench is not None and (issue is None or issue.kind not in _DRIVER_KINDS):
            comparisons[_comparison_bucket(bench)] += 1
    return _summary(
        root,
        application,
        session,
        state=state,
        tests=tests,
        clean=clean,
        not_run=not_run,
        comparisons=dict(comparisons),
        issues=issues,
        **common,
    )


# ── an application's own tests ───────────────────────────────────────────────


def _test_row(root: Path, run_id: str) -> TestBenchmarkRow | None:
    """The run's benchmark row in the tests root; None when the file is
    absent, unreadable, or does not hold the run."""
    path = root / CONVENTIONS.benchmarks
    if not path.is_file():
        return None
    try:
        rows = read_test_benchmarks_csv(path)
    except ValueError:
        return None
    return next((row for row in rows if row.run_id == run_id), None)


def outcome_text(outcome: StageOutcome) -> str:
    """failed (exit N), passed, or skipped."""
    if outcome.state is StageState.FAILED:
        return f"failed (exit {outcome.exit_status})"
    return outcome.state.value


def summarize_stage(
    outcome: StageOutcome, record: StageRecord, *, tail: int = DEFAULT_TAIL
) -> StageSummary:
    """One stage's outcome with its issue: a failed stage's log tail and
    path, or the failure that skipped it."""
    issues: list[Issue] = []
    if outcome.state is StageState.FAILED:
        log = outcome.log
        issues.append(
            Issue(
                kind=IssueKind.STAGE_FAILED,
                detail=f"{outcome.script} {outcome_text(outcome)}",
                exit_code=outcome.exit_status,
                out_tail=(tail_lines(log, tail) or []) if log is not None else [],
                paths=[log] if log is not None and log.is_file() else [],
            )
        )
    elif outcome.state is StageState.SKIPPED:
        cause = record.outcomes.get(outcome.cause or "")
        why = (
            f"{outcome.cause} {outcome_text(cause)}"
            if cause is not None
            else f"{outcome.cause} failed"
        )
        issues.append(
            Issue(
                kind=IssueKind.STAGE_SKIPPED,
                detail=f"{outcome.script} was skipped: {why}",
            )
        )
    return StageSummary(outcome=outcome, issues=issues)


def _created_at(run_id: str) -> datetime | None:
    """When a test run's ledger was minted: its run id is a ULID."""
    try:
        return ULID.from_str(run_id).datetime
    except ValueError:
        return None


def _stale(
    created: datetime | None, lane: Mapping[Stage, StageOutcome]
) -> Issue | None:
    """A ledger older than the lane's latest build, or than its latest test
    stage when that one did not pass: the result shown is not this build's."""
    if created is None:
        return None
    build, test = lane.get(Stage.BUILD), lane.get(Stage.TEST)
    later: tuple[StageOutcome, str] | None = None
    if build is not None and build.started_at > created:
        later = (build, "build")
    elif (
        test is not None
        and test.started_at > created
        and test.state is not StageState.PASSED
    ):
        later = (test, "test stage")
    if later is None:
        return None
    outcome, what = later
    return Issue(
        kind=IssueKind.TESTS_STALE,
        detail=(
            f"this test run predates the latest {what} ({outcome.script}, "
            f"{outcome_text(outcome)} at {outcome.started_at.strftime(_STAMP)}); the "
            f"result is from {created.strftime(_STAMP)}"
        ),
    )


def summarize_tests(
    root: Path,
    *,
    application: str,
    toolchain: str | None = None,
    build: str | None = None,
    tail: int = DEFAULT_TAIL,
    lane: Mapping[Stage, StageOutcome] | None = None,
) -> TestsSummary:
    """One tests root classified from its files alone: the test ledger (the
    latest run and its verdict), the run's benchmark row (wall time, Slurm
    state), the junit report (the failed tests' names) and the last `tail`
    lines of the output. A run that failed is one issue: TESTS_FAILED when
    the report names failed tests, TESTS_JOB_FAILED otherwise (a job killed
    by its limit, a report never written). `lane`: the stage record's latest
    source, build and test stage of this run's application, toolchain and
    variant — naming why a run is missing, and flagging one older than the
    latest build (TESTS_STALE)."""
    lane = lane or {}
    if not path_of_test_ledger(root).is_file():
        cause = next(
            (
                outcome
                for stage in _TEST_LANE
                if (outcome := lane.get(stage)) is not None
                and outcome.state is not StageState.PASSED
            ),
            None,
        )
        detail = (
            f"no test run recorded ({root} has no "
            f"{CONVENTIONS.test_ledger}): the test stage has not run"
            if cause is None
            else f"no test run: {cause.script} {outcome_text(cause)}"
        )
        return TestsSummary(
            application=application,
            toolchain=toolchain,
            build=build,
            root=root,
            run_id=None,
            state=SessionState.NOT_SUBMITTED,
            exit_status=None,
            evaluated_at=None,
            submitted_by=None,
            job_id=None,
            tests=None,
            wall_s=None,
            out=None,
            junit=None,
            issues=[Issue(kind=IssueKind.NOT_SUBMITTED, detail=detail)],
        )
    ledger = read_test_ledger(root)
    job_id = ledger.job.job_id if ledger.job else None
    existing = [path for path in (ledger.out_path, ledger.junit_path) if path.is_file()]
    row = _test_row(root, ledger.run_id)
    issues: list[Issue] = []
    state = SessionState.EVALUATED
    if ledger.evaluated_at is None:
        state = SessionState.NOT_EVALUATED
        issues.append(
            Issue(
                kind=IssueKind.NOT_EVALUATED,
                detail=(
                    "the test run is not evaluated: `ufs-chem-assay watch` (or "
                    "evaluate-tests) evaluates it once its job has left the queue"
                ),
                job_id=job_id,
                paths=existing,
            )
        )
    elif ledger.exit_status != 0:
        counts = ledger.tests
        failed: list[str] = []
        if counts is not None and counts.failed and ledger.junit_path.is_file():
            try:
                failed = parse_junit(ledger.junit_path)[1]
            except ValueError:
                failed = []  # rewritten since the evaluation: the counts stand
        slurm_state = row.slurm_state if row else None
        if counts is not None and counts.failed:
            kind = IssueKind.TESTS_FAILED
            detail = f"{counts.failed} of {counts.total} tests failed"
        else:
            kind = IssueKind.TESTS_JOB_FAILED
            subject = (
                f"the test job ended {slurm_state or 'in an unknown state'}"
                if job_id is not None
                else "the test command failed"
            )
            reason = (
                "with no junit report"
                if counts is None
                else "without a failed test in its report"
            )
            detail = f"{subject} (exit {ledger.exit_status}) {reason}"
        issues.append(
            Issue(
                kind=kind,
                detail=detail,
                job_id=job_id,
                slurm_state=slurm_state,
                exit_code=ledger.exit_status,
                failed_tests=failed[:tail] if tail > 0 else [],
                out_tail=tail_lines(ledger.out_path, tail) or [],
                paths=existing,
            )
        )
    stale = _stale(_created_at(ledger.run_id), lane)
    if stale is not None:
        issues.append(stale)
    return TestsSummary(
        application=application,
        toolchain=toolchain,
        build=build,
        root=root,
        run_id=ledger.run_id,
        state=state,
        exit_status=ledger.exit_status,
        evaluated_at=ledger.evaluated_at,
        submitted_by=ledger.submitted_by,
        job_id=job_id,
        tests=ledger.tests,
        wall_s=row.wall_s if row else None,
        out=ledger.out_path,
        junit=ledger.junit_path,
        issues=issues,
    )


# ── rendering ────────────────────────────────────────────────────────────────


def _tests_line(summary: TestsSummary, label: str) -> str:
    parts = [label]
    if summary.run_id is not None:
        parts.append(f"run {summary.run_id}")
    if summary.state is SessionState.EVALUATED:
        parts.append(f"evaluated (exit {summary.exit_status})")
        counts = summary.tests
        parts.append(
            "no junit report"
            if counts is None
            else f"{counts.passed} passed / {counts.failed} failed / "
            f"{counts.skipped} skipped"
        )
        if summary.wall_s is not None:
            parts.append(f"{summary.wall_s:.0f} s")
    else:
        parts.append(summary.state.value)
        if summary.job_id is not None:
            parts.append(f"job {summary.job_id}")
    return "  ".join(parts)


def _job_text(issue: Issue) -> str:
    job: list[str] = []
    if issue.job_id is not None:
        job.append(f"job {issue.job_id}")
        if issue.slurm_state is not None:
            job.append(issue.slurm_state)
    if issue.exit_code is not None:
        job.append(f"exit {issue.exit_code}")
    return " ".join(job)


def _session_line(summary: SessionSummary, label: str) -> str:
    parts = [label]
    if summary.run_id is not None:
        parts.append(f"run {summary.run_id}")
    if summary.state is SessionState.EVALUATED or summary.tests:
        exit_text = (
            "unknown" if summary.exit_status is None else str(summary.exit_status)
        )
        parts.append(
            f"evaluated (exit {exit_text})"
            if summary.state is SessionState.EVALUATED
            else summary.state.value
        )
        tests = summary.tests
        parts.append(
            f"tests {tests.get('passed', 0)} passed / {tests.get('failed', 0)} failed / "
            f"{tests.get('skipped', 0)} skipped"
        )
        parts.append(f"clean {summary.clean}/{summary.combinations}")
        if summary.not_run:
            parts.append(f"{summary.not_run} not run")
        if set(summary.comparisons) == {"off"}:
            parts.append("comparisons off")
        elif summary.comparisons:
            parts.append(
                "comparisons "
                + " / ".join(
                    f"{summary.comparisons[key]} {key}"
                    for key in _COMPARISON_ORDER
                    if key in summary.comparisons
                )
            )
        parts.append(
            f"uploaded {summary.uploaded_to}" if summary.uploaded_to else "not uploaded"
        )
    else:
        parts.append(summary.state.value)
    return "  ".join(parts)


def _stage_line(summary: StageSummary, label: str) -> str:
    o = summary.outcome
    return "  ".join([label, outcome_text(o), o.finished_at.strftime(_STAMP), o.script])


def _issue_lines(issue: Issue, tail: int | None) -> list[str]:
    shown: list[str]
    if issue.kind in _STAGE_KINDS:
        lines = [f"  [{issue.kind}] {issue.detail}"]
        shown = (
            issue.out_tail if tail is None else issue.out_tail[-tail:] if tail else []
        )
        if shown:
            lines.append(f"    log tail ({len(shown)}):")
            lines.extend(f"      | {line}" for line in shown)
    elif issue.kind in _TESTS_KINDS:
        job = _job_text(issue)
        lines = [f"  [{issue.kind}] {issue.detail}" + (f"  {job}" if job else "")]
        if issue.failed_tests:
            lines.append(
                f"    failed ({len(issue.failed_tests)}): "
                + ", ".join(issue.failed_tests)
            )
        shown = (
            issue.out_tail if tail is None else issue.out_tail[-tail:] if tail else []
        )
        if shown:
            lines.append(f"    .out tail ({len(shown)}):")
            lines.extend(f"      | {line}" for line in shown)
    elif issue.combo is None:
        lines = [f"  [{issue.kind}] {issue.detail}"]
    else:
        head = f"  [{issue.kind}] {issue.suite}/{issue.combo} ({issue.combo_id})"
        job = _job_text(issue)
        if job:
            head += "  " + job
        if issue.duration_s is not None:
            head += f"  {issue.duration_s:.0f} s" + (
                " from submission" if issue.job_id is not None else ""
            )
        lines = [head, f"    {issue.detail}"]
        shown = (
            issue.out_tail if tail is None else issue.out_tail[-tail:] if tail else []
        )
        if shown:
            lines.append(f"    .out tail ({len(shown)}):")
            lines.extend(f"      | {line}" for line in shown)
    if issue.paths:
        lines.append("    see: " + ", ".join(str(p) for p in issue.paths))
    return lines


def render(
    report: RunReport, *, quiet: bool = False, tail: int | None = None
) -> list[str]:
    """The report as lines: a header, one line per session and then per
    test run (its issues indented beneath it unless `quiet`), and the
    verdict line with the exit code the caller returns. `tail` caps the
    .out lines shown (None: every stored line; 0: none). Test runs are
    named in the header and the verdict only when the report has any."""
    where = f" under {report.root_dir}" if report.root_dir is not None else ""
    stamp = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    runs = f", {len(report.tests)} test run(s)" if report.tests else ""
    staged = f", {len(report.stages)} stage(s)" if report.stages else ""
    lines = [
        (
            f"report: {len(report.sessions)} session(s){runs}{staged}{where} "
            f"(generated {stamp})"
        )
    ]
    # The stages first: the builds the sessions and the test runs stand on.
    stage_width = max((len(s.label) for s in report.stages), default=0)
    for stage in report.stages:
        lines.append(_stage_line(stage, stage.label.ljust(stage_width)).rstrip())
        if not quiet:
            for issue in stage.issues:
                lines.extend(_issue_lines(issue, tail))
    several = len({s.application for s in report.sessions}) > 1
    labels = [
        f"{s.application}/{s.label}" if several and s.session else s.label
        for s in report.sessions
    ]
    width = max((len(label) for label in labels), default=0)
    for summary, label in zip(report.sessions, labels, strict=True):
        lines.append(_session_line(summary, label.ljust(width)).rstrip())
        if not quiet:
            for issue in summary.issues:
                lines.extend(_issue_lines(issue, tail))
    tests_width = max((len(t.label) for t in report.tests), default=0)
    for tests in report.tests:
        lines.append(_tests_line(tests, tests.label.ljust(tests_width)).rstrip())
        if not quiet:
            for issue in tests.issues:
                lines.extend(_issue_lines(issue, tail))
    issue_lists = [s.issues for s in report.sessions]
    issue_lists += [t.issues for t in report.tests]
    issue_lists += [s.issues for s in report.stages]
    total = sum(len(issues) for issues in issue_lists)
    affected = sum(1 for issues in issue_lists if issues)
    code = 0 if report.ok else 1
    scope = f"{len(report.sessions)} session(s)"
    if report.tests:
        scope += f" and {len(report.tests)} test run(s)"
    if report.stages:
        scope += f" and {len(report.stages)} stage(s)"
    if total:
        lines.append(f"report: {total} issue(s) in {affected} of {scope}; exit {code}")
    else:
        lines.append(f"report: no issue in {scope}; exit {code}")
    return lines
