"""Opt-in execution of the CECE driver's shipped example configs.

Examples are external artifacts under test, discovered in the CECE checkout
(settings.root_dir) and executed verbatim — deliberately never loaded
through the pydantic config models. Downloads and execution both go through
the checkout's own python entrypoints (examples/download-example-data.py,
examples/run-example.py); the docker wrapping for execution stays on this
side — the entrypoints themselves are container-agnostic.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from logs import get_logger
from platforms import Runtime
from runner import docker_prefix
from settings import Settings

logger = get_logger("examples")

# The unified layout (design/feat/20260722-1553): configs under
# examples/config/, entrypoints beside them. Public: the gating harness
# fabricates CECE-shaped trees from these.
EXAMPLES_SUBDIR = Path("examples") / "config"
DOWNLOAD_ENTRYPOINT = Path("examples") / "download-example-data.py"
RUN_ENTRYPOINT = Path("examples") / "run-example.py"


class DownloadResult(BaseModel):
    """Outcome of one per-example download invocation; failures are recorded
    here, never raised — a broken download must not hide other examples'
    results."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str = Field(
        description="Example id passed to download-example-data.py (e.g. ex3)"
    )
    returncode: int = Field(
        description="Invocation exit status; 0 = success, -1 = timeout"
    )
    output: str = Field(
        description="Combined stdout+stderr captured from the invocation"
    )


class ExampleRunResult(BaseModel):
    """Outcome of one example's execution via run-example.py in docker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    example: str = Field(description="Example config file stem, e.g. cece_config_ex3")
    returncode: int = Field(
        description="Entrypoint exit status; 0 = pass, -1 = timeout"
    )
    out_path: Path = Field(
        description="Host path of the captured combined output (.out)"
    )


def discover_examples(root_dir: Path) -> list[Path]:
    """The shipped example configs under the CECE checkout, sorted by name."""
    return sorted((root_dir / EXAMPLES_SUBDIR).glob("cece_config_ex*.yaml"))


def example_id(config_path: Path) -> str:
    """cece_config_ex3.yaml -> ex3 (the id the entrypoints accept)."""
    return config_path.stem.removeprefix("cece_config_")


def download_example_data(root_dir: Path, timeout_s: int = 300) -> list[DownloadResult]:
    """Invoke the checkout's download entrypoint once per discovered example
    (host-side python, cwd = the CECE root). One result per example; nothing
    raises and a failing download does not stop the ones after it."""
    results: list[DownloadResult] = []
    for config in discover_examples(root_dir):
        eid = example_id(config)
        command = [
            sys.executable,
            str(root_dir / DOWNLOAD_ENTRYPOINT),
            "--example",
            eid,
            "--dst-dir",
            str(root_dir / "data"),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=root_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_s,
            )
            returncode = completed.returncode
            output = completed.stdout
        except subprocess.TimeoutExpired as exc:
            returncode = -1
            partial = exc.output or b""
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", errors="replace")
            output = f"{partial}\n<timed out after {timeout_s}s>"
        if returncode != 0:
            logger.warning("download for %s failed (exit %s)", eid, returncode)
        results.append(DownloadResult(script=eid, returncode=returncode, output=output))
    return results


def run_example_command(settings: Settings, eid: str) -> list[str]:
    """Invocation of run-example.py for one example. The entrypoint is
    container-agnostic (it never spawns docker itself), so the wrapping lives
    here: docker run locally; natively the harness's own interpreter, the
    one guaranteed to be >= 3.11 (the entrypoint uses StrEnum) once modules
    have replaced the login node's python3."""
    if settings.runtime is Runtime.SLURM:
        raise NotImplementedError(
            "--run-examples is not supported under the slurm runtime yet "
            "(each example would need its own job and output file); use "
            "CECE_RUNTIME=native inside an allocation"
        )
    if settings.runtime is Runtime.NATIVE:
        assert settings.root_dir is not None  # guarded at collection
        return [
            sys.executable,
            str(settings.root_dir / RUN_ENTRYPOINT),
            "--example",
            eid,
        ]
    return [*docker_prefix(settings), "python3", str(RUN_ENTRYPOINT), "--example", eid]


def _tail(text: str, lines: int = 10) -> str:
    return "\n".join(text.splitlines()[-lines:])


def write_examples_report(
    downloads: list[DownloadResult],
    runs: list[ExampleRunResult],
    path: Path,
) -> None:
    """Session-end markdown record of what the downloads and executions did,
    written under the output root so every --run-examples run is
    self-documenting."""
    lines = ["# examples report", "", "## data downloads", ""]
    if not downloads:
        lines.append("No download scripts ran.")
    for download in downloads:
        status = (
            "ok" if download.returncode == 0 else f"FAILED (exit {download.returncode})"
        )
        lines.append(f"- `{download.script}`: {status}")
        if download.returncode != 0:
            lines += ["", "  ```", _tail(download.output), "  ```", ""]
    lines += ["", "## example executions", ""]
    if not runs:
        lines.append("No examples executed.")
    for run in runs:
        status = "PASS" if run.returncode == 0 else f"FAIL (exit {run.returncode})"
        lines.append(f"- `{run.example}`: {status} — output: {run.out_path}")
    path.write_text("\n".join(lines) + "\n")
    logger.info("wrote %s", path)
