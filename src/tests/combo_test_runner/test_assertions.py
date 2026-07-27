from pathlib import Path

import pytest

import numpy as np
import xarray as xr

from assertions import (
    STANDARD_DIMENSIONS,
    assert_nc_file_count,
    assert_nc_filenames,
    assert_output_variable_dimensions,
    assert_species_attributes,
    derive_expected_nc_file_count,
    expected_nc_filenames,
)
from models.cece_config import CeceConfig


def _write_species_nc(
    path: Path, variable: str = "co", attrs: dict[str, str] | None = None
) -> None:
    dataset = xr.Dataset({variable: (("lat", "lon"), np.ones((2, 3)))})
    dataset[variable].attrs.update(attrs or {})
    # Suppress xarray's automatic _FillValue so fabricated files carry
    # exactly the attributes the test declares (assertions read undecoded).
    dataset.to_netcdf(path, engine="netcdf4", encoding={variable: {"_FillValue": None}})


@pytest.fixture()
def maccity_config(cece_config_path: Path) -> CeceConfig:
    return CeceConfig.from_yaml(cece_config_path)


def test_derived_count_for_maccity(
    maccity_config: CeceConfig, maccity_n_timesteps: int
) -> None:
    assert derive_expected_nc_file_count(maccity_config) == maccity_n_timesteps


def test_derived_count_zero_when_output_disabled(maccity_config: CeceConfig) -> None:
    assert maccity_config.output is not None
    maccity_config.output.enabled = False
    assert derive_expected_nc_file_count(maccity_config) == 0


def test_derived_count_zero_when_output_absent(maccity_config: CeceConfig) -> None:
    maccity_config.output = None
    assert derive_expected_nc_file_count(maccity_config) == 0


def test_derived_count_multi_step(maccity_config: CeceConfig) -> None:
    # 6 hours at 3600s = 6 steps; one write per 2 steps -> 3 files
    maccity_config.driver.end_time = "2010-01-01T06:00:00"
    assert maccity_config.output is not None
    maccity_config.output.frequency_steps = 2
    assert derive_expected_nc_file_count(maccity_config) == 3


def test_assert_passes_with_derived_count(
    tmp_path: Path, maccity_config: CeceConfig, maccity_expected_filenames: set[str]
) -> None:
    for name in maccity_expected_filenames:
        (tmp_path / name).touch()
    assert_nc_file_count(tmp_path, maccity_config, expected=None)


def test_assert_fails_when_files_missing(
    tmp_path: Path, maccity_config: CeceConfig, maccity_n_timesteps: int
) -> None:
    with pytest.raises(AssertionError, match=f"expected {maccity_n_timesteps} NetCDF"):
        assert_nc_file_count(tmp_path, maccity_config, expected=None)


def test_explicit_zero_expects_no_files(
    tmp_path: Path, maccity_config: CeceConfig
) -> None:
    assert_nc_file_count(tmp_path, maccity_config, expected=0)
    (tmp_path / "unexpected.nc").touch()
    with pytest.raises(AssertionError, match="expected 0 NetCDF"):
        assert_nc_file_count(tmp_path, maccity_config, expected=0)


def test_count_is_non_recursive(tmp_path: Path, maccity_config: CeceConfig) -> None:
    (tmp_path / "cece_20100101_000000.nc").touch()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "ignored.nc").touch()
    assert_nc_file_count(tmp_path, maccity_config, expected=1)


_FULL_ATTRS = {"units": "kg m-2 s-1", "long_name": "carbon_monoxide_emission_flux"}


def test_species_attributes_exact_match_passes(tmp_path: Path) -> None:
    _write_species_nc(tmp_path / "a.nc", attrs=_FULL_ATTRS)
    _write_species_nc(tmp_path / "b.nc", attrs=_FULL_ATTRS)
    assert_species_attributes(tmp_path, "co", expected=dict(_FULL_ATTRS), exact=True)


def test_species_attributes_exact_fails_on_unexpected_attribute(tmp_path: Path) -> None:
    _write_species_nc(tmp_path / "a.nc", attrs={**_FULL_ATTRS, "comment": "surprise"})
    with pytest.raises(AssertionError, match="comment: unexpected"):
        assert_species_attributes(
            tmp_path, "co", expected=dict(_FULL_ATTRS), exact=True
        )


