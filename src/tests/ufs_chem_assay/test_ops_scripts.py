"""ops/assay-run.sh, the bootstrap: `module reset` and `git pull`, which only
a shell before the harness can do, then `ufs-chem-assay run --watch` on the
one run config it is given, through ops/assay: the harness with uv's cache
and Pythons in the checkout's .uv/ and nowhere else. Run with bash (3.2 on
macOS) from a scratch harness checkout, with `uv`, `git` and `module`
replaced by stubs on PATH that record their arguments and working directory
(one line per call), and uv its two directories."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from identity import HARNESS_ROOT

_OPS = HARNESS_ROOT / "ops"

# Each stub records "<name> <args> (in <cwd>)"; uv also records its two
# directories in STUB_ENV_LOG and exits STUB_UV_EXIT.
_STUB = '#!/bin/bash\necho "{name} $* (in $PWD)" >> "$STUB_LOG"\n{tail}'
_UV_TAIL = (
    'echo "UV_CACHE_DIR=$UV_CACHE_DIR UV_PYTHON_INSTALL_DIR=$UV_PYTHON_INSTALL_DIR"'
    ' >> "$STUB_ENV_LOG"\n'
    'exit "${STUB_UV_EXIT:-0}"\n'
)


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    """A scratch checkout holding the bootstrap and ops/assay, so their
    paths land there."""
    checkout = tmp_path / "harness"
    (checkout / "ops").mkdir(parents=True)
    for script in ("assay-run.sh", "assay-transfer.sh", "assay"):
        shutil.copy2(_OPS / script, checkout / "ops" / script)
    return checkout


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, tail in (
        ("uv", _UV_TAIL),
        ("git", ""),
        ("module", ""),
    ):
        path = stubs / name
        path.write_text(_STUB.format(name=name, tail=tail))
        path.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    return {
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "HOME": str(home),  # $HOME/.local/bin is prepended: no real uv there
        "STUB_LOG": str(tmp_path / "calls.log"),
        "STUB_ENV_LOG": str(tmp_path / "uv-env.log"),
    }


def _bash(
    harness: Path,
    env: dict[str, str],
    *args: str,
    cwd: Path | None = None,
    script: str = "assay-run.sh",
    **extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(harness / "ops" / script), *args],
        env={**env, **extra},
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _calls(env: dict[str, str]) -> list[str]:
    log = Path(env["STUB_LOG"])
    return log.read_text().splitlines() if log.is_file() else []


def _uv_dirs(env: dict[str, str]) -> list[str]:
    log = Path(env["STUB_ENV_LOG"])
    return log.read_text().splitlines() if log.is_file() else []


def _scoped(harness: Path) -> str:
    return (
        f"UV_CACHE_DIR={harness}/.uv/cache UV_PYTHON_INSTALL_DIR={harness}/.uv/python"
    )


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "mine" / "c7.local.yaml"
    path.parent.mkdir()
    path.write_text("extends: config/gaea-c7.yaml\n")
    return path


def test_it_resets_modules_pulls_then_runs_and_watches_the_one_config(
    tmp_path: Path, harness: Path, env: dict[str, str]
) -> None:
    config = _config(tmp_path)
    done = _bash(harness, env, str(config))
    assert done.returncode == 0, done.stderr
    assert _calls(env) == [
        f"module reset (in {harness})",
        f"git pull --ff-only (in {harness})",  # never a merge nobody watched
        (
            f"uv run --project {harness} ufs-chem-assay run --watch "
            f"--config-file {config} (in {harness})"
        ),
    ]
    assert _uv_dirs(env) == [_scoped(harness)]


def test_a_relative_path_is_the_callers(
    tmp_path: Path, harness: Path, env: dict[str, str]
) -> None:
    config = _config(tmp_path)
    done = _bash(harness, env, "c7.local.yaml", cwd=config.parent)
    assert done.returncode == 0, done.stderr
    assert f"--config-file {config} (in {harness})" in _calls(env)[-1]


def test_runs_status_is_the_bootstraps(
    tmp_path: Path, harness: Path, env: dict[str, str]
) -> None:
    done = _bash(harness, env, str(_config(tmp_path)), STUB_UV_EXIT="3")
    assert done.returncode == 3


@pytest.mark.parametrize("args", [(), ("a.yaml", "b.yaml"), ("--stage", "build")])
def test_anything_but_one_argument_is_a_usage_error(
    harness: Path, env: dict[str, str], args: tuple[str, ...]
) -> None:
    done = _bash(harness, env, *args)
    assert done.returncode == 2
    assert "usage: ops/assay-run.sh <run config yaml>" in done.stderr
    assert _calls(env) == []


def test_a_missing_config_is_refused_before_anything_runs(
    tmp_path: Path, harness: Path, env: dict[str, str]
) -> None:
    done = _bash(harness, env, str(tmp_path / "nope" / "c7.yaml"))
    assert done.returncode == 2
    assert "no run config" in done.stderr
    assert _calls(env) == []


def test_without_a_module_command_it_says_so_and_goes_on(
    tmp_path: Path, harness: Path, env: dict[str, str]
) -> None:
    (Path(env["PATH"].split(":")[0]) / "module").unlink()
    done = _bash(harness, env, str(_config(tmp_path)))
    assert done.returncode == 0, done.stderr
    assert "no module command; no module reset" in done.stderr
    assert "no module command" not in done.stdout
    assert [c.split()[0] for c in _calls(env)] == ["git", "uv"]


def test_the_bootstrap_is_read_whole_before_it_pulls() -> None:
    # bash reads a script as it runs it, and the pull may replace this very
    # file: the body is one function, called on the last line, so everything
    # after the pull is already in memory.
    lines = (_OPS / "assay-run.sh").read_text().splitlines()
    assert lines[-1] == 'main "$@"'
    assert "main() {" in lines
    body = [line for line in lines[lines.index("main() {") : -1] if line]
    assert body[-1] == "}"
    assert any(line.strip() == "git pull --ff-only" for line in body)
    assert not any(line.startswith(("git ", "exec ")) for line in lines)


@pytest.mark.parametrize("script", ["assay-run.sh", "assay-transfer.sh"])
def test_a_config_named_like_an_option_is_a_path(
    tmp_path: Path, harness: Path, env: dict[str, str], script: str
) -> None:
    config = tmp_path / "mine" / "-c.yaml"
    config.parent.mkdir()
    config.write_text("extends: config/gaea-c7.yaml\n")
    done = _bash(harness, env, "-c.yaml", cwd=config.parent, script=script)
    assert done.returncode == 0, done.stderr
    assert f"--config-file {config} (in {harness})" in _calls(env)[-1]


# ── ops/assay ───────────────────────────────────────────────────────────────


def test_assay_runs_the_harness_with_uvs_directories_in_the_checkout(
    tmp_path: Path, harness: Path, env: dict[str, str]
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # Directories exported for other projects do not reach the assay's uv.
    done = _bash(
        harness,
        env,
        "transfer",
        "--config-file",
        "config/c7.local.yaml",
        script="assay",
        cwd=elsewhere,
        UV_CACHE_DIR="/someone/elses/cache",
        UV_PYTHON_INSTALL_DIR="/someone/elses/python",
    )
    assert done.returncode == 0, done.stderr
    assert _calls(env) == [
        (
            f"uv run --project {harness} ufs-chem-assay transfer --config-file "
            f"config/c7.local.yaml (in {elsewhere})"
        )
    ]
    assert _uv_dirs(env) == [_scoped(harness)]


def test_assay_returns_uvs_status(harness: Path, env: dict[str, str]) -> None:
    done = _bash(harness, env, "report", script="assay", STUB_UV_EXIT="1")
    assert done.returncode == 1


def test_assay_finds_uv_in_the_users_local_bin(
    harness: Path, env: dict[str, str]
) -> None:
    # uv's installer puts it in ~/.local/bin, which a batch shell may lack.
    local_bin = Path(env["HOME"]) / ".local" / "bin"
    local_bin.mkdir(parents=True)
    stubs = Path(env["PATH"].split(":")[0])
    shutil.move(stubs / "uv", local_bin / "uv")
    done = _bash(harness, env, "report", script="assay")
    assert done.returncode == 0, done.stderr
    assert _calls(env)[0].startswith(
        f"uv run --project {harness} ufs-chem-assay report"
    )


# ── ops/assay-transfer.sh ───────────────────────────────────────────────────


def test_transfer_runs_the_harness_transfer_on_the_one_config(
    tmp_path: Path, harness: Path, env: dict[str, str]
) -> None:
    # The daily loop's second step, read like the first: no module reset
    # and no git pull of the harness, only the transfer through ops/assay.
    config = _config(tmp_path)
    done = _bash(
        harness, env, "c7.local.yaml", cwd=config.parent, script="assay-transfer.sh"
    )
    assert done.returncode == 0, done.stderr
    assert _calls(env) == [
        (
            f"uv run --project {harness} ufs-chem-assay transfer "
            f"--config-file {config} (in {harness})"
        )
    ]
    assert _uv_dirs(env) == [_scoped(harness)]


def test_transfers_status_is_the_scripts(
    tmp_path: Path, harness: Path, env: dict[str, str]
) -> None:
    done = _bash(
        harness,
        env,
        str(_config(tmp_path)),
        script="assay-transfer.sh",
        STUB_UV_EXIT="1",
    )
    assert done.returncode == 1


@pytest.mark.parametrize("args", [(), ("a.yaml", "b.yaml"), ("--name", "x")])
def test_transfer_takes_exactly_one_argument(
    harness: Path, env: dict[str, str], args: tuple[str, ...]
) -> None:
    done = _bash(harness, env, *args, script="assay-transfer.sh")
    assert done.returncode == 2
    assert "usage: ops/assay-transfer.sh <run config yaml>" in done.stderr
    assert _calls(env) == []


def test_transfer_refuses_a_missing_config(
    tmp_path: Path, harness: Path, env: dict[str, str]
) -> None:
    done = _bash(harness, env, str(tmp_path / "nope.yaml"), script="assay-transfer.sh")
    assert done.returncode == 2
    assert "assay-transfer: no run config" in done.stderr
    assert _calls(env) == []
