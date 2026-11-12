"""monetio's side of enumeration: sweep -> dimensions (`case`, `freq`),
config generation with output redirection, and effective-parameter rows
for `combos.csv`."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path, PurePosixPath

from applications.monetio.config import MonetioConfig
from applications.monetio.suite import MonetioSweep
from combos import Combo, Dimension, ParameterRow, sorted_values


def _as_monetio(config: object) -> MonetioConfig:
    assert isinstance(config, MonetioConfig), type(config)
    return config


def _dynamic_str_enum(name: str, values: list[str]) -> tuple[StrEnum, ...]:
    """Wrap arbitrary swept string tokens in a dynamic `StrEnum` so they
    flow through the shared `Dimension` / `Combo` enumeration machinery."""
    enum_cls = StrEnum(name, {v: v for v in sorted(values)})  # type: ignore[misc]
    return sorted_values(list(enum_cls))


def _apply_case(config: object, value: StrEnum) -> None:
    monetio = _as_monetio(config)
    assert monetio.cases is not None
    case_cfg = monetio.cases[value.value]
    if case_cfg.files is not None:
        monetio.files = list(case_cfg.files)
    if case_cfg.variables is not None:
        monetio.variables = list(case_cfg.variables)
    if case_cfg.kwargs is not None:
        monetio.kwargs = {**monetio.kwargs, **case_cfg.kwargs}


def _apply_freq(config: object, value: StrEnum) -> None:
    _as_monetio(config).kwargs["freq"] = value.value


def dimensions(
    sweep: MonetioSweep, base_config: MonetioConfig, *, target: str = "monetio"
) -> list[tuple[Dimension, tuple[StrEnum, ...]]]:
    """Validate `sweep` against `base_config` and return canonical dimensions
    (`case` first, then `freq`)."""
    dims: list[tuple[Dimension, tuple[StrEnum, ...]]] = []
    if sweep.case is not None:
        available = set(base_config.cases or {})
        for case_name in sweep.case:
            if case_name not in available:
                raise ValueError(
                    f"sweep references unknown case {case_name!r} "
                    f"(cases: {sorted(available)})"
                )
        dims.append(
            (
                Dimension(
                    target=target,
                    field="case",
                    tag="case",
                    apply=_apply_case,
                    group="reader",
                    key=target,
                    index=0,
                ),
                _dynamic_str_enum("SweptCase", sweep.case),
            )
        )
    if sweep.freq is not None:
        apply_fn: Callable[[object, StrEnum], None] = _apply_freq
        dims.append(
            (
                Dimension(
                    target=target,
                    field="freq",
                    tag="freq",
                    apply=apply_fn,
                    group="reader",
                    key=target,
                    index=0,
                ),
                _dynamic_str_enum("SweptFreq", sweep.freq),
            )
        )
    return dims


def build_config(
    combo: Combo, output_directory: str, config_path: Path
) -> MonetioConfig:
    """Load a fresh `MonetioConfig` from `config_path`, apply `combo`'s swept
    values in canonical order (`case` before `freq`), and redirect `output`
    into `output_directory`."""
    config = MonetioConfig.from_yaml(config_path)
    for dimension, value in combo.values:
        dimension.apply(config, value)
    filename = PurePosixPath(config.output).name
    config.output = str(PurePosixPath(output_directory) / filename)
    return config


def effective_parameters(
    combo: Combo, config: MonetioConfig, *, target: str = "monetio"
) -> list[ParameterRow]:
    """`(target, field, value, swept)` rows for `combos.csv`."""
    swept_values = {
        (dimension.target, dimension.field): value.value
        for dimension, value in combo.values
    }
    case_val = swept_values.get((target, "case"), "")
    rows: list[ParameterRow] = [
        (target, "source", config.source, False),
        (target, "case", case_val, (target, "case") in swept_values),
    ]
    for key in sorted(config.kwargs):
        raw_val = config.kwargs[key]
        str_val = "" if raw_val is None else str(raw_val)
        rows.append((target, key, str_val, (target, key) in swept_values))
    return rows
