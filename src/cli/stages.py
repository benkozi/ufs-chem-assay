"""One shell script per stage per application, rendered from a RunConfig.

The scripts are the deliverable as much as their execution: they are written
under <root_dir>/scripts/ for review, `--dry-run` stops after rendering, and
the Ursa runbook (docs/ursa-runbook.md) is the same commands by hand — when
one changes, the other does. Every stage runs where the CLI runs; under the
slurm runtime the harness stage's driver runs submit their own jobs. Paths
and values are shell-quoted. The source, build, and data bodies are the
application adapter's; the harness stage is shared and renders its exports
from the settings' field names.
"""

from __future__ import annotations

import shlex
from enum import StrEnum, unique
from pathlib import PurePosixPath

from pydantic import ConfigDict, Field

from applications.base import Application, ApplicationRunSection, ApplicationSettings
from cli.run_config import HARNESS_ROOT, RunConfig, search_path_value
from models.base import StrictModel
from platforms import Runtime
from settings import ENV_PREFIX, Settings

_DRIVER_COMBOS = "src/tests/test_driver_combos.py"
_SHEBANG = "#!/bin/bash"

# Harness settings that the harness: section does not mirror one-to-one:
# top-level keys, the baselines: section, and the slurm/env-derived ones.
SETTINGS_NOT_MIRRORED = frozenset(
    {
        "application",  # the applications: map's keys
        "platform",  # top level
        "baseline_root_dir",  # baselines.root_dir
        "enable_baseline_comparisons",  # baselines.enabled
        "sbatch_args",  # slurm: (account, qos, partition, cpus)
        "slurm_queue_wait_s",  # slurm.queue_wait_s
        "job_env",  # harness.env under the slurm runtime
    }
)


@unique
class Stage(StrEnum):
    """The stages of a run, in execution order."""

    SOURCE = "source"  # clone / update the application checkout
    BUILD = "build"  # configure + build the driver
    DATA = "data"  # input data (the application's) + cartopy cache
    HARNESS = "harness"  # the pytest session, on this node; driver calls per runtime


class ShellScript(StrictModel):
    """A rendered stage script: its name and full text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        description="Stage name, application-qualified: <stage>-<application>"
    )
    text: str = Field(description="Complete bash source, shebang included")


def q(value: object) -> str:
    return shlex.quote(str(value))


LMOD_INIT = 'if [ -n "${MODULESHOME:-}" ]; then source "$MODULESHOME/init/bash"; fi'


def clean_python_env(section: ApplicationRunSection) -> list[str]:
    """The harness venv must never see the module environment (spack-stack
    sets PYTHONPATH to python3.11 site-packages that shadow the venv's
    numpy): undo any modules the calling shell had loaded."""
    if section.modulefile is None:
        return []
    return [LMOD_INIT, "module purge", "unset PYTHONPATH"]


def settings_mirror_fields() -> list[str]:
    """The Settings fields the harness: section mirrors, in Settings order."""
    return [name for name in Settings.model_fields if name not in SETTINGS_NOT_MIRRORED]


def application_output_root(config: RunConfig, app: Application) -> str:
    """harness.output_root as-is for a single configured application;
    <output_root>/<application> when several share the run."""
    if len(config.applications) == 1:
        return config.harness.output_root
    return str(PurePosixPath(config.harness.output_root) / app.name)


def _env_value(value: object) -> str | None:
    """The export's value for one mirrored field; None means "not set"."""
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, list):
        return q(search_path_value(value))
    if isinstance(value, StrEnum):
        return q(value.value)
    return q(value)


