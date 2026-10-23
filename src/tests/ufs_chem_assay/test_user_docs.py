"""The user-facing tree (docs/, config/): generic — nothing user-specific, no
design references — and consistent with what the CLI renders."""

from pathlib import Path

import pytest
import yaml

from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNBOOK = _REPO_ROOT / "docs" / "ursa-runbook.md"
_GCAFS_RUNBOOK = _REPO_ROOT / "docs" / "gcafs-anthro-runbook.md"
_README = _REPO_ROOT / "README.md"


def _user_facing_files() -> list[Path]:
    files = [_RUNBOOK, _GCAFS_RUNBOOK]
    files += sorted((_REPO_ROOT / "config").glob("*.yaml"))
    return files


def test_templates_live_in_config() -> None:
    assert TEMPLATES_DIR == _REPO_ROOT / "config"
    assert sorted(p.name for p in TEMPLATES_DIR.glob("*.yaml")) == [
        "local.yaml",
        "ursa-gcafs-anthro.yaml",
        "ursa.yaml",
    ]
    assert not (_REPO_ROOT / "scripts").exists()  # nothing hand-written ships there


@pytest.mark.parametrize("name", ["local.yaml", "ursa.yaml", "ursa-gcafs-anthro.yaml"])
def test_templates_carry_the_applications_map(name: str) -> None:
    raw = yaml.safe_load((TEMPLATES_DIR / name).read_text())
    assert list(raw["applications"]) == ["cece"]
    assert "cece" not in raw  # the old top-level section is gone
    assert "example" not in (TEMPLATES_DIR / name).read_text().lower()  # retired
    assert raw["applications"]["cece"]["ref"] == "develop"
    assert "ufs-community/CECE" in raw["applications"]["cece"]["git_url"]


@pytest.mark.parametrize("path", _user_facing_files(), ids=lambda p: p.name)
def test_nothing_user_specific(path: Path) -> None:
    text = path.read_text()
    for needle in ("Benjamin.Koziol", "my_stmp", "NCEPDEV/stmp"):
        assert needle not in text, f"{path.name} contains {needle!r}"


@pytest.mark.parametrize("path", [_RUNBOOK, _GCAFS_RUNBOOK], ids=lambda p: p.name)
def test_runbook_never_mentions_the_design(path: Path) -> None:
    assert "design/" not in path.read_text()
    assert "design doc" not in path.read_text().lower()


def test_gcafs_anthro_runbook_covers_the_use_case() -> None:
    # The dedicated runbook: placed inputs, the override list, the run, the
    # publish, the cold start, the four-rank check — and it names the
    # suite and run config the CLI resolves. No scratch paths, no users.
    text = _GCAFS_RUNBOOK.read_text()
    for needle in (
        "ursa-runbook.md",
        "data/gcafs-anthro",
        "sources",
        "ASSAY_INPUT_SEARCH_PATH",
        "fetch --suite-config=gcafs-anthro-suite.yaml --record-digests",
        "git diff",
        "config/ursa-gcafs-anthro.yaml --dry-run",
        "config/ursa-gcafs-anthro.yaml --stage data --stage harness",
        "--mem=0",
        "publish-baselines",
        "baselines:enabled=true",
        "slurm:ntasks=4",
        "report-mem",
        "record-benchmarks --output-root=",
        "gcafs-anthro/benchmarks.csv",
    ):
        assert needle in text, needle
    assert (
        "GCAFS=(" not in text
    )  # the override list became config/ursa-gcafs-anthro.yaml
    assert "ROOT=" not in text  # inherits $ROOT from the Ursa runbook
    for scratch in ("/scratch3", "/scratch4", "RUNDIRS"):
        assert scratch not in text, scratch


def test_runbook_uses_placeholders_and_ref_variables() -> None:
    text = _RUNBOOK.read_text()
    assert "ROOT=<" in text  # a placeholder, set once
    assert "HARNESS_REF" in text
    assert "CECE_REF" in text
    assert "feat/run-on-rdhpc" not in text
    assert "fix/all-examples-pass" not in text
    assert "buid-test-tweaks" not in text
    assert "benkozi/CECE" not in text
    assert "tmux" in text
    assert "squeue" in text
    assert "config/ursa.yaml --stage harness" in text  # the template runs as shipped
    assert "05-harness-cece.sh" in text
    assert "05-harness.sh" not in text
    assert "root_dir" in text  # explains the derived root
    assert (
        ".sbatch" in text
    )  # the per-combo job script, and the resubmit-by-hand triage step
    assert "record-benchmarks --output-root=" in text  # the committed rows
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
        "arn:aws:s3:::ufs-chem",
        "data_integration",
        "ufs-chem-assay fetch",
        "inputs:",
        "sha256",
        "--no-sign-request",
        "publish-baselines",
        "--publish-baselines",
        "--no-suite-update",
        "ASSAY_BASELINE_STORE",
        "baseline.yaml",
        "vulture",
        "rtol",
        "--baseline-rtol",
        "ASSAY_BASELINE_RTOL",
        "url: null",
        "ASSAY_INPUT_SEARCH_PATH",
        "--record-digests",
        "ASSAY_SRUN_ARGS",
        "gcafs-anthro-runbook.md",
        "## Benchmarks",
        "--record-benchmarks",
        "record-benchmarks --output-root=",
        "harness.record_benchmarks",
        "config_sha256",
    ):
        assert needle in text, needle
    assert "buid-test-tweaks" not in text
    assert "benkozi/CECE" not in text


def test_user_docs_never_say_example() -> None:
    # "Example" is not a thing the harness has: it tests specific
    # configurations of an application, declared by suites.
    for path in (_README, _RUNBOOK, *sorted(TEMPLATES_DIR.glob("*.yaml"))):
        assert "example" not in path.read_text().lower(), path.name
