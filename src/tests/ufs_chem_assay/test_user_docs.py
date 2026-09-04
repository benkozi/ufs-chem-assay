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
_HARNESS_SCRIPT = _REPO_ROOT / "scripts" / "ursa-harness.sh"
_WRAPPER = _REPO_ROOT / "scripts" / "cece-modules.sh"


def _user_facing_files() -> list[Path]:
    files = [_RUNBOOK, _HARNESS_SCRIPT, _WRAPPER]
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
    assert "scripts/ursa-harness.sh" in text and "tmux" in text and "squeue" in text
    assert "config/ursa.yaml" in text


def test_harness_script_is_a_login_node_script() -> None:
    subprocess.run(["bash", "-n", str(_HARNESS_SCRIPT)], check=True)
    text = _HARNESS_SCRIPT.read_text()
    assert text.splitlines()[0] == "#!/bin/bash"
    assert (
        "#SBATCH" not in text
    )  # it runs pytest on the login node; sbatch is per driver
    assert "${ROOT:?" in text
    assert "module purge" in text and "unset PYTHONPATH" in text
    assert "module load" not in text


def test_wrapper_is_valid_bash_and_execs_without_modules(tmp_path: Path) -> None:
    subprocess.run(["bash", "-n", str(_WRAPPER)], check=True)
    env = {"PATH": "/usr/bin:/bin"}  # no CECE_MODULEFILE, no MODULESHOME
    completed = subprocess.run(
        ["bash", str(_WRAPPER), "echo", "ok"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout == "ok\n"
    text = _WRAPPER.read_text()
    assert 'exec "$@"' in text and "module load" in text and "CECE_MODULEFILE" in text


def test_harness_script_matches_the_rendered_harness_stage() -> None:
    """Every CECE_* export the CLI renders for the Ursa template, and the
    pytest invocation shape, also appear in the manual script — the two
    are the same commands and must not drift."""
    config = RunConfig.from_yaml(TEMPLATES_DIR / "ursa.yaml")
    rendered = render_stage(Stage.HARNESS, config).text
    script = _HARNESS_SCRIPT.read_text()
    exported = re.findall(r"^export (CECE_[A-Z_]+)=", rendered, flags=re.M)
    assert exported  # sanity: the stage does export settings
    for name in exported:
        assert re.search(rf"^export {name}=", script, flags=re.M), name
    for fragment in (
        "module purge",
        "unset PYTHONPATH",
        "uv run --no-sync pytest src/tests/test_driver_combos.py",
        "--suite-config=",
        "--combo-output-root=",
        "--combo-clean-root",
    ):
        assert fragment in script, fragment
    assert "UV_OFFLINE" not in script  # login node: network is available