def _harness(
    config: RunConfig, app: Application, section: ApplicationRunSection
) -> list[str]:
    harness = config.harness
    prefix = app.env_prefix
    exports: list[tuple[str, str]] = [
        (f"{prefix}ROOT_DIR", q(config.checkout_dir(app.name)))
    ]
    for field in ApplicationSettings.model_fields:
        if field == "root_dir":
            continue
        value = _env_value(getattr(section, field))
        if value is not None:
            exports.append((f"{prefix}{field.upper()}", value))
    exports += [
        (f"{ENV_PREFIX}PLATFORM", config.platform.value),
        (f"{ENV_PREFIX}RUNTIME", config.runtime.value),
    ]
    for field in settings_mirror_fields():
        if field == "runtime":
            continue  # resolved above
        value = _env_value(getattr(harness, field))
        if value is not None:
            exports.append((f"{ENV_PREFIX}{field.upper()}", value))
    if config.runtime is Runtime.SLURM:
        assert config.slurm is not None, "slurm runtime needs a slurm: section"
        exports.append((f"{ENV_PREFIX}SBATCH_ARGS", q(config.slurm.sbatch_args)))
        exports.append(
            (f"{ENV_PREFIX}SLURM_QUEUE_WAIT_S", str(config.slurm.queue_wait_s))
        )
        if harness.env:
            # The driver jobs' environment; the login-node shell never needs it.
            pairs = " ".join(f"{key}={value}" for key, value in harness.env.items())
            exports.append((f"{ENV_PREFIX}JOB_ENV", q(pairs)))
    exports.append(
        (
            f"{ENV_PREFIX}ENABLE_BASELINE_COMPARISONS",
            "true" if config.baselines.enabled else "false",
        )
    )
    if config.baselines.root_dir is not None:
        exports.append((f"{ENV_PREFIX}BASELINE_ROOT_DIR", q(config.baselines.root_dir)))
    exports.append(("PATH", '"$HOME/.local/bin:$PATH"'))
    exports.append(("UV_CACHE_DIR", q(config.uv_cache_dir)))
    if config.runtime is not Runtime.SLURM:
        # docker/native run the driver as a local process: env applies here.
        exports += [(key, q(value)) for key, value in harness.env.items()]

    pytest_args = [
        f"--application={app.name}",
        f"--suite-config={harness.suite_config}",
        f"--combo-output-root={application_output_root(config, app)}",
    ]
    if harness.clean_root:
        pytest_args.append("--combo-clean-root")
    if harness.pytest_dry_run:
        pytest_args.append("--dry-run")
    if harness.run_examples:
        pytest_args.append("--run-examples")
    pytest_args += harness.pytest_args
    return [
        *clean_python_env(section),
        *(f"export {key}={value}" for key, value in exports),
        f"cd {q(HARNESS_ROOT)}",
        f"uv run --no-sync pytest {_DRIVER_COMBOS} "
        + " ".join(q(arg) for arg in pytest_args),
    ]


def _data(
    config: RunConfig, app: Application, section: ApplicationRunSection, shared: bool
) -> list[str]:
    # Everything here runs the harness venv's interpreter (the application's
    # tooling may need a newer Python than the OS one after `module purge`).
    lines = [*clean_python_env(section), f"cd {q(HARNESS_ROOT)}"]
    lines += app.stage_lines(Stage.DATA, config, section)
    if shared and config.data.warm_cartopy:
        snippet = (
            "import cartopy.io.shapereader as s; "
            "[s.natural_earth(resolution=r, category='physical', name='coastline') "
            "for r in ('110m', '50m')]"
        )
        lines.append(f"uv run --no-sync python -c {q(snippet)}")
    return lines


def render_stage(
    stage: Stage, config: RunConfig, app: Application, *, shared_data: bool = True
) -> ShellScript:
    """The stand-alone script for one stage of one application: shebang,
    strict mode, banner, body. Modules are loaded only by the build body and
    by the job script. `shared_data`: render the harness-wide data lines
    (the cartopy cache) — once per run, under the first application."""
    section = config.applications[app.name]
    if stage is Stage.HARNESS:
        body = _harness(config, app, section)
    elif stage is Stage.DATA:
        body = _data(config, app, section, shared_data)
    else:
        body = app.stage_lines(stage, config, section)
    lines = [
        _SHEBANG,
        "set -euo pipefail",
        f'echo ">>> stage: {stage.value} ({app.name})"',
        *body,
    ]
    return ShellScript(name=f"{stage.value}-{app.name}", text="\n".join(lines) + "\n")
