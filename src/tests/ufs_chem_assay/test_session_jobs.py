"""The asynchronous slurm session, end to end through a real pytest
subprocess: `--submit-only` submits every combination's job and writes the
ledger; `--resume` reopens the root, adopts its ids, waits for the jobs and
runs the whole session-end pipeline. Slurm itself is four stub executables
(sbatch, squeue, sacct, scancel) put first on PATH — no mocking inside the
session, no cluster. Runs from a neutral cwd so the repo-root .env cannot
influence the session."""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

from jobs import JobLedger, read_ledger
from models.conventions import CONVENTIONS

_RUNNER_ROOT = Path(__file__).resolve().parents[3]  # <repo root>/
_DRIVER_COMBOS = _RUNNER_ROOT / "src" / "tests" / "test_driver_combos.py"

# sbatch: a fresh id per call (1000, 1001, ...), the script recorded; the
# second call fails when a `fail-second` marker exists (a half-way refusal).
_SBATCH = """#!/bin/bash
here=$(dirname "$0")
n=$(cat "$here/next" 2>/dev/null || echo 1000)
echo $((n + 1)) > "$here/next"
echo "$@" >> "$here/sbatch.log"
if [ -f "$here/fail-second" ] && [ "$n" = "1001" ]; then
  echo "sbatch: error: Batch job submission failed: fabricated" >&2
  exit 1
fi
echo "$n;cluster"
"""
# squeue: the ids listed in `queued` (one per line) are PENDING; the rest
# are gone (squeue exits 1, with its "Invalid job id" message on stderr,
# when every id is unknown to it; a nonzero exit without that message means
# the controller could not answer and the harness keeps waiting).
_SQUEUE = """#!/bin/bash
here=$(dirname "$0")
echo "$@" >> "$here/squeue.log"
shown=0
while [ $# -gt 0 ]; do
  if [ "$1" = "-j" ]; then ids=$2; fi
  shift
done
for id in ${ids//,/ }; do
  if [ -f "$here/queued" ] && grep -qx "$id" "$here/queued"; then
    echo "$id PENDING"; shown=1
  fi
done
[ $shown = 1 ] || { echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1; }
"""
# sacct: one COMPLETED row (the job) and one step row with a MaxRSS; the
# state and exit code can be overridden through `state` and `exit`.
_SACCT = """#!/bin/bash
here=$(dirname "$0")
while [ $# -gt 0 ]; do
  if [ "$1" = "-j" ]; then id=$2; fi
  shift
done
state=$(cat "$here/state" 2>/dev/null || echo COMPLETED)
code=$(cat "$here/exit" 2>/dev/null || echo 0:0)
echo "$id|2026-11-04T10:00:00|2026-11-04T10:00:05|2026-11-04T10:00:15|00:00:10||$state|$code"
echo "$id.0|2026-11-04T10:00:05|2026-11-04T10:00:05|2026-11-04T10:00:15|00:00:10|1234K|$state|$code"
"""
_SCANCEL = """#!/bin/bash
echo "$@" >> "$(dirname "$0")/scancel.log"
"""


def _stub_slurm(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, text in (
        ("sbatch", _SBATCH),
        ("squeue", _SQUEUE),
        ("sacct", _SACCT),
        ("scancel", _SCANCEL),
    ):
        path = bindir / name
        path.write_text(text)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return bindir


def _checkout(tmp_path: Path) -> Path:
    """A fabricated CECE checkout: a git repo with the suite's one input."""
    root = tmp_path / "CECE"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
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
        cwd=root,
        check=True,
    )
    (root / "data").mkdir()
    (root / "data" / "MACCity_4x5.nc").write_bytes(b"not netcdf")
    return root


@pytest.fixture
def slurm_env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CECE_", "ASSAY_"))}
    env["PATH"] = f"{_stub_slurm(tmp_path)}{os.pathsep}{env.get('PATH', '')}"
    env["CECE_ROOT_DIR"] = str(_checkout(tmp_path))
    env["ASSAY_PLATFORM"] = "ursa"
    env["ASSAY_RUNTIME"] = "slurm"
    env["ASSAY_SBATCH_ARGS"] = "-A epic -q debug"
    env["ASSAY_SLURM_POLL_S"] = "1"
    env["ASSAY_ENABLE_BASELINE_COMPARISONS"] = "false"  # no store, no network
    return env


