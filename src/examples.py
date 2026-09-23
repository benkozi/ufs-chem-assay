"""Opt-in execution of an application's shipped example configs: the
generic parts — result models and the session report. Discovery, data
staging, and the run command are the adapter's (ExamplesSupport in
applications/base.py): examples are external artifacts under test, executed
verbatim through the application's own tooling, never loaded through the
config models."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from logs import get_logger

logger = get_logger("examples")


class DownloadResult(BaseModel):
    """Outcome of one per-example download invocation; failures are recorded
    here, never raised — a broken download must not hide other examples'
    results."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str = Field(
        description="Example id passed to the download tooling (e.g. ex3)"
    )
    returncode: int = Field(
        description="Invocation exit status; 0 = success, -1 = timeout"
    )
    output: str = Field(
        description="Combined stdout+stderr captured from the invocation"
    )


class ExampleRunResult(BaseModel):
    """Outcome of one example's execution through the application's runner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    example: str = Field(description="Example config file stem, e.g. cece_config_ex3")
    returncode: int = Field(
        description="Entrypoint exit status; 0 = pass, -1 = timeout"
    )
    out_path: Path = Field(
        description="Host path of the captured combined output (.out)"
    )


def _tail(text: str, lines: int = 10) -> str:
    return "\n".join(text.splitlines()[-lines:])


def write_examples_report(
    application: str,
    downloads: list[DownloadResult],
    runs: list[ExampleRunResult],
    path: Path,
) -> None:
    """Session-end markdown record of what the downloads and executions did,
    written under the output root so every --run-examples run is
    self-documenting."""
    lines = [f"# examples report ({application})", "", "## data downloads", ""]
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
