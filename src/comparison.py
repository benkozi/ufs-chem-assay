"""Baseline comparison of combination NetCDF output, modeled on nccmp.

Structural checks (file sets, formats, dimensions, variables, attributes)
are always exact; data compares bit-for-bit (atol=0) or within an absolute
tolerance (atol>0, no scaling by the baseline's magnitude). Per-variable
reductions are dask graphs gathered into one dask.compute per file pair,
executed on the active distributed client.
"""

from __future__ import annotations

import re
from pathlib import Path

import dask
import dask.array as dsa
import netCDF4
import numpy as np
import pandas as pd
import xarray as xr
from pydantic import ConfigDict, Field

from combos import Combo
from logs import get_logger
from models.base import StrictModel
from models.suite_config import (
    BaselineComparison,
    SpeciesEntrySweepSelector,
    StreamSweepSelector,
    SweepSelector,
)

logger = get_logger("comparison")


class VariableComparison(StrictModel):
    """Comparison outcome for one variable of one file pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="Variable name")
    dtype_match: bool = Field(
        description="Whether realization and baseline dtypes are identical"
    )
    data_match: bool = Field(
        description="Whether the data matched (bit-for-bit or within atol)"
    )
    attributes_match: bool = Field(
        description="Whether the variable attribute dictionaries are identical"
    )
    max_abs_diff: float | None = Field(
        None,
        description="Maximum absolute elementwise difference; None when shapes/dtypes prevented comparison",
    )
    rmse: float | None = Field(
        None,
        description="Root-mean-square of the difference; None when comparison was prevented",
    )
    n_evaluated: int | None = Field(
        None,
        description="Number of elements evaluated (non-NaN difference values); None when comparison was prevented",
    )
    n_mismatched: int | None = Field(
        None,
        description="Number of elements failing the data check under atol (NaN-position mismatches included); data_match iff 0",
    )
    diff_sum: float | None = Field(
        None, description="Sum of the difference values (nan-aware)"
    )
    diff_mean: float | None = Field(
        None, description="Mean of the difference values (nan-aware)"
    )
    diff_std: float | None = Field(
        None, description="Standard deviation of the difference values (nan-aware)"
    )
    diff_min: float | None = Field(
        None, description="Minimum difference value (nan-aware)"
    )
    diff_max: float | None = Field(
        None, description="Maximum difference value (nan-aware)"
    )
    diff_median: float | None = Field(
        None, description="Median difference value (nan-aware)"
    )
    detail: str | None = Field(
        None, description="Human-readable mismatch detail; None when everything matched"
    )

    @property
    def passed(self) -> bool:
        return self.dtype_match and self.data_match and self.attributes_match


class FileComparison(StrictModel):
    """Comparison outcome for one realization/baseline file pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str = Field(description="NetCDF filename (identical on both sides)")
    format_match: bool = Field(
        description="Whether the NetCDF data models (e.g. NETCDF4) are identical"
    )
    dimensions_match: bool = Field(
        description="Whether dimension names and sizes are identical"
    )
    variables_match: bool = Field(
        description="Whether the variable name sets are identical"
    )
    global_attributes_match: bool = Field(
        description="Whether the global attribute dictionaries are identical"
    )
    variables: list[VariableComparison] = Field(
        description="Per-variable outcomes for the common variables"
    )
    passed: bool = Field(description="Whether every check for this file pair passed")


