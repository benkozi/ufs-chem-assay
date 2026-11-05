"""Fabricated session roots for the run-report tests (the classifier and
the `report` / `watch` commands): run.yaml, combos.csv, jobs.yaml,
test-report.csv, benchmarks.csv, the .out files and a log, written with
the harness's own writers into tmp_path. Three combinations of one suite,
`s`, swept over MACCITY's mapalgo."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import yaml

from benchmarks import BenchmarkRow, write_benchmarks_csv
from combos import Combo, Dimension, write_combos_csv
from jobs import JobLedger, SubmittedJob, write_ledger
from models.conventions import CONVENTIONS
from models.suite_config import RecordedBaseline, RunManifest
from platforms import Platform, Runtime
from report import TestReportRow, TestResult, write_test_report_csv

RUN_ID = "01JRUNRUNRUNRUNRUNRUNRUNRU"
T0 = datetime(2026, 11, 5, 7, 0, tzinfo=UTC)
TESTS = (
    "test_driver_execution",
    "test_nc_file_count",
    "test_species_attributes",
    "test_baseline_comparison",
)


class Algo(StrEnum):
    BILINEAR = "bilinear"
    CONSD = "consd"
    PASSTHROUGH = "passthrough"


DIM = Dimension(
    target="MACCITY",
    field="mapalgo",
    tag="map",
    apply=lambda _config, _value: None,
    group="stream",
    key="MACCITY",
    index=0,
)


def combo(value: Algo, combo_id: str) -> Combo:
    return Combo(values=((DIM, value),), combo_id=combo_id)


COMBOS = [
    combo(Algo.BILINEAR, "01JCOMBOBILINEAR0000000001"),
    combo(Algo.CONSD, "01JCOMBOCONSD0000000000002"),
    combo(Algo.PASSTHROUGH, "01JCOMBOPASSTHROUGH0000003"),
]


def manifest(
    runtime: Runtime = Runtime.SLURM, *, comparisons: bool = True
) -> RunManifest:
    return RunManifest(
        run_id=RUN_ID,
        application="cece",
        application_commit="36da92e0f02ec9aa05d4261596927527350509b8",
        harness_version="0.1.0-rc.9",
        harness_commit="abc123",
        platform=Platform.URSA if runtime is Runtime.SLURM else Platform.LOCAL,
        runtime=runtime,
        modulefile=None,
        suites=[],
        baselines=(
            [
                RecordedBaseline(
                    suite="s",
                    ulid="01JPREV0000000000000000001",
                    source="s3://b/p/01JPREV0000000000000000001",
                    path=Path("/b/01JPREV0000000000000000001"),
                    state="synced",
                    detail="synced",
                )
            ]
            if comparisons
            else []
        ),
    )


def write_run(root: Path, manifest: RunManifest, combos: list[Combo] = COMBOS) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with open(root / CONVENTIONS.run_manifest, "w") as f:
        yaml.safe_dump(manifest.model_dump(mode="json"), f, sort_keys=False)
    write_combos_csv(
        [("s", combo, [("MACCITY", "mapalgo", combo.name, True)]) for combo in combos],
        RUN_ID,
        "cece",
        root / CONVENTIONS.combos_csv,
    )


def job(root: Path, combo: Combo, job_id: str) -> SubmittedJob:
    return SubmittedJob(
        suite="s",
        combo=combo.name,
        combo_id=combo.combo_id,
        job_id=job_id,
        script=root / combo.combo_id / f"{combo.combo_id}.sbatch",
        out_path=root / combo.combo_id / f"{combo.combo_id}.out",
        timeout_s=1800,
        queue_wait_s=3600,
        submitted_at=T0,
    )


def write_session_ledger(
    root: Path,
    combos: list[Combo] = COMBOS,
    *,
    evaluated: bool = True,
    exit_status: int | None = 0,
) -> JobLedger:
    ledger = JobLedger(
        run_id=RUN_ID,
        application="cece",
        submitted_by="u@ursa",
        jobs=[job(root, combo, str(100 + i)) for i, combo in enumerate(combos)],
        evaluated_at=T0 if evaluated else None,
        exit_status=exit_status if evaluated else None,
    )
    write_ledger(ledger, root)
    return ledger


def report_rows(
    combo: Combo,
    failed: tuple[str, ...] = (),
    skipped: tuple[str, ...] = (),
    *,
    multi: bool = False,
) -> list[TestReportRow]:
    def result(test: str) -> TestResult:
        if test in failed:
            return "failed"
        return "skipped" if test in skipped else "passed"

    part = f"s/{combo.name}" if multi else combo.name
    return [
        TestReportRow(
            pytest_name=f"{test}[{part}]",
            application="cece",
            suite="s",
            combo_id=combo.combo_id,
            combo=combo.name,
            result=result(test),
        )
        for test in TESTS
    ]


def benchmark(combo: Combo, job_id: str | None, **overrides: object) -> BenchmarkRow:
    values: dict[str, object] = {
        "recorded_at": T0,
        "run_id": RUN_ID,
        "application": "cece",
        "suite": "s",
        "combo": combo.name,
        "combo_id": combo.combo_id,
        "config_sha256": "a" * 64,
        "application_commit": "36da92e0f02ec9aa05d4261596927527350509b8",
        "harness_version": "0.1.0-rc.9",
        "harness_commit": "abc123",
        "platform": Platform.URSA,
        "runtime": Runtime.SLURM,
        "modulefile": None,
        "sbatch_args": None,
        "srun_args": None,
        "job_env": None,
        "timeout_s": 1800,
        "driver_result": "passed",
        "exit_code": 0,
        "duration_s": 600.0,
        "wall_s": 500.0,
        "queue_s": 100.0,
        "elapsed_s": 500.0,
        "max_rss_kb": None,
        "slurm_job_id": job_id,
        "slurm_state": "COMPLETED" if job_id else None,
        "nc_files": 3,
        "output_bytes": 1000,
        "baseline_ulid": "01JPREV0000000000000000001",
        "baseline_state": "synced",
        "comparison_result": "passed",
    }
    values.update(overrides)
    return BenchmarkRow.model_validate(values)


def write_outcomes(
    root: Path, rows: list[TestReportRow], benchmarks: list[BenchmarkRow]
) -> None:
    write_test_report_csv(rows, root / CONVENTIONS.test_report)
    write_benchmarks_csv(benchmarks, root / CONVENTIONS.benchmarks)


def write_out(root: Path, combo: Combo, lines: list[str]) -> Path:
    directory = root / combo.combo_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{combo.combo_id}.out"
    path.write_text("".join(f"{line}\n" for line in lines))
    (directory / "cece.log").write_text("log\n")
    return path


def write_log(logs_dir: Path, name: str, text: str) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / name
    path.write_text(text)
    return path


def clean_root(root: Path) -> None:
    """A slurm session evaluated with every test passed: the baseline shape."""
    write_run(root, manifest())
    write_session_ledger(root)
    rows = [row for combo in COMBOS for row in report_rows(combo)]
    benchmarks = [benchmark(combo, str(100 + i)) for i, combo in enumerate(COMBOS)]
    write_outcomes(root, rows, benchmarks)
    for combo in COMBOS:
        write_out(root, combo, ["tic: t", "driver output", "toc: t"])
