"""The generic suite model: config_path resolution, the assertion and
analysis sections, name rules, the `application` field, and the run
manifest. CECE's sweep schema is tested in applications/cece/test_suite.py."""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from applications.registry import load_suite
from models.suite_config import (
    Assertions,
    AttributesAssertion,
    RunManifest,
    SpeciesAssertions,
    SuiteConfig,
)
from platforms import Platform, Runtime

_SWEEP = "sweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"


def _manifest(suites: list[SuiteConfig], **overrides: object) -> RunManifest:
    values: dict[str, object] = {
        "run_id": "01JZZZZZZZZZZZZZZZZZZZZZZZ",
        "application": "cece",
        "application_commit": None,
        "harness_version": "0.1.0",
        "harness_commit": None,
        "platform": Platform.LOCAL,
        "runtime": Runtime.DOCKER,
        "modulefile": None,
        "suites": suites,
    }
    values.update(overrides)
    return RunManifest.model_validate(values)


def test_config_path_resolves_relative_to_suite_file(suite_path: Path) -> None:
    suite = load_suite(suite_path)
    assert suite.name == "simple-maccity"
    assert (
        suite.config_path
        == (suite_path.parent / ".." / "cece" / "simple-maccity.yaml").resolve()
    )
    assert suite.config_path.is_file()


def test_config_search_path_prepends_whole_path(
    tmp_path: Path, suite_path: Path, cece_config_path: Path
) -> None:
    # Mirror the config tree layout under a search directory; the suite's
    # ../cece reference walks out of the search dir (accepted behavior).
    (tmp_path / "suite").mkdir()
    (tmp_path / "cece").mkdir()
    shutil.copy(suite_path, tmp_path / "suite" / "copied-suite.yaml")
    shutil.copy(cece_config_path, tmp_path / "cece" / "simple-maccity.yaml")

    suite = load_suite(
        tmp_path / "suite" / "copied-suite.yaml", config_search_path=tmp_path / "suite"
    )
    assert suite.config_path == (tmp_path / "cece" / "simple-maccity.yaml").resolve()


