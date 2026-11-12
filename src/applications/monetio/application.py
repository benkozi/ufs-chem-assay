"""The monetio adapter: wiring `applications/monetio/*` behind the
`Application` interface."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path, PurePosixPath

from applications.base import (
    Application,
    ApplicationRunSection,
    DriverConfig,
    RunConfigView,
    Stage,
    SweepBase,
    SweepSelectorBase,
)
from applications.monetio import assertions, cli, combos, suite
from applications.monetio.config import KwargValue, MonetioConfig, ScalarKwarg
from applications.monetio.settings import (
    CONTAINER_WORKDIR,
    ENV_PREFIX,
    MonetioSettings,
)
from combos import Combo, Dimension, ParameterRow


def _config(config: DriverConfig) -> MonetioConfig:
    assert isinstance(config, MonetioConfig), type(config)
    return config


def _format_scalar(value: ScalarKwarg) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _kwarg_flags(key: str, value: KwargValue) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [
            arg
            for item in value
            if item is not None
            for arg in ("-k", f"{key}={_format_scalar(item)}")
        ]
    return ["-k", f"{key}={_format_scalar(value)}"]


def _resolve_yaml_path(driver_yaml: PurePosixPath) -> Path:
    path = Path(driver_yaml)
    if path.is_file():
        return path
    root = os.environ.get(f"{ENV_PREFIX}ROOT_DIR")
    return Path(root) / path if root is not None else path


class MonetioApplication(Application):
    name = "monetio"
    env_prefix = ENV_PREFIX
    container_workdir = CONTAINER_WORKDIR
    default_suite = "aeronet-suite.yaml"
    checkout_dirname = "monetio"
    data_dirname = "data"
    standard_dimensions = assertions.STANDARD_DIMENSIONS
    volatile_global_attributes = assertions.VOLATILE_GLOBAL_ATTRIBUTES
    settings_model = MonetioSettings
    config_model = MonetioConfig
    suite_model = suite.MonetioSuiteConfig
    run_section_model = cli.MonetioRunSection

    def dimensions(
        self, sweep: SweepBase, base_config: DriverConfig
    ) -> list[tuple[Dimension, tuple[StrEnum, ...]]]:
        assert isinstance(sweep, suite.MonetioSweep), type(sweep)
        return combos.dimensions(sweep, _config(base_config))

    def build_config(
        self, combo: Combo, output_directory: str, config_path: Path
    ) -> MonetioConfig:
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

    def driver_arguments(self, driver_yaml: PurePosixPath) -> list[str]:
        config = MonetioConfig.from_yaml(_resolve_yaml_path(driver_yaml))
        args = ["load", config.source]
        for file_path in config.files:
            args += ["-f", file_path]
        args += ["-o", config.output]
        for key, value in config.kwargs.items():
            args += _kwarg_flags(key, value)
        return args

    def config_fingerprint(self, path: Path) -> str:
        config = MonetioConfig.from_yaml(path)
        config.output = f"<combo>/{PurePosixPath(config.output).name}"
        return config.model_dump_json()

    def selector_matches(self, selector: SweepSelectorBase, combo: Combo) -> bool:
        assert isinstance(selector, suite.MonetioSweepSelector), type(selector)
        return suite.selector_matches(selector, combo)

    def stage_lines(
        self, stage: Stage, config: RunConfigView, section: ApplicationRunSection
    ) -> list[str]:
        return cli.stage_lines(stage, config, section, config.checkout_dir(self))
