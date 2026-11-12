"""monetio's suite schema: sweep axes (`case`, `freq`), baseline selectors,
and the suite subclass."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from applications.base import SweepBase, SweepSelectorBase
from combos import Combo
from models.suite_config import BaselineComparison, SuiteConfig, valid_regex


def _unique_strings(values: list[str] | None) -> list[str] | None:
    if values is not None and len(values) != len(set(values)):
        raise ValueError("duplicate values in sweep list")
    return values


class MonetioSweep(SweepBase):
    """Swept dimensions for a monetio reader suite (`case` preset and/or
    resampling `freq`)."""

    case: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Named reader presets from the base config's `cases:` map",
    )
    freq: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Resampling frequency strings setting `kwargs.freq` (e.g. '1D', '30D')",
    )

    _unique = field_validator("case", "freq")(_unique_strings)


class MonetioSweepSelector(SweepSelectorBase):
    """Structural pattern matching a combination's swept monetio dimensions."""

    case: str | None = Field(
        default=None,
        description="Regex (fullmatch) against the combination's swept `case` value",
    )
    freq: str | None = Field(
        default=None,
        description="Regex (fullmatch) against the combination's swept `freq` value",
    )

    _regex = field_validator("case", "freq")(valid_regex)


class MonetioBaselineComparison(BaselineComparison):
    sweep_selector: MonetioSweepSelector = Field(
        description="Structural pattern selecting exactly one enumerated combination"
    )


class MonetioSuiteConfig(SuiteConfig):
    """A monetio suite: the generic suite with monetio's sweep and selectors."""

    application: Literal["monetio"] = "monetio"
    baseline_comparisons: list[MonetioBaselineComparison] = Field(
        default_factory=list,
        description="Per-combination baseline comparisons; empty/absent disables",
    )
    sweep: MonetioSweep = Field(
        default_factory=MonetioSweep,
        description=(
            "Dimensions and values defining the combination space; absent "
            "or empty runs the base config as the single combination"
        ),
    )


def selector_matches(selector: MonetioSweepSelector, combo: Combo) -> bool:
    """Every constrained field in `selector` must fullmatch the combo's
    corresponding swept dimension value."""
    swept = {dimension.field: value.value for dimension, value in combo.values}
    for field, pattern in (("case", selector.case), ("freq", selector.freq)):
        if pattern is not None and (
            field not in swept or re.fullmatch(pattern, swept[field]) is None
        ):
            return False
    return True
