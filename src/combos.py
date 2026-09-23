"""Sweep -> combinations: the shared enumeration core — dimensions with their
value lists (produced by the application adapter, which knows the sweep's
shape), the cartesian product, canonical naming, runtime ids, and the
effective-parameter table.

The adapter sorts targets and value lists before handing over dimensions,
so declaration order never influences combo ids, names, or enumeration
order. Every generated config is an instance of the adapter's DriverConfig,
written only via its to_yaml, so the driver never receives anything that
did not pass pydantic validation.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

import pandas as pd
from ulid import ULID

from applications.base import DriverConfig
from logs import get_logger

logger = get_logger("combos")

NAME_SEPARATOR = "__"  # shell-safe; nothing parses the name back (combos.csv does)

# One combos.csv row: (target, field, value, swept).
ParameterRow = tuple[str, str, str, bool]


@dataclass(frozen=True)
class Dimension:
    """One swept dimension: where it attaches, what it sets, how it is
    named, and how to apply a value to a config. `group`/`key`/`index` are
    the adapter's attachment metadata (e.g. stream vs species entry), which
    its selector matching reads back."""

    target: str  # attachment target label, e.g. "MACCITY", "co", "co-1"
    field: str  # config field name, e.g. "mapalgo"
    tag: str  # short tag used in combination names, e.g. "map"
    apply: Callable[[DriverConfig, StrEnum], None]
    group: str  # attachment group, e.g. "stream" or "species"
    key: str  # attachment key, e.g. stream name or species name
    index: int  # attachment index within the key (0 when not positional)


@dataclass(frozen=True)
class Combo:
    """One point in the combination space: swept dimensions in canonical order
    plus a runtime id.

    combo_id is a ULID minted at enumeration (runtime-only, never from
    configuration — same rule as the session run_id): unique per combo per
    run, time-ordered, filesystem-safe. It carries no content semantics;
    combos.csv records every combo's effective parameters, and cross-run
    joins use application + suite + name (or the parameter columns), not
    the id."""

    values: tuple[tuple[Dimension, StrEnum], ...]
    combo_id: str

    @property
    def name(self) -> str:
        """Canonical combination string; deterministic, used as the pytest id.
        The dimension-less identity combo (sweep-less suites) is "base" — an
        empty string cannot serve as a pytest id or directory-name seed."""
        if not self.values:
            return "base"
        return NAME_SEPARATOR.join(
            f"{dim.target}.{dim.tag}-{value.value}" for dim, value in self.values
        )


def sorted_values(values: list[StrEnum]) -> tuple[StrEnum, ...]:
    """Adapters sort every value list: cannot change any combo's id (a name
    holds only its own values), but makes enumeration, execution, and
    combos.csv order declaration-independent."""
    return tuple(sorted(values, key=lambda value: value.value))


def enumerate_combos(
    dimensions: list[tuple[Dimension, tuple[StrEnum, ...]]],
) -> list[Combo]:
    """Cartesian product of the dimensions, in the order the adapter gave
    them (its canonical order). No dimensions: the sweep-less suite, whose
    base config is the single combination."""
    if not dimensions:
        return [Combo(values=(), combo_id=str(ULID()))]
    ordered = tuple(dimension for dimension, _ in dimensions)
    return [
        Combo(values=tuple(zip(ordered, chosen)), combo_id=str(ULID()))
        for chosen in itertools.product(*(values for _, values in dimensions))
    ]


def write_combos_csv(
    entries: list[tuple[str, Combo, list[ParameterRow]]],
    run_id: str,
    application: str,
    csv_path: Path,
) -> pd.DataFrame:
    """The effective-parameter table: for every combo of every suite, one row
    per (target, sweepable field) with the value from the combo's generated
    config — the dereference map from combo-id directories back to the full
    tested parameter set, joinable on parameters whether swept or pinned.
    Entries are (suite name, combo, the adapter's effective-parameter rows)."""
    columns = [
        "run_id",
        "combo_id",
        "application",
        "suite",
        "name",
        "target",
        "field",
        "value",
        "swept",
    ]
    rows = [
        {
            "run_id": run_id,
            "combo_id": combo.combo_id,
            "application": application,
            "suite": suite_name,
            "name": combo.name,
            "target": target,
            "field": field,
            "value": value,
            "swept": swept,
        }
        for suite_name, combo, parameter_rows in entries
        for target, field, value, swept in parameter_rows
    ]
    frame = pd.DataFrame(rows, columns=columns)
    frame.to_csv(csv_path, index=False)
    logger.info("wrote %s combination row(s) to %s", len(frame), csv_path)
    return frame
