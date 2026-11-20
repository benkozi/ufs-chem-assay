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
from pathlib import Path, PurePosixPath

from pydantic import ConfigDict, Field

from applications.base import (
    Application,
    ApplicationRunSection,
    ApplicationSettings,
    RunConfigView,
    Stage,
    TestPlan,
    TestsSection,
)
from cli.run_config import (
    HARNESS_ROOT,
    ResolvedSession,
    RunConfig,
    search_path_value,
)
from identity import HARNESS_NAME
from lmod import LMOD_INIT, module_lines
from models.base import StrictModel
from models.conventions import CONVENTIONS
from platforms import Runtime
from runner import (
    TESTS_TEMPLATE_PATH,
    docker_prefix,
    render_job_script,
    sbatch_directives,
)
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
        "cartopy_data_dir",  # data.cartopy_dir
    }
)


class ShellScript(StrictModel):
    """A rendered stage script: its name and full text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        description=(
            "Stage name, application-qualified — <stage>-<application> — then "
            "toolchain-qualified for a config built with one, then "
            "session-qualified for the per-session stages of a named session: "
            "<stage>-<application>[-<toolchain>][-<session>] (stage_name)"
        )
    )
    text: str = Field(description="Complete bash source, shebang included")
    attachments: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Files written beside the script under its stem, by suffix: the test "
            "stage's Slurm job script (.sbatch), so a dry run shows the whole job"
        ),
    )


def q(value: object) -> str:
    return shlex.quote(str(value))


def harness_paths(paths: list[Path]) -> list[Path]:
    """Run-config module directories as the scripts name them: an absolute
    entry as written, a relative one under the harness checkout
    (`modulefiles` is the harness's own UFS modulefiles)."""
    return [path if path.is_absolute() else HARNESS_ROOT / path for path in paths]


def module_block(
    config: RunConfigView, checkout: Path, section: ApplicationRunSection
) -> list[str]:
    """Load the application's modulefile in this shell — only where compiled
    code runs in-process (the build stage): the platform's purge or reset,
    the section's module_env exports, its modulepath (default: the
    checkout's own modulefiles), the load. Returns `[]` when no modulefile
    is configured. The job scripts render the same block from the settings
    the harness stage exports (runner.py)."""
    if section.modulefile is None:
        return []
    return module_lines(
        init=config.module_init,
        env=section.module_env,
        paths=harness_paths(section.modulepath) or [checkout / "modulefiles"],
        modulefile=section.modulefile,
    )


# The first lines of every build stage, test stage and test job: leave the
# harness's venv before anything of the application's runs. `uv run` exports
# VIRTUAL_ENV and puts <venv>/bin first on PATH, every stage script (and,
# through sbatch, every test job) inherits both, and `module reset` touches
# neither. CMake projects read them: HELM's AXIS prepends $CONDA_PREFIX, else
# $VIRTUAL_ENV, to CMAKE_PREFIX_PATH, so every find_package after it searched
# the harness venv first. The harness's own `uv run --no-sync` lines later in
# a script need neither: uv finds the project venv from the checkout.
# The entry is matched through a variable: a `/` in the pattern itself would
# end it, even quoted.
HARNESS_VENV_OFF = [
    (
        'if [ -n "${VIRTUAL_ENV:-}" ]; then _venv_bin=":$VIRTUAL_ENV/bin:"; '
        'PATH=":$PATH:"; PATH="${PATH//"$_venv_bin"/:}"; PATH="${PATH#:}"; '
        'PATH="${PATH%:}"; unset _venv_bin; fi'
    ),
    "unset VIRTUAL_ENV CONDA_PREFIX",
]

# The stages whose scripts build or run the application itself.
_APPLICATION_STAGES = frozenset({Stage.BUILD, Stage.TEST})


def toolchain_probe(section: ApplicationRunSection, default: str) -> str:
    """The build stage's `which` line: the section's toolchain_tools when it
    names any, else the adapter's `default` probe."""
    if not section.toolchain_tools:
        return default
    return "which " + " ".join(q(tool) for tool in section.toolchain_tools)


def gate_lines(log: Path, gates: list[str]) -> list[str]:
    """Two lines per configure gate: fail the stage, naming the gate, when
    the configure log does not match it (grep -E); then print the matching
    lines so the stage log shows what satisfied it."""
    lines: list[str] = []
    for gate in gates:
        message = q(f"configure gate failed: {gate}")
        lines += [
            f"grep -Eq {q(gate)} {q(log)} || {{ echo {message} >&2; exit 1; }}",
            f"grep -E {q(gate)} {q(log)}",
        ]
    return lines


class OfflineBuild(StrictModel):
    """What a CMake build changes for the compute-node probe, where nothing
    can be fetched, and how the login node then finishes it for the tests."""

    args: list[str] = Field(description="The compute-node configure's arguments")
    note: str = Field(description="One line for the log: what is off and why")
    finish_args: list[str] = Field(
        description=(
            "The login-node finish's configure arguments (rendered with "
            "tests.enabled): what the probe turned off, back on"
        )
    )
    finish_note: str = Field(description="One line for the log: what the finish adds")


def cmake_stage(
    config: RunConfigView,
    section: ApplicationRunSection,
    *,
    module_dir: Path,
    source: Path,
    build: Path,
    cmake_args: list[str],
    targets: list[str],
    build_jobs: int,
    gates: list[str],
    default_probe: str,
    offline: OfflineBuild | None = None,
) -> list[str]:
    """The build stage of a CMake application, in the three shapes of
    build_node. Always first: the node, the module block (`module_dir` is
    the checkout whose own modulefiles are the default path), the toolchain
    probe, the build directory. Then, on the login node: one configure with
    `cmake_args`, teed to <build>/configure.log, the `gates` (shell lines
    over that log), the build. On a compute node: the configure and the
    build as two srun steps (two allocations, never one spanning both) in
    the module environment this shell loaded, with `offline.args` when the
    application must turn something off to configure without a network —
    and, with tests enabled, the login-node finish: `offline.finish_args`
    into the same build directory, the gates again, a build reusing the
    compute-built objects. Without `offline` the compute build is the whole
    build: nothing differs between the nodes."""
    log = build / "configure.log"
    launcher = config.build_launcher(section, cpus=build_jobs)
    prefix = "".join(f"{q(part)} " for part in launcher)

    def configure(args: list[str]) -> list[str]:
        # The toolchain's MPI launcher last, as the shell words they are
        # (one resolves a path where this line runs): never quoted here.
        command = " ".join(
            [
                f"cmake -S {q(source)} -B {q(build)}",
                *(q(arg) for arg in args),
                *config.mpi_configure_args(),
            ]
        )
        return [f"{command} 2>&1 | tee {q(log)}", *gates]

    compile_targets = " ".join(
        [
            f"cmake --build {q(build)}",
            *(f"--target {q(target)}" for target in targets),
            f"--parallel {build_jobs}",
        ]
    )
    setup = [
        *module_block(config, module_dir, section),
        toolchain_probe(section, default_probe),
        f"mkdir -p {q(build)}",
    ]
    if not launcher:
        return [
            f"echo {q('build node: login')}",
            *setup,
            *configure(cmake_args),
            compile_targets,
        ]
    finish = offline is not None and section.tests.enabled
    where = f"build node: compute ({' '.join(launcher)})"
    probe_configure, *probe_gates = configure(
        cmake_args if offline is None else offline.args
    )
    lines = [
        f"echo {q(where + (', then login (tests)' if finish else ''))}",
        *setup,
        *([] if offline is None else [f"echo {q(offline.note)}"]),
        f"{prefix}{probe_configure}",
        *probe_gates,
        f"{prefix}{compile_targets}",
    ]
    if offline is not None and finish:
        lines += [
            f"echo {q(offline.finish_note)}",
            *configure(offline.finish_args),
            compile_targets,
        ]
    return lines


def ctest_plan(tests: TestsSection, junit: str) -> TestPlan:
    """A CMake application's own tests: ctest over the build directory,
    from the checkout root, with the section's filter and parallelism.
    Every MPI test launches its own ranks (the launcher is baked in at
    configure time), so ctest itself is never behind one."""
    words = ["ctest", "--test-dir", "build", "--output-on-failure"]
    words += ["--output-junit", junit]
    if tests.filter is not None:
        words += ["-R", q(tests.filter)]
    if tests.parallel is not None:
        words += ["-j", str(tests.parallel)]
    return TestPlan(requires="build/CTestTestfile.cmake", command=" ".join(words))


def source_lines(
    checkout: Path,
    section: ApplicationRunSection,
    *,
    recurse_submodules: bool = False,
    extra_checks: list[str] | None = None,
) -> list[str]:
    """Clone or update an application checkout from `section`."""
    clone = q(checkout)
    ref = q(section.ref)
    submodules = "--recurse-submodules " if recurse_submodules else ""
    lines = [
        f"if [ ! -d {clone}/.git ]; then",
        f"  git clone {submodules}--branch {ref} {q(section.git_url)} {clone}",
    ]
    if section.update_source:
        lines += [
            "else",
            f"  git -C {clone} fetch origin",
            f"  git -C {clone} checkout {ref}",
            f"  git -C {clone} pull --ff-only origin {ref}",
        ]
        if recurse_submodules:
            lines.append(f"  git -C {clone} submodule update --init --recursive")
    else:
        lines += ["else", f'  echo "using existing clone at {clone} as-is"']
    lines.append("fi")
    if extra_checks:
        lines += extra_checks
    lines.append(f"git -C {clone} log -1 --oneline")
    return lines


def clean_python_env(
    config: RunConfigView, section: ApplicationRunSection
) -> list[str]:
    """The harness venv must never see the module environment (spack-stack
    sets PYTHONPATH to python3.11 site-packages that shadow the venv's
    numpy): undo any modules the calling shell had loaded, the platform's
    way (purge, or reset where purge strips the machine's own environment)."""
    if section.modulefile is None:
        return []
    return [LMOD_INIT, f"module {config.module_init.value}", "unset PYTHONPATH"]


def settings_mirror_fields() -> list[str]:
    """The Settings fields the harness: section mirrors, in Settings order."""
    return [name for name in Settings.model_fields if name not in SETTINGS_NOT_MIRRORED]


# The stages rendered once per session (the others once per application).
PER_SESSION = frozenset({Stage.HARNESS, Stage.RESUME})

# Script file numbering (<NN>-<stage>-...): fixed slots, so the harness stage
# is always 05 and its resume half 06. The application's own tests hold slot
# 04 — the number says "after the build", though the stage runs before the
# data stage (execution order is the Stage enum's, not the slots').
SCRIPT_INDEX = {
    Stage.SOURCE: 1,
    Stage.BUILD: 2,
    Stage.DATA: 3,
    Stage.TEST: 4,
    Stage.HARNESS: 5,
    Stage.RESUME: 6,
}


def stage_name(
    stage: Stage,
    application: str,
    toolchain: str | None = None,
    session: str | None = None,
) -> str:
    """A rendered script's name (and its log's stem, after the slot number):
    <stage>-<application>[-<toolchain>][-<session>] — the toolchain for a
    config built with one, the session for the per-session stages of a
    named session."""
    parts = [stage.value, application, toolchain, session]
    return "-".join(part for part in parts if part is not None)


# Where a tests root is mounted in the application's image (docker runtime).
_TESTS_MOUNT = PurePosixPath("/assay-tests")
_CLOCK = "$(date -u +%Y-%m-%dT%H:%M:%SZ)"


def _env_value(value: object) -> str | None:
    """The export's value for one mirrored field; None means "not set"."""
    if value is None or value == "" or value == [] or value == {}:
        return None
    if isinstance(value, list):
        return q(search_path_value(value))
    if isinstance(value, dict):
        # NAME=VALUE pairs, each a shell word (ASSAY_JOB_ENV's spelling).
        return q(" ".join(q(f"{name}={item}") for name, item in value.items()))
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
    runtime = session.harness.effective_runtime(config.runtime)
    prefix = app.env_prefix
    exports: list[tuple[str, str]] = [
        (f"{prefix}ROOT_DIR", q(config.checkout_dir(app)))
    ]
    for field in ApplicationSettings.model_fields:
        if field == "root_dir":
            continue
        setting: object = getattr(section, field)
        if field == "modulepath":
            # As the scripts name them: the jobs run from the checkout, not
            # from the harness.
            setting = harness_paths(section.modulepath)
        value = _env_value(setting)
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
    exports.append(
        (CONVENTIONS.env_var("CARTOPY_DATA_DIR"), q(config.cartopy_data_dir))
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
        *clean_python_env(config, section),
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
    lines = [*clean_python_env(config, section)]
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
        # The harness's own warm-up, so that it downloads into the directory
        # the session exports name (ASSAY_CARTOPY_DATA_DIR, above).
        snippet = "from plotting import warm_natural_earth; warm_natural_earth()"
        lines.append(f"uv run --no-sync python -c {q(snippet)}")
    return lines


def tests_enabled(config: RunConfig, app: Application) -> bool:
    """Whether `app` has a test stage in this run: its section's
    tests.enabled. ValueError when tests are enabled for an application that
    has no test suite of its own."""
    section = config.applications[app.name]
    if not section.tests.enabled:
        return False
    if app.test_plan(section, "junit") is None:
        raise ValueError(
            f"applications.{app.name}.tests.enabled is true, but {app.name} has no "
            "test suite of its own"
        )
    return True


def _harness_command(
    config: RunConfig, section: ApplicationRunSection, command: str
) -> list[str]:
    """One `ufs-chem-assay` subcommand from a stage script: the harness venv
    clean of the module environment, uv on the PATH with its cache, from the
    harness checkout — as the data stage calls `fetch`."""
    return [
        *clean_python_env(config, section),
        'export PATH="$HOME/.local/bin:$PATH"',
        f"export UV_CACHE_DIR={q(config.uv_cache_dir)}",
        f"cd {q(HARNESS_ROOT)}",
        f"uv run --no-sync ufs-chem-assay {command}",
    ]


def _test(
    config: RunConfig, app: Application, section: ApplicationRunSection
) -> tuple[list[str], dict[str, str]]:
    """The test stage's body and attachments: the application's own tests
    against the build the build stage left — never a compile. A guard that
    the test stack exists, then the launch for the runtime. slurm: one job
    (rendered beside the script as its .sbatch, the test command directly on
    the node — the test runner launches the MPI ranks itself), submitted
    through `submit-tests`, which records it; the stage returns at once and
    `watch` evaluates. native and docker: the command in place, its output
    teed to the tests root between tic/toc lines, then `evaluate-tests`
    records the verdict and the stage exits with the tests' status."""
    if not tests_enabled(config, app):
        raise ValueError(
            f"applications.{app.name}.tests.enabled is false: there is no test "
            "stage to render"
        )
    tests = section.tests
    checkout = config.checkout_dir(app)
    tests_root = config.tests_root(app)
    stem = CONVENTIONS.tests_stem
    runtime = config.runtime
    selection = f"--root-dir {q(config.root_dir)} --application {q(app.name)}"
    if config.toolchain is not None:
        selection += f" --toolchain {q(config.toolchain)}"

    def plan_for(junit: str) -> str:
        plan = app.test_plan(section, junit)
        assert plan is not None  # tests_enabled checked
        return plan.command

    required = app.test_plan(section, "junit")
    assert required is not None
    missing = (
        f"no tests built ({checkout / required.requires} is missing): the build "
        f"stage builds the test stack when applications.{app.name}.tests.enabled "
        "is true"
    )
    guard = (
        f"[ -f {q(checkout / required.requires)} ] || "
        f"{{ echo {q(missing)} >&2; exit 1; }}"
    )
    exports = {name: q(value) for name, value in tests.env.items()}

    if runtime is Runtime.SLURM:
        assert config.slurm is not None, "slurm runtime needs a slurm: section"
        job = render_job_script(
            job_name="-".join(
                part
                for part in (HARNESS_NAME, "tests", app.name, config.toolchain)
                if part is not None
            ),
            out_path=tests_root / f"{stem}-%j.out",
            root_dir=checkout,
            minutes=tests.minutes,
            directives=sbatch_directives(
                config.slurm.job_args(ntasks=tests.ntasks, cpus=tests.cpus)
            ),
            # submit-tests runs under `uv run`: the job inherits its venv.
            module_lines=[*HARNESS_VENV_OFF, *module_block(config, checkout, section)],
            env=exports,
            command=plan_for(f'{q(tests_root)}/{stem}-"$SLURM_JOB_ID".xml'),
            srun_args="",
            template_text=TESTS_TEMPLATE_PATH.read_text(),
        )
        lines = [guard, *_harness_command(config, section, f"submit-tests {selection}")]
        return lines, {".sbatch": job}

    out = q(tests_root / f"{stem}.out")
    if runtime is Runtime.DOCKER:
        settings = (
            app.settings_model(root_dir=checkout)
            if section.docker_image is None
            else app.settings_model(
                root_dir=checkout, docker_image=section.docker_image
            )
        )
        prefix = docker_prefix(
            settings, app.container_workdir, output_mount=(tests_root, _TESTS_MOUNT)
        )
        inside = [f"{name}={value}" for name, value in exports.items()]
        command = " ".join(
            [
                *(q(part) for part in prefix),
                *(["env", *inside] if inside else []),
                plan_for(q(_TESTS_MOUNT / f"{stem}.xml")),
            ]
        )
        setup = [f"mkdir -p {q(tests_root)}"]
    else:
        launcher = f"{config.harness.launcher} " if config.harness.launcher else ""
        command = f"{launcher}{plan_for(q(tests_root / f'{stem}.xml'))}"
        setup = [
            f"mkdir -p {q(tests_root)}",
            *module_block(config, checkout, section),
            *(f"export {name}={value}" for name, value in exports.items()),
            f"cd {q(checkout)}",
        ]
    lines = [
        guard,
        *setup,
        "status=0",
        f'echo "tic: {_CLOCK}" | tee {out}',
        f"{command} 2>&1 | tee -a {out} || status=$?",
        f'echo "toc: {_CLOCK}" | tee -a {out}',
        *_harness_command(
            config, section, f'evaluate-tests {selection} --exit-status "$status"'
        ),
        'exit "$status"',
    ]
    return lines, {}


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
    job script; the build and test stages first leave the harness's venv
    (HARNESS_VENV_OFF). `session`: required when the config names sessions, implied
    otherwise; the resume stage needs the session's harness.submit_only.
    `sessions`: the sessions the data stage fetches for (default: all).
    `shared_data`: render the harness-wide data lines (the cartopy cache) —
    once per run, under the first application."""
    if stage not in app.stages:
        mine = ", ".join(s.value for s in Stage if s in app.stages)
        raise ValueError(
            f"{app.name} takes no part in the {stage.value} stage (its stages: {mine})"
        )
    section = config.applications[app.name]
    who = app.name
    attachments: dict[str, str] = {}
    if stage in PER_SESSION:
        session = _required_session(config, session)
        if stage is Stage.RESUME and not session.harness.submit_only:
            raise ValueError("resume is rendered only with harness.submit_only: true")
        body = _pytest_stage(
            config, app, section, session, resume=stage is Stage.RESUME
        )
        name = stage_name(stage, app.name, config.toolchain, session.name)
        if session.name is not None:
            who = f"{app.name}/{session.name}"
    elif stage is Stage.DATA:
        body = _data(
            config,
            app,
            section,
            shared_data,
            config.resolved_sessions() if sessions is None else sessions,
        )
        name = stage_name(stage, app.name, config.toolchain)
    elif stage is Stage.TEST:
        body, attachments = _test(config, app, section)
        name = stage_name(stage, app.name, config.toolchain)
    else:
        body = app.stage_lines(stage, config, section)
        name = stage_name(stage, app.name, config.toolchain)
    if config.toolchain is not None:
        who = f"{who}, {config.toolchain}"
    lines = [
        _SHEBANG,
        "set -euo pipefail",
        f'echo ">>> stage: {stage.value} ({who})"',
        *(HARNESS_VENV_OFF if stage in _APPLICATION_STAGES else []),
        *body,
    ]
    return ShellScript(name=name, text="\n".join(lines) + "\n", attachments=attachments)
