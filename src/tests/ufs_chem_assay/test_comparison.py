from pathlib import Path
from typing import Literal

import numpy as np
import pytest
import xarray as xr

from comparison import (
    BaselineComparisonResult,
    VariableComparison,
    compare_with_baseline,
    concatenate_comparison_csvs,
    write_comparison_csv,
)


def _write_nc(
    path: Path,
    values: np.ndarray,
    var: str = "co",
    var_attrs: dict[str, str] | None = None,
    global_attrs: dict[str, str] | None = None,
    fmt: Literal["NETCDF4", "NETCDF3_CLASSIC"] = "NETCDF4",
    extra_var: bool = False,
) -> None:
    dataset = xr.Dataset({var: (("time", "lat", "lon"), values)})
    if extra_var:
        dataset["surprise"] = (("time", "lat", "lon"), values)
    dataset[var].attrs.update(
        var_attrs if var_attrs is not None else {"units": "kg m-2 s-1"}
    )
    dataset.attrs.update(
        global_attrs if global_attrs is not None else {"title": "CECE test"}
    )
    encoding = {str(name): {"_FillValue": None} for name in dataset.data_vars}
    dataset.to_netcdf(path, format=fmt, engine="netcdf4", encoding=encoding)


def _values() -> np.ndarray:
    rng = np.random.default_rng(11)
    return rng.random((2, 3, 4))


@pytest.fixture()
def pair_dirs(tmp_path: Path) -> tuple[Path, Path]:
    realization = tmp_path / "realization"
    baseline = tmp_path / "baseline"
    realization.mkdir()
    baseline.mkdir()
    return realization, baseline


def _compare(
    realization: Path, baseline: Path, atol: float = 0.0
) -> BaselineComparisonResult:
    return compare_with_baseline(
        realization,
        baseline,
        atol=atol,
        run_id="01JTESTRUN",
        application="cece",
        suite="simple-maccity",
        combo="MACCITY.map-consd",
        combo_id="deadbeefdeadbeef",
        baseline_ulid="01JTESTBASELINE",
    )


def test_identical_pair_passes_bit_for_bit(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values)
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)

    assert result.passed
    assert result.file_names_match
    assert result.files[0].passed
    assert result.files[0].variables[0].data_match


def test_perturbed_value_respects_absolute_tolerance(
    pair_dirs: tuple[Path, Path],
) -> None:
    realization, baseline = pair_dirs
    values = _values()
    perturbed = values.copy()
    perturbed[0, 0, 0] += 1e-6
    _write_nc(realization / "cece_a.nc", perturbed)
    _write_nc(baseline / "cece_a.nc", values)

    assert not _compare(realization, baseline, atol=0.0).passed  # bit-for-bit
    assert _compare(realization, baseline, atol=1e-5).passed  # covering atol
    assert not _compare(realization, baseline, atol=1e-7).passed  # tighter atol

    # n_mismatched respects atol: the one perturbed element at 0.0/1e-7,
    # none at the covering tolerance (data_match <=> n_mismatched == 0).
    def _variable(result: BaselineComparisonResult) -> VariableComparison:
        (variable,) = [v for f in result.files for v in f.variables]
        return variable

    assert _variable(_compare(realization, baseline, atol=0.0)).n_mismatched == 1
    assert _variable(_compare(realization, baseline, atol=1e-5)).n_mismatched == 0
    assert _variable(_compare(realization, baseline, atol=1e-7)).n_mismatched == 1

    result = _compare(realization, baseline, atol=0.0)
    (variable,) = [v for f in result.files for v in f.variables]
    assert variable.max_abs_diff == pytest.approx(1e-6, rel=1e-3)


def test_nan_position_mismatch_fails_even_under_tolerance(
    pair_dirs: tuple[Path, Path],
) -> None:
    realization, baseline = pair_dirs
    values = _values()
    with_nan = values.copy()
    with_nan[0, 1, 1] = np.nan
    _write_nc(realization / "cece_a.nc", values)
    _write_nc(baseline / "cece_a.nc", with_nan)

    assert not _compare(realization, baseline, atol=1.0).passed


def test_changed_variable_attribute_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values, var_attrs={"units": "mol mol-1"})
    _write_nc(baseline / "cece_a.nc", values, var_attrs={"units": "kg m-2 s-1"})

    result = _compare(realization, baseline, atol=1.0)  # attributes are always exact
    assert not result.passed
    assert not result.files[0].variables[0].attributes_match


