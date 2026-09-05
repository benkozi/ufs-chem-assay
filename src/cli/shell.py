"""Writing and executing rendered scripts with bash on this node; every real
run tees to a timestamped log under <root>/logs/."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from cli.stages import ShellScript
from logs import get_logger

logger = get_logger("cli")


def write_script(script: ShellScript, scripts_dir: Path, index: int) -> Path:
    """<scripts_dir>/<NN>-<name>.sh, executable, overwritten per invocation."""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = scripts_dir / f"{index:02d}-{script.name}.sh"
    path.write_text(script.text)
    path.chmod(0o755)
    return path


def _log_path(logs_dir: Path, name: str) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return logs_dir / f"{name}-{stamp}.log"


def run_bash(path: Path, logs_dir: Path) -> int:
    """Run a script with bash, streaming its combined output — the child's
    bytes, passed through unchanged — to stdout and to its log file. The
    CLI's own messages go through the logger (stderr), so a captured
    `> log 2>&1` interleaves both in order. Returns the exit code."""
    log = _log_path(logs_dir, path.stem)
    logger.info("running %s (log: %s)", path, log)
    with open(log, "wb") as sink:
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
