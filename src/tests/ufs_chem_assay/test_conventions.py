"""The harness-wide conventions model: one frozen, described place for the
names every module agrees on (files, directories, prefixes, defaults)."""

import pytest
from pydantic import ValidationError

from baselines.models import BASELINE_CONVENTIONS, BaselineConventions
from models.conventions import CONVENTIONS, Conventions


def test_every_field_is_described() -> None:
    for model in (Conventions, BaselineConventions):
        for name, field in model.model_fields.items():
            assert field.description, (model.__name__, name)


def test_frozen_and_strict() -> None:
    with pytest.raises(ValidationError):
        CONVENTIONS.run_manifest = "other.yaml"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="extra"):
        Conventions(extra="no")  # type: ignore[call-arg]


def test_pinned_values() -> None:
    # A change to any of these renames something on disk or in the
    # environment: deliberate, never incidental.
    assert CONVENTIONS.env_prefix == "ASSAY_"
    assert "legacy_env_prefix" not in type(CONVENTIONS).model_fields
    assert CONVENTIONS.env_file == ".env"
    assert CONVENTIONS.default_baseline_store == "s3://ufs-chem/baselines"
    assert CONVENTIONS.default_output_store == "s3://ufs-chem/assay-output"
    assert CONVENTIONS.run_manifest == "run.yaml"
    assert CONVENTIONS.test_report == "test-report.csv"
    assert CONVENTIONS.combos_csv == "combos.csv"
    assert CONVENTIONS.descriptive_stats == "descriptive_stats.csv"
    assert CONVENTIONS.stats_comparison == "stats-comparison.csv"
    assert CONVENTIONS.benchmarks == "benchmarks.csv"
    assert CONVENTIONS.upload_record == "upload.yaml"
    assert CONVENTIONS.effective_config == "run-config.yaml"
    assert CONVENTIONS.staging_dirname == ".staging"
    assert CONVENTIONS.digest_chunk == 1024 * 1024
    assert CONVENTIONS.clock_format == "%Y-%m-%dT%H:%M:%SZ"


def test_env_var_spells_a_harness_variable() -> None:
    assert CONVENTIONS.env_var("input_search_path") == "ASSAY_INPUT_SEARCH_PATH"
    assert CONVENTIONS.env_var("LOG_LEVEL") == "ASSAY_LOG_LEVEL"
    with pytest.raises(TypeError):
        CONVENTIONS.env_var("log_level", legacy=True)  # type: ignore[call-arg]


def test_baseline_conventions_hold_only_baseline_names() -> None:
    # The transfer names live on the shared model, once.
    assert set(BaselineConventions.model_fields) == {
        "manifest",
        "driver_test",
        "comparison_test",
        "unpublished_ulid",
    }
    assert BASELINE_CONVENTIONS.manifest == "baseline.yaml"


def test_settings_re_exports_the_conventions() -> None:
    import settings

    assert settings.CONVENTIONS is CONVENTIONS
