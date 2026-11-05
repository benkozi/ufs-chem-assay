"""cli/run_report.py: the classifier over one session root's files
(run.yaml, combos.csv, jobs.yaml, test-report.csv, benchmarks.csv, the
.out files, upload.yaml) plus an optional log, and the renderer. Every
root is built by hand in tmp_path (tests/ufs_chem_assay/report_roots.py);
no Slurm command is called — the module has none to mock."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cli.run_report import (
    Issue,
    RunReport,
    SessionSummary,
    render,
    summarize_session,
    tail_lines,
)
from models.conventions import CONVENTIONS
from outputs import OutputUploadRecord
from platforms import Platform, Runtime
from report import write_test_report_csv
from tests.ufs_chem_assay.report_roots import (
    COMBOS,
    RUN_ID,
    T0,
    TESTS,
    B,
    C,
    P,
    benchmark,
    clean_root,
    manifest,
    report_rows,
    write_log,
    write_out,
    write_outcomes,
    write_run,
    write_session_ledger,
)


def _kinds(summary: SessionSummary) -> list[str]:
    return [issue.kind for issue in summary.issues]


# ── states ───────────────────────────────────────────────────────────────────


def test_no_run_yaml_is_not_submitted(tmp_path: Path) -> None:
    summary = summarize_session(tmp_path / "root", application="cece", session="s1")
    assert summary.state == "not submitted"
    assert summary.run_id is None
    assert summary.combinations == 0
    assert _kinds(summary) == ["not-submitted"]
    assert "run.yaml" in summary.issues[0].detail
    assert summary.issues[0].combo is None
    assert summary.issues[0].paths == []
    assert not summary.ok


def test_slurm_dry_run_root_without_a_ledger_is_not_submitted(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest())
    summary = summarize_session(root, application="cece", session=None)
    assert summary.state == "not submitted"
    assert summary.run_id == RUN_ID
    assert summary.combinations == 3
    assert summary.jobs == 0
    assert _kinds(summary) == ["not-submitted"]
    assert "jobs.yaml" in summary.issues[0].detail


def test_half_submitted_ledger_is_submission_incomplete(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest())
    write_session_ledger(root, COMBOS[:1], evaluated=False)
    summary = summarize_session(root, application="cece", session="s1")
    assert summary.state == "submission incomplete"
    assert summary.jobs == 1
    assert _kinds(summary) == ["submission-incomplete", "not-evaluated"]
    detail = summary.issues[0].detail
    assert "2 combination(s) have no recorded job" in detail
    assert f"s/{C}" in detail
    assert f"s/{P}" in detail


def test_unevaluated_ledger_is_not_evaluated(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest())
    write_session_ledger(root, evaluated=False)
    summary = summarize_session(root, application="cece", session="s1")
    assert summary.state == "not evaluated"
    assert summary.exit_status is None
    assert summary.submitted_by == "u@ursa"
    assert _kinds(summary) == ["not-evaluated"]
    assert "evaluated_at" in summary.issues[0].detail
    assert summary.tests == {}
    assert summary.log is None


def test_not_evaluated_detail_comes_from_the_newest_resume_log(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest())
    write_session_ledger(root, evaluated=False)
    logs = tmp_path / "logs"
    write_log(logs, "06-resume-cece-s1-20261105-070000.log", "old\n")
    newest = write_log(
        logs,
        "06-resume-cece-s1-20261105-073000.log",
        "collecting ...\n"
        "ERROR: usage: pytest [options]\n"
        "pytest: error: --resume: /x has no jobs.yaml\n"
        "trailing line\n",
    )
    # Another session's log, a later harness log, and a session whose name
    # extends this one: none of them is this session's resume log.
    write_log(logs, "06-resume-cece-s1-extra-20261105-080000.log", "other\n")
    write_log(logs, "05-harness-cece-s1-20261105-080000.log", "submit log\n")
    summary = summarize_session(root, application="cece", session="s1", logs_dir=logs)
    assert summary.log == newest
    issue = summary.issues[0]
    assert issue.kind == "not-evaluated"
    assert "pytest: error: --resume" in issue.detail
    assert issue.paths == [newest]
    # A log without an error line names the log and says what is missing.
    newest.write_text("nothing wrong here\n")
    summary = summarize_session(root, application="cece", session="s1", logs_dir=logs)
    assert "evaluated_at" in summary.issues[0].detail
    assert summary.issues[0].paths == [newest]


def test_a_corrupt_ledger_is_not_evaluated(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest())
    (root / CONVENTIONS.job_ledger).write_text("not: [a ledger\n")
    summary = summarize_session(root, application="cece", session="s1")
    assert summary.state == "not evaluated"
    assert _kinds(summary) == ["not-evaluated"]
    assert "not a job ledger" in summary.issues[0].detail


def test_evaluated_clean_session(tmp_path: Path) -> None:
    root = tmp_path / "root"
    clean_root(root)
    summary = summarize_session(root, application="cece", session="s1")
    assert summary.state == "evaluated"
    assert summary.exit_status == 0
    assert summary.evaluated_at == T0
    assert summary.combinations == 3
    assert summary.jobs == 3
    assert summary.tests == {"passed": 12, "failed": 0, "skipped": 0}
    assert summary.clean == 3
    assert summary.not_run == 0
    assert summary.comparisons == {"passed": 3}
    assert summary.uploaded_to is None
    assert summary.issues == []
    assert summary.ok


def test_upload_record_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "root"
    clean_root(root)
    record = OutputUploadRecord(
        run_id=RUN_ID,
        store="s3://ufs-chem/assay-output",
        destination=f"s3://ufs-chem/assay-output/{RUN_ID}/",
        uploaded_at=T0,
        uploaded_by="u@ursa",
        files=10,
        bytes=100,
    )
    with open(root / CONVENTIONS.upload_record, "w") as f:
        yaml.safe_dump(record.model_dump(mode="json"), f)
    summary = summarize_session(root, application="cece", session="s1")
    assert summary.uploaded_to == record.destination


# ── combination issues ───────────────────────────────────────────────────────


def test_driver_timeout_and_failure_with_the_out_tail(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest())
    write_session_ledger(root, exit_status=1)
    bilinear, consd, passthrough = COMBOS
    rows = (
        report_rows(bilinear, failed=("test_driver_execution",), skipped=TESTS[1:])
        + report_rows(consd, failed=("test_driver_execution",), skipped=TESTS[1:])
        + report_rows(passthrough)
    )
    benchmarks = [
        benchmark(
            bilinear,
            "100",
            driver_result="timeout",
            slurm_state="TIMEOUT",
            exit_code=0,
            duration_s=2154.0,
            comparison_result=None,
        ),
        benchmark(
            consd,
            "101",
            driver_result="failed",
            slurm_state="FAILED",
            exit_code=2,
            comparison_result=None,
        ),
        benchmark(passthrough, "102"),
    ]
    write_outcomes(root, rows, benchmarks)
    write_out(root, bilinear, [f"line {i}" for i in range(40)])
    # consd never got a .out: reported, not an error.
    (root / consd.combo_id).mkdir()
    summary = summarize_session(root, application="cece", session="s1", tail=5)
    assert summary.state == "evaluated"
    assert summary.exit_status == 1
    assert summary.tests == {"passed": 4, "failed": 2, "skipped": 6}
    assert summary.clean == 1
    assert summary.comparisons == {"passed": 1}
    assert _kinds(summary) == ["driver-timeout", "driver-failed"]
    timeout, failed = summary.issues
    assert timeout.combo == B
    assert timeout.combo_id == bilinear.combo_id
    assert timeout.suite == "s"
    assert timeout.job_id == "100"
    assert timeout.slurm_state == "TIMEOUT"
    assert timeout.exit_code == 0
    assert timeout.duration_s == 2154.0
    assert timeout.failed_tests == ["test_driver_execution"]
    assert timeout.detail == "driver timeout (Slurm TIMEOUT, exit 0)"
    assert timeout.out_tail == [f"line {i}" for i in range(35, 40)]
    out = root / bilinear.combo_id / f"{bilinear.combo_id}.out"
    assert timeout.paths == [out, out.with_name("cece.log"), out.parent]
    assert failed.detail == "driver failed (Slurm FAILED, exit 2)"
    assert failed.out_tail == [
        "(no .out: the job never wrote and no session judged it)"
    ]
    assert failed.paths == [root / consd.combo_id]
    assert not summary.ok


def test_log_lines_decorate_the_file_verdicts(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest())
    write_session_ledger(root, exit_status=1)
    bilinear, consd, passthrough = COMBOS
    rows = (
        report_rows(bilinear, failed=("test_driver_execution",), skipped=TESTS[1:])
        + report_rows(consd, failed=("test_nc_file_count", "test_species_attributes"))
        + report_rows(passthrough)
    )
    benchmarks = [
        # No accounting at all: the sacct row never appeared.
        benchmark(
            bilinear,
            "100",
            driver_result="failed",
            slurm_state=None,
            exit_code=None,
            queue_s=None,
            elapsed_s=None,
            comparison_result=None,
        ),
        benchmark(consd, "101"),
        benchmark(passthrough, "102"),
    ]
    write_outcomes(root, rows, benchmarks)
    logs = tmp_path / "logs"
    log = write_log(
        logs,
        "06-resume-cece-s1-20261105-073000.log",
        f"test_driver_combos.py::test_driver_execution[{B}] FAILED\n"
        f"test_driver_combos.py::test_nc_file_count[{C}] FAILED\n"
        f"test_driver_combos.py::test_nc_file_count[{P}] PASSED\n"
        "2026-11-05 07:35:00,000 ERROR ufs-chem-assay.conftest: combo "
        f"{B} FAILED (10.0s from submission): no sacct row\n"
        "=== short test summary info ===\n"
        f"FAILED test_driver_combos.py::test_driver_execution[{B}] - "
        "RuntimeError: job 100 left the Slurm queue but sacct has no record of it; "
        "check by hand: sacct -j 100\n"
        f"FAILED test_driver_combos.py::test_nc_file_count[{C}] - "
        "AssertionError: expected 3 files, found 2\n"
        f"FAILED test_driver_combos.py::test_species_attributes[{C}-co] - "
        "AssertionError: units\n"
        "FAILED test_driver_combos.py::test_nc_file_count[stale] - "
        "AssertionError: a line from an older run of this root\n"
        "FAILED test_driver_combos.py::test_without_a_message[x]\n"
        "2026-11-05 07:40:00,000 ERROR ufs-chem-assay.conftest: benchmarks failed: "
        "boom; record the run later: ufs-chem-assay record-benchmarks --output-root=/r\n"
        "2026-11-05 07:40:01,000 ERROR ufs-chem-assay.conftest: uploading output "
        "failed: no network; upload it later: ufs-chem-assay upload-output --output-root=/r\n",
    )
    summary = summarize_session(root, application="cece", session="s1", logs_dir=logs)
    assert summary.log == log
    assert _kinds(summary) == [
        "pipeline-error",
        "pipeline-error",
        "accounting-missing",
        "test-failed",
    ]
    benchmarks_error, upload_error, accounting, tests = summary.issues
    assert benchmarks_error.detail.startswith("benchmarks failed: boom")
    assert benchmarks_error.paths == [log]
    assert upload_error.detail.startswith("uploading output failed: no network")
    assert accounting.combo == B
    assert accounting.detail.endswith("check by hand: sacct -j 100")
    assert accounting.job_id == "100"
    assert accounting.slurm_state is None
    assert accounting.paths == [log]  # no combination directory on disk
    assert tests.combo == C
    assert tests.failed_tests == ["test_nc_file_count", "test_species_attributes"]
    assert tests.detail == (
        "test_nc_file_count: AssertionError: expected 3 files, found 2; "
        "test_species_attributes: AssertionError: units"
    )
    assert tests.job_id == "101"
    assert tests.out_tail == ["(no .out: the job never wrote and no session judged it)"]
    assert summary.clean == 1


def test_accounting_missing_without_a_log(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest(), COMBOS[:1])
    write_session_ledger(root, COMBOS[:1], exit_status=1)
    bilinear = COMBOS[0]
    rows = report_rows(bilinear, failed=("test_driver_execution",), skipped=TESTS[1:])
    write_outcomes(
        root,
        rows,
        [
            benchmark(
                bilinear,
                "100",
                driver_result="failed",
                slurm_state=None,
                exit_code=None,
                comparison_result=None,
            )
        ],
    )
    summary = summarize_session(root, application="cece", session="s1")
    assert _kinds(summary) == ["accounting-missing"]
    assert summary.issues[0].detail == (
        "driver failed; sacct has no record of job 100 (check by hand: sacct -j 100)"
    )


def test_driver_failure_without_a_benchmark_row(tmp_path: Path) -> None:
    # A run older than benchmarking, or benchmarks.csv lost: the test
    # report alone still classifies, the ledger supplies the job id.
    root = tmp_path / "root"
    write_run(root, manifest(), COMBOS[:1])
    write_session_ledger(root, COMBOS[:1], exit_status=1)
    bilinear = COMBOS[0]
    rows = report_rows(bilinear, failed=("test_driver_execution",), skipped=TESTS[1:])
    write_test_report_csv(rows, root / CONVENTIONS.test_report)
    summary = summarize_session(root, application="cece", session="s1")
    assert _kinds(summary) == ["driver-failed"]
    assert summary.issues[0].detail == "driver failed"
    assert summary.issues[0].job_id == "100"
    assert summary.comparisons == {}


def test_comparison_failed_and_the_comparison_counts(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest())
    write_session_ledger(root, exit_status=1)
    bilinear, consd, passthrough = COMBOS
    rows = (
        report_rows(bilinear, failed=("test_baseline_comparison",))
        + report_rows(consd, skipped=("test_baseline_comparison",))
        + report_rows(passthrough, skipped=("test_baseline_comparison",))
    )
    benchmarks = [
        benchmark(bilinear, "100", comparison_result="failed"),
        benchmark(
            consd,
            "101",
            baseline_ulid=None,
            baseline_state="unpublished",
            comparison_result="skipped",
        ),
        benchmark(
            passthrough,
            "102",
            baseline_ulid=None,
            baseline_state=None,
            comparison_result=None,
        ),
    ]
    write_outcomes(root, rows, benchmarks)
    (root / bilinear.combo_id).mkdir()
    (root / bilinear.combo_id / f"{bilinear.combo_id}-stats-comparison.csv").write_text(
        ""
    )
    (root / bilinear.combo_id / "plots-baselines").mkdir()
    summary = summarize_session(root, application="cece", session="s1")
    assert _kinds(summary) == ["comparison-failed"]
    issue = summary.issues[0]
    assert issue.combo == B
    assert issue.failed_tests == ["test_baseline_comparison"]
    assert issue.detail == "comparison against 01JPREV0000000000000000001 failed"
    assert issue.out_tail == []
    assert issue.paths == [
        root / bilinear.combo_id / f"{bilinear.combo_id}-stats-comparison.csv",
        root / bilinear.combo_id / "plots-baselines",
        root / bilinear.combo_id,
    ]
    assert summary.clean == 2  # a skipped comparison is never an issue
    assert summary.comparisons == {"failed": 1, "unpublished": 1, "off": 1}


def test_comparison_failed_without_a_benchmark_row_then_with_a_log(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    write_run(root, manifest(), COMBOS[:1])
    write_session_ledger(root, COMBOS[:1], exit_status=1)
    bilinear = COMBOS[0]
    rows = report_rows(bilinear, failed=("test_baseline_comparison",))
    write_test_report_csv(rows, root / CONVENTIONS.test_report)
    summary = summarize_session(root, application="cece", session="s1")
    assert _kinds(summary) == ["comparison-failed"]
    assert summary.issues[0].detail == "comparison failed"
    assert summary.comparisons == {}  # nothing to count without the rows
    logs = tmp_path / "logs"
    write_log(
        logs,
        "06-resume-cece-s1-20261105-073000.log",
        f"FAILED test_driver_combos.py::test_baseline_comparison[{B}] - "
        "AssertionError: co differs beyond rtol\n",
    )
    summary = summarize_session(root, application="cece", session="s1", logs_dir=logs)
    assert summary.issues[0].detail == (
        "test_baseline_comparison: AssertionError: co differs beyond rtol"
    )
    assert summary.issues[0].paths == [logs / "06-resume-cece-s1-20261105-073000.log"]


def test_test_failed_without_a_log_names_the_tests(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest(), COMBOS[:1])
    write_session_ledger(root, COMBOS[:1], exit_status=1)
    bilinear = COMBOS[0]
    rows = report_rows(bilinear, failed=("test_nc_file_count",))
    write_outcomes(root, rows, [benchmark(bilinear, "100")])
    summary = summarize_session(root, application="cece", session="s1")
    assert _kinds(summary) == ["test-failed"]
    assert summary.issues[0].detail == "test_nc_file_count"
    assert summary.comparisons == {"passed": 1}  # the comparison itself passed


def test_a_root_without_combos_csv_counts_nothing(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest(Runtime.DOCKER))
    (root / CONVENTIONS.combos_csv).unlink()
    write_outcomes(root, report_rows(COMBOS[0]), [])
    summary = summarize_session(root, application="cece", session=None)
    assert summary.state == "evaluated"
    assert summary.combinations == 0
    assert summary.tests == {"passed": 4, "failed": 0, "skipped": 0}


def test_comparisons_off_for_the_whole_run(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest(comparisons=False))
    write_session_ledger(root)
    rows = [
        row
        for combo in COMBOS
        for row in report_rows(combo, skipped=("test_baseline_comparison",))
    ]
    benchmarks = [
        benchmark(
            combo,
            str(100 + i),
            baseline_ulid=None,
            baseline_state=None,
            comparison_result="skipped",
        )
        for i, combo in enumerate(COMBOS)
    ]
    write_outcomes(root, rows, benchmarks)
    summary = summarize_session(root, application="cece", session="s1")
    assert summary.comparisons == {"off": 3}
    assert summary.clean == 3
    assert summary.issues == []
    lines = render(RunReport(root_dir=None, generated_at=T0, sessions=[summary]))
    assert "clean 3/3  comparisons off  not uploaded" in lines[1]


def test_a_combination_deselected_by_k_is_not_run(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest())
    write_session_ledger(root)
    rows = report_rows(COMBOS[0])
    write_outcomes(root, rows, [benchmark(COMBOS[0], "100")])
    summary = summarize_session(root, application="cece", session="s1")
    assert summary.combinations == 3
    assert summary.clean == 1
    assert summary.not_run == 2
    assert summary.issues == []
    assert summary.ok


def test_multi_suite_ids_join_on_combo_id(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest())
    write_session_ledger(root, exit_status=1)
    bilinear = COMBOS[0]
    rows = report_rows(bilinear, failed=("test_nc_file_count",), multi=True) + [
        row for combo in COMBOS[1:] for row in report_rows(combo, multi=True)
    ]
    write_outcomes(
        root, rows, [benchmark(c, str(100 + i)) for i, c in enumerate(COMBOS)]
    )
    logs = tmp_path / "logs"
    write_log(
        logs,
        "06-resume-cece-s1-20261105-073000.log",
        f"FAILED test_driver_combos.py::test_nc_file_count[s/{B}] - "
        "AssertionError: 2 != 3\n",
    )
    summary = summarize_session(root, application="cece", session="s1", logs_dir=logs)
    assert _kinds(summary) == ["test-failed"]
    assert summary.issues[0].detail == "test_nc_file_count: AssertionError: 2 != 3"


# ── roots without a ledger (docker / native) ─────────────────────────────────


def test_docker_root_is_evaluated_without_a_ledger(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest(Runtime.DOCKER))
    rows = [row for combo in COMBOS for row in report_rows(combo)]
    benchmarks = [
        benchmark(
            combo,
            None,
            platform=Platform.LOCAL,
            runtime=Runtime.DOCKER,
            queue_s=None,
            elapsed_s=None,
        )
        for combo in COMBOS
    ]
    write_outcomes(root, rows, benchmarks)
    logs = tmp_path / "logs"
    harness_log = write_log(logs, "05-harness-cece-20261105-073000.log", "ok\n")
    summary = summarize_session(root, application="cece", session=None, logs_dir=logs)
    assert summary.state == "evaluated"
    assert summary.exit_status is None  # unknown without a ledger
    assert summary.jobs == 0
    assert summary.clean == 3
    assert summary.log == harness_log
    assert summary.issues == []
    assert summary.ok  # exit unknown is not an issue; the files are clean


def test_docker_driver_failure_has_no_job(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest(Runtime.DOCKER))
    bilinear = COMBOS[0]
    rows = report_rows(bilinear, failed=("test_driver_execution",), skipped=TESTS[1:])
    write_outcomes(
        root,
        rows,
        [
            benchmark(
                bilinear,
                None,
                platform=Platform.LOCAL,
                runtime=Runtime.DOCKER,
                driver_result="failed",
                exit_code=3,
                comparison_result=None,
            )
        ],
    )
    summary = summarize_session(root, application="cece", session=None)
    assert _kinds(summary) == ["driver-failed"]
    assert summary.issues[0].detail == "driver failed (exit 3)"
    assert summary.issues[0].job_id is None


def test_docker_root_that_died_before_the_report(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest(Runtime.DOCKER))
    summary = summarize_session(root, application="cece", session=None)
    assert summary.state == "not evaluated"
    assert _kinds(summary) == ["not-evaluated"]
    assert "test-report.csv" in summary.issues[0].detail
    assert "jobs.yaml" in summary.issues[0].detail


def test_docker_dry_run_root_with_an_empty_report(tmp_path: Path) -> None:
    # A dry run writes run.yaml and header-only CSVs: evaluated, nothing ran.
    root = tmp_path / "root"
    write_run(root, manifest(Runtime.DOCKER))
    write_outcomes(root, [], [])
    summary = summarize_session(root, application="cece", session=None)
    assert summary.state == "evaluated"
    assert summary.tests == {"passed": 0, "failed": 0, "skipped": 0}
    assert summary.not_run == 3
    assert summary.comparisons == {}
    assert summary.issues == []


# ── the .out tail ────────────────────────────────────────────────────────────


def test_tail_lines_reads_from_the_end_and_decodes_with_replacement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "x.out"
    path.write_bytes(
        b"".join(f"line {i}\n".encode() for i in range(10_000)) + b"\xff\xfe end"
    )
    assert tail_lines(path, 3) == ["line 9998", "line 9999", "�� end"]
    assert tail_lines(path, 0) == []
    assert tail_lines(tmp_path / "missing.out", 3) is None
    path.write_text("")
    assert tail_lines(path, 3) == []
    path.write_text("one\ntwo\n")
    assert tail_lines(path, 5) == ["one", "two"]


# ── rendering ────────────────────────────────────────────────────────────────


def _two_session_report(tmp_path: Path) -> RunReport:
    clean = tmp_path / "out" / "clean"
    clean_root(clean)
    broken = tmp_path / "out" / "broken"
    write_run(broken, manifest())
    write_session_ledger(broken, exit_status=1)
    bilinear, consd, passthrough = COMBOS
    rows = report_rows(
        bilinear, failed=("test_driver_execution",), skipped=TESTS[1:]
    ) + [row for combo in (consd, passthrough) for row in report_rows(combo)]
    benchmarks = [
        benchmark(
            bilinear,
            "100",
            driver_result="timeout",
            slurm_state="TIMEOUT",
            exit_code=0,
            duration_s=2154.0,
            comparison_result=None,
        ),
        benchmark(consd, "101"),
        benchmark(passthrough, "102"),
    ]
    write_outcomes(broken, rows, benchmarks)
    write_out(broken, bilinear, ["tic: t", "srun: error: step killed"])
    logs = tmp_path / "logs"
    write_log(
        logs,
        "06-resume-cece-broken-20261105-073000.log",
        f"FAILED test_driver_combos.py::test_driver_execution[{B}] - "
        "driver run failed: Slurm job 100 timed out\n"
        "2026-11-05 07:40:00,000 ERROR ufs-chem-assay.conftest: publishing baselines "
        "failed: no network; the run is intact — publish it from a node with network: "
        "ufs-chem-assay publish-baselines --output-root=/r\n",
    )
    return RunReport(
        root_dir=tmp_path,
        generated_at=T0,
        sessions=[
            summarize_session(
                clean, application="cece", session="clean", logs_dir=logs
            ),
            summarize_session(
                broken, application="cece", session="broken", logs_dir=logs
            ),
        ],
    )


def test_render_two_sessions(tmp_path: Path) -> None:
    report = _two_session_report(tmp_path)
    assert not report.ok
    lines = render(report)
    broken = tmp_path / "out" / "broken"
    bilinear = COMBOS[0]
    out = broken / bilinear.combo_id / f"{bilinear.combo_id}.out"
    log = tmp_path / "logs" / "06-resume-cece-broken-20261105-073000.log"
    assert lines == [
        f"report: 2 session(s) under {tmp_path} (generated 2026-11-05 07:00 UTC)",
        (
            f"clean   run {RUN_ID}  evaluated (exit 0)  tests 12 passed / 0 failed / "
            "0 skipped  clean 3/3  comparisons 3 passed  not uploaded"
        ),
        (
            f"broken  run {RUN_ID}  evaluated (exit 1)  tests 8 passed / 1 failed / "
            "3 skipped  clean 2/3  comparisons 2 passed  not uploaded"
        ),
        (
            "  [pipeline-error] publishing baselines failed: no network; the run is intact "
            "— publish it from a node with network: ufs-chem-assay publish-baselines "
            "--output-root=/r"
        ),
        f"    see: {log}",
        (
            f"  [driver-timeout] s/{B} ({bilinear.combo_id})  job 100 TIMEOUT exit 0  "
            "2154 s from submission"
        ),
        "    test_driver_execution: driver run failed: Slurm job 100 timed out",
        "    .out tail (2):",
        "      | tic: t",
        "      | srun: error: step killed",
        f"    see: {out}, {out.with_name('cece.log')}, {out.parent}, {log}",
        "report: 2 issue(s) in 1 of 2 session(s); exit 1",
    ]


def test_render_quiet_tail_zero_and_an_ok_report(tmp_path: Path) -> None:
    report = _two_session_report(tmp_path)
    quiet = render(report, quiet=True)
    assert len(quiet) == 4
    assert quiet[0].startswith("report: 2 session(s)")
    assert quiet[-1] == "report: 2 issue(s) in 1 of 2 session(s); exit 1"
    assert not any(".out tail" in line for line in render(report, tail=0))
    assert any(".out tail (1):" in line for line in render(report, tail=1))
    assert any(".out tail (2):" in line for line in render(report))
    ok = RunReport(root_dir=None, generated_at=T0, sessions=report.sessions[:1])
    assert ok.ok
    lines = render(ok)
    assert lines[0] == "report: 1 session(s) (generated 2026-11-05 07:00 UTC)"
    assert lines[-1] == "report: no issue in 1 session(s); exit 0"


def test_render_names_the_application_when_there_are_several(tmp_path: Path) -> None:
    root = tmp_path / "root"
    clean_root(root)
    a = summarize_session(root, application="cece", session="s1")
    b = a.model_copy(update={"application": "catchem"})
    lines = render(RunReport(root_dir=None, generated_at=T0, sessions=[a, b]))
    assert lines[1].startswith("cece/s1     run")
    assert lines[2].startswith("catchem/s1  run")
    # A nameless session renders as its application, and an unknown exit
    # status as such.
    nameless = a.model_copy(update={"session": None, "exit_status": None})
    lines = render(RunReport(root_dir=None, generated_at=T0, sessions=[nameless]))
    assert lines[1].startswith(f"cece  run {RUN_ID}  evaluated (exit unknown)")
    assert lines[-1] == "report: no issue in 1 session(s); exit 0"


def test_render_not_run_uploaded_and_the_unsubmitted_states(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_run(root, manifest())
    write_session_ledger(root)
    write_outcomes(root, report_rows(COMBOS[0]), [benchmark(COMBOS[0], "100")])
    partial = summarize_session(root, application="cece", session="s1").model_copy(
        update={"uploaded_to": "s3://b/p/X/"}
    )
    unsubmitted = summarize_session(tmp_path / "none", application="cece", session="s2")
    dry = tmp_path / "dry"
    write_run(dry, manifest())
    dry_run = summarize_session(dry, application="cece", session="s3")
    lines = render(
        RunReport(
            root_dir=None, generated_at=T0, sessions=[partial, unsubmitted, dry_run]
        )
    )
    assert lines[1].endswith(
        "clean 1/3  2 not run  comparisons 1 passed  uploaded s3://b/p/X/"
    )
    assert lines[2] == "s2  not submitted"
    assert lines[3].startswith("  [not-submitted] no run.yaml at ")
    assert lines[4] == f"s3  run {RUN_ID}  not submitted"
    assert lines[-1] == "report: 2 issue(s) in 2 of 3 session(s); exit 1"


def test_render_a_failed_session_with_no_combination_issue(tmp_path: Path) -> None:
    # Exit 1 from the ledger with clean files (a failure the files cannot
    # show): the session is not ok and the report says so.
    root = tmp_path / "root"
    clean_root(root)
    summary = summarize_session(root, application="cece", session="s1").model_copy(
        update={"exit_status": 1}
    )
    assert not summary.ok
    report = RunReport(root_dir=None, generated_at=T0, sessions=[summary])
    assert not report.ok
    assert render(report)[-1] == "report: no issue in 1 session(s); exit 1"


def test_models_round_trip_through_yaml(tmp_path: Path) -> None:
    report = _two_session_report(tmp_path)
    dumped = yaml.safe_dump(report.model_dump(mode="json"), sort_keys=False)
    loaded = RunReport.model_validate(yaml.safe_load(dumped))
    assert loaded == report
    assert isinstance(loaded.sessions[1].issues[0], Issue)


@pytest.mark.parametrize("field", ["kind", "detail", "paths"])
def test_issue_fields_are_described(field: str) -> None:
    assert Issue.model_fields[field].description
