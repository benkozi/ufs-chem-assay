"""The user-facing tree (README.md, docs/, ops/, config/, modulefiles/): generic — nothing user-specific, no
design references — and consistent with what the CLI renders."""

import re
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from cli.main import main
from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNBOOK = _REPO_ROOT / "docs" / "ursa-runbook.md"
_GCAFS_RUNBOOK = _REPO_ROOT / "docs" / "gcafs-anthro-runbook.md"
_KOKKOS_RUNBOOK = _REPO_ROOT / "docs" / "kokkos-runbook.md"
_README = _REPO_ROOT / "README.md"


def _user_facing_files() -> list[Path]:
    files = [_README, _RUNBOOK, _GCAFS_RUNBOOK, _KOKKOS_RUNBOOK]
    files += sorted((_REPO_ROOT / "ops").glob("*.md"))
    files += sorted((_REPO_ROOT / "config").glob("*.yaml"))
    files += sorted((_REPO_ROOT / "modulefiles").glob("*.lua"))
    return files


def test_templates_live_in_config() -> None:
    assert TEMPLATES_DIR == _REPO_ROOT / "config"
    assert sorted(p.name for p in TEMPLATES_DIR.glob("*.yaml")) == [
        "gaea-c7.yaml",
        "local.yaml",
        "ursa-kokkos.yaml",
        "ursa.yaml",
    ]
    assert not (_REPO_ROOT / "scripts").exists()  # nothing hand-written ships there


@pytest.mark.parametrize("name", ["local.yaml", "ursa.yaml"])
def test_templates_carry_the_applications_map(name: str) -> None:
    raw = yaml.safe_load((TEMPLATES_DIR / name).read_text())
    expected = ["monetio", "cece"] if name == "local.yaml" else ["cece"]
    assert list(raw["applications"]) == expected
    assert "cece" not in raw  # the old top-level section is gone
    assert "example" not in (TEMPLATES_DIR / name).read_text().lower()  # retired
    assert raw["applications"]["cece"]["ref"] == "develop"
    assert "ufs-community/CECE" in raw["applications"]["cece"]["git_url"]


def test_modulefiles_are_user_facing() -> None:
    names = {path.name for path in _user_facing_files()}
    assert {"ufs_common.lua", "ufs_ursa.oneapi.lua", "ufs_gaeac7.oneapi.lua"} <= names
    assert "gaea-c7.yaml" in names


@pytest.mark.parametrize("path", _user_facing_files(), ids=lambda p: p.name)
def test_nothing_user_specific(path: Path) -> None:
    text = path.read_text()
    for needle in (
        "Benjamin.Koziol",
        "Richard.Grubin",
        "my_stmp",
        "NCEPDEV/stmp",
        "bkoziol",  # a laptop's home
        "/Users/",
        "benkozi-work",  # a person's transfer repository
    ):
        assert needle not in text, f"{path.name} contains {needle!r}"


_DOCUMENTS = [p for p in _user_facing_files() if p.suffix == ".md"]


def test_the_ops_documents_are_user_facing() -> None:
    names = {path.name for path in _DOCUMENTS}
    assert {"README.md", "c7-runbook.md", "kokkos-runbook.md"} <= names


@pytest.mark.parametrize("path", _DOCUMENTS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_runbook_never_mentions_the_design(path: Path) -> None:
    # The design records are not in this repository: nothing to link to.
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
        "config/ursa.yaml --dry-run --session gcafs-anthro",
        "config/ursa.yaml --stage data --stage harness --session gcafs-anthro",
        "watch --session gcafs-anthro",
        "report --session gcafs-anthro",
        "sessions:gcafs-anthro:slurm:ntasks=4",
        "--mem=0",
        "publish-baselines",
        "baselines:enabled=true",
        "slurm:ntasks=4",
        "report-mem",
        "record-benchmarks --output-root=",
        "gcafs-anthro/benchmarks.csv",
        "upload-output --output-root=",
        "download-output --ulid",
        "assay-output/<run ULID>",
    ):
        assert needle in text, needle
    assert "GCAFS=(" not in text  # the override list became a session overlay
    assert "ursa-gcafs-anthro.yaml" not in text  # retired: one Ursa template
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
    assert "tmux" in text  # optional now, and the runbook says so
    assert "squeue" in text
    assert "config/ursa.yaml --stage harness" in text  # the template runs as shipped
    assert "05-harness-cece-simple-maccity.sh" in text  # per-session scripts
    assert "06-resume-cece-simple-maccity.sh" in text
    assert "ufs-chem-assay watch" in text
    assert "jobs.yaml" in text
    assert "ufs-chem-assay cancel" in text
    assert "ufs-chem-assay report" in text
    assert "report --session simple-maccity --write" in text  # keep the YAML
    assert "05-harness-cece.sh" not in text
    assert "05-harness.sh" not in text
    assert "ursa-gcafs-anthro.yaml" not in text
    assert "root_dir" in text  # explains the derived root
    assert (
        ".sbatch" in text
    )  # the per-combo job script, and the resubmit-by-hand triage step
    assert "record-benchmarks --output-root=" in text  # the committed rows
    assert "upload-output --output-root=" in text  # keeping a run
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
        "## Output upload",
        "upload-output --output-root=",
        "download-output --ulid=",
        "ASSAY_OUTPUT_STORE",
        "s3://ufs-chem/assay-output",
        "--upload-output",
        "harness.upload_output",
        "upload.yaml",
        "sessions:",
        "--session",
        "harness.submit_only",
        "jobs.yaml",
        "ufs-chem-assay watch",
        "ufs-chem-assay cancel",
        "--submit-only",
        "--resume",
        "ASSAY_SLURM_POLL_S",
        "06-resume-",
        "flock",
        "ufs-chem-assay report",
        "--output-root PATH",
        "--write PATH",
        "pipeline-error",
        "CECE_MODULEPATH",
        "CECE_MODULE_ENV",
        "modulepath",
        "module_env",
        "toolchain_tools",
        "configure_gates",
        "modulefiles/ufs_ursa.oneapi.lua",
        "ASSAY_SPACK_ENV_ROOT",
        "module reset",
        "gaea-c6",
        "gaea-c7",
        "build_node",
        "build_minutes",
        "tests.enabled",
        "-DCECE_BUILD_TESTING=OFF",
        "CECE dependency ESMF: system",
        "04-test-<application>.sbatch",
        "submit-tests",
        "evaluate-tests",
        "tests.yaml",
        "watch --only tests",
        "cancel --tests-root",
        "record-benchmarks --tests-root",
        "tests-benchmarks.csv",
        "build_type",
        "tests-failed",
        "tests-job-failed",
        "catchem",
        "kokkos-smoke",
        "CATCHEM_ROOT_DIR",
        "has no driver suite",
        "Using system Kokkos",
        "-DCATCHEM_BUILD_MUSICA=OFF",
        "SMOKE TEST PASSED",
        "toolchains:",
        "--toolchain all",
        "mpi_launcher",
        "tests_env",
        "<root_dir>/<toolchain>",
        "selected_toolchains",
        "-DMPIEXEC_EXECUTABLE",
        "config/ursa-kokkos.yaml",
        "kokkos-runbook.md",
    ):
        assert needle in text, needle
    assert "buid-test-tweaks" not in text
    assert "benkozi/CECE" not in text
    assert "ursa-gcafs-anthro.yaml" not in text  # retired: one Ursa template


