"""Post-run assertions evaluated against a combination's output directory.

Generic: every expectation (file count, filenames, variable names and their
standard dimensions) is derived by the application adapter from the combo's
generated config and passed in; the attribute assertion reads the suite's
expectations directly."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import xarray as xr

from logs import get_logger

logger = get_logger("assertions")


def render_filename_pattern(pattern: str, when: datetime) -> str:
    """Expand the {YYYY}{MM}{DD}{HH}{mm}{ss} placeholders of an output
    filename pattern at one write time."""
    return (
        pattern.replace("{YYYY}", f"{when.year:04d}")
        .replace("{MM}", f"{when.month:02d}")
        .replace("{DD}", f"{when.day:02d}")
        .replace("{HH}", f"{when.hour:02d}")
        .replace("{mm}", f"{when.minute:02d}")
        .replace("{ss}", f"{when.second:02d}")
    )


def assert_nc_file_count(combo_dir: Path, expected: int) -> None:
    """Assert the number of NetCDF files in the combo directory
    (non-recursive); 0 asserts that none were produced."""
    found = len(list(combo_dir.glob("*.nc")))
    logger.info("testing expected_nc_file_count=%s, found %s files", expected, found)
    assert found == expected, (
        f"expected {expected} NetCDF file(s) in {combo_dir}, found {found}"
    )


def assert_nc_filenames(combo_dir: Path, expected: set[str]) -> None:
    """Assert the NetCDF filenames in the combo directory (non-recursive)
    exactly match the expected set."""
    found = {path.name for path in combo_dir.glob("*.nc")}
    logger.info(
        "testing expected filenames=%s, found %s", sorted(expected), sorted(found)
    )
    missing = sorted(expected - found)
    unexpected = sorted(found - expected)
    assert found == expected, (
        f"NetCDF filenames in {combo_dir} do not match: missing {missing}, unexpected {unexpected}"
    )


def assert_output_variable_dimensions(
    combo_dir: Path, names: list[str], standard: tuple[str, ...]
) -> None:
    """Assert every named output variable carries exactly the standard
    dimensions in every NetCDF of the combo directory. A missing variable
    fails; no names checks nothing. (A synthetic "<var>_dimN" where a
    coordinate belongs means the writer failed to associate it — observed
    from CECE's AMIO with amio_worker_threads >= 2.)"""
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
                    standard,
                    dims,
                    nc_path.name,
                )
                if dims != standard:
                    failures.append(
                        f"{nc_path.name}: {name} has dimensions {dims}, "
                        f"expected {standard}"
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