def _pytest(
    tmp_path: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(_DRIVER_COMBOS),
            "--application=cece",
            "-q",
            "-p",
            "no:cacheprovider",
            *args,
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _root(tmp_path: Path) -> Path:
    return tmp_path / "CECE" / "combo_runs"


def _ledger(tmp_path: Path) -> JobLedger:
    return read_ledger(_root(tmp_path))


def _submit(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return _pytest(tmp_path, env, "--submit-only", "--combo-output-root=combo_runs")


def test_submit_only_submits_everything_and_stops(
    tmp_path: Path, slurm_env: dict[str, str]
) -> None:
    result = _submit(tmp_path, slurm_env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "21 skipped" in result.stdout  # every combo test skips, naming its job
    root = _root(tmp_path)
    assert (root / CONVENTIONS.run_manifest).is_file()
    assert (root / CONVENTIONS.combos_csv).is_file()
    assert not (root / CONVENTIONS.test_report).exists()  # no verdicts yet
    assert not (root / CONVENTIONS.benchmarks).exists()
    ledger = _ledger(tmp_path)
    manifest = yaml.safe_load((root / CONVENTIONS.run_manifest).read_text())
    assert ledger.run_id == manifest["run_id"]
    assert ledger.application == "cece"
    assert "@" in ledger.submitted_by
    assert ledger.evaluated_at is None
    assert ledger.exit_status is None
    jobs = ledger.jobs
    assert [job.job_id for job in jobs] == ["1000", "1001", "1002"]
    combos = pd.read_csv(root / CONVENTIONS.combos_csv)
    assert [job.combo_id for job in jobs] == list(dict.fromkeys(combos["combo_id"]))
    for job in jobs:
        assert job.suite == "simple-maccity"
        assert job.script.is_file()
        assert job.script.name == f"{job.combo_id}.sbatch"
        assert job.out_path.name == f"{job.combo_id}.out"
        assert job.timeout_s > 0
        assert job.queue_wait_s == 3600
        assert job.submitted_at.tzinfo is not None
    submitted = (tmp_path / "bin" / "sbatch.log").read_text().splitlines()
    assert submitted == [f"--parsable {job.script}" for job in jobs]
    assert not (tmp_path / "bin" / "sacct.log").exists()
    assert "evaluate with" in result.stdout + result.stderr


def test_resume_adopts_the_ids_waits_and_evaluates(
    tmp_path: Path, slurm_env: dict[str, str]
) -> None:
    assert _submit(tmp_path, slurm_env).returncode == 0
    root = _root(tmp_path)
    before = {
        name: (root / name).read_text()
        for name in (CONVENTIONS.run_manifest, CONVENTIONS.combos_csv)
    }
    ledger_before = _ledger(tmp_path)

    # The first poll still sees job 1001 queued; the second does not.
    queued = tmp_path / "bin" / "queued"
    queued.write_text("1001\n")
    poller = tmp_path / "bin" / "squeue"
    poller.write_text(
        _SQUEUE.replace(
            'echo "$@" >> "$here/squeue.log"',
            'echo "$@" >> "$here/squeue.log"; '
            '[ $(wc -l < "$here/squeue.log") -ge 2 ] && rm -f "$here/queued"',
        )
    )
    result = _pytest(tmp_path, slurm_env, "--resume", "--combo-output-root=combo_runs")
    # The stub jobs produced no NetCDF: the output assertions fail, the
    # driver execution itself passed — the session's status is the tests'.
    assert result.returncode == 1, result.stdout + result.stderr
    for name, text in before.items():
        assert (root / name).read_text() == text  # the submit session's, untouched
    ledger = _ledger(tmp_path)
    assert ledger.jobs == ledger_before.jobs
    assert ledger.evaluated_at is not None
    assert ledger.exit_status == 1
    report = pd.read_csv(root / CONVENTIONS.test_report)
    execution = report[report["pytest_name"].str.startswith("test_driver_execution")]
    assert sorted(execution["combo_id"]) == sorted(j.combo_id for j in ledger.jobs)
    assert set(execution["result"]) == {"passed"}
    assert {"passed", "failed"} <= set(report["result"])  # comparisons skip (off)
    benchmarks = pd.read_csv(root / CONVENTIONS.benchmarks, dtype=str)
    assert sorted(benchmarks["combo_id"]) == sorted(j.combo_id for j in ledger.jobs)
    assert sorted(benchmarks["slurm_job_id"]) == ["1000", "1001", "1002"]
    assert set(benchmarks["slurm_state"]) == {"COMPLETED"}
    assert set(benchmarks["driver_result"]) == {"passed"}
    assert set(benchmarks["run_id"]) == {ledger.run_id}
    # duration_s: submission to end (the accounting's queue + elapsed).
    assert set(benchmarks["duration_s"].astype(float)) == {15.0}
    # The .out Slurm never wrote was written from the accounting.
    for job in ledger.jobs:
        assert "COMPLETED" in job.out_path.read_text()
    squeue_calls = (tmp_path / "bin" / "squeue.log").read_text().splitlines()
    assert len(squeue_calls) >= 2  # polled until 1001 left the queue
    assert not (tmp_path / "bin" / "scancel.log").exists()
    # Nothing was resubmitted.
    assert len((tmp_path / "bin" / "sbatch.log").read_text().splitlines()) == 3


def test_resume_records_failed_and_timed_out_jobs(
    tmp_path: Path, slurm_env: dict[str, str]
) -> None:
    assert _submit(tmp_path, slurm_env).returncode == 0
    (tmp_path / "bin" / "state").write_text("TIMEOUT\n")
    (tmp_path / "bin" / "exit").write_text("0:0\n")
    result = _pytest(tmp_path, slurm_env, "--resume", "--combo-output-root=combo_runs")
    assert result.returncode == 1
    benchmarks = pd.read_csv(_root(tmp_path) / CONVENTIONS.benchmarks, dtype=str)
    assert set(benchmarks["driver_result"]) == {"timeout"}
    assert set(benchmarks["slurm_state"]) == {"TIMEOUT"}
    assert _ledger(tmp_path).exit_status == 1
    # Re-evaluated as failed once accounting says so (idempotent re-run).
    (tmp_path / "bin" / "state").write_text("FAILED\n")
    (tmp_path / "bin" / "exit").write_text("2:0\n")
    result = _pytest(tmp_path, slurm_env, "--resume", "--combo-output-root=combo_runs")
    assert result.returncode == 1
    benchmarks = pd.read_csv(_root(tmp_path) / CONVENTIONS.benchmarks, dtype=str)
    assert set(benchmarks["driver_result"]) == {"failed"}
    assert set(benchmarks["exit_code"]) == {"2"}


def test_a_refused_submission_leaves_a_truthful_ledger(
    tmp_path: Path, slurm_env: dict[str, str]
) -> None:
    (tmp_path / "bin" / "fail-second").touch()
    result = _submit(tmp_path, slurm_env)
    assert result.returncode != 0
    assert "fabricated" in result.stdout + result.stderr
    ledger = _ledger(tmp_path)
    assert [job.job_id for job in ledger.jobs] == ["1000"]
    assert ledger.evaluated_at is None
    # A resume over it: the rebinding names the combinations without a job.
    result = _pytest(tmp_path, slurm_env, "--resume", "--combo-output-root=combo_runs")
    assert result.returncode == 4  # usage error
    assert "2 combination(s) have no recorded job" in result.stderr


def test_live_root_guard_refuses_clean_root_while_jobs_are_queued(
    tmp_path: Path, slurm_env: dict[str, str]
) -> None:
    assert _submit(tmp_path, slurm_env).returncode == 0
    (tmp_path / "bin" / "queued").write_text("1000\n1002\n")
    result = _pytest(
        tmp_path,
        slurm_env,
        "--dry-run",
        "--combo-output-root=combo_runs",
        "--combo-clean-root",
    )
    assert result.returncode == 4, result.stdout + result.stderr
    assert "2 job(s) still in the Slurm queue" in result.stderr
    assert "ufs-chem-assay cancel --output-root=" in result.stderr
    assert (_root(tmp_path) / CONVENTIONS.job_ledger).is_file()  # nothing removed
    # Once they are gone the root is cleaned as before.
    (tmp_path / "bin" / "queued").unlink()
    result = _pytest(
        tmp_path,
        slurm_env,
        "--dry-run",
        "--combo-output-root=combo_runs",
        "--combo-clean-root",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (_root(tmp_path) / CONVENTIONS.job_ledger).exists()


def test_deferred_actions_and_the_resume_usage_errors(
    tmp_path: Path, slurm_env: dict[str, str]
) -> None:
    # Usage errors before anything is submitted.
    for args, needle in (
        (("--resume",), "--resume needs --combo-output-root"),
        (
            ("--resume", "--combo-output-root=combo_runs", "--dry-run"),
            "--resume and --dry-run",
        ),
        (
            ("--resume", "--combo-output-root=combo_runs", "--combo-clean-root"),
            "--resume and --combo-clean-root",
        ),
        (
            ("--resume", "--submit-only", "--combo-output-root=combo_runs"),
            "--submit-only and --resume",
        ),
        (("--resume", "--combo-output-root=combo_runs"), "no run.yaml"),
    ):
        result = _pytest(tmp_path, slurm_env, *args)
        assert result.returncode == 4, (args, result.stdout + result.stderr)
        assert needle in result.stderr, (args, result.stderr)
    # A dry-run root has run.yaml but no ledger.
    assert (
        _pytest(
            tmp_path, slurm_env, "--dry-run", "--combo-output-root=combo_runs"
        ).returncode
        == 0
    )
    result = _pytest(tmp_path, slurm_env, "--resume", "--combo-output-root=combo_runs")
    assert result.returncode == 4
    assert "jobs.yaml" in result.stderr
    # Neither flag outside the slurm runtime.
    local = dict(slurm_env, ASSAY_PLATFORM="local", ASSAY_RUNTIME="docker")
    for flag in ("--submit-only", "--resume"):
        result = _pytest(tmp_path, local, flag, "--combo-output-root=combo_runs2")
        assert result.returncode == 4
        assert f"{flag} needs the slurm runtime" in result.stderr
    # The session flags ride on the submit line (the probes run there) and
    # are deferred to the resume session with one line.
    result = _pytest(
        tmp_path,
        slurm_env,
        "--submit-only",
        "--combo-output-root=combo_runs",
        "--combo-clean-root",
        "--record-benchmarks",
        "--upload-output",
        "--publish-baselines",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "deferred to --resume" in result.stdout + result.stderr
    assert not (_root(tmp_path) / CONVENTIONS.upload_record).exists()
