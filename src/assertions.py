"""Post-run assertions evaluated against a combination's output directory."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import xarray as xr

from logs import get_logger
from models.cece_config import CeceConfig

logger = get_logger("assertions")


def _render_filename_pattern(pattern: str, when: datetime) -> str:
    return (
        pattern.replace("{YYYY}", f"{when.year:04d}")
        .replace("{MM}", f"{when.month:02d}")
        .replace("{DD}", f"{when.day:02d}")
        .replace("{HH}", f"{when.hour:02d}")
        .replace("{mm}", f"{when.minute:02d}")
        .replace("{ss}", f"{when.second:02d}")
    )


def derive_expected_nc_file_count(config: CeceConfig) -> int:
    """Expected NetCDF output file count from the generated combo config:
    one file per output.frequency_steps timesteps; 0 when output is disabled
    or absent."""
    if config.output is None or not config.output.enabled:
        return 0
    start = datetime.fromisoformat(config.driver.start_time)
    end = datetime.fromisoformat(config.driver.end_time)
    n_steps = int((end - start).total_seconds()) // config.driver.timestep_seconds
    logger.info(
        "deriving expected_nc_file_count: timestep_seconds=%s n_steps=%s frequency_steps=%s",
        config.driver.timestep_seconds,
        n_steps,
        config.output.frequency_steps,
    )
    return n_steps // config.output.frequency_steps


def assert_nc_file_count(
    combo_dir: Path, config: CeceConfig, expected: int | None
) -> None:
    """Assert the number of NetCDF files in the combo directory (non-recursive).

    expected=None derives the count from the combo config; an explicit 0
    asserts that no NetCDF files were produced.
    """
    if expected is None:
        expected = derive_expected_nc_file_count(config)
    found = len(list(combo_dir.glob("*.nc")))
    logger.info("testing expected_nc_file_count=%s, found %s files", expected, found)
    assert found == expected, (
        f"expected {expected} NetCDF file(s) in {combo_dir}, found {found}"
    )


def expected_nc_filenames(config: CeceConfig) -> set[str]:
    """Expected NetCDF filenames: filename_pattern rendered at each write
    time, the first at start_time + frequency_steps * timestep_seconds (the
    end of the first output interval, not t=0)."""
    if config.output is None or not config.output.enabled:
        return set()
    count = derive_expected_nc_file_count(config)
    start = datetime.fromisoformat(config.driver.start_time)
    interval = timedelta(
        seconds=config.output.frequency_steps * config.driver.timestep_seconds
    )
    return {
        _render_filename_pattern(config.output.filename_pattern, start + k * interval)
        for k in range(1, count + 1)
    }


def assert_nc_filenames(combo_dir: Path, config: CeceConfig) -> None:
    """Assert the NetCDF filenames in the combo directory (non-recursive)
    exactly match the expected set rendered from filename_pattern."""
    expected = expected_nc_filenames(config)
    found = {path.name for path in combo_dir.glob("*.nc")}
    logger.info(
        "testing expected filenames=%s, found %s", sorted(expected), sorted(found)
    )
    missing = sorted(expected - found)
    unexpected = sorted(found - expected)
    assert found == expected, (
        f"NetCDF filenames in {combo_dir} do not match: missing {missing}, unexpected {unexpected}"
    )


# The driver's standard output layout: every output variable is 4-D on
# exactly these named dimensions. A deviation (e.g. a synthetic "<var>_dimN"
# where lat should be) means the writer failed to associate the coordinate —
# observed from AMIO with amio_worker_threads >= 2, where async coordinate
# and data writes race the dimension definitions.
STANDARD_DIMENSIONS = ("time", "lev", "lat", "lon")


def _output_field_names(config: CeceConfig) -> list[str]:
    """Configured output variable names (a fields entry is a plain string or
    an OutputField map)."""
    if config.output is None or not config.output.enabled:
        return []
    return [
        field if isinstance(field, str) else field.name
        for field in config.output.fields
    ]


def assert_output_variable_dimensions(combo_dir: Path, config: CeceConfig) -> None:
    """Assert every configured output variable carries exactly the standard
    (time, lev, lat, lon) dimensions in every NetCDF of the combo directory.
    A missing variable fails; disabled/absent output checks nothing."""
    names = _output_field_names(config)
    failures: list[str] = []
    for nc_path in sorted(combo_dir.glob("*.nc")):
        with xr.open_dataset(
            nc_path, engine="netcdf4", decode_cf=False, decode_coords=False
        ) as ds:
            for name in names:
                if name not in ds.variables:
                    failures.append(f"{nc_path.name}: variable {name!r} not present")
                    continue
                dims = tuple(str(dim) for dim in ds[name].dims)
                logger.info(
                    "testing dimensions of %r: expected %s, found %s (%s)",
                    name,
                    STANDARD_DIMENSIONS,
                    dims,
                    nc_path.name,
                )
                if dims != STANDARD_DIMENSIONS:
                    failures.append(
                        f"{nc_path.name}: {name} has dimensions {dims}, "
                        f"expected {STANDARD_DIMENSIONS}"
                    )
    assert not failures, (
        f"non-standard output variable dimensions in {combo_dir}: "
        + "; ".join(failures)
    )


IGNORE_VALUE = "__ignore__"  # mirrors suite_config.IGNORE_VALUE (no import cycle)


def _attribute_diffs(
    found: dict[str, str], expected: dict[str, str | None], exact: bool
) -> list[str]:
    diffs: list[str] = []
    for key, expectation in expected.items():
        if expectation == IGNORE_VALUE:
            continue
        if expectation is None:
            if key in found:
                diffs.append(f"{key}: expected absent, found {found[key]!r}")
        elif key not in found:
            diffs.append(f"{key}: missing (expected {expectation!r})")
        elif found[key] != expectation:
            diffs.append(f"{key}: expected {expectation!r}, found {found[key]!r}")
    if exact:
        for key in sorted(set(found) - set(expected)):
            diffs.append(f"{key}: unexpected (value {found[key]!r})")
    return diffs


def assert_species_attributes(
    combo_dir: Path, species: str, expected: dict[str, str | None], exact: bool
) -> None:
    """Assert the species' variable carries the expected attribute dictionary
    in every NetCDF of the combo directory.

    Attributes are read undecoded (decode_cf=False) so structural attributes
    like coordinates and _FillValue stay visible — the assertion targets what
    the driver actually wrote. exact=True requires the dictionaries to match
    exactly; exact=False checks expected as a subset. Values compare as
    strings; a missing variable fails.
    """
    failures: list[str] = []
    for nc_path in sorted(combo_dir.glob("*.nc")):
        with xr.open_dataset(
            nc_path, engine="netcdf4", decode_cf=False, decode_coords=False
        ) as ds:
            if species not in ds.variables:
                failures.append(f"{nc_path.name}: variable {species!r} not present")
                continue
            found = {str(key): str(value) for key, value in ds[species].attrs.items()}
        logger.info(
            "testing species %r attributes (exact=%s) expected=%s, found=%s (%s)",
            species,
            exact,
            expected,
            found,
            nc_path.name,
        )
        diffs = _attribute_diffs(found, expected, exact)
        if diffs:
            failures.append(f"{nc_path.name}: " + ", ".join(diffs))
    assert not failures, (
        f"attribute mismatch for species {species!r} in {combo_dir}: "
        + "; ".join(failures)
    )
