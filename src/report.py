"""test-report.csv: one row per executed combo-parameterized test.

The integration conftest collects outcomes via a pytest_runtest_makereport
hookwrapper and writes the CSV at session end; the row model, the outcome
precedence, and the artifact writing live here so they are testable without
a pytest session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import Field

from logs import get_logger
from models.base import StrictModel

logger = get_logger("report")

TestResult = Literal["passed", "failed", "skipped"]

_RESULT_PRECEDENCE: dict[str, int] = {"passed": 0, "skipped": 1, "failed": 2}
_REPORT_COLUMNS = ["pytest_name", "suite", "combo_id", "combo", "result"]


class TestReportRow(StrictModel):
    """One combo-parameterized test's final outcome."""

    pytest_name: str = Field(
        description="Test name including the combo parameter id, e.g. test_driver_execution[MACCITY.map-consd]"
    )
    suite: str = Field(
        description="Unique name of the suite the combination belongs to"
    )
    combo_id: str = Field(
        description="Runtime ULID of the combination (joins combos.csv and the combo directory within this run)"
    )
    combo: str = Field(description="Canonical combination name")
    result: TestResult = Field(
        description="Final test outcome across its setup/call/teardown phases"
    )


def worst_result(current: TestResult, new: TestResult) -> TestResult:
    """Combine a test's phase outcomes into one result: failed > skipped >
    passed (a setup skip or a teardown failure wins over a passing call)."""
    return new if _RESULT_PRECEDENCE[new] > _RESULT_PRECEDENCE[current] else current


def write_test_report_csv(rows: list[TestReportRow], csv_path: Path) -> pd.DataFrame:
    """Write the session's test report: one row per combo-parameterized test,
    in execution order."""
    frame = pd.DataFrame([row.model_dump() for row in rows], columns=_REPORT_COLUMNS)
    frame.to_csv(csv_path, index=False)
    logger.info("wrote %s test-report row(s) to %s", len(frame), csv_path)
    return frame
