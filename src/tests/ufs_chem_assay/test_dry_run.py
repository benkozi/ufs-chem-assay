"""--dry-run: the full session minus driver execution, exercised through a
real pytest subprocess against the integration suite — no docker, no mocking.
Session artifacts (run.yaml, combos.csv, every generated config) and the
all-skipped test-report.csv must exist; nothing the driver would produce may.
Runs from a neutral cwd so the repo-root .env cannot influence the session.
"""

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

_RUNNER_ROOT = Path(__file__).resolve().parents[3]  # <repo root>/


def test_dry_run_generates_everything_but_never_executes(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CECE_")}
    env["CECE_ROOT_DIR"] = str(tmp_path)
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
    assert len(manifest["cece_commit"]) == 40  # the fabricated checkout's HEAD

    combos = pd.read_csv(root / "combos.csv")
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
        "suite",
        "combo_id",
        "combo",
        "result",
    ]
    assert set(report["result"]) == {"skipped"}
    assert set(report["combo_id"]) == combo_ids
    # Seven combo-parameterized tests per combination (execution, file count,
    # filenames, dimensions, species attributes for co, baseline comparison,
    # stats).
    assert len(report) == 7 * 3


def test_run_yaml_records_cece_commit_sha(tmp_path: Path) -> None:
    # A git-checkout CECE root stamps its HEAD SHA into run.yaml.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CECE_")}
    env["CECE_ROOT_DIR"] = str(tmp_path)
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
    assert manifest["cece_commit"] == head