def test_user_docs_never_say_example() -> None:
    # "Example" is not a thing the harness has: it tests specific
    # configurations of an application, declared by suites.
    for path in (_README, _RUNBOOK, *sorted(TEMPLATES_DIR.glob("*.yaml"))):
        assert "example" not in path.read_text().lower(), path.name


def test_documented_driver_commands_name_an_application_or_a_suite() -> None:
    # Several applications are registered, so a bare
    # `pytest src/tests/test_driver_combos.py` is a usage error ("pass
    # --application or --suite-config"): every documented command selects.
    for path in (_README, _RUNBOOK, _GCAFS_RUNBOOK):
        joined = path.read_text().replace("\\\n", " ")  # shell continuations
        commands = [
            line
            for line in joined.splitlines()
            if "src/tests/test_driver_combos.py" in line and "pytest" in line
        ]
        for command in commands:
            assert "--application" in command or "--suite-config" in command, (
                f"{path.name}: {command.strip()}"
            )
    assert "`cece` today" not in _README.read_text()  # monetio is registered too


def test_kokkos_runbook_covers_the_use_case() -> None:
    # The runbook of config/ursa-kokkos.yaml: what the template builds and
    # tests, what to edit first, the commands, the same steps by hand, and
    # what to record from a first run. No user's paths, no forks by name.
    text = _KOKKOS_RUNBOOK.read_text()
    for needle in (
        "ursa-runbook.md",
        "config/ursa-kokkos.yaml",
        "modulefiles/",
        "modules/Core",
        "--toolchain all",
        "--stage source --stage build --stage test",
        "--dry-run",
        "02-build-cece-oneapi.sh",
        "04-test-cece-gnu.sbatch",
        "watch --only tests",
        "report --only tests",
        "cancel --tests-root",
        "record-benchmarks --tests-root",
        "submit-tests",
        "--resubmit",
        "tests.yaml",
        "--session simple-maccity",
        "module purge",
        "module load ufs_ursa.oneapi",
        "srun ",
        "-DCECE_BUILD_TESTING=OFF",
        "-DCATCHEM_BUILD_MUSICA=OFF",
        "Using system Kokkos",
        "CECE dependency Kokkos: system",
        "SMOKE TEST PASSED",
        "ctest --test-dir build",
        "module avail kokkos",
        "kokkos_ver",
        "mpi_launcher",
        "report-mem",
    ):
        assert needle in text, needle
    for absent in ("benkozi", "Richard.Grubin", "build-test-kokkos.sh"):
        assert absent not in text, absent
    assert "example" not in text.lower()


def test_kokkos_runbook_names_scripts_the_template_renders(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    # CATChem has two build variants, and a variant is part of a script's
    # name: every script the runbook cites is one a dry run writes.
    mocker.patch("cli.commands.run.run_bash")
    root = tmp_path / "root"
    assert (
        main(
            [
                "run",
                f"--config-file={TEMPLATES_DIR / 'ursa-kokkos.yaml'}",
                f"--root-dir={root}",
                "--application=all",
                "--toolchain=all",
                "--stage=source",
                "--stage=build",
                "--stage=test",
                "--dry-run",
            ]
        )
        == 0
    )
    rendered = {p.name for p in (root / "scripts").iterdir()}
    text = _KOKKOS_RUNBOOK.read_text()
    cited = set(re.findall(r"\b0\d-[a-z0-9-]+\.(?:sh|sbatch)\b", text))
    assert "02-build-catchem-gnu-musica.sh" in cited
    assert cited <= rendered, sorted(cited - rendered)
    # The pattern it gives for a variant's scripts, filled in, is one too.
    for pattern in (
        "02-build-catchem-<toolchain>-compute.sh",
        "04-test-catchem-<toolchain>-musica.sbatch",
    ):
        assert pattern in text
        assert pattern.replace("<toolchain>", "oneapi") in rendered
