"""CECE's shipped examples: discovered in the checkout (examples/config/),
data staged and runs launched through the checkout's own python
entrypoints — the docker wrapping stays on this side (the entrypoints are
container-agnostic)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from applications.base import ApplicationSettings, ExamplesSupport
from applications.cece.settings import CONTAINER_WORKDIR
from examples import DownloadResult
from logs import get_logger
from platforms import Runtime
from runner import docker_prefix
from settings import Settings

logger = get_logger("cece.examples")

# The unified layout (design/feat/20260722-1553): configs under
# examples/config/, entrypoints beside them. Public: the gating harness
# fabricates CECE-shaped trees from these.
EXAMPLES_SUBDIR = Path("examples") / "config"
DOWNLOAD_ENTRYPOINT = Path("examples") / "download-example-data.py"
RUN_ENTRYPOINT = Path("examples") / "run-example.py"
_CONFIG_PREFIX = "cece_config_"


class CeceExamples(ExamplesSupport):
    def discover(self, root_dir: Path) -> list[Path]:
        """The shipped example configs under the CECE checkout, sorted by name."""
        return sorted((root_dir / EXAMPLES_SUBDIR).glob(f"{_CONFIG_PREFIX}ex*.yaml"))

    def example_id(self, config_path: Path) -> str:
        """cece_config_ex3.yaml -> ex3 (the id the entrypoints accept)."""
        return config_path.stem.removeprefix(_CONFIG_PREFIX)

    def download(self, root_dir: Path, timeout_s: int = 300) -> list[DownloadResult]:
        """Invoke the checkout's download entrypoint once per discovered example
        (host-side python, cwd = the CECE root). One result per example; nothing
        raises and a failing download does not stop the ones after it."""
        results: list[DownloadResult] = []
        for config in self.discover(root_dir):
            eid = self.example_id(config)
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
            results.append(
                DownloadResult(script=eid, returncode=returncode, output=output)
            )
        return results

    def run_command(
        self, settings: Settings, app_settings: ApplicationSettings, eid: str
    ) -> list[str]:
        """Invocation of run-example.py for one example. The entrypoint is
        container-agnostic (it never spawns docker itself), so the wrapping
        lives here: docker run locally; natively the harness's own
        interpreter, the one guaranteed to be >= 3.11 (the entrypoint uses
        StrEnum) once modules have replaced the login node's python3."""
        if settings.runtime is Runtime.SLURM:
            raise NotImplementedError(
                "--run-examples is not supported under the slurm runtime yet "
                "(each example would need its own job and output file); use "
                "ASSAY_RUNTIME=native inside an allocation"
            )
        if settings.runtime is Runtime.NATIVE:
            assert app_settings.root_dir is not None  # guarded at collection
            return [
                sys.executable,
                str(app_settings.root_dir / RUN_ENTRYPOINT),
                "--example",
                eid,
            ]
        return [
            *docker_prefix(app_settings, CONTAINER_WORKDIR),
            "python3",
            str(RUN_ENTRYPOINT),
            "--example",
            eid,
        ]
