"""From a finished run to the publication plan: the gate over the test
outcomes, one record per baseline_comparisons entry, and the CLI's reader
of an output root (the session builds the same inputs from its own
contexts)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import yaml

from applications.base import Application
from baselines.models import (
    CONVENTIONS,
    Action,
    BaselineParameter,
    PublishRecord,
    SuiteInput,
)
from combos import enumerate_combos
from comparison import resolve_baseline_comparisons
from models.suite_config import RunManifest, SuiteConfig
from report import TestReportRow


def _test_name(row: TestReportRow) -> str:
    return row.pytest_name.split("[", 1)[0]


def publishable(rows: Sequence[TestReportRow]) -> tuple[bool, str]:
    """(ok, reason): the driver test passed and no test but the comparison
    against the old baseline failed. Skips are allowed (a suite with stats
    off, a dry run) — a dry run publishes nothing because its driver test
    skipped, with no special case."""
    driver = [row for row in rows if _test_name(row) == CONVENTIONS.driver_test]
    if not driver:
        return False, f"{CONVENTIONS.driver_test} did not run"
    if driver[0].result != "passed":
        return False, f"{CONVENTIONS.driver_test} {driver[0].result}"
    failed = sorted(
        {
            _test_name(row)
            for row in rows
            if row.result == "failed" and _test_name(row) != CONVENTIONS.comparison_test
        }
    )
    if failed:
        return False, "failed: " + ", ".join(failed)
    return True, "driver passed; no other test failed"


def _verdict(
    run: RunManifest,
    rows: Sequence[TestReportRow],
    combo_id: str,
    pinned: str,
    source_dir: Path,
) -> tuple[Action, str]:
    if run.application_commit is None:
        return "failed", "run has no application commit (no checkout configured)"
    ok, reason = publishable([row for row in rows if row.combo_id == combo_id])
    if not ok:
        return "skipped", reason
    if pinned == combo_id:
        return "skipped", "already published"
    if not source_dir.is_dir():
        return "failed", f"combination directory missing: {source_dir}"
    if not any(source_dir.glob("*.nc")):
        return "skipped", "no NetCDF output: nothing to compare against"
    return "would-publish", reason


def plan(
    run: RunManifest,
    suites: Sequence[SuiteInput],
    rows: Sequence[TestReportRow],
    output_root: Path,
    store: str,
) -> list[PublishRecord]:
    """Pure, in memory: one record per baseline_comparisons entry of every
    suite — would-publish, skipped (the gate, an already pinned ULID, no
    NetCDF output), or failed (no application commit, directory missing).
    Nothing is touched."""
    records: list[PublishRecord] = []
    for suite_input in suites:
        for combo_name, entry in suite_input.baselines.items():
            combo_id = suite_input.combo_ids[combo_name]
            source_dir = output_root / combo_id
            action, detail = _verdict(run, rows, combo_id, entry.ulid, source_dir)
            records.append(
                PublishRecord(
                    suite=suite_input.suite.name,
                    suite_path=suite_input.path,
                    combo=combo_name,
                    ulid=combo_id,
                    previous_ulid=entry.ulid,
                    source_dir=source_dir,
                    store=store,
                    action=action,
                    detail=detail,
                    suite_updated=False,
                )
            )
    return records


# ── the CLI's reader ──────────────────────────────────────────────────────────


def default_selector(run: RunManifest) -> str:
    """The --suite-config selector naming the run's suites, by the
    `X-suite.yaml` convention."""
    names = "|".join(re.escape(suite.name) for suite in run.suites)
    return rf"(?:{names})-suite\.yaml"


def read_run(output_root: Path) -> RunManifest:
    with open(output_root / "run.yaml") as f:
        return RunManifest.model_validate(yaml.safe_load(f))


def read_test_report(output_root: Path) -> list[TestReportRow]:
    frame = pd.read_csv(output_root / "test-report.csv", dtype=str)
    return [TestReportRow.model_validate(row) for row in frame.to_dict("records")]


def read_parameters(output_root: Path, combo_id: str) -> list[BaselineParameter]:
    frame = pd.read_csv(output_root / "combos.csv", dtype=str)
    mine = frame[frame["combo_id"] == combo_id]
    return [
        BaselineParameter(
            target=str(row["target"]),
            field=str(row["field"]),
            value=str(row["value"]),
            swept=str(row["swept"]).lower() == "true",
        )
        for row in mine.to_dict("records")
    ]


def _entries_as_run(run: RunManifest, path: Path, suite: SuiteConfig) -> SuiteConfig:
    """The recorded suite of the same name, provided the file's entries are
    the ones the run executed. The ulid is what publishing rewrites, so a
    different ulid is not an edit (a second publish of the same root must
    load and find "already published"); anything else about the entries
    must match."""
    recorded = {s.name: s for s in run.suites}
    if suite.name not in recorded:
        raise ValueError(
            f"suite {suite.name!r} ({path}) was not executed by run {run.run_id} "
            f"(recorded suites: {sorted(recorded)})"
        )
    mine = [e.model_dump(exclude={"ulid"}) for e in suite.baseline_comparisons]
    theirs = [
        e.model_dump(exclude={"ulid"})
        for e in recorded[suite.name].baseline_comparisons
    ]
    if mine != theirs:
        raise ValueError(
            f"suite {suite.name!r} ({path}): baseline_comparisons differ from the ones "
            f"run {run.run_id} executed (edited since the run?); publish from a run of "
            "the file as it is now"
        )
    return recorded[suite.name]


def load_run(
    output_root: Path,
    app: Application,
    suites: Sequence[tuple[Path, SuiteConfig]],
) -> tuple[RunManifest, list[SuiteInput], list[TestReportRow]]:
    """From a finished output root to the plan's inputs: run.yaml,
    test-report.csv, and — per loaded suite — a re-enumeration against its
    base config (fresh ULIDs, but the canonical names are the run's), the
    entries resolved as the session resolved them, and the names joined to
    combos.csv for the run's ids. A suite the run did not execute, or one
    whose entries differ from the recorded ones, is a ValueError."""
    run = read_run(output_root)
    combos_csv = pd.read_csv(output_root / "combos.csv", dtype=str)
    inputs: list[SuiteInput] = []
    for path, suite in suites:
        _entries_as_run(run, path, suite)
        base = app.config_model.from_yaml(suite.config_path)
        combos = enumerate_combos(app.dimensions(suite.sweep, base))
        baselines = resolve_baseline_comparisons(
            app, suite.baseline_comparisons, combos
        )
        mine = combos_csv[combos_csv["suite"] == suite.name]
        ids = dict(zip(mine["name"], mine["combo_id"]))
        missing = sorted(set(baselines) - set(ids))
        if missing:
            raise ValueError(
                f"suite {suite.name!r}: combinations {missing} are not in combos.csv "
                f"of run {run.run_id} (the sweep changed since the run?)"
            )
        inputs.append(
            SuiteInput(
                path=path,
                suite=suite,
                baselines=baselines,
                combo_ids={name: ids[name] for name in baselines},
            )
        )
    return run, inputs, read_test_report(output_root)
