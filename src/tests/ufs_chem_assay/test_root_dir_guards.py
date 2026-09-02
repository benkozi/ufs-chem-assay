"""root_dir fail-fast guards, exercised through real pytest subprocesses (the
same no-mocking style as the dry-run harness test). Every subprocess runs with
the ambient CECE_* variables stripped and from a neutral cwd (tmp_path), so
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
    env = {k: v for k, v in os.environ.items() if not k.startswith("CECE_")}
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
    assert "--cece-root-dir" in result.stderr


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


def test_cece_root_dir_flag_satisfies_requirement(tmp_path: Path) -> None:
    _git_checkout(tmp_path)
    result = _run_pytest(
        ["--dry-run", "--combo-output-root=combo_runs", f"--cece-root-dir={tmp_path}"],
        tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "combo_runs" / "run.yaml").is_file()


def test_flag_wins_over_env(tmp_path: Path) -> None:
    env_root = tmp_path / "from-env"
    flag_root = tmp_path / "from-flag"
    env_root.mkdir()
    flag_root.mkdir()
    _git_checkout(flag_root)  # only the winning root must be a checkout
    result = _run_pytest(
        ["--dry-run", "--combo-output-root=combo_runs", f"--cece-root-dir={flag_root}"],
        tmp_path,
        {"CECE_ROOT_DIR": str(env_root)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (flag_root / "combo_runs" / "run.yaml").is_file()
    assert not (env_root / "combo_runs").exists()


def test_non_git_root_dir_is_usage_error(tmp_path: Path) -> None:
    # A configured root that is not a git checkout is fatal at sessionstart:
    # the run must record the CECE commit it ran against.
    plain = tmp_path / "not-a-checkout"
    plain.mkdir()
    result = _run_pytest(["--dry-run"], tmp_path, {"CECE_ROOT_DIR": str(plain)})
    assert result.returncode == _USAGE_ERROR, result.stdout + result.stderr
    assert str(plain) in result.stderr
    assert "git" in result.stderr
