"""Docker invocation of cece_standalone_driver for a single combination."""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, InstanceOf

from combos import Combo
from models.cece_config import CeceConfig
from settings import Settings


class DriverRunResult(BaseModel):
    """Outcome of one driver invocation. The driver-run fixture never raises;
    execution failure is reported explicitly by test_driver_execution and
    downstream assertion tests skip."""

    model_config = ConfigDict(frozen=True)

    # InstanceOf: Combo is enumeration machinery (callables, enum members),
    # validated by isinstance rather than deep pydantic validation.
    combo: InstanceOf[Combo]
    combo_dir: Path
    out_path: Path
    config: CeceConfig
    error: InstanceOf[Exception] | None


def build_command(
    settings: Settings,
    container_yaml: PurePosixPath,
    output_mount: tuple[Path, PurePosixPath] | None = None,
) -> list[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{settings.root_dir}:/work",  # bind mount: <host path>:<container path>
    ]
    if output_mount is not None:
        # Output root outside the /work mount (the pytest tmp default) needs
        # its own bind mount so artifacts survive --rm.
        host_root, container_root = output_mount
        command += ["-v", f"{host_root}:{container_root}"]
    command += [
        "-w",
        "/work",
        "-e",
        "OMPI_ALLOW_RUN_AS_ROOT=1",
        "-e",
        "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1",
        settings.docker_image,
        settings.driver_path,
        str(container_yaml),
    ]
    return command


def _print_output(out_path: Path, output: bytes) -> None:
    # out_path.stem is the combo name. Visibility is delegated to pytest's
    # capture model: shown live with -s, in failure reports otherwise.
    print(f"----- cece driver output [{out_path.stem}] -----")
    print(output.decode("utf-8", errors="replace"), end="")
    print(f"----- end driver output [{out_path.stem}] -----")


def run_driver(
    settings: Settings,
    container_yaml: PurePosixPath,
    out_path: Path,
    timeout_s: int,
    output_mount: tuple[Path, PurePosixPath] | None = None,
) -> None:
    """Run one driver invocation in a fresh container.

    Combined stdout/stderr is written to out_path and printed whether the run
    passes or fails; a nonzero exit re-raises CalledProcessError to fail the
    test, and a hung driver fails with TimeoutExpired after timeout_s.
    """
    command = build_command(settings, container_yaml, output_mount=output_mount)
    try:
        output = subprocess.check_output(
            command, stderr=subprocess.STDOUT, timeout=timeout_s
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        out_path.write_bytes(exc.output or b"")
        _print_output(out_path, exc.output or b"")
        raise
    out_path.write_bytes(output)
    _print_output(out_path, output)
