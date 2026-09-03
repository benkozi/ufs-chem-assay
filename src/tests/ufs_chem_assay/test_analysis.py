from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest
import xarray as xr

if TYPE_CHECKING:
    from dask.distributed import Client

from analysis import (
    RunContext,
    VariableStats,
    compute_file_stats,
    concatenate_stats_csvs,
    spatial_variables,
    write_combo_stats_csv,
)


@pytest.fixture()
def driver_like_nc(tmp_path: Path) -> tuple[Path, np.ndarray]:
    """A temporary NetCDF matching driver output: a co variable on a small
    time × lat × lon grid, with a NaN to exercise nan-aware stats."""
    rng = np.random.default_rng(42)
    data = rng.random((2, 4, 5))
    data[0, 0, 0] = np.nan
    dataset = xr.Dataset(
        {"co": (("time", "lat", "lon"), data)},
        coords={
            "time": pd.date_range("2010-01-01T01:00:00", periods=2, freq="h"),
            "lat": np.linspace(-90.0, 90.0, 4),
            "lon": np.linspace(-180.0, 180.0, 5),
        },
    )
    path = tmp_path / "cece_20100101_010000.nc"
    dataset.to_netcdf(path, engine="netcdf4")
    return path, data


_RUN_ID = "01JZZZZZZZZZZZZZZZZZZZZZZZ"  # fixed 26-char ULID-shaped id for row fixtures
_RUN = RunContext(run_id=_RUN_ID, suite="simple-maccity")
_COMBO_ID = "3f9a1c2b7d4e8a01"  # fixed hash-shaped combo id for row fixtures


def _stats_rows() -> list[VariableStats]:
    return [
        VariableStats(
            run_id=_RUN_ID,
            suite="simple-maccity",
            combo_id=_COMBO_ID,
            combo="MACCITY.map-consd",
            file=f"cece_2010010{day}_010000.nc",
            time=f"2010-01-0{day}T01:00:00",
            year=2010,
            month=1,
            day=day,
            hour=1,
            minute=0,
            second=0,
            variable="co",
            count=40,
            sum=20.0,
            mean=0.5,
            std=0.1,
            min=0.0,
            max=1.0,
            median=0.5,
        )
        for day in (1, 2)
    ]


def test_compute_file_stats_matches_numpy(
    dask_client: "Client", driver_like_nc: tuple[Path, np.ndarray]
) -> None:
    from ulid import ULID

    path, data = driver_like_nc
    run = RunContext(run_id=str(ULID()), suite="simple-maccity")
    (stats,) = compute_file_stats(
        path, combo="MACCITY.map-consd", combo_id=_COMBO_ID, run=run
    )

    assert stats.run_id == run.run_id and len(stats.run_id) == 26
    assert stats.suite == "simple-maccity"
    assert stats.combo_id == _COMBO_ID
    assert (stats.combo, stats.file, stats.variable) == (
        "MACCITY.map-consd",
        path.name,
        "co",
    )
    # Timestamp columns come from the file's time coordinate (first value).
    assert stats.time == "2010-01-01T01:00:00"
    assert (stats.year, stats.month, stats.day) == (2010, 1, 1)
    assert (stats.hour, stats.minute, stats.second) == (1, 0, 0)
    assert stats.count == int(np.sum(~np.isnan(data)))
    assert stats.sum == pytest.approx(np.nansum(data))
    assert stats.mean == pytest.approx(np.nanmean(data))
    assert stats.std == pytest.approx(np.nanstd(data))
    assert stats.min == pytest.approx(np.nanmin(data))
    assert stats.max == pytest.approx(np.nanmax(data))
    assert stats.median == pytest.approx(np.nanmedian(data))


