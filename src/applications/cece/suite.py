"""CECE's suite schema: the sweep and baseline-selector models that mirror
its driver-config structure, the suite subclass that carries them, and the
selector matching against enumerated combinations."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Callable, Literal, TypeVar

from pydantic import Field, field_validator

from applications.base import SweepBase, SweepSelectorBase
from applications.cece.config import (
    Category,
    Mapalgo,
    Operation,
    Taxmode,
    Tintalgo,
    VdistMethod,
)
from combos import Combo
from models.base import StrictModel
from models.suite_config import BaselineComparison, SuiteConfig, valid_regex

_SweptEnum = TypeVar("_SweptEnum", bound=StrEnum)


def _unique_values(values: list[_SweptEnum] | None) -> list[_SweptEnum] | None:
    """Duplicate sweep values would enumerate two combinations with the same
    id and directory — rejected loudly rather than deduped."""
    if values is not None and len(values) != len(set(values)):
        raise ValueError("duplicate values in sweep list")
    return values


def _expand_enum_regex(enum_cls: type[StrEnum]) -> Callable[[object], object]:
    """Sweep values accept a regex string in place of a value list: expanded
    (fullmatch) against the enum's values into the sorted matching list at
    load time, so ".*" always means every value — including ones added after
    the suite was written — and run.yaml records the expanded list. A regex
    matching nothing, like an invalid one, fails the load."""

    def expand(value: object) -> object:
        if isinstance(value, str):
            try:
                pattern = re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regex {value!r}: {exc}") from exc
            matched = sorted(
                member.value for member in enum_cls if pattern.fullmatch(member.value)
            )
            if not matched:
                raise ValueError(
                    f"regex {value!r} matches no {enum_cls.__name__} value "
                    f"(values: {sorted(member.value for member in enum_cls)})"
                )
            return matched
        return value

    return expand


# ── Sweep ────────────────────────────────────────────────────────────────────


class StreamSweep(StrictModel):
    """Swept dimensions attached to one stream, selected by name."""

    name: str = Field(description="Stream in the base config this sweep attaches to")
    taxmode: list[Taxmode] | None = Field(None, min_length=1)
    tintalgo: list[Tintalgo] | None = Field(None, min_length=1)
    mapalgo: list[Mapalgo] | None = Field(None, min_length=1)

    _expand_taxmode = field_validator("taxmode", mode="before")(
        _expand_enum_regex(Taxmode)
    )
    _expand_tintalgo = field_validator("tintalgo", mode="before")(
        _expand_enum_regex(Tintalgo)
    )
    _expand_mapalgo = field_validator("mapalgo", mode="before")(
        _expand_enum_regex(Mapalgo)
    )
    _unique = field_validator("taxmode", "tintalgo", "mapalgo")(_unique_values)


class CeceDataSweep(StrictModel):
    streams: list[StreamSweep] = Field(min_length=1)

    @field_validator("streams")
    @classmethod
    def _unique_stream_names(cls, streams: list[StreamSweep]) -> list[StreamSweep]:
        names = [stream.name for stream in streams]
        if len(names) != len(set(names)):
            raise ValueError("duplicate stream names in sweep")
        return streams


class SpeciesEntrySweep(StrictModel):
    """Swept dimensions attached to one species entry, selected by list
    position (sweep list index i -> species.<name>[i]); {} skips an entry."""

    operation: list[Operation] | None = Field(None, min_length=1)
    category: list[Category] | None = Field(None, min_length=1)
    vdist_method: list[VdistMethod] | None = Field(None, min_length=1)

    _expand_operation = field_validator("operation", mode="before")(
        _expand_enum_regex(Operation)
    )
    _expand_category = field_validator("category", mode="before")(
        _expand_enum_regex(Category)
    )
    _expand_vdist_method = field_validator("vdist_method", mode="before")(
        _expand_enum_regex(VdistMethod)
    )
    _unique = field_validator("operation", "category", "vdist_method")(_unique_values)


class CeceSweep(SweepBase):
    """Sweeps mirror the driver-config structure and attach to named streams
    or positional species entries. The combination space is the cartesian
    product of every attached value list."""

    cece_data: CeceDataSweep | None = None
    species: dict[str, list[SpeciesEntrySweep]] | None = None


# ── Baseline selectors ───────────────────────────────────────────────────────


class StreamSweepSelector(StrictModel):
    """Mirror of StreamSweep with regexes at the leaves: name selects the
    stream target, sibling fields constrain that stream's swept values."""

    name: str = Field(description="Regex (fullmatch) against the stream target's name")
    taxmode: str | None = Field(
        None, description="Regex (fullmatch) against the stream's swept taxmode value"
    )
    tintalgo: str | None = Field(
        None, description="Regex (fullmatch) against the stream's swept tintalgo value"
    )
    mapalgo: str | None = Field(
        None, description="Regex (fullmatch) against the stream's swept mapalgo value"
    )

    _regex = field_validator("name", "taxmode", "tintalgo", "mapalgo")(valid_regex)


class CeceDataSweepSelector(StrictModel):
    streams: list[StreamSweepSelector] = Field(
        min_length=1, description="Stream selector blocks; all must be satisfied"
    )


class SpeciesEntrySweepSelector(StrictModel):
    """Mirror of SpeciesEntrySweep with regexes at the leaves; list position
    selects the species entry index, as in the sweep."""

    operation: str | None = Field(
        None, description="Regex (fullmatch) against the entry's swept operation value"
    )
    category: str | None = Field(
        None, description="Regex (fullmatch) against the entry's swept category value"
    )
    vdist_method: str | None = Field(
        None,
        description="Regex (fullmatch) against the entry's swept vdist_method value",
    )

    _regex = field_validator("operation", "category", "vdist_method")(valid_regex)


class CeceSweepSelector(SweepSelectorBase):
    """Mirror of CeceSweep: a structural pattern matched against a
    combination's swept elements. Unspecified structure is unconstrained."""

    cece_data: CeceDataSweepSelector | None = Field(
        None, description="Stream selector blocks; None leaves streams unconstrained"
    )
    species: dict[str, list[SpeciesEntrySweepSelector]] | None = Field(
        None,
        description="Species-name regex -> positional entry selectors; None leaves species unconstrained",
    )

    @field_validator("species")
    @classmethod
    def _species_keys_are_regexes(
        cls, species: dict[str, list[SpeciesEntrySweepSelector]] | None
    ) -> dict[str, list[SpeciesEntrySweepSelector]] | None:
        if species is not None:
            for key in species:
                valid_regex(key)
        return species


class CeceBaselineComparison(BaselineComparison):
    sweep_selector: CeceSweepSelector = Field(
        description="Structural pattern selecting exactly one enumerated combination"
    )


class CeceSuiteConfig(SuiteConfig):
    """A CECE suite: the generic suite with CECE's sweep and selectors."""

    application: Literal["cece"] = "cece"
    baseline_comparisons: list[CeceBaselineComparison] = Field(
        default_factory=list,
        description="Per-combination baseline comparisons; empty/absent disables",
    )
    sweep: CeceSweep = Field(
        default_factory=CeceSweep,
        description=(
            "Enum dimensions and values defining the combination space; absent "
            "or empty runs the base config as the single combination"
        ),
    )


# ── Selector matching ────────────────────────────────────────────────────────


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


def selector_matches(selector: CeceSweepSelector, combo: Combo) -> bool:
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
