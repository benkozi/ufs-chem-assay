"""root_dir fail-fast guards, exercised through real pytest subprocesses (the
same no-mocking style as the dry-run harness test). Every subprocess runs with
the ambient ASSAY_*/CECE_* variables stripped and from a neutral cwd (tmp_path), so
neither the shell nor the repo-root .env file can supply configuration.
"""

import os
import subprocess
import sys
from pathlib import Path

_RUNNER_ROOT = Path(__file__).resolve().parents[3]  # <repo root>/
_INTEGRATION_TESTS = _RUNNER_ROOT / "src" / "tests" / "test_driver_combos.py"
_USAGE_ERROR = 4  # pytest.ExitCode.USAGE_ERROR


def _run_pytest(
    args: list[str], cwd: Path, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CECE_", "ASSAY_"))}
    # Explicit platform: the child cannot inherit the in-process hostname
    # patch, and on an RDHPC login node detection would pick that machine.
    env["ASSAY_PLATFORM"] = "local"
    env |= env_overrides or {}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(_INTEGRATION_TESTS),
            *args,
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _git_checkout(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
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
        cwd=path,
        check=True,
    )


def test_driver_execution_without_root_dir_is_usage_error(tmp_path: Path) -> None:
    result = _run_pytest([], tmp_path)
    assert result.returncode == _USAGE_ERROR, result.stdout + result.stderr
    assert "CECE_ROOT_DIR" in result.stderr
    assert "--cece-root-dir" not in result.stderr  # the flag is retired


def test_driver_execution_with_nonexistent_root_dir_is_usage_error(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "does-not-exist"
    result = _run_pytest([], tmp_path, {"CECE_ROOT_DIR": str(missing)})
    assert result.returncode == _USAGE_ERROR, result.stdout + result.stderr
    assert str(missing) in result.stderr


def test_output_root_without_root_dir_is_usage_error_even_dry_run(
    tmp_path: Path,
) -> None:
    result = _run_pytest(["--dry-run", "--combo-output-root=combo_runs"], tmp_path)
    assert result.returncode == _USAGE_ERROR, result.stdout + result.stderr
    assert "CECE_ROOT_DIR" in result.stderr


def test_bare_dry_run_passes_with_no_environment(tmp_path: Path) -> None:
    result = _run_pytest(["--dry-run"], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_root_dir_flag_is_retired(tmp_path: Path) -> None:
    # CECE_ROOT_DIR (or .env, or the run config) is the root's one source in
    # the pytest process; the old --cece-root-dir is an unknown option.
    result = _run_pytest(["--dry-run", f"--cece-root-dir={tmp_path}"], tmp_path)
    assert result.returncode == _USAGE_ERROR, result.stdout + result.stderr
    assert "unrecognized arguments: --cece-root-dir" in result.stderr


def test_env_root_dir_satisfies_requirement(tmp_path: Path) -> None:
    _git_checkout(tmp_path)
    result = _run_pytest(
        ["--dry-run", "--combo-output-root=combo_runs"],
        tmp_path,
        {"CECE_ROOT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "combo_runs" / "run.yaml").is_file()


def test_non_git_root_dir_is_usage_error(tmp_path: Path) -> None:
    # A configured root that is not a git checkout is fatal at sessionstart:
    # the run must record the application commit it ran against.
    plain = tmp_path / "not-a-checkout"
    plain.mkdir()
    result = _run_pytest(["--dry-run"], tmp_path, {"CECE_ROOT_DIR": str(plain)})
    assert result.returncode == _USAGE_ERROR, result.stdout + result.stderr
    assert str(plain) in result.stderr
    assert "git" in result.stderr


def test_clean_root_refuses_a_directory_that_is_not_a_harness_root(
    tmp_path: Path,
) -> None:
    """--combo-clean-root only ever removes a previous harness output root
    (one with run.yaml at its top); anything else is refused untouched —
    an absolute output_root under native/slurm can point anywhere."""
    _git_checkout(tmp_path)
    foreign = tmp_path / "combo_runs"
    foreign.mkdir()
    (foreign / "precious.txt").write_text("keep me")
    result = _run_pytest(
        ["--dry-run", "--combo-output-root=combo_runs", "--combo-clean-root"],
        tmp_path,
        {"CECE_ROOT_DIR": str(tmp_path)},
    )
    assert result.returncode == _USAGE_ERROR, result.stdout + result.stderr
    assert "run.yaml" in result.stderr
    assert (foreign / "precious.txt").read_text() == "keep me"


def test_clean_root_removes_a_previous_harness_root(tmp_path: Path) -> None:
    _git_checkout(tmp_path)
    previous = tmp_path / "combo_runs"
    previous.mkdir()
    (previous / "run.yaml").write_text("run_id: old\n")
    (previous / "stale.txt").write_text("x")
    result = _run_pytest(
        ["--dry-run", "--combo-output-root=combo_runs", "--combo-clean-root"],
        tmp_path,
        {"CECE_ROOT_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (previous / "stale.txt").exists()
    assert (previous / "run.yaml").is_file()  # the new run's manifest
