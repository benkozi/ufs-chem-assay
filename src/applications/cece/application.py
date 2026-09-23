"""The CECE adapter: the wiring of applications/cece/* behind the
Application surface."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from applications.base import (
    Application,
    ApplicationRunSection,
    DriverConfig,
    SweepBase,
    SweepSelectorBase,
)
from applications.cece import assertions, cli, combos, suite
from applications.cece.config import CeceConfig
from applications.cece.examples import CeceExamples
from applications.cece.settings import CONTAINER_WORKDIR, ENV_PREFIX, CeceSettings
from cli.stages import Stage
from combos import Combo, Dimension, ParameterRow


def _config(config: DriverConfig) -> CeceConfig:
    # The one generic-to-concrete narrowing per method: a wrong application
    # cannot reach here (the suite model validated the sweep for this adapter).
    assert isinstance(config, CeceConfig), type(config)
    return config


class CeceApplication(Application):
    name = "cece"
    env_prefix = ENV_PREFIX
    container_workdir = CONTAINER_WORKDIR
    default_suite = "simple-maccity-suite.yaml"
    checkout_dirname = "CECE"
    standard_dimensions = assertions.STANDARD_DIMENSIONS
    settings_model = CeceSettings
    config_model = CeceConfig
    suite_model = suite.CeceSuiteConfig
    run_section_model = cli.CeceRunSection
    examples = CeceExamples()

    def dimensions(
        self, sweep: SweepBase, base_config: DriverConfig
    ) -> list[tuple[Dimension, tuple[StrEnum, ...]]]:
        assert isinstance(sweep, suite.CeceSweep), type(sweep)
        return combos.dimensions(sweep, _config(base_config))

    def build_config(
        self, combo: Combo, output_directory: str, config_path: Path
    ) -> CeceConfig:
        return combos.build_config(combo, output_directory, config_path)

    def effective_parameters(
        self, combo: Combo, config: DriverConfig
    ) -> list[ParameterRow]:
        return combos.effective_parameters(combo, _config(config))

    def expected_output_count(self, config: DriverConfig) -> int:
        return assertions.expected_output_count(_config(config))

    def expected_output_filenames(self, config: DriverConfig) -> set[str]:
        return assertions.expected_output_filenames(_config(config))

    def output_variable_names(self, config: DriverConfig) -> list[str]:
        return assertions.output_variable_names(_config(config))

    def selector_matches(self, selector: SweepSelectorBase, combo: Combo) -> bool:
        assert isinstance(selector, suite.CeceSweepSelector), type(selector)
        return suite.selector_matches(selector, combo)

    def stage_lines(
        self, stage: Stage, config: object, section: ApplicationRunSection
    ) -> list[str]:
        from cli.run_config import RunConfig

        assert isinstance(config, RunConfig), type(config)
        return cli.stage_lines(stage, config, section)
