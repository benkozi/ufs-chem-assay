"""What a correct CECE run writes, derived from the generated config: file
count, filenames, output variable names, standard dimensions."""

from pathlib import Path

import pytest

from applications.cece.assertions import (
    STANDARD_DIMENSIONS,
    expected_output_count,
    expected_output_filenames,
    output_variable_names,
)
from applications.cece.config import CeceConfig
from applications.registry import get_application


@pytest.fixture()
def maccity_config(cece_config_path: Path) -> CeceConfig:
    return CeceConfig.from_yaml(cece_config_path)


def test_adapter_exposes_the_derivations(maccity_config: CeceConfig) -> None:
    app = get_application("cece")
    assert app.expected_output_count(maccity_config) == expected_output_count(
        maccity_config
    )
    assert app.expected_output_filenames(maccity_config) == expected_output_filenames(
        maccity_config
    )
    assert app.output_variable_names(maccity_config) == ["co"]
    assert (
        app.standard_dimensions == STANDARD_DIMENSIONS == ("time", "lev", "lat", "lon")
    )


def test_derived_count_for_maccity(
    maccity_config: CeceConfig, maccity_n_timesteps: int
) -> None:
    assert expected_output_count(maccity_config) == maccity_n_timesteps


def test_derived_count_zero_when_output_disabled(maccity_config: CeceConfig) -> None:
    assert maccity_config.output is not None
    maccity_config.output.enabled = False
    assert expected_output_count(maccity_config) == 0
    assert output_variable_names(maccity_config) == []


def test_derived_count_zero_when_output_absent(maccity_config: CeceConfig) -> None:
    maccity_config.output = None
    assert expected_output_count(maccity_config) == 0
    assert output_variable_names(maccity_config) == []


def test_derived_count_multi_step(maccity_config: CeceConfig) -> None:
    # 6 hours at 3600s = 6 steps; one write per 2 steps -> 3 files
    maccity_config.driver.end_time = "2010-01-01T06:00:00"
    assert maccity_config.output is not None
    maccity_config.output.frequency_steps = 2
    assert expected_output_count(maccity_config) == 3


def test_expected_filenames_maccity(
    maccity_config: CeceConfig, maccity_expected_filenames: set[str]
) -> None:
    # First write at hour 1, then hourly through the run's end.
    assert expected_output_filenames(maccity_config) == maccity_expected_filenames


def test_expected_filenames_multi_step(maccity_config: CeceConfig) -> None:
    # 6 hours at 3600s, one write per 2 steps -> files at hours 2, 4, 6
    maccity_config.driver.end_time = "2010-01-01T06:00:00"
    assert maccity_config.output is not None
    maccity_config.output.frequency_steps = 2
    assert expected_output_filenames(maccity_config) == {
        "cece_20100101_020000.nc",
        "cece_20100101_040000.nc",
        "cece_20100101_060000.nc",
    }


def test_expected_filenames_empty_when_output_disabled(
    maccity_config: CeceConfig,
) -> None:
    assert maccity_config.output is not None
    maccity_config.output.enabled = False
    assert expected_output_filenames(maccity_config) == set()


def test_expected_filenames_empty_when_output_absent(
    maccity_config: CeceConfig,
) -> None:
    maccity_config.output = None
    assert expected_output_filenames(maccity_config) == set()


def test_output_variable_names_mixed_entries(maccity_config: CeceConfig) -> None:
    from applications.cece.config import OutputField

    assert maccity_config.output is not None
    maccity_config.output.fields = [OutputField(name="co"), "nox"]
    assert output_variable_names(maccity_config) == ["co", "nox"]
