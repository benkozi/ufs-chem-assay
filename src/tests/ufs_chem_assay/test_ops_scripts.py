"""ops/assay-run.sh, ops/assay-transfer.sh and ops/assay-benchmarks.sh, run
with bash against a scratch root and transfer repo, with `uv`, `git` and `module` replaced by
stubs on PATH that record their arguments (one line per call, in
calls.log) and exit as the test tells them."""

import os
import subprocess
from pathlib import Path

import pytest

from identity import HARNESS_ROOT

_OPS = HARNESS_ROOT / "ops"

# uv: `uv run [--no-sync] [--project <dir>] ufs-chem-assay <command> ...`.
# Exits with STUB_<COMMAND>_EXIT (default 0); `run` writes the root's
# effective config unless STUB_RUN_NO_CONFIG is set, as a run that got past
# loading does; `record-benchmarks` fails for a --tests-root containing
# STUB_RECORD_FAIL.
_UV = """#!/bin/bash
echo "uv $*" >> "$STUB_LOG"
shift  # run
while [ "$1" != "ufs-chem-assay" ]; do
  case "$1" in --project) shift ;; esac
  shift
done
shift  # ufs-chem-assay
command="$1"; shift
root=""; tests_root=""
while [ $# -gt 0 ]; do
  case "$1" in
    --root-dir) root="$2"; shift ;; --root-dir=*) root="${1#*=}" ;;
    --tests-root) tests_root="$2"; shift ;;
  esac
  shift
done
echo "stub $command output"
case "$command" in
  run) [ -n "${STUB_RUN_NO_CONFIG:-}" ] || { mkdir -p "$root/scripts"; echo "root_dir: $root" > "$root/scripts/run-config.yaml"; }
       exit "${STUB_RUN_EXIT:-0}" ;;
  watch) exit "${STUB_WATCH_EXIT:-0}" ;;
  report) exit "${STUB_REPORT_EXIT:-0}" ;;
  record-benchmarks)
    case "$tests_root" in *"${STUB_RECORD_FAIL:-//none//}"*) exit 1 ;; esac ;;
esac
"""
_RECORD = '#!/bin/bash\necho "{name} $*" >> "$STUB_LOG"\n'


