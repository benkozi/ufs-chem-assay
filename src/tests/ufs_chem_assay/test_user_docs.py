"""The user-facing tree (docs/, config/): generic — nothing user-specific, no
design references — and consistent with what the CLI renders."""

from pathlib import Path

import pytest

from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNBOOK = _REPO_ROOT / "docs" / "ursa-runbook.md"


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
    assert "config/ursa.yaml" in text and "05-harness.sh" in text
    assert "--stage harness" in text
    assert (
        ".sbatch" in text
    )  # the per-combo job script, and the resubmit-by-hand triage step
    assert not (_REPO_ROOT / "scripts" / "ursa-harness.sh").exists()
