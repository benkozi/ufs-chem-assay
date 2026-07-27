"""Descriptive statistics for NetCDF output.

Modeled on the stats portions of cece-data-viewer/app/analysis.py: nan-aware
per-variable reductions built as dask graphs and gathered into a single
dask.compute call (executed on the active distributed client).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TypedDict

import dask
import dask.array as dsa
import numpy as np
import pandas as pd
import xarray as xr
from pydantic import BaseModel, ConfigDict

from logs import get_logger

logger = get_logger("analysis")

_STATS_PER_VARIABLE = 7  # count, sum, mean, std, min, max, median


class RunContext(BaseModel):
    """Run-level identity stamped into every stats row. Extend here (not the
    compute_file_stats signature) when future identity columns arrive."""

    model_config = ConfigDict(frozen=True)

    run_id: str  # session ULID
    suite: str  # unique suite name (SuiteConfig.name)


class VariableStats(BaseModel):
    """Descriptive statistics for one data variable of one NetCDF file, all
    values flattened (a lev dimension, when present, is flattened too).

    The timestamp columns carry the file's time coordinate (first value) —
    what the data claims, not what the filename says — split into parts down
    to seconds for easy time summaries; null when the file has no datetime
    time coordinate."""

    model_config = ConfigDict(frozen=True)

    run_id: str  # session ULID; differentiates runs when CSVs accumulate
    suite: str  # which suite produced this row
    combo_id: str  # content hash of the combination; stable across runs
    combo: str  # human-readable canonical combination string
    file: str  # NetCDF filename, not path
    time: str | None  # ISO-8601
    year: int | None
    month: int | None
    day: int | None
    hour: int | None
    minute: int | None
    second: int | None
    variable: str
    count: int
    sum: float
    mean: float
    std: float
    min: float
    max: float
    median: float


def _file_time(ds: xr.Dataset) -> datetime | None:
    """First value of the dataset's time coordinate, if present and datetime."""
    if "time" not in ds.coords or ds["time"].size == 0:
        return None
    value = np.asarray(ds["time"].values).ravel()[0]
    if not np.issubdtype(np.asarray(value).dtype, np.datetime64):
        return None
    return pd.Timestamp(value).to_pydatetime()


class _TimeFields(TypedDict):
    """Timestamp columns of VariableStats, as **kwargs for its construction."""

    time: str | None
    year: int | None
    month: int | None
    day: int | None
    hour: int | None
    minute: int | None
    second: int | None


def _time_fields(when: datetime | None) -> _TimeFields:
    if when is None:
        return {
            "time": None,
            "year": None,
            "month": None,
            "day": None,
            "hour": None,
            "minute": None,
            "second": None,
        }
    return {
        "time": when.isoformat(),
        "year": when.year,
        "month": when.month,
        "day": when.day,
        "hour": when.hour,
        "minute": when.minute,
        "second": when.second,
    }


def compute_file_stats(
    nc_path: Path, combo: str, combo_id: str, run: RunContext
) -> list[VariableStats]:
    """Nan-aware descriptive stats for every data variable in one NetCDF file."""
    with xr.open_dataset(nc_path, chunks="auto", engine="netcdf4") as ds:
        time_fields = _time_fields(_file_time(ds))
        deferred: list[object] = []
        names: list[str] = []
        for name in ds.data_vars:
            flat = dsa.asarray(ds[str(name)].data).ravel().astype(float)
            deferred.extend(
                [
                    dsa.sum(~dsa.isnan(flat)),
                    dsa.nansum(flat),
                    dsa.nanmean(flat),
                    dsa.nanstd(flat),
                    dsa.nanmin(flat),
                    dsa.nanmax(flat),
                    dsa.nanmedian(flat, axis=0),
                ]
            )
            names.append(str(name))
        computed = dask.compute(*deferred)

    stats = []
    for index, name in enumerate(names):
        count, total, mean, std, minimum, maximum, median = computed[
            index * _STATS_PER_VARIABLE : (index + 1) * _STATS_PER_VARIABLE
        ]
        stats.append(
            VariableStats(
                run_id=run.run_id,
                suite=run.suite,
                combo_id=combo_id,
                combo=combo,
                file=nc_path.name,
                **time_fields,
                variable=name,
                count=int(count),
                sum=float(total),
                mean=float(mean),
                std=float(std),
                min=float(minimum),
                max=float(maximum),
                median=float(median),
            )
        )
        logger.info(
            "computed stats for %s:%s (count=%s mean=%.6g)",
            nc_path.name,
            name,
            int(count),
            float(mean),
        )
    return stats


def write_combo_stats_csv(stats: list[VariableStats], csv_path: Path) -> pd.DataFrame:
    """Write one combination's stats (all its NetCDF files) to a single CSV."""
    frame = pd.DataFrame(
        [entry.model_dump() for entry in stats],
        columns=list(VariableStats.model_fields),
    )
    frame.to_csv(csv_path, index=False)
    logger.info("wrote %s stats row(s) to %s", len(frame), csv_path)
    return frame


def concatenate_stats_csvs(csv_paths: list[Path], out_path: Path) -> pd.DataFrame:
    """Concatenate per-combo stats CSVs into the suite-level CSV."""
    combined = pd.concat([pd.read_csv(path) for path in csv_paths], ignore_index=True)
    combined.to_csv(out_path, index=False)
    logger.info(
        "concatenated %s csv file(s) (%s rows) to %s",
        len(csv_paths),
        len(combined),
        out_path,
    )
    return combined