def test_absolute_config_path_ignores_search_path(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "abs-suite.yaml"
    suite_file.write_text(
        f"name: inline-suite\nconfig_path: {cece_config_path}\ntimeout_s: 5\n{_SWEEP}"
    )
    suite = load_suite(suite_file, config_search_path=tmp_path)
    assert suite.config_path == cece_config_path


def test_missing_config_path_raises(tmp_path: Path) -> None:
    suite_file = tmp_path / "broken-suite.yaml"
    suite_file.write_text(
        f"name: broken-suite\nconfig_path: nope.yaml\ntimeout_s: 5\n{_SWEEP}"
    )
    with pytest.raises(FileNotFoundError, match="nope.yaml"):
        load_suite(suite_file)


def test_assertions_default_when_section_absent(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "no-assertions-suite.yaml"
    suite_file.write_text(
        f"name: inline-suite\nconfig_path: {cece_config_path}\ntimeout_s: 5\n{_SWEEP}"
    )
    suite = load_suite(suite_file)
    assert suite.assertions.expected_nc_file_count is None
    assert suite.assertions.validate_filenames is True


def test_missing_suite_name_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    suite_file = tmp_path / "nameless-suite.yaml"
    suite_file.write_text(f"config_path: {cece_config_path}\ntimeout_s: 5\n{_SWEEP}")
    with pytest.raises(ValidationError, match="name"):
        load_suite(suite_file)


def test_malformed_suite_name_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    suite_file = tmp_path / "badname-suite.yaml"
    suite_file.write_text(
        f"name: Simple Maccity!\nconfig_path: {cece_config_path}\ntimeout_s: 5\n{_SWEEP}"
    )
    with pytest.raises(ValidationError, match="name"):
        load_suite(suite_file)


def test_application_defaults_to_cece_and_is_recorded(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "default-app-suite.yaml"
    suite_file.write_text(
        f"name: default-app\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
    )
    suite = load_suite(suite_file)
    assert suite.application == "cece"
    manifest_path = tmp_path / "run.yaml"
    _manifest([suite]).to_yaml(manifest_path)
    dumped = yaml.safe_load(manifest_path.read_text())
    assert dumped["application"] == "cece"
    assert dumped["suites"][0]["application"] == "cece"


def test_species_assertions_defaults() -> None:
    assert SpeciesAssertions().attributes is None  # omitted -> no attribute test
    assertion = AttributesAssertion()
    assert assertion.exact is True
    assert assertion.expected == {}


def test_species_attributes_block_parses(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "species-suite.yaml"
    suite_file.write_text(
        f"name: species-suite\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        "assertions:\n  species:\n    co:\n      attributes:\n        exact: false\n"
        "        expected:\n          units: kg m-2 s-1\n          history: null\n"
        f"{_SWEEP}"
    )
    suite = load_suite(suite_file)
    assert suite.assertions.species is not None
    attributes = suite.assertions.species["co"].attributes
    assert attributes is not None
    assert attributes.exact is False
    assert attributes.expected == {"units": "kg m-2 s-1", "history": None}


def test_old_units_schema_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    # The units-only first cut (species.<name>.units) is a removed schema.
    suite_file = tmp_path / "old-units-suite.yaml"
    suite_file.write_text(
        f"name: old-units\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        "assertions:\n  species:\n    co:\n      units: kg m-2 s-1\n"
        f"{_SWEEP}"
    )
    with pytest.raises(ValidationError, match="units"):
        load_suite(suite_file)


def test_unknown_suite_key_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    # ulid is runtime-only and must never come from configuration; unknown
    # keys generally fail loudly rather than being silently dropped.
    suite_file = tmp_path / "ulid-suite.yaml"
    suite_file.write_text(
        f"name: inline-suite\nconfig_path: {cece_config_path}\nulid: 01JZZ\ntimeout_s: 5\n{_SWEEP}"
    )
    with pytest.raises(ValidationError, match="ulid"):
        load_suite(suite_file)


def test_plotting_requires_stats(tmp_path: Path, cece_config_path: Path) -> None:
    suite_file = tmp_path / "plot-no-stats-suite.yaml"
    suite_file.write_text(
        f"name: plot-no-stats\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        "analysis:\n  compute_descriptive_stats: false\nplotting:\n  enabled: true\n"
    )
    with pytest.raises(ValidationError, match="compute_descriptive_stats"):
        load_suite(suite_file)


# ── Run-only controls ─────────────────────────────────────────────────────────


def test_validate_file_count_defaults_true() -> None:
    assert Assertions().validate_file_count is True


def test_validate_dimensions_defaults_true() -> None:
    # The standard-dimensions assertion is on unless a suite disables it.
    assert Assertions().validate_dimensions is True


def test_validate_file_count_parses_false(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "no-count-suite.yaml"
    suite_file.write_text(
        f"name: no-count\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        "assertions:\n  validate_file_count: false\n"
        f"{_SWEEP}"
    )
    suite = load_suite(suite_file)
    assert suite.assertions.validate_file_count is False


# ── The run manifest ──────────────────────────────────────────────────────────


def test_run_manifest_round_trips_through_yaml(
    tmp_path: Path, suite_path: Path
) -> None:
    # suites is a list in selection order — one element for single-suite runs.
    suite = load_suite(suite_path)
    manifest = _manifest([suite], harness_commit="abc123-dirty")
    manifest_path = tmp_path / "run.yaml"
    manifest.to_yaml(manifest_path)

    dumped = yaml.safe_load(manifest_path.read_text())
    assert list(dumped)[:5] == [
        "run_id",
        "application",
        "application_commit",
        "harness_version",
        "harness_commit",
    ]
    assert dumped["harness_commit"] == "abc123-dirty"
    # SerializeAsAny: the suite dumps with the adapter's schema — the sweep
    # is present (the base SuiteConfig type alone would drop it).
    assert dumped["suites"][0]["sweep"]["cece_data"]["streams"][0]["mapalgo"] == [
        "bilinear",
        "consd",
        "passthrough",
    ]
    assert dumped["suites"][0]["baseline_comparisons"][0]["sweep_selector"]["cece_data"]

    reloaded = RunManifest.model_validate(dumped)
    assert reloaded.run_id == manifest.run_id
    assert reloaded.application_commit is None  # explicit null round-trips
    (reloaded_suite,) = reloaded.suites
    assert reloaded_suite.config_path == suite.config_path


@pytest.mark.parametrize(
    "missing",
    ["application", "application_commit", "harness_version", "harness_commit"],
)
def test_run_manifest_requires_every_identity_key(
    suite_path: Path, missing: str
) -> None:
    # Required-but-nullable: omitting a key is a validation error, so no
    # writer can silently record null by accident; deliberate null stays
    # expressible for checkout-less dry-runs.
    suite = load_suite(suite_path)
    values = _manifest([suite]).model_dump()
    del values[missing]
    values["suites"] = [suite]
    with pytest.raises(ValidationError, match=missing):
        RunManifest.model_validate(values)
