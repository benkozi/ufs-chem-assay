"""The generic suite model: config_path resolution, the assertion and
analysis sections, name rules, the `application` field, and the run
manifest. CECE's sweep schema is tested in applications/cece/test_suite.py."""

import shutil
from pathlib import Path, PurePosixPath

import pytest
import yaml
from pydantic import ValidationError

from applications.registry import load_suite
from models.suite_config import (
    Assertions,
    AttributesAssertion,
    InputFile,
    RecordedInput,
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
    # Suites live beside their configuration: a bare file name resolves there.
    assert suite.config_path == (suite_path.parent / "maccity.yaml").resolve()
    assert suite.config_path.is_file()


def test_config_search_path_prepends_whole_path(
    tmp_path: Path, suite_path: Path, cece_config_path: Path
) -> None:
    # The suite copied away from its configuration: config_search_path points
    # at where the configuration now lives and is prepended whole.
    (tmp_path / "suites").mkdir()
    (tmp_path / "configs").mkdir()
    shutil.copy(suite_path, tmp_path / "suites" / "copied-suite.yaml")
    shutil.copy(cece_config_path, tmp_path / "configs" / "maccity.yaml")

    suite = load_suite(
        tmp_path / "suites" / "copied-suite.yaml",
        config_search_path=tmp_path / "configs",
    )
    assert suite.config_path == (tmp_path / "configs" / "maccity.yaml").resolve()


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


# ── Inputs ────────────────────────────────────────────────────────────────────

_MACCITY_URL = "s3://geos-chem/HEMCO/MACCITY/v2014-07/MACCity_4x5.nc"
_MACCITY_SHA = "ef70975af53499ab45f620a778043ff6fd29ec4e7e2777f3fd1f2db59a12aab9"


def _suite_with_inputs(
    tmp_path: Path, cece_config_path: Path, inputs_yaml: str
) -> Path:
    suite_file = tmp_path / "inputs-suite.yaml"
    suite_file.write_text(
        f"name: inputs\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        f"inputs:\n{inputs_yaml}"
    )
    return suite_file


def test_inputs_default_to_empty(tmp_path: Path, cece_config_path: Path) -> None:
    suite_file = tmp_path / "plain-suite.yaml"
    suite_file.write_text(
        f"name: plain\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
    )
    assert load_suite(suite_file).inputs == []


def test_inputs_parse_with_public_defaulting_false(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite = load_suite(
        _suite_with_inputs(
            tmp_path,
            cece_config_path,
            f"  - url: {_MACCITY_URL}\n    dst: MACCity_4x5.nc\n    sha256: {_MACCITY_SHA}\n"
            "  - url: s3://ufs-chem/private/sub/dir/file.nc\n    public: true\n"
            "    dst: sub/file.nc\n",
        )
    )
    first, second = suite.inputs
    assert isinstance(first, InputFile)
    assert first.url == _MACCITY_URL
    assert first.dst == PurePosixPath("MACCity_4x5.nc")
    assert first.sha256 == _MACCITY_SHA
    assert first.public is False
    assert second.public is True and second.sha256 is None
    assert second.dst == PurePosixPath("sub/file.nc")


def test_input_parent_prefix_and_filename() -> None:
    entry = InputFile(url=_MACCITY_URL, dst=PurePosixPath("x.nc"))
    assert entry.parent_prefix == "s3://geos-chem/HEMCO/MACCITY/v2014-07/"
    assert entry.filename == "MACCity_4x5.nc"
    top = InputFile(url="s3://bucket/file.nc", dst=PurePosixPath("file.nc"))
    assert top.parent_prefix == "s3://bucket/" and top.filename == "file.nc"


# A leading "./" is normalized away by PurePosixPath, so it is not rejectable.
@pytest.mark.parametrize("dst", ["/abs/x.nc", "../x.nc", "a/../x.nc", ""])
def test_inputs_dst_must_be_relative_and_clean(
    tmp_path: Path, cece_config_path: Path, dst: str
) -> None:
    with pytest.raises(ValidationError, match="dst"):
        load_suite(
            _suite_with_inputs(
                tmp_path,
                cece_config_path,
                f"  - url: {_MACCITY_URL}\n    dst: '{dst}'\n",
            )
        )


@pytest.mark.parametrize(
    "url",
    [
        "s3://geos-chem",
        "s3://geos-chem/",
        "s3://geos-chem/HEMCO/",
        "https://geos-chem.s3.amazonaws.com/HEMCO/x.nc",
        "s3://Bad_Bucket/x.nc",
        "geos-chem/x.nc",
    ],
)
def test_inputs_url_must_be_an_s3_object_uri(
    tmp_path: Path, cece_config_path: Path, url: str
) -> None:
    with pytest.raises(ValidationError, match="url"):
        load_suite(
            _suite_with_inputs(
                tmp_path, cece_config_path, f"  - url: '{url}'\n    dst: x.nc\n"
            )
        )


@pytest.mark.parametrize("digest", ["abc", "G" * 64, _MACCITY_SHA.upper()])
def test_inputs_sha256_must_be_64_lowercase_hex(
    tmp_path: Path, cece_config_path: Path, digest: str
) -> None:
    with pytest.raises(ValidationError, match="sha256"):
        load_suite(
            _suite_with_inputs(
                tmp_path,
                cece_config_path,
                f"  - url: {_MACCITY_URL}\n    dst: x.nc\n    sha256: '{digest}'\n",
            )
        )


def test_inputs_duplicate_dst_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    with pytest.raises(ValidationError, match="dst"):
        load_suite(
            _suite_with_inputs(
                tmp_path,
                cece_config_path,
                f"  - url: {_MACCITY_URL}\n    dst: same.nc\n"
                "  - url: s3://ufs-chem/other.nc\n    dst: same.nc\n",
            )
        )


def test_inputs_unknown_key_rejected(tmp_path: Path, cece_config_path: Path) -> None:
    with pytest.raises(ValidationError, match="nope"):
        load_suite(
            _suite_with_inputs(
                tmp_path,
                cece_config_path,
                f"  - url: {_MACCITY_URL}\n    dst: x.nc\n    nope: 1\n",
            )
        )


def test_run_manifest_records_inputs(tmp_path: Path, suite_path: Path) -> None:
    suite = load_suite(suite_path)
    recorded = RecordedInput(
        suite="simple-maccity",
        url=_MACCITY_URL,
        path=tmp_path / "data" / "MACCity_4x5.nc",
        sha256=_MACCITY_SHA,
        bytes=None,
    )
    manifest = _manifest([suite], inputs=[recorded])
    manifest_path = tmp_path / "run.yaml"
    manifest.to_yaml(manifest_path)
    dumped = yaml.safe_load(manifest_path.read_text())
    assert dumped["inputs"] == [
        {
            "suite": "simple-maccity",
            "url": _MACCITY_URL,
            "path": str(tmp_path / "data" / "MACCity_4x5.nc"),
            "sha256": _MACCITY_SHA,
            "bytes": None,
        }
    ]
    reloaded = RunManifest.model_validate(dumped)
    assert reloaded.inputs[0].bytes is None
    assert _manifest([suite]).inputs == []  # output-only; absent means none declared


# ── A configuration may be a directory ────────────────────────────────────────


def test_directory_config_path_resolves(tmp_path: Path) -> None:
    # An application whose configuration spans several files names the
    # directory; the adapter's loader decides the shape, resolution does not.
    config_dir = tmp_path / "configs" / "chapman"
    config_dir.mkdir(parents=True)
    (config_dir / "a.yaml").write_text("x: 1\n")
    suite_file = tmp_path / "dir-suite.yaml"
    suite_file.write_text("name: dir\nconfig_path: configs/chapman\ntimeout_s: 5\n")
    suite = load_suite(suite_file)
    assert suite.config_path == config_dir.resolve()
    assert suite.config_path.is_dir()


def test_missing_directory_config_path_raises(tmp_path: Path) -> None:
    suite_file = tmp_path / "dir-suite.yaml"
    suite_file.write_text("name: dir\nconfig_path: configs/absent\ntimeout_s: 5\n")
    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_suite(suite_file)


# ── The ${<PREFIX>ROOT_DIR} anchor ────────────────────────────────────────────


@pytest.fixture()
def fake_checkout(tmp_path: Path, cece_config_path: Path) -> Path:
    """An application-checkout-shaped tree holding a schema-valid config."""
    root = tmp_path / "checkout"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "base.yaml").write_text(cece_config_path.read_text())
    return root


def _anchored_suite(tmp_path: Path) -> Path:
    suite_file = tmp_path / "token-suite.yaml"
    suite_file.write_text(
        "name: token-suite\nconfig_path: ${CECE_ROOT_DIR}/configs/base.yaml\ntimeout_s: 5\n"
    )
    return suite_file


def test_root_token_resolves_against_root_dir(
    tmp_path: Path, fake_checkout: Path
) -> None:
    suite = load_suite(_anchored_suite(tmp_path), root_dir=fake_checkout)
    assert suite.config_path == (fake_checkout / "configs" / "base.yaml").resolve()


def test_root_token_without_root_dir_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="CECE_ROOT_DIR"):
        load_suite(_anchored_suite(tmp_path))


def test_root_token_beats_config_search_path(
    tmp_path: Path, fake_checkout: Path
) -> None:
    # The token is an explicit anchor; the search path (which would break on
    # it anyway) must not be consulted.
    suite = load_suite(
        _anchored_suite(tmp_path),
        config_search_path=tmp_path / "elsewhere",
        root_dir=fake_checkout,
    )
    assert suite.config_path.is_file()


def test_plain_relative_config_path_ignores_root_dir(
    suite_path: Path, fake_checkout: Path
) -> None:
    # A suite without the token resolves exactly as before even when a root is known.
    suite = load_suite(suite_path, root_dir=fake_checkout)
    assert suite.config_path.parent != fake_checkout
    assert suite.config_path.is_file()
