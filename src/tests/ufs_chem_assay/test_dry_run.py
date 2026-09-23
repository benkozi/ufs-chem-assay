"""--dry-run: the full session minus driver execution, exercised through a
real pytest subprocess against the integration suite — no docker, no mocking.
Session artifacts (run.yaml, combos.csv, every generated config) and the
all-skipped test-report.csv must exist; nothing the driver would produce may.
Runs from a neutral cwd so the repo-root .env cannot influence the session.
"""

import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pandas as pd
import pytest
import yaml

_RUNNER_ROOT = Path(__file__).resolve().parents[3]  # <repo root>/


def test_dry_run_generates_everything_but_never_executes(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CECE_", "ASSAY_"))}
    env["CECE_ROOT_DIR"] = str(tmp_path)
    env["ASSAY_PLATFORM"] = "local"  # the child cannot inherit the hostname patch
    # A configured root must be a git checkout (the SHA is fatal otherwise).
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "fabricated",
        ],
        cwd=tmp_path,
        check=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(_RUNNER_ROOT / "src" / "tests" / "test_driver_combos.py"),
            "--dry-run",
            "--combo-output-root=combo_runs",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    root = tmp_path / "combo_runs"
    assert (root / "run.yaml").is_file()
    manifest = yaml.safe_load((root / "run.yaml").read_text())
    assert len(manifest["application_commit"]) == 40  # the fabricated checkout's HEAD
    assert manifest["application"] == "cece"
    assert manifest["suites"][0]["application"] == "cece"
    # The harness records itself: the installed version and this checkout's
    # HEAD (suffixed -dirty while the tree is being edited).
    assert manifest["harness_version"] == version("ufs-chem-assay")
    head = subprocess.run(
        ["git", "-C", str(_RUNNER_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert manifest["harness_commit"] in (head, f"{head}-dirty")

    combos = pd.read_csv(root / "combos.csv")
    assert list(combos.columns)[:4] == ["run_id", "combo_id", "application", "suite"]
    assert set(combos["application"]) == {"cece"}
    combo_ids = set(combos["combo_id"])
    assert len(combo_ids) == 3  # simple-maccity: mapalgo x 3
    # Effective-parameter table: every sweepable dimension of every combo
    # (co: 3 fields + MACCITY: 3 fields), swept flagged, suite stamped.
    assert len(combos) == 6 * 3
    assert set(combos["suite"]) == {"simple-maccity"}
    swept = combos[combos["swept"]]
    assert set(zip(swept["target"], swept["field"])) == {("MACCITY", "mapalgo")}
    for combo_id in combo_ids:
        assert len(combo_id) == 26  # runtime ULID directory names
        assert (root / combo_id / f"{combo_id}.yaml").is_file()

    # The driver never ran: no captured output, no NetCDF, anywhere.
    assert not list(root.rglob("*.out"))
    assert not list(root.rglob("*.nc"))

    report = pd.read_csv(root / "test-report.csv")
    assert list(report.columns) == [
        "pytest_name",
        "application",
        "suite",
        "combo_id",
        "combo",
        "result",
    ]
    assert set(report["application"]) == {"cece"}
    assert set(report["result"]) == {"skipped"}
    assert set(report["combo_id"]) == combo_ids
    # Seven combo-parameterized tests per combination (execution, file count,
    # filenames, dimensions, species attributes for co, baseline comparison,
    # stats).
    assert len(report) == 7 * 3


def test_run_yaml_records_the_application_commit_sha(tmp_path: Path) -> None:
    # A git-checkout application root stamps its HEAD SHA into run.yaml.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CECE_", "ASSAY_"))}
    env["CECE_ROOT_DIR"] = str(tmp_path)
    env["ASSAY_PLATFORM"] = "local"
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(_RUNNER_ROOT / "src" / "tests" / "test_driver_combos.py"),
            "--dry-run",
            "--combo-output-root=combo_runs",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = yaml.safe_load((tmp_path / "combo_runs" / "run.yaml").read_text())
    assert manifest["application_commit"] == head


def test_slurm_dry_run_writes_a_job_script_per_combo(tmp_path: Path) -> None:
    """Under the slurm runtime every combo's job script is a recorded
    artifact beside its yaml, even when nothing is submitted."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CECE_", "ASSAY_"))}
    env["CECE_ROOT_DIR"] = str(tmp_path)
    env["ASSAY_PLATFORM"] = "ursa"
    env["ASSAY_RUNTIME"] = "slurm"
    env["ASSAY_SBATCH_ARGS"] = "-A epic -q debug"
    env["CECE_MODULEFILE"] = "cece_ursa.intelllvm"
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        cwd=tmp_path,
        check=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(_RUNNER_ROOT / "src" / "tests" / "test_driver_combos.py"),
            "--dry-run",
            "--combo-output-root=combo_runs",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    root = tmp_path / "combo_runs"
    combo_dirs = [p for p in root.iterdir() if p.is_dir()]
    assert len(combo_dirs) == 3
    for combo_dir in combo_dirs:
        script = combo_dir / f"{combo_dir.name}.sbatch"
        assert script.is_file()
        text = script.read_text()
        assert f"#SBATCH --output={combo_dir}/{combo_dir.name}.out" in text
        assert "#SBATCH -A epic" in text and "module load cece_ursa.intelllvm" in text
        assert text.rstrip().endswith(
            f"srun --ntasks=1 ./build/cece_standalone_driver {combo_dir}/{combo_dir.name}.yaml"
        )
    manifest = yaml.safe_load((root / "run.yaml").read_text())
    assert (
        manifest["runtime"] == "slurm"
        and manifest["modulefile"] == "cece_ursa.intelllvm"
    )
    assert not list(root.rglob("*.out"))  # nothing submitted


def test_slurm_dry_run_without_a_checkout_writes_no_job_scripts(tmp_path: Path) -> None:
    """The checkout-less dry run (no CECE_ROOT_DIR) is supported under every
    runtime: with nothing to --chdir into there is no job to describe, so
    slurm records no .sbatch and behaves like docker/native. (Third Ursa
    run: this errored at setup in generated_combos.)"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CECE_", "ASSAY_"))}
    env["ASSAY_PLATFORM"] = "ursa"
    env["ASSAY_RUNTIME"] = "slurm"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(_RUNNER_ROOT / "src" / "tests" / "test_driver_combos.py"),
            "--dry-run",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "bt"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "21 skipped" in result.stdout
    assert not list((tmp_path / "bt").rglob("*.sbatch"))
    manifest = yaml.safe_load(next((tmp_path / "bt").rglob("run.yaml")).read_text())
    assert manifest["runtime"] == "slurm" and manifest["application_commit"] is None