def test_changed_global_attribute_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values, global_attrs={"title": "changed"})
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)
    assert not result.passed
    assert not result.files[0].global_attributes_match


def test_dimension_size_change_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    _write_nc(realization / "cece_a.nc", _values()[:, :2, :])  # lat 2 vs 3
    _write_nc(baseline / "cece_a.nc", _values())

    result = _compare(realization, baseline)
    assert not result.passed
    assert not result.files[0].dimensions_match


def test_variable_added_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values, extra_var=True)
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)
    assert not result.passed
    assert not result.files[0].variables_match


def test_file_set_mismatch_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values)
    _write_nc(baseline / "cece_a.nc", values)
    _write_nc(baseline / "cece_b.nc", values)  # baseline has an extra file

    result = _compare(realization, baseline)
    assert not result.passed
    assert not result.file_names_match


def test_format_mismatch_fails(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values, fmt="NETCDF3_CLASSIC")
    _write_nc(baseline / "cece_a.nc", values, fmt="NETCDF4")

    result = _compare(realization, baseline)
    assert not result.passed
    assert not result.files[0].format_match


def test_difference_statistics_match_numpy(pair_dirs: tuple[Path, Path]) -> None:
    realization, baseline = pair_dirs
    values = _values()
    rng = np.random.default_rng(7)
    perturbed = values + rng.normal(0, 1e-3, values.shape)
    _write_nc(realization / "cece_a.nc", perturbed)
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)
    (variable,) = [v for f in result.files for v in f.variables]
    diff = perturbed - values
    assert variable.rmse == pytest.approx(float(np.sqrt(np.nanmean(diff**2))))
    assert variable.n_evaluated == int(np.sum(~np.isnan(diff)))
    assert variable.n_mismatched == int(np.sum(diff != 0))
    assert variable.diff_sum == pytest.approx(float(np.nansum(diff)))
    assert variable.diff_mean == pytest.approx(float(np.nanmean(diff)))
    assert variable.diff_std == pytest.approx(float(np.nanstd(diff)))
    assert variable.diff_min == pytest.approx(float(np.nanmin(diff)))
    assert variable.diff_max == pytest.approx(float(np.nanmax(diff)))
    assert variable.diff_median == pytest.approx(float(np.nanmedian(diff)))


def test_identical_pair_has_zero_difference_statistics(
    pair_dirs: tuple[Path, Path],
) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values)
    _write_nc(baseline / "cece_a.nc", values)

    (variable,) = [
        v for f in _compare(realization, baseline).files for v in f.variables
    ]
    assert variable.rmse == 0.0
    assert variable.diff_min == 0.0
    assert variable.diff_max == 0.0
    assert variable.n_mismatched == 0  # data_match <=> n_mismatched == 0


def test_comparison_csv_one_row_per_file_variable(
    pair_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values + 1.0)
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)
    csv_path = tmp_path / "x-stats-comparison.csv"
    frame = write_comparison_csv(result, csv_path)

    assert csv_path.is_file()
    assert len(frame) == 1  # one file, one variable
    row = frame.iloc[0]
    assert (row["run_id"], row["application"], row["suite"], row["combo"]) == (
        "01JTESTRUN",
        "cece",
        "simple-maccity",
        "MACCITY.map-consd",
    )
    assert list(frame.columns)[:4] == ["run_id", "application", "suite", "combo_id"]
    assert row["baseline_ulid"] == "01JTESTBASELINE"
    assert (row["file"], row["variable"]) == ("cece_a.nc", "co")
    assert not row["data_match"]
    assert row["rmse"] == pytest.approx(1.0)
    assert row["n_evaluated"] == 24  # 2 x 3 x 4 elements
    assert row["n_mismatched"] == 24  # every element shifted by 1.0
    assert not row["passed"]


def test_comparison_csvs_concatenate_to_root(
    pair_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    realization, baseline = pair_dirs
    values = _values()
    _write_nc(realization / "cece_a.nc", values)
    _write_nc(baseline / "cece_a.nc", values)

    result = _compare(realization, baseline)
    path_a = tmp_path / "a-stats-comparison.csv"
    path_b = tmp_path / "b-stats-comparison.csv"
    write_comparison_csv(result, path_a)
    write_comparison_csv(result, path_b)

    combined = concatenate_comparison_csvs(
        [path_a, path_b], tmp_path / "stats-comparison.csv"
    )
    assert (tmp_path / "stats-comparison.csv").is_file()
    assert len(combined) == 2
