import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from combos import enumerate_combos
from models.cece_config import Category, CeceConfig, Mapalgo, VdistMethod
from platforms import Platform, Runtime
from models.suite_config import (
    Assertions,
    AttributesAssertion,
    RunManifest,
    SpeciesAssertions,
    SuiteConfig,
)


def test_config_path_resolves_relative_to_suite_file(suite_path: Path) -> None:
    suite = SuiteConfig.from_yaml(suite_path)
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

    suite = SuiteConfig.from_yaml(
        tmp_path / "suite" / "copied-suite.yaml", config_search_path=tmp_path / "suite"
    )
    assert suite.config_path == (tmp_path / "cece" / "simple-maccity.yaml").resolve()


def test_absolute_config_path_ignores_search_path(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "abs-suite.yaml"
    suite_file.write_text(
        f"name: inline-suite\nconfig_path: {cece_config_path}\ntimeout_s: 5\nsweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    suite = SuiteConfig.from_yaml(suite_file, config_search_path=tmp_path)
    assert suite.config_path == cece_config_path


def test_missing_config_path_raises(tmp_path: Path) -> None:
    suite_file = tmp_path / "broken-suite.yaml"
    suite_file.write_text(
        "name: broken-suite\nconfig_path: nope.yaml\ntimeout_s: 5\nsweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    with pytest.raises(FileNotFoundError, match="nope.yaml"):
        SuiteConfig.from_yaml(suite_file)


def test_assertions_default_when_section_absent(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "no-assertions-suite.yaml"
    suite_file.write_text(
        f"name: inline-suite\nconfig_path: {cece_config_path}\ntimeout_s: 5\nsweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    suite = SuiteConfig.from_yaml(suite_file)
    assert suite.assertions.expected_nc_file_count is None
    assert suite.assertions.validate_filenames is True


def test_invalid_sweep_value_fails_at_load(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "typo-suite.yaml"
    suite_file.write_text(
        f"name: inline-suite\nconfig_path: {cece_config_path}\ntimeout_s: 5\nsweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [bilinnear]\n"
    )
    with pytest.raises(ValidationError):
        SuiteConfig.from_yaml(suite_file)


def test_missing_suite_name_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    suite_file = tmp_path / "nameless-suite.yaml"
    suite_file.write_text(
        f"config_path: {cece_config_path}\ntimeout_s: 5\nsweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    with pytest.raises(ValidationError, match="name"):
        SuiteConfig.from_yaml(suite_file)


def test_malformed_suite_name_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    suite_file = tmp_path / "badname-suite.yaml"
    suite_file.write_text(
        f"name: Simple Maccity!\nconfig_path: {cece_config_path}\ntimeout_s: 5\nsweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    with pytest.raises(ValidationError, match="name"):
        SuiteConfig.from_yaml(suite_file)


def test_sweep_optional_defaults_empty(tmp_path: Path, cece_config_path: Path) -> None:
    # A suite without sweep: runs the base config as the single combination
    # (examples-as-suites design).
    suite_file = tmp_path / "sweepless-suite.yaml"
    suite_file.write_text(
        f"name: sweepless\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
    )
    suite = SuiteConfig.from_yaml(suite_file)
    assert suite.sweep.cece_data is None
    assert suite.sweep.species is None


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
        "sweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    suite = SuiteConfig.from_yaml(suite_file)
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
        "sweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    with pytest.raises(ValidationError, match="units"):
        SuiteConfig.from_yaml(suite_file)


def test_old_flat_sweep_format_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    # Pre-attachment flat sweeps (enum lists directly under sweep:) are a
    # removed schema; StrictModel rejects the unknown keys loudly.
    suite_file = tmp_path / "flat-suite.yaml"
    suite_file.write_text(
        f"name: flat-suite\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        "sweep:\n  mapalgo: [bilinear, consd]\n"
    )
    with pytest.raises(ValidationError, match="mapalgo"):
        SuiteConfig.from_yaml(suite_file)


def test_duplicate_sweep_values_rejected(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "dup-suite.yaml"
    suite_file.write_text(
        f"name: dup-suite\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        "sweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd, consd]\n"
    )
    with pytest.raises(ValidationError, match="duplicate values"):
        SuiteConfig.from_yaml(suite_file)


def test_unknown_suite_key_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    # ulid is runtime-only and must never come from configuration; unknown
    # keys generally fail loudly rather than being silently dropped.
    suite_file = tmp_path / "ulid-suite.yaml"
    suite_file.write_text(
        f"name: inline-suite\nconfig_path: {cece_config_path}\nulid: 01JZZ\ntimeout_s: 5\nsweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    with pytest.raises(ValidationError, match="ulid"):
        SuiteConfig.from_yaml(suite_file)


def test_output_fields_mixed_shorthand_and_map_entries(
    tmp_path: Path, cece_config_path: Path
) -> None:
    # An output.fields entry is a plain string (shorthand: no configured
    # attributes) or a {name, attributes} map — matching the driver schema.
    from models.cece_config import OutputField

    content = yaml.safe_load(cece_config_path.read_text())
    content["output"]["fields"] = [
        {"name": "co", "attributes": {"units": "kg m-2 s-1"}},
        "nox",
    ]
    config_file = tmp_path / "mixed-fields-config.yaml"
    config_file.write_text(yaml.dump(content))

    config = CeceConfig.from_yaml(config_file)
    assert config.output is not None
    assert config.output.fields == [
        OutputField(name="co", attributes={"units": "kg m-2 s-1"}),
        "nox",
    ]

    # Round-trips to the driver schema: shorthand stays a scalar.
    round_trip = tmp_path / "round-trip.yaml"
    config.to_yaml(round_trip)
    dumped = yaml.safe_load(round_trip.read_text())
    assert dumped["output"]["fields"] == [
        {"name": "co", "attributes": {"units": "kg m-2 s-1"}},
        "nox",
    ]


def test_unknown_nested_cece_config_key_rejected(
    tmp_path: Path, cece_config_path: Path
) -> None:
    content = yaml.safe_load(cece_config_path.read_text())
    content["driver"]["bogus_knob"] = 1
    config_file = tmp_path / "bogus-config.yaml"
    config_file.write_text(yaml.dump(content))
    with pytest.raises(ValidationError, match="bogus_knob"):
        CeceConfig.from_yaml(config_file)


# ── Enum ground truth (mirrors the C++ driver; hand-maintained, so asserted
# here — see design/feat/20260716-1647-exhaustive-maccity.md) ────────────────


def test_mapalgo_matches_driver_canonical_values() -> None:
    # The regridder silently falls to the default method for unknown strings,
    # so only its canonical values may exist here (consf/redist were removed).
    assert {member.value for member in Mapalgo} == {
        "passthrough",
        "nn",
        "bilinear",
        "cubic",
        "conss",
        "consd",
    }
    for removed in ("consf", "redist"):
        with pytest.raises(ValueError):
            Mapalgo(removed)


def test_vdist_method_matches_driver_parser_lowercase() -> None:
    # The parser matches lowercase strings (the uppercase validator whitelist
    # is dead code in standalone mode); anything else silently runs as single.
    assert {member.value for member in VdistMethod} == {
        "single",
        "range",
        "pressure",
        "height",
        "pbl",
    }


# ── Nested vdist (the driver parses `vdist:` as a map; flat vdist_* keys are
# silently ignored by it) ─────────────────────────────────────────────────────


def test_nested_vdist_round_trips_through_cece_config(
    tmp_path: Path, cece_config_path: Path
) -> None:
    content = yaml.safe_load(cece_config_path.read_text())
    content["species"]["co"][0]["vdist"] = {
        "method": "range",
        "layer_start": 0,
        "layer_end": 2,
    }
    config_file = tmp_path / "vdist-config.yaml"
    config_file.write_text(yaml.dump(content))

    config = CeceConfig.from_yaml(config_file)
    entry = config.species["co"][0]
    assert entry.vdist is not None
    assert entry.vdist.method is VdistMethod.range
    assert (entry.vdist.layer_start, entry.vdist.layer_end) == (0, 2)

    round_trip = tmp_path / "round-trip.yaml"
    config.to_yaml(round_trip)
    dumped = yaml.safe_load(round_trip.read_text())
    assert dumped["species"]["co"][0]["vdist"] == {
        "method": "range",
        "layer_start": 0,
        "layer_end": 2,
    }


def test_flat_vdist_keys_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    # Flat vdist_* keys are a removed schema the driver never read; rejecting
    # them prevents configs that silently no-op vertical distribution.
    content = yaml.safe_load(cece_config_path.read_text())
    content["species"]["co"][0]["vdist_method"] = "pbl"
    config_file = tmp_path / "flat-vdist-config.yaml"
    config_file.write_text(yaml.dump(content))
    with pytest.raises(ValidationError, match="vdist_method"):
        CeceConfig.from_yaml(config_file)


# ── Regex sweep values: a string is a regex expanded (fullmatch) against the
# enum's values into the sorted matching list at load time ────────────────────


def _regex_suite_text(cece_config_path: Path, mapalgo: str) -> str:
    return (
        f"name: regex-suite\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        f'sweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: "{mapalgo}"\n'
    )


def test_regex_sweep_value_expands_to_all_enum_values(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "regex-suite.yaml"
    suite_file.write_text(_regex_suite_text(cece_config_path, ".*"))
    suite = SuiteConfig.from_yaml(suite_file)
    assert suite.sweep.cece_data is not None
    assert suite.sweep.cece_data.streams[0].mapalgo == [
        Mapalgo.bilinear,
        Mapalgo.consd,
        Mapalgo.conss,
        Mapalgo.cubic,
        Mapalgo.nn,
        Mapalgo.passthrough,
    ]  # expanded to the full sorted value list at load


def test_regex_sweep_value_expands_to_matches(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "partial-regex-suite.yaml"
    suite_file.write_text(_regex_suite_text(cece_config_path, "cons.*"))
    suite = SuiteConfig.from_yaml(suite_file)
    assert suite.sweep.cece_data is not None
    assert suite.sweep.cece_data.streams[0].mapalgo == [Mapalgo.consd, Mapalgo.conss]


def test_species_regex_sweep_value_expands(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "species-regex-suite.yaml"
    suite_file.write_text(
        f"name: species-regex\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        'sweep:\n  species:\n    co:\n      - vdist_method: ".*"\n'
    )
    suite = SuiteConfig.from_yaml(suite_file)
    assert suite.sweep.species is not None
    assert suite.sweep.species["co"][0].vdist_method == [
        VdistMethod.height,
        VdistMethod.pbl,
        VdistMethod.pressure,
        VdistMethod.range,
        VdistMethod.single,
    ]


def test_zero_match_sweep_regex_rejected(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "zero-match-suite.yaml"
    suite_file.write_text(_regex_suite_text(cece_config_path, "z.*"))
    with pytest.raises(ValidationError, match="matches no"):
        SuiteConfig.from_yaml(suite_file)


def test_invalid_sweep_regex_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    suite_file = tmp_path / "invalid-regex-suite.yaml"
    suite_file.write_text(_regex_suite_text(cece_config_path, "["))
    with pytest.raises(ValidationError, match="invalid regex"):
        SuiteConfig.from_yaml(suite_file)


def test_expanded_regex_recorded_in_run_manifest(
    tmp_path: Path, cece_config_path: Path
) -> None:
    # run.yaml records what actually ran: the expanded list, not the regex, so
    # an exhaustive run stays reproducible after the enum grows.
    suite_file = tmp_path / "manifest-regex-suite.yaml"
    suite_file.write_text(_regex_suite_text(cece_config_path, "cons.*"))
    suite = SuiteConfig.from_yaml(suite_file)
    manifest = RunManifest(
        run_id="01JZZZZZZZZZZZZZZZZZZZZZZZ",
        cece_commit=None,
        platform=Platform.LOCAL,
        runtime=Runtime.DOCKER,
        suites=[suite],
    )
    manifest_path = tmp_path / "run.yaml"
    manifest.to_yaml(manifest_path)
    dumped = yaml.safe_load(manifest_path.read_text())
    assert dumped["suites"][0]["sweep"]["cece_data"]["streams"][0]["mapalgo"] == [
        "consd",
        "conss",
    ]


# ── Run-only controls and the exhaustive suite ────────────────────────────────


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
        "sweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    suite = SuiteConfig.from_yaml(suite_file)
    assert suite.assertions.validate_file_count is False


def test_category_has_inert_undefined_label() -> None:
    # The driver never reads category (pure pass-through label); "undefined"
    # exists so suites that don't care can pin it instead of sweeping.
    assert Category.undefined.value == "undefined"


def test_exhaustive_suite_enumerates_full_product(exhaustive_suite_path: Path) -> None:
    suite = SuiteConfig.from_yaml(exhaustive_suite_path)
    assert suite.name == "exhaustive-maccity-run-only"
    assert suite.assertions.validate_file_count is False
    assert suite.assertions.validate_filenames is False
    assert suite.assertions.species is None
    assert suite.analysis.compute_descriptive_stats is False
    assert suite.plotting.enabled is False
    assert suite.baseline_comparisons == []
    # category is a driver-inert label: pinned to "undefined", never swept.
    assert suite.sweep.species is not None
    assert suite.sweep.species["co"][0].category == [Category.undefined]

    base_config = CeceConfig.from_yaml(suite.config_path)
    combos = enumerate_combos(suite.sweep, base_config)
    # op x vd x tax x tint x map — every enum value via ".*" regexes;
    # category contributes a factor of 1.
    assert len(combos) == 2 * 1 * 5 * 2 * 2 * 6


def test_run_manifest_round_trips_through_yaml(
    tmp_path: Path, suite_path: Path
) -> None:
    # suites is a list in selection order — one element for single-suite runs.
    suite = SuiteConfig.from_yaml(suite_path)
    manifest = RunManifest(
        run_id="01JZZZZZZZZZZZZZZZZZZZZZZZ",
        cece_commit=None,
        platform=Platform.LOCAL,
        runtime=Runtime.DOCKER,
        suites=[suite],
    )
    manifest_path = tmp_path / "run.yaml"
    manifest.to_yaml(manifest_path)

    reloaded = RunManifest.model_validate(yaml.safe_load(manifest_path.read_text()))
    assert reloaded.run_id == manifest.run_id
    assert reloaded.cece_commit is None  # explicit null round-trips
    (reloaded_suite,) = reloaded.suites
    assert reloaded_suite.config_path == suite.config_path
    assert reloaded_suite.sweep == suite.sweep


def test_run_manifest_requires_explicit_cece_commit(suite_path: Path) -> None:
    # Required-but-nullable: omitting the SHA is a validation error, so no
    # writer can silently record null by accident; deliberate null stays
    # expressible for checkout-less dry-runs.
    suite = SuiteConfig.from_yaml(suite_path)
    with pytest.raises(ValidationError, match="cece_commit"):
        RunManifest(  # type: ignore[call-arg]
            run_id="01JZZZZZZZZZZZZZZZZZZZZZZZ",
            platform=Platform.LOCAL,
            runtime=Runtime.DOCKER,
            suites=[suite],
        )