def test_species_attributes_exact_fails_on_missing_key(tmp_path: Path) -> None:
    _write_species_nc(tmp_path / "a.nc", attrs={"units": "kg m-2 s-1"})
    with pytest.raises(AssertionError, match="long_name: missing"):
        assert_species_attributes(
            tmp_path, "co", expected=dict(_FULL_ATTRS), exact=True
        )


def test_species_attributes_wrong_value_fails_naming_files(tmp_path: Path) -> None:
    _write_species_nc(tmp_path / "a.nc", attrs=_FULL_ATTRS)
    _write_species_nc(tmp_path / "b.nc", attrs={**_FULL_ATTRS, "units": "mol mol-1"})
    with pytest.raises(AssertionError) as excinfo:
        assert_species_attributes(
            tmp_path, "co", expected=dict(_FULL_ATTRS), exact=True
        )
    message = str(excinfo.value)
    assert "b.nc: units: expected 'kg m-2 s-1', found 'mol mol-1'" in message
    assert "a.nc" not in message  # the matching file is not reported


def test_species_attributes_subset_allows_extras(tmp_path: Path) -> None:
    _write_species_nc(
        tmp_path / "a.nc", attrs={**_FULL_ATTRS, "comment": "extra is fine"}
    )
    assert_species_attributes(
        tmp_path, "co", expected={"units": "kg m-2 s-1"}, exact=False
    )


def test_species_attributes_subset_still_checks_values(tmp_path: Path) -> None:
    _write_species_nc(tmp_path / "a.nc", attrs={"units": "mol mol-1"})
    with pytest.raises(AssertionError, match="units: expected"):
        assert_species_attributes(
            tmp_path, "co", expected={"units": "kg m-2 s-1"}, exact=False
        )


def test_species_attributes_null_asserts_absence(tmp_path: Path) -> None:
    _write_species_nc(tmp_path / "a.nc", attrs={"long_name": "x"})
    assert_species_attributes(
        tmp_path, "co", expected={"units": None, "long_name": "x"}, exact=True
    )
    _write_species_nc(
        tmp_path / "b.nc", attrs={"units": "", "long_name": "x"}
    )  # empty = present
    with pytest.raises(AssertionError, match="units: expected absent, found ''"):
        assert_species_attributes(
            tmp_path, "co", expected={"units": None, "long_name": "x"}, exact=True
        )


def test_species_attributes_ignore_permits_any_value_in_exact_mode(
    tmp_path: Path,
) -> None:
    _write_species_nc(tmp_path / "a.nc", attrs={"units": "whatever", "long_name": "x"})
    assert_species_attributes(
        tmp_path, "co", expected={"units": "__ignore__", "long_name": "x"}, exact=True
    )


def test_species_attributes_missing_variable_fails(tmp_path: Path) -> None:
    _write_species_nc(tmp_path / "a.nc", variable="nox", attrs=_FULL_ATTRS)
    with pytest.raises(AssertionError, match="variable 'co' not present"):
        assert_species_attributes(
            tmp_path, "co", expected=dict(_FULL_ATTRS), exact=True
        )


def test_expected_filenames_maccity(
    maccity_config: CeceConfig, maccity_expected_filenames: set[str]
) -> None:
    # First write at hour 1, then hourly through the run's end.
    assert expected_nc_filenames(maccity_config) == maccity_expected_filenames


def test_expected_filenames_multi_step(maccity_config: CeceConfig) -> None:
    # 6 hours at 3600s, one write per 2 steps -> files at hours 2, 4, 6
    maccity_config.driver.end_time = "2010-01-01T06:00:00"
    assert maccity_config.output is not None
    maccity_config.output.frequency_steps = 2
    assert expected_nc_filenames(maccity_config) == {
        "cece_20100101_020000.nc",
        "cece_20100101_040000.nc",
        "cece_20100101_060000.nc",
    }


def test_expected_filenames_empty_when_output_disabled(
    maccity_config: CeceConfig,
) -> None:
    assert maccity_config.output is not None
    maccity_config.output.enabled = False
    assert expected_nc_filenames(maccity_config) == set()


