"""What a correct monetio run of a generated config writes: one NetCDF file
at `Path(config.output).name` with CF-1.8 / UGRID-1.0 `(time, node)` dimensions
and a volatile global `history` attribute."""

from __future__ import annotations

from pathlib import PurePosixPath

from applications.monetio.config import MonetioConfig

STANDARD_DIMENSIONS = ("time", "node")

# Static coordinate/metadata variables on `(node,)` or `(time,)` rather than `(time, node)`.
_COORDINATE_VARIABLES = frozenset(
    {"latitude", "longitude", "lat", "lon", "time", "siteid"}
)

# monetio's BaseReader.log_history() stamps wall-clock `datetime.now()` into
# the global `history` attribute on every read.
VOLATILE_GLOBAL_ATTRIBUTES = ("history",)


def expected_output_count(_config: MonetioConfig) -> int:
    """`monetio load -o <output>` writes a single NetCDF file per invocation."""
    return 1


def expected_output_filenames(config: MonetioConfig) -> set[str]:
    """The basename of `config.output` inside the combo output directory."""
    return {PurePosixPath(config.output).name}


def output_variable_names(config: MonetioConfig) -> list[str]:
    """Expected observation output variables configured to check against `STANDARD_DIMENSIONS`."""
    return [v for v in config.variables if v not in _COORDINATE_VARIABLES]