@pytest.mark.parametrize("how", ["flag", "env"])
def test_application_switch_selects_the_cece_default_suite(
    tmp_path: Path, how: str
) -> None:
    """--application=cece (or ASSAY_APPLICATION) runs the adapter's default
    suite when no --suite-config is given, and the run records it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CECE_", "ASSAY_"))}
    env["ASSAY_PLATFORM"] = "local"
    args = [
        "--dry-run",
        "-q",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(tmp_path / "bt"),
    ]
    if how == "flag":
        args.append("--application=cece")
    else:
        env["ASSAY_APPLICATION"] = "cece"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(_RUNNER_ROOT / "src" / "tests" / "test_driver_combos.py"),
            *args,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "21 skipped" in result.stdout  # simple-maccity, the default
    manifest = yaml.safe_load(next((tmp_path / "bt").rglob("run.yaml")).read_text())
    assert manifest["application"] == "cece"


def test_unknown_application_is_a_usage_error(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CECE_", "ASSAY_"))}
    env["ASSAY_PLATFORM"] = "local"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(_RUNNER_ROOT / "src" / "tests" / "test_driver_combos.py"),
            "--dry-run",
            "--application=catchem",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 4, result.stdout + result.stderr  # USAGE_ERROR
    assert "unknown application 'catchem'" in result.stderr
    assert "['cece']" in result.stderr
