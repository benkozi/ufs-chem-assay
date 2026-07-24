from pathlib import Path

import pytest

from analysis import RunContext, compute_file_stats, write_combo_stats_csv
from assertions import (
    assert_nc_file_count,
    assert_nc_filenames,
    assert_output_variable_dimensions,
    assert_species_attributes,
)
from comparison import compare_with_baseline, write_comparison_csv
from models.suite_config import Analysis, Assertions, BaselineComparison
from runner import DriverRunResult
from settings import Settings


def test_driver_execution(driver_run: DriverRunResult) -> None:
    """The driver ran to completion with exit code 0."""
    if driver_run.error is not None:
        pytest.fail(f"driver run failed: {driver_run.error}")


def test_nc_file_count(
    driver_run: DriverRunResult, suite_assertions: Assertions
) -> None:
    """The combo directory holds the expected number of NetCDF output files."""
    if driver_run.error is not None:
        pytest.skip(f"driver run failed: {driver_run.error}")
    if not suite_assertions.validate_file_count:
        pytest.skip("file count validation disabled by suite config")
    assert_nc_file_count(
        driver_run.combo_dir,
        driver_run.config,
        suite_assertions.expected_nc_file_count,
    )


def test_nc_filenames(
    driver_run: DriverRunResult, suite_assertions: Assertions
) -> None:
    """The NetCDF filenames match filename_pattern at the expected write times."""
    if driver_run.error is not None:
        pytest.skip(f"driver run failed: {driver_run.error}")
    if not suite_assertions.validate_filenames:
        pytest.skip("filename validation disabled by suite config")
    assert_nc_filenames(driver_run.combo_dir, driver_run.config)


def test_nc_variable_dimensions(
    driver_run: DriverRunResult, suite_assertions: Assertions
) -> None:
    """Every configured output variable carries the standard
    (time, lev, lat, lon) dimensions in every NetCDF the combo produced —
    a synthetic dimension (e.g. nox_dim2 where lat belongs) means the
    writer failed to associate the coordinate."""
    if driver_run.error is not None:
        pytest.skip(f"driver run failed: {driver_run.error}")
    if not suite_assertions.validate_dimensions:
        pytest.skip("dimension validation disabled by suite config")
    assert_output_variable_dimensions(driver_run.combo_dir, driver_run.config)


def test_species_attributes(
    driver_run: DriverRunResult, species_name: str, suite_assertions: Assertions
) -> None:
    """The species' variable carries the expected attribute dictionary in
    every NetCDF the combo produced (exact or subset match per the suite)."""
    if driver_run.error is not None:
        pytest.skip(f"driver run failed: {driver_run.error}")
    assert suite_assertions.species is not None
    attributes = suite_assertions.species[species_name].attributes
    if attributes is None:
        pytest.skip("attribute check not configured for this species")
    assert_species_attributes(
        driver_run.combo_dir,
        species_name,
        expected=attributes.expected,
        exact=attributes.exact,
    )


def test_baseline_comparison(
    request: pytest.FixtureRequest,
    driver_run: DriverRunResult,
    run_context: RunContext,
    settings: Settings,
    baseline_comparisons: dict[str, BaselineComparison],
) -> None:
    """The combination's NetCDF output matches its configured baseline
    (nccmp-style: structure and attributes exact; data bit-for-bit or
    within the suite's absolute tolerance)."""
    if driver_run.error is not None:
        pytest.skip(f"driver run failed: {driver_run.error}")
    if not settings.enable_baseline_comparisons:
        pytest.skip("baseline comparisons disabled by settings")
    entry = baseline_comparisons.get(driver_run.combo.name)
    if entry is None:
        pytest.skip("no baseline configured for this combination")
    # Lazy: only comparing runs pay dask cluster startup.
    request.getfixturevalue("dask_client")

    baseline_dir = (settings.baseline_root_dir or Path.cwd()) / entry.ulid
    if not baseline_dir.is_dir():
        pytest.fail(
            f"configured baseline {entry.ulid} not found at {baseline_dir} "
            "(set CECE_BASELINE_ROOT_DIR)"
        )

    result = compare_with_baseline(
        driver_run.combo_dir,
        baseline_dir,
        atol=entry.atol,
        run_id=run_context.run_id,
        suite=run_context.suite,
        combo=driver_run.combo.name,
        combo_id=driver_run.combo.combo_id,
        baseline_ulid=entry.ulid,
    )
    write_comparison_csv(
        result,
        driver_run.combo_dir / f"{driver_run.combo.combo_id}-stats-comparison.csv",
    )
    assert result.passed, f"baseline comparison failed: {result.failure_summary()}"


def test_descriptive_stats(
    request: pytest.FixtureRequest,
    driver_run: DriverRunResult,
    suite_analysis: Analysis,
    run_context: RunContext,
) -> None:
    """Descriptive statistics for every NetCDF the combo produced, written to
    the combo's stats CSV. No value assertions yet — baselines come later."""
    if driver_run.error is not None:
        pytest.skip(f"driver run failed: {driver_run.error}")
    if not suite_analysis.compute_descriptive_stats:
        pytest.skip("descriptive stats disabled by suite config")
    # Lazy: only analysis runs pay dask cluster startup.
    request.getfixturevalue("dask_client")

    nc_files = sorted(driver_run.combo_dir.glob("*.nc"))
    stats = [
        entry
        for nc_file in nc_files
        for entry in compute_file_stats(
            nc_file,
            combo=driver_run.combo.name,
            combo_id=driver_run.combo.combo_id,
            run=run_context,
        )
    ]
    csv_path = driver_run.combo_dir / f"{driver_run.combo.combo_id}-stats.csv"
    frame = write_combo_stats_csv(stats, csv_path)

    assert csv_path.is_file()
    assert set(frame["file"]) == {nc_file.name for nc_file in nc_files}
    assert len(frame) >= len(nc_files)