class BaselineComparisonResult(StrictModel):
    """The full comparison record for one combination, flattened to CSV rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(
        description="Session ULID of the run that produced the realization"
    )
    suite: str = Field(description="Unique suite name the realization was produced by")
    combo: str = Field(description="Canonical combination name")
    combo_id: str = Field(description="Content-hash combination id")
    baseline_ulid: str = Field(description="ULID identifying the baseline")
    atol: float = Field(description="Absolute data tolerance used (0 = bit-for-bit)")
    file_names_match: bool = Field(
        description="Whether the NetCDF file name sets are identical"
    )
    files: list[FileComparison] = Field(
        description="Per-file outcomes for the common files"
    )
    passed: bool = Field(description="Whether the whole comparison passed")

    def failure_summary(self) -> str:
        parts: list[str] = []
        if not self.file_names_match:
            parts.append("file name sets differ")
        for file in self.files:
            if file.passed:
                continue
            checks = [
                name
                for flag, name in (
                    (file.format_match, "format"),
                    (file.dimensions_match, "dimensions"),
                    (file.variables_match, "variables"),
                    (file.global_attributes_match, "global attributes"),
                )
                if not flag
            ]
            checks += [
                f"{variable.name} ({variable.detail})"
                for variable in file.variables
                if not variable.passed
            ]
            parts.append(f"{file.file}: " + ", ".join(checks))
        return "; ".join(parts) or "passed"


def _stream_block_matches(block: StreamSweepSelector, combo: Combo) -> bool:
    """A streams selector block scopes its fields to one name-matched stream:
    some swept stream must fullmatch `name` and satisfy every specified field."""
    by_stream: dict[str, dict[str, str]] = {}
    for dimension, value in combo.values:
        if dimension.group == "stream":
            by_stream.setdefault(dimension.key, {})[dimension.field] = value.value
    criteria = {
        field: pattern
        for field, pattern in (
            ("taxmode", block.taxmode),
            ("tintalgo", block.tintalgo),
            ("mapalgo", block.mapalgo),
        )
        if pattern is not None
    }
    for stream_name, fields in by_stream.items():
        if re.fullmatch(block.name, stream_name) is None:
            continue
        if all(
            field in fields and re.fullmatch(pattern, fields[field]) is not None
            for field, pattern in criteria.items()
        ):
            return True
    return False


def _species_entry_matches(
    key_pattern: str,
    index: int,
    entry_selector: SpeciesEntrySweepSelector,
    combo: Combo,
) -> bool:
    """A species entry selector requires a swept species whose name fullmatches
    the dict key, with the selector's list position pinning the entry index."""
    by_species: dict[tuple[str, int], dict[str, str]] = {}
    for dimension, value in combo.values:
        if dimension.group == "species":
            by_species.setdefault((dimension.key, dimension.index), {})[
                dimension.field
            ] = value.value
    criteria = {
        field: pattern
        for field, pattern in (
            ("operation", entry_selector.operation),
            ("category", entry_selector.category),
            ("vdist_method", entry_selector.vdist_method),
        )
        if pattern is not None
    }
    if not criteria:
        return True  # {} entry: no constraint at this index
    for (species_name, entry_index), fields in by_species.items():
        if entry_index != index or re.fullmatch(key_pattern, species_name) is None:
            continue
        if all(
            field in fields and re.fullmatch(pattern, fields[field]) is not None
            for field, pattern in criteria.items()
        ):
            return True
    return False


def _selector_matches(selector: SweepSelector, combo: Combo) -> bool:
    """Structural walk mirroring the sweep: every constrained element must be
    satisfied; unspecified structure is unconstrained."""
    if selector.cece_data is not None:
        if not all(
            _stream_block_matches(block, combo) for block in selector.cece_data.streams
        ):
            return False
    if selector.species is not None:
        for key_pattern, entries in selector.species.items():
            for index, entry_selector in enumerate(entries):
                if not _species_entry_matches(
                    key_pattern, index, entry_selector, combo
                ):
                    return False
    return True


def resolve_baseline_comparisons(
    comparisons: list[BaselineComparison], combos: list[Combo]
) -> dict[str, BaselineComparison]:
    """Resolve each baseline_comparisons entry to exactly one combination.

    Fails (ValueError) before any container runs when a selector matches no
    combination, a selector matches more than one, or a combination is
    claimed by multiple selectors — ambiguity is surfaced, never guessed.
    Returns combination name -> BaselineComparison entry.
    """
    resolved: dict[str, BaselineComparison] = {}
    for position, entry in enumerate(comparisons):
        selector_repr = entry.sweep_selector.model_dump(exclude_none=True)
        matches = [
            combo for combo in combos if _selector_matches(entry.sweep_selector, combo)
        ]
        if not matches:
            raise ValueError(
                f"baseline_comparisons[{position}] (ulid {entry.ulid}, selector {selector_repr}) "
                "matches no combination"
            )
        if len(matches) > 1:
            names = [combo.name for combo in matches]
            raise ValueError(
                f"baseline_comparisons[{position}] (ulid {entry.ulid}, selector {selector_repr}) "
                f"is ambiguous: it matches {names}; refine the selector"
            )
        (combo,) = matches
        if combo.name in resolved:
            raise ValueError(
                f"combination {combo.name!r} is claimed by multiple selectors "
                f"(ulids {resolved[combo.name].ulid} and {entry.ulid})"
            )
        resolved[combo.name] = entry
        logger.info("baseline %s resolved to combination %s", entry.ulid, combo.name)
    return resolved


def _stringified_attrs(attrs: dict[str, object]) -> dict[str, str]:
    return {str(key): str(value) for key, value in attrs.items()}