@pytest.fixture
def ops_env(tmp_path: Path) -> dict[str, str]:
    """The environment both scripts run under: stubs first on PATH, a
    scratch HOME (env.sh prepends $HOME/.local/bin, which must not hold a
    real uv), and every ASSAY_* path in tmp_path."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, text in (
        ("uv", _UV),
        ("git", _RECORD.format(name="git")),
        ("module", _RECORD.format(name="module")),
    ):
        path = stubs / name
        path.write_text(text)
        path.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    harness = tmp_path / "harness"
    harness.mkdir()
    (tmp_path / "transfer").mkdir()
    env = {
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "HOME": str(home),
        "STUB_LOG": str(tmp_path / "calls.log"),
        "ASSAY_HARNESS": str(harness),
        "ASSAY_ROOT": str(tmp_path / "root"),
        "ASSAY_TRANSFER_REPO": str(tmp_path / "transfer"),
    }
    return env


def _bash(
    script: str, env: dict[str, str], *args: str, **extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_OPS / script), *args],
        env={**env, **extra},
        capture_output=True,
        text=True,
        check=False,
    )


def _calls(env: dict[str, str]) -> list[str]:
    log = Path(env["STUB_LOG"])
    return log.read_text().splitlines() if log.is_file() else []


# ── assay-run.sh ────────────────────────────────────────────────────────────


def test_a_run_writes_its_transcript_into_a_fresh_root(
    ops_env: dict[str, str],
) -> None:
    # A fresh root has no logs/: the transcript is still written there.
    root = Path(ops_env["ASSAY_ROOT"])
    assert not root.exists()
    done = _bash("assay-run.sh", ops_env)
    assert done.returncode == 0, done.stderr
    (transcript,) = (root / "logs").glob("assay-run-*.log")
    text = transcript.read_text()
    assert "stub run output" in text
    assert "stub watch output" in text
    assert f"ASSAY_ROOT={root}" in text
    assert "LD_LIBRARY_PATH=[" in text
    assert "PYTHONPATH=[" in text
    last = text.splitlines()[-1]
    assert last == f"assay-run: run exit 0, watch exit 0; transcript {transcript}"
    assert done.stdout.splitlines()[-1] == last  # the terminal sees it all too


def test_a_run_resets_modules_pulls_runs_and_watches_in_order(
    ops_env: dict[str, str],
) -> None:
    done = _bash("assay-run.sh", ops_env, "--toolchain", "oneapi")
    assert done.returncode == 0, done.stderr
    root = ops_env["ASSAY_ROOT"]
    calls = _calls(ops_env)
    assert calls[0] == "module reset"
    assert calls[1] == "git pull"
    run = [c for c in calls if c.startswith("uv run ufs-chem-assay run ")]
    assert run == [
        (
            "uv run ufs-chem-assay run --config-file=config/gaea-c7.yaml "
            f"--application all --root-dir {root} "
            "--stage source --stage build --stage test --toolchain oneapi"
        )
    ]
    assert calls[-1] == f"uv run ufs-chem-assay watch --root-dir {root}"


def test_an_exported_value_wins_over_env_sh(ops_env: dict[str, str]) -> None:
    done = _bash(
        "assay-run.sh",
        ops_env,
        ASSAY_STAGES="build test",
        ASSAY_APPLICATIONS="cece catchem",
    )
    assert done.returncode == 0, done.stderr
    (run,) = [c for c in _calls(ops_env) if " ufs-chem-assay run " in c]
    assert "--application cece catchem " in run
    assert run.endswith("--stage build --stage test")


def test_watch_runs_after_a_failed_run_and_the_worse_status_wins(
    ops_env: dict[str, str],
) -> None:
    # A failed build: run exits 2 but submitted the other builds' tests,
    # which watch still evaluates.
    done = _bash("assay-run.sh", ops_env, STUB_RUN_EXIT="2")
    assert done.returncode == 2
    assert any(" ufs-chem-assay watch " in c for c in _calls(ops_env))
    assert done.stdout.splitlines()[-1].startswith(
        "assay-run: run exit 2, watch exit 0;"
    )
    # run passed, watch found a failure: watch's status.
    done = _bash("assay-run.sh", ops_env, STUB_WATCH_EXIT="1")
    assert done.returncode == 1


def test_no_watch_without_an_effective_config(ops_env: dict[str, str]) -> None:
    # A config error: run stopped before writing scripts/run-config.yaml.
    done = _bash("assay-run.sh", ops_env, STUB_RUN_EXIT="1", STUB_RUN_NO_CONFIG="1")
    assert done.returncode == 1
    assert not any(" ufs-chem-assay watch " in c for c in _calls(ops_env))
    assert "nothing to watch" in done.stdout


def test_no_watch_for_a_run_without_a_stage_to_wait_for(
    ops_env: dict[str, str],
) -> None:
    # A build-only run leaves no test job or session: watch would only say
    # "nothing to watch" and exit 1.
    done = _bash("assay-run.sh", ops_env, ASSAY_STAGES="source build")
    assert done.returncode == 0, done.stdout
    assert not any(" ufs-chem-assay watch " in c for c in _calls(ops_env))
    assert done.stdout.splitlines()[-1].startswith(
        "assay-run: run exit 0, watch skipped;"
    )


# ── assay-transfer.sh ───────────────────────────────────────────────────────


def _a_finished_root(root: Path) -> None:
    for rel, text in (
        ("logs/02-build-cece-oneapi-20261123-130438.log", "# ufs-chem-assay run\n"),
        ("logs/assay-run-20261123-130420.log", "transcript\n"),
        ("scripts/run-config.yaml", "root_dir: x\n"),
        ("scripts/stages.yaml", "outcomes: {}\n"),
        ("scripts/02-build-cece-oneapi.sh", "#!/bin/bash\n"),
        ("scripts/04-test-cece-oneapi.sbatch", "#!/bin/bash\n"),
        ("oneapi/tests/cece/tests.yaml", "run_id: x\n"),
        ("oneapi/tests/catchem/compute/tests.yaml", "run_id: y\n"),
        ("oneapi/CECE/build/CMakeCache.txt", "CMAKE_BUILD_TYPE:STRING=Debug\n"),
        ("oneapi/CATChem/build-musica/CMakeCache.txt", "X:BOOL=ON\n"),
        ("oneapi/CECE/src/big.cpp", "not copied\n"),
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_a_transfer_copies_the_evidence_with_the_report_and_pushes(
    ops_env: dict[str, str],
) -> None:
    root = Path(ops_env["ASSAY_ROOT"])
    _a_finished_root(root)
    # The report found issues (exit 1): still the report, not an error.
    done = _bash("assay-transfer.sh", ops_env, "assay-test", STUB_REPORT_EXIT="1")
    assert done.returncode == 0, done.stderr
    dst = Path(ops_env["ASSAY_TRANSFER_REPO"]) / "assay-test"
    copied = sorted(str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file())
    assert copied == [
        "cmake/oneapi/CATChem/build-musica/CMakeCache.txt",
        "cmake/oneapi/CECE/build/CMakeCache.txt",
        "logs/02-build-cece-oneapi-20261123-130438.log",
        "logs/assay-run-20261123-130420.log",
        "oneapi/tests/catchem/compute/tests.yaml",
        "oneapi/tests/cece/tests.yaml",
        "report.txt",
        "scripts/02-build-cece-oneapi.sh",
        "scripts/04-test-cece-oneapi.sbatch",
        "scripts/run-config.yaml",
        "scripts/stages.yaml",
    ]
    assert (dst / "report.txt").read_text() == "stub report output\n"
    calls = _calls(ops_env)
    assert f"uv run --no-sync ufs-chem-assay report --root-dir {root}" in calls
    assert [c for c in calls if c.startswith("git ")] == [
        "git pull",
        "git add -f assay-test",
        "git commit -m assay-test",
        "git push",
    ]
    assert done.stdout.splitlines()[-1].startswith(f"pushed {dst} (")


def test_a_transfer_names_its_directory_by_the_time_by_default(
    ops_env: dict[str, str],
) -> None:
    _a_finished_root(Path(ops_env["ASSAY_ROOT"]))
    done = _bash("assay-transfer.sh", ops_env)
    assert done.returncode == 0, done.stderr
    (dst,) = Path(ops_env["ASSAY_TRANSFER_REPO"]).glob("assay-*")
    assert len(dst.name) == len("assay-20261123-1316")
    assert f"git add -f {dst.name}" in _calls(ops_env)


# ── assay-benchmarks.sh ─────────────────────────────────────────────────────


def _a_transfer(transfer: Path) -> None:
    """A transfer directory as assay-transfer.sh writes it: CECE's and
    CATChem's two variants' tests roots, and one without a ledger."""
    _a_finished_root(transfer)  # the same tree, copied
    for rel in ("cece", "catchem/compute", "catchem/musica"):
        tests_root = transfer / "oneapi" / "tests" / rel
        tests_root.mkdir(parents=True, exist_ok=True)
        (tests_root / "tests.yaml").write_text("run_id: x\n")
        (tests_root / "benchmarks.csv").write_text("h\nr\n")
    lonely = transfer / "oneapi" / "tests" / "kokkos-smoke"
    lonely.mkdir(parents=True)
    (lonely / "benchmarks.csv").write_text("h\n")  # no tests.yaml: no test run


def _records(env: dict[str, str]) -> list[str]:
    return [c for c in _calls(env) if " record-benchmarks " in c]


def test_benchmarks_are_recorded_from_each_tests_root_of_a_transfer(
    ops_env: dict[str, str], tmp_path: Path
) -> None:
    transfer = tmp_path / "transfer" / "assay-20261123-1405"
    _a_transfer(transfer)
    done = _bash("assay-benchmarks.sh", ops_env, "--dry-run", str(transfer))
    assert done.returncode == 0, done.stdout + done.stderr
    tests = transfer / "oneapi" / "tests"
    assert _records(ops_env) == [
        f"uv run --project {HARNESS_ROOT} ufs-chem-assay record-benchmarks "
        f"--tests-root {tests / rel} --dry-run"
        for rel in ("catchem/compute", "catchem/musica", "cece")
    ]
    git = [c for c in _calls(ops_env) if c.startswith("git ")]
    assert git == [
        f"git -C {HARNESS_ROOT} diff --stat -- src/tests/config/*/tests-benchmarks.csv"
    ]  # the diff to review, and no commit
    # Without --dry-run, nothing else changes but the flag.
    Path(ops_env["STUB_LOG"]).unlink()
    assert _bash("assay-benchmarks.sh", ops_env, str(transfer)).returncode == 0
    assert all(not c.endswith("--dry-run") for c in _records(ops_env))
    assert len(_records(ops_env)) == 3


def test_a_failing_tests_root_does_not_stop_the_others(
    ops_env: dict[str, str], tmp_path: Path
) -> None:
    transfer = tmp_path / "transfer" / "assay-x"
    _a_transfer(transfer)
    done = _bash(
        "assay-benchmarks.sh", ops_env, str(transfer), STUB_RECORD_FAIL="musica"
    )
    assert done.returncode == 1
    assert len(_records(ops_env)) == 3
    assert "assay-benchmarks: record-benchmarks failed for " in done.stdout


def test_a_transfer_without_a_test_run_records_nothing(
    ops_env: dict[str, str], tmp_path: Path
) -> None:
    transfer = tmp_path / "transfer" / "assay-build-only"
    (transfer / "scripts").mkdir(parents=True)
    (transfer / "scripts" / "run-config.yaml").write_text("root_dir: x\n")
    done = _bash("assay-benchmarks.sh", ops_env, str(transfer))
    assert done.returncode == 0
    assert _records(ops_env) == []
    assert f"assay-benchmarks: no test run in {transfer}" in done.stdout


@pytest.mark.parametrize("args", [[], ["a", "b"], ["not-a-transfer"]])
def test_benchmarks_take_exactly_one_transfer_directory(
    ops_env: dict[str, str], tmp_path: Path, args: list[str]
) -> None:
    (tmp_path / "not-a-transfer").mkdir()  # no scripts/run-config.yaml
    paths = [str(tmp_path / a) for a in args]
    done = _bash("assay-benchmarks.sh", ops_env, *paths)
    assert done.returncode == 2
    assert "usage: ops/assay-benchmarks.sh [--dry-run] <transfer dir>" in done.stderr
    assert _records(ops_env) == []
