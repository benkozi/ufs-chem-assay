"""The user-facing tree (docs/, config/, scripts/): generic, and the manual
Ursa batch script in step with what the CLI renders."""

import re
import subprocess
from pathlib import Path

import pytest

from cli.run_config import RunConfig
from cli.stages import Stage, render_stage
from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNBOOK = _REPO_ROOT / "docs" / "ursa-runbook.md"
_BATCH_SCRIPT = _REPO_ROOT / "scripts" / "ursa-harness.sh"


def _user_facing_files() -> list[Path]:
    files = [_RUNBOOK, _BATCH_SCRIPT]
    files += sorted((_REPO_ROOT / "config").glob("*.yaml"))
    return files


def test_templates_live_in_config() -> None:
    assert TEMPLATES_DIR == _REPO_ROOT / "config"
    assert sorted(p.name for p in TEMPLATES_DIR.glob("*.yaml")) == [
        "local.yaml",
        "ursa.yaml",
    ]
    assert not list((_REPO_ROOT / "scripts").glob("*.yaml"))


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
    assert "sbatch" in text and "scripts/ursa-harness.sh" in text
    assert "config/ursa.yaml" in text


def test_batch_script_is_valid_bash_with_sbatch_directives() -> None:
    subprocess.run(["bash", "-n", str(_BATCH_SCRIPT)], check=True)
    lines = _BATCH_SCRIPT.read_text().splitlines()
    assert lines[0] == "#!/bin/bash"
    directives = [line for line in lines if line.startswith("#SBATCH")]
    joined = " ".join(directives)
    for flag in (
        "-A epic",
        "-q debug",
        "-p u1-compute",
        "-N 1",
        "-n 1",
        "-c 8",
        "-t 00:30:00",
    ):
        assert flag in joined, flag
    assert "-o " not in joined  # the log path is given on the sbatch command line
    assert "${ROOT:?" in _BATCH_SCRIPT.read_text()


def test_batch_script_matches_the_rendered_harness_stage() -> None:
    """Every CECE_* export the CLI renders for the Ursa template, and the
    pytest invocation shape, also appear in the manual script — the two
    are the same commands and must not drift."""
    config = RunConfig.from_yaml(TEMPLATES_DIR / "ursa.yaml")
    rendered = render_stage(Stage.HARNESS, config).text
    script = _BATCH_SCRIPT.read_text()
    exported = re.findall(r"^export (CECE_[A-Z_]+)=", rendered, flags=re.M)
    assert exported  # sanity: the stage does export settings
    for name in exported:
        assert re.search(rf"^export {name}=", script, flags=re.M), name
    for fragment in (
        "module purge",
        "module load",
        "uv run --no-sync pytest src/tests/test_driver_combos.py",
        "--suite-config=",
        "--combo-output-root=",
        "--combo-clean-root",
        "UV_OFFLINE=1",
    ):
        assert fragment in script, fragment