def _compare_variable(
    name: str, realization: xr.DataArray, baseline: xr.DataArray, atol: float
) -> VariableComparison:
    dtype_match = realization.dtype == baseline.dtype
    attrs_real = _stringified_attrs(realization.attrs)
    attrs_base = _stringified_attrs(baseline.attrs)
    attributes_match = attrs_real == attrs_base
    detail_parts: list[str] = []
    if not dtype_match:
        detail_parts.append(f"dtype {realization.dtype} != {baseline.dtype}")
    if not attributes_match:
        detail_parts.append(f"attributes {attrs_real} != {attrs_base}")

    if realization.dims != baseline.dims or realization.shape != baseline.shape:
        detail_parts.append(
            f"shape {realization.dims}{realization.shape} != {baseline.dims}{baseline.shape}"
        )
        return VariableComparison(
            name=name,
            dtype_match=dtype_match,
            data_match=False,
            attributes_match=attributes_match,
            max_abs_diff=None,
            detail="; ".join(detail_parts),
        )

    real = dsa.asarray(realization.data)
    base = dsa.asarray(baseline.data)
    if np.issubdtype(realization.dtype, np.floating) and np.issubdtype(
        baseline.dtype, np.floating
    ):
        nan_positions_equal = dsa.isnan(real) == dsa.isnan(base)
        both_nan = dsa.isnan(real) & dsa.isnan(base)
        if atol == 0.0:
            values_equal = (real == base) | both_nan
        else:
            values_equal = (abs(real - base) <= atol) | both_nan
        elements_ok = values_equal & nan_positions_equal
        equal_graph = elements_ok.all()
        mismatch_graph = dsa.sum(~elements_ok)
        diff_graph = dsa.nanmax(abs(real - base))
    else:
        elements_ok = (real == base) if atol == 0.0 else (abs(real - base) <= atol)
        equal_graph = elements_ok.all()
        mismatch_graph = dsa.sum(~elements_ok)
        diff_graph = abs(real - base).max()

    # Difference statistics: the descriptive set applied to the difference
    # field, plus RMSE — one batched compute alongside the match reductions.
    diff_flat = (real.astype(float) - base.astype(float)).ravel()
    stats_graphs = (
        dsa.sqrt(dsa.nanmean(diff_flat**2)),
        dsa.sum(~dsa.isnan(diff_flat)),
        dsa.nansum(diff_flat),
        dsa.nanmean(diff_flat),
        dsa.nanstd(diff_flat),
        dsa.nanmin(diff_flat),
        dsa.nanmax(diff_flat),
        dsa.nanmedian(diff_flat, axis=0),
    )
    (
        equal,
        n_mismatched,
        max_diff,
        rmse,
        n_evaluated,
        diff_sum,
        diff_mean,
        diff_std,
        diff_min,
        diff_max,
        diff_median,
    ) = dask.compute(equal_graph, mismatch_graph, diff_graph, *stats_graphs)
    data_match = bool(equal) and dtype_match
    max_abs_diff = float(max_diff) if np.isfinite(max_diff) else None
    if not bool(equal):
        detail_parts.append(f"data differs (max abs diff {max_abs_diff})")

    return VariableComparison(
        name=name,
        dtype_match=dtype_match,
        data_match=data_match,
        attributes_match=attributes_match,
        max_abs_diff=max_abs_diff,
        rmse=float(rmse),
        n_evaluated=int(n_evaluated),
        n_mismatched=int(n_mismatched),
        diff_sum=float(diff_sum),
        diff_mean=float(diff_mean),
        diff_std=float(diff_std),
        diff_min=float(diff_min),
        diff_max=float(diff_max),
        diff_median=float(diff_median),
        detail="; ".join(detail_parts) or None,
    )


def _netcdf_data_model(path: Path) -> str:
    with netCDF4.Dataset(path) as ds:
        return str(ds.data_model)


def _compare_file(
    realization_path: Path, baseline_path: Path, atol: float
) -> FileComparison:
    format_match = _netcdf_data_model(realization_path) == _netcdf_data_model(
        baseline_path
    )

    with (
        xr.open_dataset(
            realization_path,
            engine="netcdf4",
            chunks="auto",
            decode_cf=False,
            decode_coords=False,
        ) as real,
        xr.open_dataset(
            baseline_path,
            engine="netcdf4",
            chunks="auto",
            decode_cf=False,
            decode_coords=False,
        ) as base,
    ):
        dimensions_match = dict(real.sizes) == dict(base.sizes)
        real_vars = set(map(str, real.variables))
        base_vars = set(map(str, base.variables))
        variables_match = real_vars == base_vars
        global_attributes_match = _stringified_attrs(real.attrs) == _stringified_attrs(
            base.attrs
        )

        variables = [
            _compare_variable(name, real[name], base[name], atol)
            for name in sorted(real_vars & base_vars)
        ]

    passed = (
        format_match
        and dimensions_match
        and variables_match
        and global_attributes_match
        and all(variable.passed for variable in variables)
    )
    comparison = FileComparison(
        file=realization_path.name,
        format_match=format_match,
        dimensions_match=dimensions_match,
        variables_match=variables_match,
        global_attributes_match=global_attributes_match,
        variables=variables,
        passed=passed,
    )
    logger.info(
        "compared %s: %s", realization_path.name, "passed" if passed else "FAILED"
    )
    return comparison


