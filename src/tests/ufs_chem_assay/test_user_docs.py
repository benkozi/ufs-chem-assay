"""The user-facing tree (docs/, config/): generic — nothing user-specific, no
design references — and consistent with what the CLI renders."""

from pathlib import Path

import pytest
import yaml

from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNBOOK = _REPO_ROOT / "docs" / "ursa-runbook.md"
_README = _REPO_ROOT / "README.md"


def _user_facing_files() -> list[Path]:
    files = [_RUNBOOK]
    files += sorted((_REPO_ROOT / "config").glob("*.yaml"))
    return files


def test_templates_live_in_config() -> None:
    assert TEMPLATES_DIR == _REPO_ROOT / "config"
    assert sorted(p.name for p in TEMPLATES_DIR.glob("*.yaml")) == [
        "local.yaml",
        "ursa.yaml",
    ]
    assert not (_REPO_ROOT / "scripts").exists()  # nothing hand-written ships there


@pytest.mark.parametrize("name", ["local.yaml", "ursa.yaml"])
def test_templates_carry_the_applications_map(name: str) -> None:
    raw = yaml.safe_load((TEMPLATES_DIR / name).read_text())
    assert list(raw["applications"]) == ["cece"]
    assert "cece" not in raw  # the old top-level section is gone
    assert "examples" not in raw["data"]  # example ids are the application's


@pytest.mark.parametrize("path", _user_facing_files(), ids=lambda p: p.name)
def test_nothing_user_specific(path: Path) -> None:
    text = path.read_text()
    for needle in ("Benjamin.Koziol", "my_stmp", "NCEPDEV/stmp"):
        assert needle not in text, f"{path.name} contains {needle!r}"


def test_runbook_never_mentions_the_design() -> None:
    assert "design/" not in _RUNBOOK.read_text()
    assert "design doc" not in _RUNBOOK.read_text().lower()


def test_runbook_uses_placeholders_and_ref_variables() -> None:
    text = _RUNBOOK.read_text()
    assert "ROOT=<" in text  # a placeholder, set once
    assert "HARNESS_REF" in text and "CECE_REF" in text
    assert "feat/run-on-rdhpc" not in text
    assert "fix/all-examples-pass" not in text
    assert "tmux" in text and "squeue" in text
    assert "config/ursa.yaml --stage harness" in text  # the template runs as shipped
    assert "05-harness-cece.sh" in text and "05-harness.sh" not in text
    assert "root_dir" in text  # explains the derived root
    assert (
        ".sbatch" in text
    )  # the per-combo job script, and the resubmit-by-hand triage step
    assert not (_REPO_ROOT / "scripts" / "ursa-harness.sh").exists()


def test_user_docs_name_no_retired_option() -> None:
    for path in (_README, _RUNBOOK):
        assert "--cece-root-dir" not in path.read_text(), path.name


def test_readme_documents_the_new_surfaces() -> None:
    text = _README.read_text()
    for needle in (
        "--application",
        "--override",
        "run-config.yaml",
        "ASSAY_PLATFORM",
        "CECE_ROOT_DIR",
        "harness_commit",
        "applications:",
    ):
        assert needle in text, needle
