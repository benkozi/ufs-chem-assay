"""The stage outcome record: what every stage script `run` executed or
skipped came to, kept beside the effective config in
<root_dir>/scripts/stages.yaml so `report` and `watch` can say that a source,
build or data stage failed, why a test run is missing, and when a test
result is older than the build it stands on. Test jobs and driver sessions
keep records of their own (the test ledger, jobs.yaml); a build stage had
only its log."""

from __future__ import annotations

import os
import re
from datetime import datetime
from enum import StrEnum, unique
from pathlib import Path

import yaml
from pydantic import Field

from applications.base import Stage
from models.base import StrictModel
from models.conventions import CONVENTIONS


@unique
class StageState(StrEnum):
    """What one stage script came to in its latest run."""

    PASSED = "passed"  # exited 0
    FAILED = "failed"  # exited nonzero
    SKIPPED = "skipped"  # not run: an earlier stage it depends on failed


class StageOutcome(StrictModel):
    """One stage script's latest outcome."""

    script: str = Field(
        description="The rendered script's file name (01-source-cece.sh)"
    )
    stage: Stage = Field(description="The stage the script runs")
    application: str = Field(description="The application's registry name")
    toolchain: str | None = Field(description="The toolchain; None without one")
    build: str | None = Field(
        description="The build variant; None for the unnamed build and per-application stages"
    )
    session: str | None = Field(
        description="The session of a per-session stage; None otherwise"
    )
    state: StageState = Field(description="What the script came to")
    exit_status: int | None = Field(description="Its exit status; None when skipped")
    started_at: datetime = Field(
        description="When it started, or was skipped (UTC, aware)"
    )
    finished_at: datetime = Field(
        description="When it ended, or was skipped (UTC, aware)"
    )
    log: Path | None = Field(
        description="Its log under <root_dir>/logs; None when skipped or not found"
    )
    cause: str | None = Field(
        description="For a skipped script, the failed script that skipped it; None otherwise"
    )
    run_id: str = Field(
        description="The ULID of the `run` invocation that ran or skipped it"
    )


class StageRecord(StrictModel):
    """<root_dir>/scripts/stages.yaml: the latest outcome per script name."""

    outcomes: dict[str, StageOutcome] = Field(
        default_factory=dict,
        description="Script file name -> its latest outcome, in the order first recorded",
    )


def path_of_stage_record(scripts_dir: Path) -> Path:
    return scripts_dir / CONVENTIONS.stage_record


def read_stage_record(scripts_dir: Path) -> StageRecord:
    """The record under scripts_dir; empty when `run` has executed nothing
    there yet. A file that is not a record is a ValueError naming it."""
    path = path_of_stage_record(scripts_dir)
    if not path.is_file():
        return StageRecord()
    try:
        with open(path) as f:
            return StageRecord.model_validate(yaml.safe_load(f) or {})
    except (ValueError, yaml.YAMLError) as exc:  # ValidationError is a ValueError
        raise ValueError(f"{path}: not a stage record ({exc})") from exc


def record_outcome(scripts_dir: Path, outcome: StageOutcome) -> StageRecord:
    """Merge one outcome into the record (replacing the script's previous
    one) and write it whole, through a temporary file and an atomic rename,
    so a reader never sees a partial file."""
    record = read_stage_record(scripts_dir)
    record.outcomes[outcome.script] = outcome
    path = path_of_stage_record(scripts_dir)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w") as f:
        yaml.safe_dump(
            record.model_dump(mode="json"), f, default_flow_style=False, sort_keys=False
        )
    os.replace(tmp, path)
    return record


def newest_stage_log(logs_dir: Path, stem: str) -> Path | None:
    """The newest <logs_dir>/<stem>-<YYYYmmdd-HHMMSS>.log run_bash wrote for
    a script; None when there is none. The stamp is matched exactly, so a
    variant's logs (<stem>-compute-...) are never taken for its
    application's."""
    if not logs_dir.is_dir():
        return None
    pattern = re.compile(rf"{re.escape(stem)}-\d{{8}}-\d{{6}}\.log")
    logs = sorted(p for p in logs_dir.iterdir() if pattern.fullmatch(p.name))
    return logs[-1] if logs else None