def compare_with_baseline(
    combo_dir: Path,
    baseline_dir: Path,
    atol: float,
    run_id: str,
    suite: str,
    combo: str,
    combo_id: str,
    baseline_ulid: str,
) -> BaselineComparisonResult:
    """Compare every NetCDF of a combination against its baseline."""
    logger.info(
        "comparing combo %s against baseline %s (atol=%s)", combo, baseline_ulid, atol
    )
    real_names = {path.name for path in combo_dir.glob("*.nc")}
    base_names = {path.name for path in baseline_dir.glob("*.nc")}
    file_names_match = real_names == base_names
    if not file_names_match:
        logger.error(
            "file sets differ for combo %s: missing %s, unexpected %s",
            combo,
            sorted(base_names - real_names),
            sorted(real_names - base_names),
        )

    files = [
        _compare_file(combo_dir / name, baseline_dir / name, atol)
        for name in sorted(real_names & base_names)
    ]
    passed = file_names_match and all(file.passed for file in files)
    result = BaselineComparisonResult(
        run_id=run_id,
        suite=suite,
        combo=combo,
        combo_id=combo_id,
        baseline_ulid=baseline_ulid,
        atol=atol,
        file_names_match=file_names_match,
        files=files,
        passed=passed,
    )
    if passed:
        logger.info("comparison passed for combo %s", combo)
    else:
        logger.error(
            "comparison FAILED for combo %s: %s", combo, result.failure_summary()
        )
    return result


_COMPARISON_COLUMNS = [
    "run_id",
    "suite",
    "combo_id",
    "combo",
    "baseline_ulid",
    "atol",
    "file",
    "file_names_match",
    "format_match",
    "dimensions_match",
    "variables_match",
    "global_attributes_match",
    "variable",
    "dtype_match",
    "data_match",
    "attributes_match",
    "max_abs_diff",
    "rmse",
    "n_evaluated",
    "n_mismatched",
    "diff_sum",
    "diff_mean",
    "diff_std",
    "diff_min",
    "diff_max",
    "diff_median",
    "passed",
]


def write_comparison_csv(
    result: BaselineComparisonResult, csv_path: Path
) -> pd.DataFrame:
    """Flatten one combination's comparison result to CSV: one row per
    (file, variable), replacing the former YAML artifact."""
    rows = []
    for file in result.files:
        for variable in file.variables:
            rows.append(
                {
                    "run_id": result.run_id,
                    "suite": result.suite,
                    "combo_id": result.combo_id,
                    "combo": result.combo,
                    "baseline_ulid": result.baseline_ulid,
                    "atol": result.atol,
                    "file": file.file,
                    "file_names_match": result.file_names_match,
                    "format_match": file.format_match,
                    "dimensions_match": file.dimensions_match,
                    "variables_match": file.variables_match,
                    "global_attributes_match": file.global_attributes_match,
                    "variable": variable.name,
                    "dtype_match": variable.dtype_match,
                    "data_match": variable.data_match,
                    "attributes_match": variable.attributes_match,
                    "max_abs_diff": variable.max_abs_diff,
                    "rmse": variable.rmse,
                    "n_evaluated": variable.n_evaluated,
                    "n_mismatched": variable.n_mismatched,
                    "diff_sum": variable.diff_sum,
                    "diff_mean": variable.diff_mean,
                    "diff_std": variable.diff_std,
                    "diff_min": variable.diff_min,
                    "diff_max": variable.diff_max,
                    "diff_median": variable.diff_median,
                    "passed": result.passed,
                }
            )
    frame = pd.DataFrame(rows, columns=_COMPARISON_COLUMNS)
    frame.to_csv(csv_path, index=False)
    logger.info("wrote %s comparison row(s) to %s", len(frame), csv_path)
    return frame


def concatenate_comparison_csvs(csv_paths: list[Path], out_path: Path) -> pd.DataFrame:
    """Concatenate per-combo comparison CSVs into the suite-level record."""
    combined = pd.concat([pd.read_csv(path) for path in csv_paths], ignore_index=True)
    combined.to_csv(out_path, index=False)
    logger.info(
        "concatenated %s comparison csv file(s) (%s rows) to %s",
        len(csv_paths),
        len(combined),
        out_path,
    )
    return combined
