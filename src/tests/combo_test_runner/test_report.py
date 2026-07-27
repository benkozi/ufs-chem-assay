"""test-report.csv: row model, outcome precedence, and the CSV artifact.

The conftest hook only collects outcomes; everything it delegates to lives in
report.py and is tested here without a pytest session.
"""

from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from report import TestReportRow, TestResult, worst_result, write_test_report_csv


def _row(result: TestResult, suffix: str = "consd") -> TestReportRow:
    return TestReportRow(
        pytest_name=f"test_driver_execution[MACCITY.map-{suffix}]",
        suite="simple-maccity",
        combo_id="01JZZZZZZZZZZZZZZZZZZZZZZZ",
        combo=f"MACCITY.map-{suffix}",
        result=result,
    )


def test_worst_result_precedence() -> None:
    # A test's phases (setup/call/teardown) combine to one result:
    # failed > skipped > passed.
    assert worst_result("passed", "passed") == "passed"
    assert worst_result("passed", "skipped") == "skipped"
    assert worst_result("skipped", "passed") == "skipped"
    assert worst_result("skipped", "failed") == "failed"
    assert worst_result("failed", "skipped") == "failed"
    assert worst_result("failed", "passed") == "failed"


def test_write_test_report_csv(tmp_path: Path) -> None:
    rows = [
        _row("passed", "bilinear"),
        _row("failed", "consd"),
        _row("skipped", "passthrough"),
    ]
    csv_path = tmp_path / "test-report.csv"
    frame = write_test_report_csv(rows, csv_path)

    assert csv_path.is_file()
    assert list(frame.columns) == [
        "pytest_name",
        "suite",
        "combo_id",
        "combo",
        "result",
    ]
    assert list(frame["result"]) == ["passed", "failed", "skipped"]

    reloaded = pd.read_csv(csv_path)
    assert list(reloaded["pytest_name"]) == [row.pytest_name for row in rows]


def test_report_row_rejects_unknown_result() -> None:
    with pytest.raises(ValidationError, match="result"):
        # Deliberately invalid input: the runtime Literal validation under test.
        _row("errored")  # type: ignore[arg-type]
