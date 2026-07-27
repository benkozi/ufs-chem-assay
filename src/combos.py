"""Sweep -> combinations: enumeration, naming, ids, and driver-config generation.

Dimensions are derived from the sweep and attached to named streams or
positional species entries. The sweep is normalized before enumeration
(targets and value lists sorted) so declaration order never influences combo
ids, names, or enumeration order.

All generated configs are built as CeceConfig instances and written only via
CeceConfig.to_yaml() so every config the driver receives has passed pydantic
validation.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Callable, TypedDict

import pandas as pd
from ulid import ULID

from logs import get_logger
from models.cece_config import CeceConfig, Vdist, VdistMethod
from models.suite_config import Sweep

logger = get_logger("combos")

NAME_SEPARATOR = "__"  # shell-safe; nothing parses the name back (combos.csv does)


# Companion vdist fields required for a meaningful config when sweeping
# VdistMethod. Layer indices are 0-based and inclusive in the stacking engine.
class _VdistCompanions(TypedDict, total=False):
    """Companion Vdist fields each swept method needs to take effect."""

    h_start: float
    h_end: float
    p_start: float
    p_end: float
    layer_start: int
    layer_end: int


_VDIST_COMPANIONS: dict[VdistMethod, _VdistCompanions] = {
    VdistMethod.height: {"h_start": 0.0, "h_end": 100.0},
    VdistMethod.pressure: {"p_start": 100000.0, "p_end": 90000.0},
    VdistMethod.range: {"layer_start": 0, "layer_end": 2},
    VdistMethod.single: {"layer_start": 0},
    VdistMethod.pbl: {},
}

# Fixed canonical field order within a target: (config field, name tag).
_SPECIES_FIELDS: tuple[tuple[str, str], ...] = (
    ("operation", "op"),
    ("category", "cat"),
    ("vdist_method", "vd"),
)
_STREAM_FIELDS: tuple[tuple[str, str], ...] = (
    ("taxmode", "tax"),
    ("tintalgo", "tint"),
    ("mapalgo", "map"),
)


@dataclass(frozen=True)
class Dimension:
    target: str  # attachment target label, e.g. "MACCITY", "co", "co-1"
    field: str  # config field name, e.g. "mapalgo"
    tag: str  # short tag used in combination names, e.g. "map"
    apply: Callable[[CeceConfig, StrEnum], None]
    group: str  # "stream" or "species": which sweep structure this came from
    key: str  # stream name or species name (without the entry-index suffix)
    index: int  # species entry index; always 0 for streams


@dataclass(frozen=True)
class Combo:
    """One point in the combination space: swept dimensions in canonical order
    plus a runtime id.

    combo_id is a ULID minted at enumeration (runtime-only, never from
    configuration — same rule as the session run_id): unique per combo per
    run, time-ordered, filesystem-safe. It carries no content semantics;
    combos.csv records every combo's effective parameters, and cross-run
    joins use suite + name (or the parameter columns), not the id."""

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


def _apply_stream_field(
    stream_name: str, field: str
) -> Callable[[CeceConfig, StrEnum], None]:
    def apply(config: CeceConfig, value: StrEnum) -> None:
        for stream in config.cece_data.streams:
            if stream.name == stream_name:
                setattr(stream, field, value)
                return
        raise ValueError(f"stream {stream_name!r} not found in config")

    return apply


def _apply_species_field(
    species: str, index: int, field: str
) -> Callable[[CeceConfig, StrEnum], None]:
    def apply(config: CeceConfig, value: StrEnum) -> None:
        entry = config.species[species][index]
        if field == "vdist_method":
            # The driver reads vdist as a nested block; the swept method gets
            # the companion fields it needs to take effect.
            assert isinstance(value, VdistMethod)
            entry.vdist = Vdist(method=value, **_VDIST_COMPANIONS[value])
        else:
            setattr(entry, field, value)

    return apply


def _sorted_values(values: list[StrEnum]) -> tuple[StrEnum, ...]:
    # Cannot change any combo's id (a name holds only its own values), but
    # makes enumeration, execution, and combos.csv order declaration-independent.
    return tuple(sorted(values, key=lambda value: value.value))


def _species_dimensions(
    sweep: Sweep, base_config: CeceConfig
) -> list[tuple[Dimension, tuple[StrEnum, ...]]]:
    if sweep.species is None:
        return []
    dimensions: list[tuple[Dimension, tuple[StrEnum, ...]]] = []
    for species in sorted(sweep.species):  # lexicographic, not declaration order
        if species not in base_config.species:
            raise ValueError(
                f"sweep targets species {species!r}, which is not in the base config "
                f"(species: {sorted(base_config.species)})"
            )
        entry_sweeps = sweep.species[species]
        n_entries = len(base_config.species[species])
        if len(entry_sweeps) > n_entries:
            raise ValueError(
                f"sweep for species {species!r} has {len(entry_sweeps)} entry blocks; "
                f"the base config has {n_entries} entries"
            )
        for index, entry_sweep in enumerate(entry_sweeps):
            target = species if index == 0 else f"{species}-{index}"
            for field, tag in _SPECIES_FIELDS:
                values = getattr(entry_sweep, field)
                if values:
                    dimensions.append(
                        (
                            Dimension(
                                target,
                                field,
                                tag,
                                _apply_species_field(species, index, field),
                                group="species",
                                key=species,
                                index=index,
                            ),
                            _sorted_values(values),
                        )
                    )
    return dimensions


def _stream_dimensions(
    sweep: Sweep, base_config: CeceConfig
) -> list[tuple[Dimension, tuple[StrEnum, ...]]]:
    if sweep.cece_data is None:
        return []
    base_names = [stream.name for stream in base_config.cece_data.streams]
    dimensions: list[tuple[Dimension, tuple[StrEnum, ...]]] = []
    for stream_sweep in sorted(
        sweep.cece_data.streams, key=lambda s: s.name
    ):  # not declaration order
        if base_names.count(stream_sweep.name) != 1:
            raise ValueError(
                f"sweep targets stream {stream_sweep.name!r}, which must match exactly one "
                f"base-config stream (streams: {base_names})"
            )
        for field, tag in _STREAM_FIELDS:
            values = getattr(stream_sweep, field)
            if values:
                dimensions.append(
                    (
                        Dimension(
                            stream_sweep.name,
                            field,
                            tag,
                            _apply_stream_field(stream_sweep.name, field),
                            group="stream",
                            key=stream_sweep.name,
                            index=0,
                        ),
                        _sorted_values(values),
                    )
                )
    return dimensions


def enumerate_combos(sweep: Sweep, base_config: CeceConfig) -> list[Combo]:
    """Cartesian product of the sweep's attached dimensions, validated against
    the base config (unknown selectors fail here, before any container runs).
    Canonical order: species targets first, then stream targets."""
    dimensions = _species_dimensions(sweep, base_config) + _stream_dimensions(
        sweep, base_config
    )
    if not dimensions:
        # Sweep-less suite: the base config itself is the single combination.
        return [Combo(values=(), combo_id=str(ULID()))]
    ordered = tuple(dimension for dimension, _ in dimensions)
    return [
        Combo(values=tuple(zip(ordered, chosen)), combo_id=str(ULID()))
        for chosen in itertools.product(*(values for _, values in dimensions))
    ]


def build_config(combo: Combo, output_directory: str, config_path: Path) -> CeceConfig:
    """Fresh base config loaded from config_path with the combo's enum values
    applied and NetCDF output pointed at the combo's own directory. Loading
    per combo keeps combinations isolated."""
    config = CeceConfig.from_yaml(config_path)
    for dimension, value in combo.values:
        dimension.apply(config, value)
    assert config.output is not None
    config.output.directory = output_directory
    # Always — whatever the base config says: a relative log_file (the driver
    # tees run output to it) would otherwise land in the checkout at /work.
    config.driver.log_file = str(PurePosixPath(output_directory) / "cece.log")
    return config


def _effective_parameter_rows(
    combo: Combo, config: CeceConfig
) -> list[tuple[str, str, str, bool]]:
    """(target, field, value, swept) for every sweepable dimension of one
    combo, read from its generated config — swept rows carry the swept value,
    pinned rows the base value, and unset optionals an empty value. Canonical
    order: species targets (sorted, entry order) first, then streams (sorted),
    fields in the fixed field order."""
    swept = {(dimension.target, dimension.field) for dimension, _ in combo.values}
    rows: list[tuple[str, str, str, bool]] = []
    for species in sorted(config.species):
        for index, entry in enumerate(config.species[species]):
            target = species if index == 0 else f"{species}-{index}"
            effective = {
                "operation": entry.operation.value,
                "category": entry.category.value if entry.category else "",
                "vdist_method": entry.vdist.method.value if entry.vdist else "",
            }
            for field, _ in _SPECIES_FIELDS:
                rows.append((target, field, effective[field], (target, field) in swept))
    for stream in sorted(config.cece_data.streams, key=lambda s: s.name):
        for field, _ in _STREAM_FIELDS:
            value: StrEnum = getattr(stream, field)
            rows.append(
                (stream.name, field, value.value, (stream.name, field) in swept)
            )
    return rows


def write_combos_csv(
    entries: list[tuple[str, Combo, CeceConfig]], run_id: str, csv_path: Path
) -> pd.DataFrame:
    """The effective-parameter table: for every combo of every suite, one row
    per (target, sweepable field) with the value from the combo's generated
    config — the dereference map from combo-id directories back to the full
    tested parameter set, joinable on parameters whether swept or pinned.
    Entries are (suite name, combo, generated config)."""
    columns = [
        "run_id",
        "combo_id",
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
            "suite": suite_name,
            "name": combo.name,
            "target": target,
            "field": field,
            "value": value,
            "swept": swept,
        }
        for suite_name, combo, config in entries
        for target, field, value, swept in _effective_parameter_rows(combo, config)
    ]
    frame = pd.DataFrame(rows, columns=columns)
    frame.to_csv(csv_path, index=False)
    logger.info("wrote %s combination row(s) to %s", len(frame), csv_path)
    return frame
