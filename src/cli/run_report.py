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
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from pydantic import Field

from baselines.models import BASELINE_CONVENTIONS
from baselines.plan import read_run, read_test_report
from benchmarks import BenchmarkRow, read_benchmarks_csv
from jobs import JobLedger, ledger_path, read_ledger
from models.base import StrictModel
from models.conventions import CONVENTIONS
from outputs import OutputUploadRecord
from platforms import Runtime
from plotting import BIAS_PLOTS_DIRNAME
from report import TestReportRow, TestResult

IssueKind = Literal[
    "not-submitted",
    "submission-incomplete",
    "not-evaluated",
    "driver-failed",
    "driver-timeout",
    "accounting-missing",
    "test-failed",
    "comparison-failed",
    "pipeline-error",
]

SessionState = Literal[
    "not submitted", "submission incomplete", "not evaluated", "evaluated"
]

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
_DRIVER_KINDS = frozenset({"driver-failed", "driver-timeout", "accounting-missing"})
_TEST_RESULTS: tuple[TestResult, ...] = ("passed", "failed", "skipped")


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
    root: Path = Field(description="The session's output root (host path)")
    run_id: str | None = Field(description="run.yaml's run_id; None without run.yaml")
    state: SessionState = Field(
        description=(
            "not submitted: no run.yaml, or a slurm root without jobs.yaml; "
            "submission incomplete: jobs.yaml lacks combinations; not evaluated: "
            "no verdicts yet; evaluated: the test report is on disk"
        )
    )
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
        return self.session or self.application

    @property
    def ok(self) -> bool:
        """Evaluated, nothing wrong, and the session did not exit nonzero (an
        unknown exit — a root without a ledger — is judged by the files)."""
        return (
            self.state == "evaluated"
            and not self.issues
            and self.exit_status in (0, None)
        )


class RunReport(StrictModel):
    """Every selected session's summary, in config order."""

    root_dir: Path | None = Field(
        description="The run root the effective config came from; None for one output root"
    )
    generated_at: datetime = Field(description="When the report was built (UTC)")
    sessions: list[SessionSummary] = Field(description="One summary per session")

    @property
    def ok(self) -> bool:
        return all(session.ok for session in self.sessions)


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
    frame = pd.read_csv(path, dtype=str).drop_duplicates("combo_id")
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
    logs_dir: Path | None, application: str, session: str | None
) -> Path | None:
    """The newest `06-resume-<app>[-<session>]-<stamp>.log` under logs_dir,
    else the newest `05-harness-…` one (a session evaluated in place);
    None without a logs directory or a matching file. Newest by name: the
    stamp is lexicographic."""
    if logs_dir is None or not logs_dir.is_dir():
        return None
    suffix = re.escape(application) + (f"-{re.escape(session)}" if session else "")
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