def test_compute_file_stats_null_time_without_time_coordinate(
    dask_client: "Client", tmp_path: Path
) -> None:
    dataset = xr.Dataset({"co": (("lat", "lon"), np.ones((2, 3)))})
    path = tmp_path / "timeless.nc"
    dataset.to_netcdf(path, engine="netcdf4")

    (stats,) = compute_file_stats(
        path, combo="MACCITY.map-consd", combo_id=_COMBO_ID, run=_RUN
    )

    assert stats.time is None
    assert (stats.year, stats.month, stats.day) == (None, None, None)
    assert (stats.hour, stats.minute, stats.second) == (None, None, None)
    assert stats.count == 6  # stats themselves are unaffected


def test_write_combo_stats_csv_one_row_per_file_variable(tmp_path: Path) -> None:
    csv_path = tmp_path / "map-consd-stats.csv"
    frame = write_combo_stats_csv(_stats_rows(), csv_path)

    assert csv_path.is_file()
    assert len(frame) == 2
    assert list(frame.columns) == list(VariableStats.model_fields)
    assert set(frame["file"]) == {"cece_20100101_010000.nc", "cece_20100102_010000.nc"}
    assert set(frame["combo_id"]) == {_COMBO_ID}


def test_write_combo_stats_csv_empty_keeps_columns(tmp_path: Path) -> None:
    frame = write_combo_stats_csv([], tmp_path / "empty-stats.csv")
    assert len(frame) == 0
    assert list(frame.columns) == list(VariableStats.model_fields)


def test_concatenate_stats_csvs(tmp_path: Path) -> None:
    first, second = _stats_rows()
    path_a = tmp_path / "map-consd-stats.csv"
    path_b = tmp_path / "map-bilinear-stats.csv"
    write_combo_stats_csv([first], path_a)
    write_combo_stats_csv(
        [second.model_copy(update={"combo": "MACCITY.map-bilinear"})], path_b
    )

    combined = concatenate_stats_csvs(
        [path_b, path_a], tmp_path / "descriptive_stats.csv"
    )

    assert (tmp_path / "descriptive_stats.csv").is_file()
    assert len(combined) == 2
    assert set(combined["combo"]) == {"MACCITY.map-consd", "MACCITY.map-bilinear"}
    assert set(combined["run_id"]) == {_RUN_ID}  # run id survives both CSV layers
    assert set(combined["suite"]) == {"simple-maccity"}  # suite name too


def _with_bounds(dataset: xr.Dataset) -> xr.Dataset:
    """Add CF-1.9 cell bounds the way the driver writes them: data
    variables (no `bounds` attribute on the coordinates), lat_bnds reusing
    the lon bounds dimension name."""
    lon = dataset["lon"].values
    lat = dataset["lat"].values
    dataset["lon_bnds"] = (
        ("lon", "lon_bnds_dim1"),
        np.stack([lon - 1, lon + 1], axis=1),
    )
    dataset["lat_bnds"] = (
        ("lat", "lon_bnds_dim1"),
        np.stack([lat - 1, lat + 1], axis=1),
    )
    dataset["lon_bnds"].attrs["units"] = "degrees_east"
    dataset["lat_bnds"].attrs["units"] = "degrees_north"
    return dataset


def test_spatial_variables_are_those_with_lat_and_lon(
    driver_like_nc: tuple[Path, np.ndarray],
) -> None:
    path, _ = driver_like_nc
    with xr.open_dataset(path) as ds:
        ds = _with_bounds(ds.load())
        ds["nox"] = ds["co"] * 2  # a second field, in file order after co
        assert spatial_variables(ds) == ["co", "nox"]


def test_compute_file_stats_skips_bounds_variables(
    driver_like_nc: tuple[Path, np.ndarray], dask_client: "Client", tmp_path: Path
) -> None:
    path, _ = driver_like_nc
    with xr.open_dataset(path) as ds:
        dataset = _with_bounds(ds.load())
    bounded = tmp_path / "bounded.nc"
    dataset.to_netcdf(bounded, engine="netcdf4")

    stats = compute_file_stats(bounded, combo="c", combo_id=_COMBO_ID, run=_RUN)

    assert [row.variable for row in stats] == ["co"]