def test_expected_filenames_empty_when_output_absent(
    maccity_config: CeceConfig,
) -> None:
    maccity_config.output = None
    assert expected_nc_filenames(maccity_config) == set()


def test_assert_filenames_passes_with_expected_files(
    tmp_path: Path, maccity_config: CeceConfig, maccity_expected_filenames: set[str]
) -> None:
    for name in maccity_expected_filenames:
        (tmp_path / name).touch()
    assert_nc_filenames(tmp_path, maccity_config)


def test_assert_filenames_fails_on_hour_zero_stamps(
    tmp_path: Path, maccity_config: CeceConfig, maccity_n_timesteps: int
) -> None:
    # The shape of the (since fixed) hour-0 driver stamp bug: stamps shifted
    # to start at hour 0, so the run's final hour is missing and hour 0 is
    # unexpected — the assertion must catch any recurrence.
    for hour in range(maccity_n_timesteps):
        (tmp_path / f"cece_20100101_{hour:02d}0000.nc").touch()
    with pytest.raises(AssertionError) as excinfo:
        assert_nc_filenames(tmp_path, maccity_config)
    assert f"missing ['cece_20100101_{maccity_n_timesteps:02d}0000.nc']" in str(
        excinfo.value
    )
    assert "unexpected ['cece_20100101_000000.nc']" in str(excinfo.value)


# -- Standard output dimensions (time, lev, lat, lon) -------------------------


def _write_dims_nc(path: Path, variable: str, dims: tuple[str, ...]) -> None:
    shape = tuple(2 for _ in dims)
    dataset = xr.Dataset({variable: (dims, np.ones(shape))})
    dataset.to_netcdf(path, engine="netcdf4", encoding={variable: {"_FillValue": None}})


def test_standard_dimensions_pass(tmp_path: Path, maccity_config: CeceConfig) -> None:
    _write_dims_nc(tmp_path / "a.nc", "co", STANDARD_DIMENSIONS)
    assert_output_variable_dimensions(tmp_path, maccity_config)


def test_synthetic_dimension_fails_naming_variable_and_dims(
    tmp_path: Path, maccity_config: CeceConfig
) -> None:
    # The observed AMIO threads>=2 signature: lat's slot named <var>_dim2.
    _write_dims_nc(tmp_path / "a.nc", "co", ("time", "lev", "co_dim2", "lon"))
    with pytest.raises(AssertionError, match=r"co_dim2"):
        assert_output_variable_dimensions(tmp_path, maccity_config)


def test_wrong_dimension_order_fails(
    tmp_path: Path, maccity_config: CeceConfig
) -> None:
    _write_dims_nc(tmp_path / "a.nc", "co", ("time", "lev", "lon", "lat"))
    with pytest.raises(AssertionError, match="expected"):
        assert_output_variable_dimensions(tmp_path, maccity_config)


def test_missing_output_variable_fails_dimension_check(
    tmp_path: Path, maccity_config: CeceConfig
) -> None:
    _write_dims_nc(tmp_path / "a.nc", "other", STANDARD_DIMENSIONS)
    with pytest.raises(AssertionError, match="not present"):
        assert_output_variable_dimensions(tmp_path, maccity_config)


def test_dimension_check_vacuous_without_output(
    tmp_path: Path, maccity_config: CeceConfig
) -> None:
    maccity_config.output = None
    _write_dims_nc(tmp_path / "a.nc", "co", ("time", "co_dim1"))
    assert_output_variable_dimensions(tmp_path, maccity_config)  # nothing configured


def test_failure_names_every_offending_file(
    tmp_path: Path, maccity_config: CeceConfig
) -> None:
    _write_dims_nc(tmp_path / "a.nc", "co", ("time", "lev", "co_dim2", "lon"))
    _write_dims_nc(tmp_path / "b.nc", "co", STANDARD_DIMENSIONS)
    _write_dims_nc(tmp_path / "c.nc", "co", ("time", "lev", "co_dim2", "lon"))
    with pytest.raises(AssertionError) as excinfo:
        assert_output_variable_dimensions(tmp_path, maccity_config)
    message = str(excinfo.value)
    assert "a.nc" in message and "c.nc" in message and "b.nc" not in message
