"""A second, inert application adapter for the tests that need "several
applications in one run" (stage rendering, the CLI, the run config): every
method raises, the constants are real. Registered per test via
monkeypatch.setitem(REGISTRY, "stub", stub_application())."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path, PurePosixPath

from applications.base import (
    Application,
    ApplicationRunSection,
    ApplicationSettings,
    DriverConfig,
    SweepBase,
    SweepSelectorBase,
)
from cli.stages import Stage
from combos import Combo, Dimension, ParameterRow
from models.suite_config import SuiteConfig


class StubSection(ApplicationRunSection):
    pass


class StubSettings(ApplicationSettings):
    docker_image: str = "stub/image"
    driver_path: str = "./stub"


class StubSuiteConfig(SuiteConfig):
    pass


class StubApplication(Application):
    name = "stub"
    env_prefix = "STUB_"
    container_workdir = PurePosixPath("/opt/stub")
    default_suite = "stub-suite.yaml"
    checkout_dirname = "STUB"
    standard_dimensions = ("time",)
    settings_model = StubSettings
    config_model = DriverConfig
    suite_model = StubSuiteConfig
    run_section_model = StubSection
    examples = None

    def dimensions(
        self, sweep: SweepBase, base_config: DriverConfig
    ) -> list[tuple[Dimension, tuple[StrEnum, ...]]]:
        raise NotImplementedError

    def build_config(
        self, combo: Combo, output_directory: str, config_path: Path
    ) -> DriverConfig:
        raise NotImplementedError

    def effective_parameters(
        self, combo: Combo, config: DriverConfig
    ) -> list[ParameterRow]:
        raise NotImplementedError

    def expected_output_count(self, config: DriverConfig) -> int:
        raise NotImplementedError

    def expected_output_filenames(self, config: DriverConfig) -> set[str]:
        raise NotImplementedError

    def output_variable_names(self, config: DriverConfig) -> list[str]:
        raise NotImplementedError

    def selector_matches(self, selector: SweepSelectorBase, combo: Combo) -> bool:
        raise NotImplementedError

    def stage_lines(
        self, stage: Stage, config: object, section: ApplicationRunSection
    ) -> list[str]:
        return [f"echo stub {stage.value}"]


def stub_application() -> Application:
    return StubApplication()
