"""The application registry and the suite loader that dispatches on
`application:`."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest_mock import MockerFixture

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
    assert list(REGISTRY) == ["cece"]
    assert DEFAULT_APPLICATION == "cece"
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
    assert app.data_dirname == "data"
    assert app.data_dir(Path("/checkout")) == Path("/checkout/data")


def test_every_adapter_names_its_data_directory() -> None:
    for app in REGISTRY.values():
        assert app.data_dirname, app.name
        assert "/" not in app.data_dirname, app.name


def test_examples_support_is_gone() -> None:
    import importlib

    base = importlib.import_module("applications.base")
    assert not hasattr(base, "ExamplesSupport")
    assert not hasattr(get_application("cece"), "examples")


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


def test_peek_application_defaults_for_a_non_mapping_document(tmp_path: Path) -> None:
    from applications.registry import peek_application

    path = tmp_path / "list-suite.yaml"
    path.write_text("- not\n- a mapping\n")
    assert peek_application(path) == "cece"


def test_commit_sha_wraps_a_failing_git(tmp_path: Path, mocker: MockerFixture) -> None:
    from applications.cece.settings import CeceSettings

    mocker.patch("subprocess.run", side_effect=OSError("no git"))
    with pytest.raises(ValueError, match="cannot determine the application commit"):
        CeceSettings(root_dir=tmp_path).commit_sha()


def test_base_config_fingerprint_dumps_the_model(tmp_path: Path) -> None:
    from tests.ufs_chem_assay.stubs import stub_application

    path = tmp_path / "c.yaml"
    path.write_text("x: 1\n")
    app = stub_application()
    assert app.config_model.from_yaml(path).x == 1  # type: ignore[attr-defined]
    assert app.config_fingerprint(path) == '{"x":1}'


def test_cece_stage_lines_refuse_the_shared_stages() -> None:
    from applications.base import Stage
    from applications.registry import get_application
    from cli.run_config import RunConfig
    from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR

    config = RunConfig.from_yaml(
        TEMPLATES_DIR / "local.yaml", resolve_application=get_application
    )
    with pytest.raises(ValueError, match="shared, not the adapter's"):
        get_application("cece").stage_lines(
            Stage.HARNESS, config, config.applications["cece"]
        )
