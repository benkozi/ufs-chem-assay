"""--suite-config selector semantics through real pytest subprocesses, same
neutral-cwd, stripped-environment style as the root_dir guard tests: the
built-in suite directory is always searched, selection is by regex, and
selection failures are usage errors before any test runs."""

import os
import subprocess
import sys
from pathlib import Path

_RUNNER_ROOT = Path(__file__).resolve().parents[3]  # combo-test-runner/
_INTEGRATION_TESTS = _RUNNER_ROOT / "src" / "tests" / "test_driver_combos.py"
_USAGE_ERROR = 4  # pytest.ExitCode.USAGE_ERROR


def _run_pytest(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CECE_")}
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


def test_exhaustive_suite_selected_by_filename(tmp_path: Path) -> None:
    # No path, no search-path setting: the built-in suite directory is always
    # a search root, so the checked-in suite is selectable by name alone.
    result = _run_pytest(
        ["--dry-run", "--suite-config=exhaustive-maccity-run-only-suite.yaml"],
        tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_match_is_usage_error_listing_candidates(tmp_path: Path) -> None:
    result = _run_pytest(["--dry-run", "--suite-config=absent-suite.yaml"], tmp_path)
    assert result.returncode == _USAGE_ERROR, result.stdout + result.stderr
    assert "matches no suite" in result.stderr
    # The listing names the discoverable built-in suites.
    assert "simple-maccity-suite.yaml" in result.stderr


def test_multi_match_runs_every_selected_suite(tmp_path: Path) -> None:
    # Multiple matches are a multi-suite session (no guard: everything the
    # selector matches runs). Two maccity-config suites dry-run together:
    # maccity 3 combos x 6 tests + 3 species items (its one species x 3
    # combos), exhaustive 240 x 6 (no species configured -> no species
    # items; the joint parametrization never manufactures cross-suite or
    # empty species cases).
    result = _run_pytest(
        [
            "--dry-run",
            "--suite-config=(simple-maccity|exhaustive-maccity-run-only)-suite.yaml",
        ],
        tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1461 skipped" in result.stdout
