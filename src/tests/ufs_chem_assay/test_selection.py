"""selection.py: the one place a session's suites are resolved — shared by
pytest's sessionstart and the CLI's fetch — against the checked-in tree and
fabricated roots. No subprocess here; the subprocess-level selection tests
stay in test_suite_selection.py."""

from pathlib import Path

import pytest

from platforms import Platform
from selection import BUILTIN_CONFIG_DIR, ResolvedSuites, resolve_suites
from settings import Settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"platform": Platform.LOCAL}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_builtin_config_dir_is_the_checked_in_tree() -> None:
    assert BUILTIN_CONFIG_DIR == Path(__file__).resolve().parents[1] / "config"
    assert (
        BUILTIN_CONFIG_DIR / "cece" / "maccity" / "simple-maccity-suite.yaml"
    ).is_file()


def test_default_suite_resolves_without_an_option() -> None:
    resolved = resolve_suites(_settings(), None)
    assert isinstance(resolved, ResolvedSuites)
    assert resolved.application.name == "cece"
    assert [suite.name for _, suite in resolved.suites] == ["simple-maccity"]
    (path, suite) = resolved.suites[0]
    assert path.name == "simple-maccity-suite.yaml"
    assert suite.config_path.is_file()  # resolved, not raw


def test_regex_selects_several_suites_in_name_order() -> None:
    resolved = resolve_suites(_settings(), r"exhaustive-maccity-.*-suite\.yaml")
    assert [suite.name for _, suite in resolved.suites] == [
        "exhaustive-maccity-asserted",
        "exhaustive-maccity-run-only",
    ]


def test_explicit_application_drops_other_applications_suites(tmp_path: Path) -> None:
    other = tmp_path / "other-suite.yaml"
    other.write_text(
        "name: other\napplication: stub\nconfig_path: x.yaml\ntimeout_s: 5\n"
    )
    # An unknown application in a dropped suite is still an error at peek time.
    with pytest.raises(ValueError, match="unknown application 'stub'"):
        resolve_suites(
            _settings(application="cece", suite_config_search_path=[tmp_path]),
            ".*-suite.yaml",
        )


def test_no_match_is_a_value_error_listing_candidates() -> None:
    with pytest.raises(ValueError, match="matches no suite") as excinfo:
        resolve_suites(_settings(), "absent-suite.yaml")
    assert "simple-maccity-suite.yaml" in str(excinfo.value)


def test_duplicate_suite_names_are_rejected(
    tmp_path: Path, cece_config_path: Path
) -> None:
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "dup-suite.yaml").write_text(
            f"name: dup\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        )
    with pytest.raises(ValueError, match="defined by both"):
        resolve_suites(
            _settings(suite_config_search_path=[tmp_path]), r".*/dup-suite\.yaml"
        )


def test_root_dir_flows_into_config_path_resolution(
    tmp_path: Path, cece_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "configs").mkdir(parents=True)
    (checkout / "configs" / "base.yaml").write_text(cece_config_path.read_text())
    (tmp_path / "anchored-suite.yaml").write_text(
        "name: anchored\nconfig_path: ${CECE_ROOT_DIR}/configs/base.yaml\ntimeout_s: 5\n"
    )
    monkeypatch.setenv("CECE_ROOT_DIR", str(checkout))
    resolved = resolve_suites(
        _settings(suite_config_search_path=[tmp_path]), "anchored-suite.yaml"
    )
    assert resolved.app_settings.root_dir == checkout
    assert (
        resolved.suites[0][1].config_path
        == (checkout / "configs" / "base.yaml").resolve()
    )
