"""Writing and executing rendered scripts with bash on this node; every real
run tees to a timestamped log under <root>/logs/, whose first line names the
command that ran it."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from cli.stages import ShellScript
from identity import HARNESS_NAME, harness_commit, harness_version
from logs import current_command, get_logger

logger = get_logger("cli")


def write_script(script: ShellScript, scripts_dir: Path, index: int) -> Path:
    """<scripts_dir>/<NN>-<stage>-<application>.sh, executable, overwritten
    per invocation; the script's attachments beside it under the same stem
    (the test stage's job script, <NN>-test-<application>.sbatch)."""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = scripts_dir / f"{index:02d}-{script.name}.sh"
    path.write_text(script.text)
    path.chmod(0o755)
    for suffix, text in script.attachments.items():
        path.with_suffix(suffix).write_text(text)
    return path


def _log_path(logs_dir: Path, name: str) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")  # local, aware
    return logs_dir / f"{name}-{stamp}.log"


def _log_header(path: Path, run_id: str | None) -> str:
    """The stage log's first line: the command that ran the script (`run`,
    or `watch` for a resume script), the script, the run, the harness."""
    parts = [f"run {run_id}"] if run_id is not None else []
    parts.append(f"harness {harness_version()} {harness_commit() or 'unknown'}")
    parts.append(f"started {datetime.now().astimezone().isoformat(timespec='seconds')}")
    return f"# {HARNESS_NAME} {current_command()}: {path.name} ({', '.join(parts)})\n"


def run_bash(path: Path, logs_dir: Path, *, run_id: str | None = None) -> int:
    """Run a script with bash, streaming its combined output — the child's
    bytes, passed through unchanged — to stdout and to its log file, after
    the log's header line (the log's alone). The CLI's own messages go
    through the logger (stderr), so a captured `> log 2>&1` interleaves both
    in order. Returns the exit code."""
    log = _log_path(logs_dir, path.stem)
    logger.info("running %s (log: %s)", path, log)
    with open(log, "wb") as sink:
        sink.write(_log_header(path, run_id).encode())
        process = subprocess.Popen(
            ["bash", str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        stdout = process.stdout
        assert stdout is not None
        for chunk in iter(stdout.readline, b""):
            sink.write(chunk)
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        return process.wait()
