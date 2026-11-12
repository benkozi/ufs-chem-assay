"""One shell script per stage per application — per session for the
harness and resume stages — rendered from a RunConfig.

The scripts are the deliverable as much as their execution: they are written
under <root_dir>/scripts/ for review, `--dry-run` stops after rendering, and
the Ursa runbook (docs/ursa-runbook.md) is the same commands by hand — when
one changes, the other does. Every stage runs where the CLI runs; under the
slurm runtime the harness stage's pytest session submits every driver job
at once and, with harness.submit_only, stops there: the resume stage
(rendered beside it, run by `ufs-chem-assay watch` or `--stage resume`)
reopens the session's root and evaluates the finished jobs. Paths and
values are shell-quoted. The source and build bodies are the application
adapter's; the data, harness and resume stages are shared — one export
block per session, rendered from the settings' field names, precedes the
harness's `fetch` (data: one block per session) and its pytest session
(harness, resume), so all resolve the same suites — with an adapter hook
for application-specific data lines.
"""

from __future__ import annotations

import shlex
from enum import StrEnum

from pydantic import ConfigDict, Field

from applications.base import (
    Application,
    ApplicationRunSection,
    ApplicationSettings,
    Stage,
)
from cli.run_config import (
    HARNESS_ROOT,
    ResolvedSession,
    RunConfig,
    search_path_value,
)
from models.base import StrictModel
from models.conventions import CONVENTIONS
from platforms import Runtime
from settings import Settings

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
        "baseline_store",  # baselines.store
        "baseline_atol",  # baselines.atol
        "baseline_rtol",  # baselines.rtol
        "sbatch_args",  # slurm: (account, qos, partition, ntasks, cpus, extra_args)
        "srun_args",  # slurm: (ntasks, srun_args)
        "slurm_queue_wait_s",  # slurm.queue_wait_s
        "slurm_poll_s",  # slurm.poll_s
        "job_env",  # harness.env under the slurm runtime
        "input_search_path",  # data.input_search_path
        "output_store",  # outputs.store
    }
)


class ShellScript(StrictModel):
    """A rendered stage script: its name and full text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        description=(
            "Stage name, application-qualified — <stage>-<application> — and "
            "session-qualified for the per-session stages of a named session: "
            "<stage>-<application>-<session>"
        )
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


# The stages rendered once per session (the others once per application).
PER_SESSION = frozenset({Stage.HARNESS, Stage.RESUME})


def _env_value(value: object) -> str | None:
    """The export's value for one mirrored field; None means "not set"."""
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, list):
        return q(search_path_value(value))
    if isinstance(value, StrEnum):
        return q(value.value)
    return q(value)


