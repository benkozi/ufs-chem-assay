"""examples.py: the generic result models and the session report."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from examples import DownloadResult, ExampleRunResult, write_examples_report


def test_write_examples_report(tmp_path: Path) -> None:
    downloads = [
        DownloadResult(script="ex1", returncode=0, output="fetched\n"),
        DownloadResult(script="ex2", returncode=1, output="404 not found\n"),
    ]
    runs = [
        ExampleRunResult(
            example="cece_config_ex1", returncode=0, out_path=tmp_path / "ex1.out"
        ),
        ExampleRunResult(
            example="cece_config_ex2", returncode=2, out_path=tmp_path / "ex2.out"
        ),
    ]
    report_path = tmp_path / "examples-report.md"

    write_examples_report("cece", downloads, runs, report_path)

    text = report_path.read_text()
    assert text.startswith("# examples report (cece)")
    assert "ex1" in text and "ok" in text
    assert "ex2" in text and "404 not found" in text
    assert "cece_config_ex1" in text and "PASS" in text
    assert "cece_config_ex2" in text and "FAIL (exit 2)" in text
    assert str(tmp_path / "ex2.out") in text


def test_write_examples_report_empty(tmp_path: Path) -> None:
    report_path = tmp_path / "examples-report.md"
    write_examples_report("cece", [], [], report_path)
    assert report_path.is_file()


def test_models_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        DownloadResult(script="ex1", returncode=0, output="", surprise=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ExampleRunResult(
            example="x",
            returncode=0,
            out_path=Path("x.out"),
            surprise=1,  # type: ignore[call-arg]
        )
