"""--run-examples gating through real pytest subprocesses (neutral cwd,
CECE_* stripped): disabled by default, dry-run wins, and driver execution
without a CECE root fails fast at collection. A fabricated CECE root with a
marker-writing download script proves downloads never run when gated off.
No docker: no test here reaches driver execution."""

import os
import subprocess
import sys
from pathlib import Path

from examples import DOWNLOAD_ENTRYPOINT, EXAMPLES_SUBDIR

_RUNNER_ROOT = Path(__file__).resolve().parents[3]  # combo-test-runner/
_EXAMPLE_TESTS = _RUNNER_ROOT / "src" / "tests" / "test_examples.py"
_USAGE_ERROR = 4  # pytest.ExitCode.USAGE_ERROR


def _fake_cece_root(tmp_path: Path) -> tuple[Path, Path]:
    """A CECE-shaped tree: one example config, plus a stub python download
    entrypoint that records execution by touching a marker file."""
    root = tmp_path / "cece"
    (root / EXAMPLES_SUBDIR).mkdir(parents=True)
    (root / EXAMPLES_SUBDIR / "cece_config_ex1.yaml").write_text("stale_schema: true\n")
    marker = root / "downloads-ran.marker"
    (root / DOWNLOAD_ENTRYPOINT).write_text(
        f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n"
    )
    return root, marker


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
            str(_EXAMPLE_TESTS),
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


def test_examples_skip_without_flag(tmp_path: Path) -> None:
    root, marker = _fake_cece_root(tmp_path)
    result = _run_pytest([], tmp_path, {"CECE_ROOT_DIR": str(root)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout
    assert not marker.exists()  # downloads gated off with the tests


def test_examples_skip_under_dry_run(tmp_path: Path) -> None:
    root, marker = _fake_cece_root(tmp_path)
    result = _run_pytest(
        ["--run-examples", "--dry-run"], tmp_path, {"CECE_ROOT_DIR": str(root)}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout
    assert not marker.exists()


def test_run_examples_without_root_dir_is_usage_error(tmp_path: Path) -> None:
    result = _run_pytest(["--run-examples"], tmp_path)
    assert result.returncode == _USAGE_ERROR, result.stdout + result.stderr
    assert "CECE_ROOT_DIR" in result.stderr
    assert "--cece-root-dir" in result.stderr