def _exports(
    config: RunConfig,
    app: Application,
    section: ApplicationRunSection,
    session: ResolvedSession,
) -> list[tuple[str, str]]:
    """The environment the shared stages export for one session: the
    application's settings under its prefix, every mirrored harness setting
    under ASSAY_, the slurm bits, uv's cache, and the user's env — the
    per-session ones (timeout cap, job geometry, baselines) from the
    session's effective sections, the rest config-wide."""
    harness = session.harness
    slurm = session.slurm
    baselines = session.baselines
    runtime = session.effective_runtime(config.runtime)
    prefix = app.env_prefix
    exports: list[tuple[str, str]] = [
        (f"{prefix}ROOT_DIR", q(config.checkout_dir(app)))
    ]
    for field in ApplicationSettings.model_fields:
        if field == "root_dir":
            continue
        value = _env_value(getattr(section, field))
        if value is not None:
            exports.append((f"{prefix}{field.upper()}", value))
    exports += [
        (CONVENTIONS.env_var("PLATFORM"), config.platform.value),
        (CONVENTIONS.env_var("RUNTIME"), runtime.value),
    ]
    for field in settings_mirror_fields():
        if field == "runtime":
            continue  # resolved above
        value = _env_value(getattr(harness, field))
        if value is not None:
            exports.append((CONVENTIONS.env_var(field), value))
    if runtime is Runtime.SLURM:
        assert slurm is not None, "slurm runtime needs a slurm: section"
        exports.append((CONVENTIONS.env_var("SBATCH_ARGS"), q(slurm.sbatch_args)))
        exports.append((CONVENTIONS.env_var("SRUN_ARGS"), q(slurm.srun_args_value)))
        exports.append(
            (CONVENTIONS.env_var("SLURM_QUEUE_WAIT_S"), str(slurm.queue_wait_s))
        )
        exports.append((CONVENTIONS.env_var("SLURM_POLL_S"), str(slurm.poll_s)))
        if harness.env:
            # The driver jobs' environment; the login-node shell never needs it.
            pairs = " ".join(f"{key}={value}" for key, value in harness.env.items())
            exports.append((CONVENTIONS.env_var("JOB_ENV"), q(pairs)))
    exports.append(
        (
            CONVENTIONS.env_var("ENABLE_BASELINE_COMPARISONS"),
            "true" if baselines.enabled else "false",
        )
    )
    if baselines.root_dir is not None:
        exports.append(
            (CONVENTIONS.env_var("BASELINE_ROOT_DIR"), q(baselines.root_dir))
        )
    exports.append((CONVENTIONS.env_var("BASELINE_STORE"), q(baselines.store)))
    exports.append((CONVENTIONS.env_var("OUTPUT_STORE"), q(config.outputs.store)))
    for tolerance, override in (
        ("ATOL", baselines.atol),
        ("RTOL", baselines.rtol),
    ):
        if override is not None:
            exports.append(
                (CONVENTIONS.env_var(f"baseline_{tolerance}"), str(override))
            )
    if config.data.input_search_path:
        # Where fetch links source-less inputs from (this machine's trees).
        exports.append(
            (
                CONVENTIONS.env_var("INPUT_SEARCH_PATH"),
                q(search_path_value(config.data.input_search_path)),
            )
        )
    exports.append(("PATH", '"$HOME/.local/bin:$PATH"'))
    exports.append(("UV_CACHE_DIR", q(config.uv_cache_dir)))
    if runtime is not Runtime.SLURM:
        # docker/native run the driver as a local process: env applies here.
        exports += [(key, q(value)) for key, value in harness.env.items()]
    return exports


def _pytest_stage(
    config: RunConfig,
    app: Application,
    section: ApplicationRunSection,
    session: ResolvedSession,
    *,
    resume: bool,
) -> list[str]:
    """The harness stage's body, or the resume stage's: the same exports and
    cd, then the pytest session. The harness line carries --combo-clean-root,
    --dry-run and --submit-only as configured; the resume line carries
    --resume on the same root and none of those three. The session flags
    (publish, record, upload, no_suite_update) and pytest_args appear on
    both: the submit session performs the start-of-session probes and defers
    the actions, and -k must select the same combinations in both halves."""
    harness = session.harness
    pytest_args = [
        f"--application={app.name}",
        f"--suite-config={harness.suite_config}",
    ]
    if resume:
        pytest_args.append("--resume")
    pytest_args.append(
        f"--combo-output-root={config.session_output_root(app, session)}"
    )
    if not resume:
        if harness.clean_root:
            pytest_args.append("--combo-clean-root")
        if harness.pytest_dry_run:
            pytest_args.append("--dry-run")
        if harness.submit_only:
            pytest_args.append("--submit-only")
    if harness.publish_baselines:
        pytest_args.append("--publish-baselines")
    if harness.no_suite_update:
        pytest_args.append("--no-suite-update")
    if harness.record_benchmarks:
        pytest_args.append("--record-benchmarks")
    if harness.upload_output:
        pytest_args.append("--upload-output")
    pytest_args += harness.pytest_args
    return [
        *clean_python_env(section),
        *(
            f"export {key}={value}"
            for key, value in _exports(config, app, section, session)
        ),
        f"cd {q(HARNESS_ROOT)}",
        f"uv run --no-sync pytest {_DRIVER_COMBOS} "
        + " ".join(q(arg) for arg in pytest_args),
    ]


