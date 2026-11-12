"""What a correct monetio run of a generated config writes: one NetCDF file
at `Path(config.output).name` with CF-1.8 / UGRID-1.0 `(time, node)` dimensions
and a volatile global `history` attribute."""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import Field

from applications.monetio.config import MonetioConfig
from models.base import StrictModel


class MonetioAssertionSpec(StrictModel):
    """Expected NetCDF dimensions, coordinate variables, and volatile attributes
    for a `monetio` run."""

    standard_dimensions: tuple[str, ...] = Field(
        default=("time", "node"),
        description="Expected observation variable dimensions (CF-1.8 / UGRID-1.0)",
    )
    coordinate_variables: frozenset[str] = Field(
        default=frozenset({"latitude", "longitude", "lat", "lon", "time", "siteid"}),
        description=(
            "Static coordinate/metadata variables on `(node,)` or `(time,)` rather "
            "than `(time, node)`"
        ),
    )
    volatile_global_attributes: tuple[str, ...] = Field(
        default=("history",),
        description=(
            "Global NetCDF attributes stamped with wall-clock metadata by monetio's "
            "BaseReader.log_history()"
        ),
    )

    def output_variable_names(self, config: MonetioConfig) -> list[str]:
        """Expected observation output variables configured to check against `standard_dimensions`."""
        return [v for v in config.variables if v not in self.coordinate_variables]


def expected_output_count(_config: MonetioConfig) -> int:
    """`monetio load -o <output>` writes a single NetCDF file per invocation."""
    return 1


def expected_output_filenames(config: MonetioConfig) -> set[str]:
    """The basename of `config.output` inside the combo output directory."""
    return {PurePosixPath(config.output).name}


def output_variable_names(config: MonetioConfig) -> list[str]:
    """Expected observation output variables configured to check against `standard_dimensions`."""
    return MonetioAssertionSpec().output_variable_names(config)
