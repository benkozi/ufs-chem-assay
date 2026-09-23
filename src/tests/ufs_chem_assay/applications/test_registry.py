"""The application registry and the suite loader that dispatches on
`application:`."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from applications.base import Application
from applications.cece.suite import CeceSuiteConfig
from applications.registry import (
    DEFAULT_APPLICATION,
    REGISTRY,
    get_application,
    load_suite,
    peek_application,
)


def test_registry_holds_cece_only() -> None:
    assert list(REGISTRY) == ["cece"] and DEFAULT_APPLICATION == "cece"
    assert isinstance(get_application("cece"), Application)
    assert get_application("cece").name == "cece"


def test_unknown_application_lists_the_registry() -> None:
    with pytest.raises(ValueError, match=r"unknown application 'nope'.*\['cece'\]"):
        get_application("nope")
    with pytest.raises(ValueError, match="unknown application 5"):
        get_application(5)


def test_adapter_constants() -> None:
    app = get_application("cece")
    assert app.env_prefix == "CECE_"
    assert app.root_dir_token == "${CECE_ROOT_DIR}"
    assert str(app.container_workdir) == "/work"
    assert app.default_suite == "simple-maccity-suite.yaml"
    assert app.checkout_dirname == "CECE"
    assert app.standard_dimensions == ("time", "lev", "lat", "lon")
    assert app.examples is not None


def test_load_suite_defaults_to_cece(suite_path: Path) -> None:
    suite = load_suite(suite_path)
    assert isinstance(suite, CeceSuiteConfig)
    assert suite.application == "cece"
    assert peek_application(suite_path) == "cece"


def test_load_suite_explicit_application(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "explicit-suite.yaml"
    suite_file.write_text(
        f"name: explicit\napplication: cece\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
    )
    assert isinstance(load_suite(suite_file), CeceSuiteConfig)


def test_load_suite_unknown_application_fails(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "other-suite.yaml"
    suite_file.write_text(
        f"name: other\napplication: catchem\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
    )
    with pytest.raises(ValueError, match="unknown application 'catchem'"):
        load_suite(suite_file)
    with pytest.raises(ValueError, match="unknown application 'catchem'"):
        peek_application(suite_file)


def test_cece_suite_rejects_another_application_literal(
    cece_config_path: Path,
) -> None:
    # The subclass pins its discriminator: the registry chose it by name.
    with pytest.raises(ValidationError, match="application"):
        CeceSuiteConfig.model_validate(
            {
                "name": "x",
                "application": "catchem",
                "config_path": str(cece_config_path),
                "timeout_s": 5,
            }
        )
