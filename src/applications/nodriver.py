"""An application the harness clones, builds and tests but never sweeps:
it has no driver, so no suite, no driver configuration, no combinations.

It is an `Application` like any other — one registry, one `--application`
vocabulary — whose `stages` stop short of the driver stages. Everything a
driver sweep would ask of it is one refusal (`Application.no_driver_suite`),
written once here instead of in every such adapter; in normal operation
nothing reaches these methods, because the shared code asks `stages` first
(the registry's `driver_application`, `cli/stages.render_stage`, `run`).
A subclass names itself, its settings and run-config section, its stages,
and its stage bodies.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import ClassVar, Self

from pydantic import model_validator

from applications.base import (
    Application,
    DriverConfig,
    NoDriverSuite,
    Stage,
    SweepBase,
    SweepSelectorBase,
)
from combos import Combo, Dimension, ParameterRow
from models.suite_config import SuiteConfig

_NO_CONFIG = "an application without a driver suite has no driver configuration"


class NoDriverConfig(DriverConfig):
    """The driver configuration of an application that has no driver:
    nothing can be read or written as one."""

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        raise NoDriverSuite(f"{path}: {_NO_CONFIG}")

    def to_yaml(self, path: Path) -> None:
        raise NoDriverSuite(f"{path}: {_NO_CONFIG}")


class NoDriverSuiteConfig(SuiteConfig):
    """The suite model of an application that has no driver: no document
    validates as its suite."""

    @model_validator(mode="before")
    @classmethod
    def _refuse(cls, value: object) -> object:
        name = value.get("application") if isinstance(value, dict) else None
        raise NoDriverSuite(f"{name or 'this application'} has no driver suite")


class NoDriverApplication(Application):
    """The base of an application that is built and tested, never swept."""

    container_workdir: ClassVar[PurePosixPath] = PurePosixPath("/work")
    default_suite: ClassVar[str] = ""
    data_dirname: ClassVar[str] = "data"
    standard_dimensions: ClassVar[tuple[str, ...]] = ()
    config_model = NoDriverConfig
    suite_model = NoDriverSuiteConfig
    stages: ClassVar[frozenset[Stage]] = frozenset({Stage.BUILD, Stage.TEST})

    def dimensions(
        self, sweep: SweepBase, base_config: DriverConfig
    ) -> list[tuple[Dimension, tuple[StrEnum, ...]]]:
        _ = sweep, base_config
        raise self.no_driver_suite()

    def build_config(
        self, combo: Combo, output_directory: str, config_path: Path
    ) -> DriverConfig:
        _ = combo, output_directory, config_path
        raise self.no_driver_suite()

    def effective_parameters(
        self, combo: Combo, config: DriverConfig
    ) -> list[ParameterRow]:
        _ = combo, config
        raise self.no_driver_suite()

    def expected_output_count(self, config: DriverConfig) -> int:
        _ = config
        raise self.no_driver_suite()

    def expected_output_filenames(self, config: DriverConfig) -> set[str]:
        _ = config
        raise self.no_driver_suite()

    def output_variable_names(self, config: DriverConfig) -> list[str]:
        _ = config
        raise self.no_driver_suite()

    def selector_matches(self, selector: SweepSelectorBase, combo: Combo) -> bool:
        _ = selector, combo
        raise self.no_driver_suite()