def _data(
    config: RunConfig,
    app: Application,
    section: ApplicationRunSection,
    shared: bool,
    sessions: list[ResolvedSession],
) -> list[str]:
    # The harness's own fetch, once per session under that session's exports
    # (so it resolves the same suites against the same checkout, with the
    # same baseline settings), from the harness checkout — sessions that
    # fetch the same thing fetch once; then whatever data lines the adapter
    # adds, and the harness-wide cartopy warm-up.
    lines = [*clean_python_env(section)]
    seen: set[tuple[tuple[tuple[str, str], ...], str]] = set()
    for session in sessions:
        exports = tuple(_exports(config, app, section, session))
        key = (exports, session.harness.suite_config)
        if key in seen:
            continue
        seen.add(key)
        lines += [
            *(f"export {key}={value}" for key, value in exports),
            f"cd {q(HARNESS_ROOT)}",
            (
                "uv run --no-sync ufs-chem-assay fetch "
                f"--application={app.name} --suite-config={q(session.harness.suite_config)}"
            ),
        ]
    lines += app.stage_lines(Stage.DATA, config, section)
    if shared and config.data.warm_cartopy:
        snippet = (
            "import cartopy.io.shapereader as s; "
            "[s.natural_earth(resolution=r, category='physical', name='coastline') "
            "for r in ('110m', '50m')]"
        )
        lines.append(f"uv run --no-sync python -c {q(snippet)}")
    return lines


def _required_session(
    config: RunConfig, session: ResolvedSession | None
) -> ResolvedSession:
    """The session a per-session stage renders: the given one, or the
    nameless implicit session of a config without sessions:."""
    if session is not None:
        return session
    if config.sessions:
        names = ", ".join(config.sessions)
        raise ValueError(
            f"the run config names sessions ({names}); the harness and resume "
            "stages render one session at a time"
        )
    return config.resolved_sessions()[0]


def render_stage(
    stage: Stage,
    config: RunConfig,
    app: Application,
    session: ResolvedSession | None = None,
    *,
    shared_data: bool = True,
    sessions: list[ResolvedSession] | None = None,
) -> ShellScript:
    """The stand-alone script for one stage of one application — of one
    session for the per-session stages (harness, resume): shebang, strict
    mode, banner, body. Modules are loaded only by the build body and by the
    job script. `session`: required when the config names sessions, implied
    otherwise; the resume stage needs the session's harness.submit_only.
    `sessions`: the sessions the data stage fetches for (default: all).
    `shared_data`: render the harness-wide data lines (the cartopy cache) —
    once per run, under the first application."""
    section = config.applications[app.name]
    who = app.name
    if stage in PER_SESSION:
        session = _required_session(config, session)
        if stage is Stage.RESUME and not session.harness.submit_only:
            raise ValueError("resume is rendered only with harness.submit_only: true")
        body = _pytest_stage(
            config, app, section, session, resume=stage is Stage.RESUME
        )
        name = f"{stage.value}-{app.name}"
        if session.name is not None:
            name = f"{name}-{session.name}"
            who = f"{app.name}/{session.name}"
    elif stage is Stage.DATA:
        body = _data(
            config,
            app,
            section,
            shared_data,
            config.resolved_sessions() if sessions is None else sessions,
        )
        name = f"{stage.value}-{app.name}"
    else:
        body = app.stage_lines(stage, config, section)
        name = f"{stage.value}-{app.name}"
    lines = [
        _SHEBANG,
        "set -euo pipefail",
        f'echo ">>> stage: {stage.value} ({who})"',
        *body,
    ]
    return ShellScript(name=name, text="\n".join(lines) + "\n")
