"""CECE's side of enumeration: sweep -> dimensions (attached to named
streams or positional species entries, validated against the base config),
config generation with the vdist companions and the cece.log redirect, and
the effective-parameter rows for combos.csv."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Callable, TypedDict

from applications.cece.config import CeceConfig, Vdist, VdistMethod
from applications.cece.suite import CeceSweep
from combos import Combo, Dimension, ParameterRow, sorted_values


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


def _as_cece(config: object) -> CeceConfig:
    # The one generic-to-concrete narrowing: a wrong application cannot reach
    # here (the suite model validated the sweep for this adapter).
    assert isinstance(config, CeceConfig), type(config)
    return config


def _apply_stream_field(
    stream_name: str, field: str
) -> Callable[[object, StrEnum], None]:
    def apply(config: object, value: StrEnum) -> None:
        for stream in _as_cece(config).cece_data.streams:
            if stream.name == stream_name:
                setattr(stream, field, value)
                return
        raise ValueError(f"stream {stream_name!r} not found in config")

    return apply


def _apply_species_field(
    species: str, index: int, field: str
) -> Callable[[object, StrEnum], None]:
    def apply(config: object, value: StrEnum) -> None:
        entry = _as_cece(config).species[species][index]
        if field == "vdist_method":
            # The driver reads vdist as a nested block; the swept method gets
            # the companion fields it needs to take effect.
            assert isinstance(value, VdistMethod)
            entry.vdist = Vdist(method=value, **_VDIST_COMPANIONS[value])
        else:
            setattr(entry, field, value)

    return apply


def _species_dimensions(
    sweep: CeceSweep, base_config: CeceConfig
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
                            sorted_values(values),
                        )
                    )
    return dimensions


def _stream_dimensions(
    sweep: CeceSweep, base_config: CeceConfig
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
                        sorted_values(values),
                    )
                )
    return dimensions


def dimensions(
    sweep: CeceSweep, base_config: CeceConfig
) -> list[tuple[Dimension, tuple[StrEnum, ...]]]:
    """The sweep's attached dimensions, validated against the base config
    (unknown selectors fail here, before any container runs). Canonical
    order: species targets first, then stream targets."""
    return _species_dimensions(sweep, base_config) + _stream_dimensions(
        sweep, base_config
    )


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


def effective_parameters(combo: Combo, config: CeceConfig) -> list[ParameterRow]:
    """(target, field, value, swept) for every sweepable dimension of one
    combo, read from its generated config — swept rows carry the swept value,
    pinned rows the base value, and unset optionals an empty value. Canonical
    order: species targets (sorted, entry order) first, then streams (sorted),
    fields in the fixed field order."""
    swept = {(dimension.target, dimension.field) for dimension, _ in combo.values}
    rows: list[ParameterRow] = []
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