def _messages(log: _LogLines, test: str, suite: str, combo: str) -> list[str]:
    """The FAILED messages of one test of one combination: the parameter id
    is the combination's name (suite-qualified in multi-suite sessions),
    optionally followed by `-<species>`. Lines naming a combination the
    files do not know are never joined."""
    parts = (combo, f"{suite}/{combo}")
    exact = [m for t, i, m in log.failed if t == test and i in parts]
    if exact:
        return exact
    return [
        m
        for t, i, m in log.failed
        if t == test and any(i.startswith(f"{part}-") for part in parts)
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
        messages = _messages(log, BASELINE_CONVENTIONS.driver_test, suite, combo)
        if any("sacct -j" in m for m in messages):
            missing = True
        kind = (
            "driver-timeout"
            if result == "timeout"
            else "accounting-missing"
            if missing
            else "driver-failed"
        )
        detail = (
            f"{BASELINE_CONVENTIONS.driver_test}: {'; '.join(messages)}"
            if messages
            else _driver_detail(result, bench, job_id, missing)
        )
        decorated = bool(messages)
    elif others:
        kind = "test-failed"
        pieces: list[str] = []
        decorated = False
        for test in others:
            messages = _messages(log, test, suite, combo)
            decorated = decorated or bool(messages)
            if messages:
                pieces.extend(f"{test}: {m}" for m in messages)
            else:
                pieces.append(test)
        detail = "; ".join(pieces)
    elif BASELINE_CONVENTIONS.comparison_test in failed or (
        bench is not None and bench.comparison_result == "failed"
    ):
        kind = "comparison-failed"
        messages = _messages(log, BASELINE_CONVENTIONS.comparison_test, suite, combo)
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
    if kind != "comparison-failed":
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
        "state": "not submitted",
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
    logs_dir: Path | None = None,
    tail: int = DEFAULT_TAIL,
) -> SessionSummary:
    """Classify one session root from its files (and, with `logs_dir`, the
    session's newest log), in the order of the design: no run.yaml; the
    ledger (slurm) against the enumerated set; the verdicts; the pipeline
    errors; the upload record."""
    log_path = newest_log(logs_dir, application, session)
    log = _parse_log(log_path)
    issues: list[Issue] = []
    if not (root / CONVENTIONS.run_manifest).is_file():
        issues.append(
            Issue(
                kind="not-submitted",
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
    state: SessionState = "evaluated"
    if ledger_path(root).is_file():
        try:
            ledger = read_ledger(root)
        except ValueError as exc:
            issues.append(Issue(kind="not-evaluated", detail=str(exc)))
            return _summary(
                root,
                application,
                session,
                state="not evaluated",
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
            state = "submission incomplete"
            issues.append(
                Issue(
                    kind="submission-incomplete",
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
                    kind="not-evaluated",
                    detail=detail,
                    paths=[log_path] if log_path is not None else [],
                )
            )
            return _summary(
                root,
                application,
                session,
                state="not evaluated" if state == "evaluated" else state,
                issues=issues,
                **common,
            )
    elif run.runtime is Runtime.SLURM:
        issues.append(
            Issue(
                kind="not-submitted",
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
                kind="not-evaluated",
                detail=(
                    f"no {CONVENTIONS.test_report} and no {CONVENTIONS.job_ledger}: "
                    "the session died before writing its verdicts"
                ),
            )
        )
        return _summary(
            root, application, session, state="not evaluated", issues=issues, **common
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
            kind="pipeline-error", detail=message, paths=[log_path] if log_path else []
        )
        for message in log.pipeline
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


# ── rendering ────────────────────────────────────────────────────────────────


def _session_line(summary: SessionSummary, label: str) -> str:
    parts = [label]
    if summary.run_id is not None:
        parts.append(f"run {summary.run_id}")
    if summary.state == "evaluated" or summary.tests:
        exit_text = (
            "unknown" if summary.exit_status is None else str(summary.exit_status)
        )
        parts.append(
            f"evaluated (exit {exit_text})"
            if summary.state == "evaluated"
            else summary.state
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
        parts.append(summary.state)
    return "  ".join(parts)


def _issue_lines(issue: Issue, tail: int | None) -> list[str]:
    if issue.combo is None:
        lines = [f"  [{issue.kind}] {issue.detail}"]
    else:
        head = f"  [{issue.kind}] {issue.suite}/{issue.combo} ({issue.combo_id})"
        job: list[str] = []
        if issue.job_id is not None:
            job.append(f"job {issue.job_id}")
            if issue.slurm_state is not None:
                job.append(issue.slurm_state)
        if issue.exit_code is not None:
            job.append(f"exit {issue.exit_code}")
        if job:
            head += "  " + " ".join(job)
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
    """The report as lines: a header, one line per session (its issues
    indented beneath it unless `quiet`), and the verdict line with the exit
    code the caller returns. `tail` caps the .out lines shown (None: every
    stored line; 0: none)."""
    where = f" under {report.root_dir}" if report.root_dir is not None else ""
    stamp = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"report: {len(report.sessions)} session(s){where} (generated {stamp})"]
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
    total = sum(len(s.issues) for s in report.sessions)
    affected = sum(1 for s in report.sessions if s.issues)
    code = 0 if report.ok else 1
    if total:
        lines.append(
            f"report: {total} issue(s) in {affected} of {len(report.sessions)} "
            f"session(s); exit {code}"
        )
    else:
        lines.append(
            f"report: no issue in {len(report.sessions)} session(s); exit {code}"
        )
    return lines
