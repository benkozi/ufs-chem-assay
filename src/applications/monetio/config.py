"""Typed configuration schema for a `monetio load` driver invocation."""

from __future__ import annotations

from pathlib import Path
from typing import Self

import yaml
from pydantic import Field

from applications.base import DriverConfig
from models.base import StrictModel
from models.strict_yaml import StrictBoolLoader

ScalarKwarg = str | int | float | bool | None
KwargValue = ScalarKwarg | list[ScalarKwarg]


class MonetioCaseConfig(StrictModel):
    """Named reader preset inside a base MonetioConfig that a `case` sweep
    dimension can select to override `files`, `variables`, and `kwargs`."""

    files: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Optional override for input file path(s) passed as `-f <path>`",
    )
    variables: list[str] | None = Field(
        default=None,
        description="Optional override for expected output variables checked for standard dimensions",
    )
    kwargs: dict[str, KwargValue] | None = Field(
        default=None,
        description="Optional reader keyword arguments merged into the base `kwargs`",
    )


class MonetioConfig(DriverConfig):
    """Typed schema for a `monetio load <source>` invocation."""

    source: str = Field(
        min_length=1,
        description="Registered monetio reader name passed to `monetio load <source>` (e.g. 'aeronet')",
    )
    files: list[str] = Field(
        min_length=1,
        description="Input file path(s) passed as `-f <path>` to `monetio load`",
    )
    output: str = Field(
        min_length=1,
        description="Output NetCDF path passed as `-o <path>` to `monetio load`",
    )
    variables: list[str] = Field(
        default_factory=list,
        description="Expected output variable names verified to carry standard_dimensions",
    )
    kwargs: dict[str, KwargValue] = Field(
        default_factory=dict,
        description="Reader keyword arguments passed as `-k key=value` to `monetio load`",
    )
    cases: dict[str, MonetioCaseConfig] | None = Field(
        default=None,
        description="Named reader presets selectable by `sweep.case`",
    )

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        with open(path) as f:
            return cls.model_validate(yaml.load(f, Loader=StrictBoolLoader))

    def to_yaml(self, path: Path) -> None:
        with open(path, "w") as f:
            yaml.dump(
                self.model_dump(exclude_none=True, mode="json"),
                f,
                default_flow_style=False,
                sort_keys=False,
            )
