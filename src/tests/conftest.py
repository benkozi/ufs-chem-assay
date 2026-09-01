import shutil
import subprocess
import time
from collections.abc import Generator, Iterator
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, ConfigDict, InstanceOf

from analysis import RunContext, concatenate_stats_csvs
from combos import Combo, build_config, enumerate_combos, write_combos_csv
from comparison import concatenate_comparison_csvs, resolve_baseline_comparisons
from examples import discover_examples
from logs import configure_logging, get_logger
from models.cece_config import CeceConfig
from models.suite_config import (
    Analysis,
    Assertions,
    BaselineComparison,
    RunManifest,
    SuiteConfig,
)
from plotting import render_all_bias_plots, render_all_plots
from report import TestReportRow, worst_result, write_test_report_csv
from ulid import ULID
from resolution import resolve_output_roots, select_suites
from runner import DriverRunResult, run_driver
from settings import Settings

if TYPE_CHECKING:
    from dask.distributed import Client

logger = get_logger("conftest")

# combo-test-runner/src/tests/conftest.py -> combo-test-runner/src/tests/
_TESTS_ROOT = Path(__file__).resolve().parent
# Always the final --suite-config search root, so checked-in suites stay
# selectable and the bare default needs no configuration.
_BUILTIN_SUITE_DIR = _TESTS_ROOT / "config" / "suite"
# A literal filename is also a regex that fullmatches exactly that file.
_DEFAULT_SUITE = "simple-maccity-suite.yaml"

# Container-side mount point for the default (pytest tmp) output root, which
# lives outside the /work mount.
_CONTAINER_TMP_ROOT = PurePosixPath("/combo_runs")


class ComboRoots(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: Path
    container: PurePosixPath
    needs_mount: bool  # True when the host root is outside the /work mount

    @property
    def output_mount(self) -> tuple[Path, PurePosixPath] | None:
        return (self.host, self.container) if self.needs_mount else None


class GeneratedCombo(BaseModel):
    """A combination's generated driver config and where it lives."""

    model_config = ConfigDict(frozen=True)

    host_dir: Path
    container_yaml: PurePosixPath
    config: CeceConfig


class SuiteContext(BaseModel):
    """One selected suite, fully resolved at sessionstart: the loaded suite,
    its enumerated combinations, and its resolved baseline entries. The
    session holds a list of these in selection order; every per-suite value a
    test needs derives from the combo's owning context."""

    model_config = ConfigDict(frozen=True)

    suite: SuiteConfig
    # InstanceOf: Combo is enumeration machinery (callables, enum members),
    # validated by isinstance rather than deep pydantic validation.
    combos: list[InstanceOf[Combo]]
    baselines: dict[str, BaselineComparison]


# Session state on pytest.Config, typed end to end via config.stash — written
# once at sessionstart (realized roots: once in combo_roots), read everywhere
# else. StashKey is the sanctioned pattern for hanging state on the config.
_RUN_ID = pytest.StashKey[str]()
_CECE_COMMIT = pytest.StashKey[str | None]()
_SETTINGS = pytest.StashKey[Settings]()
_SUITE_CONTEXTS = pytest.StashKey[list[SuiteContext]]()
_EXPLICIT_ROOTS = pytest.StashKey[ComboRoots | None]()
_REALIZED_ROOTS = pytest.StashKey[ComboRoots]()
_REPORT_ROWS = pytest.StashKey[dict[str, TestReportRow]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("combo", "combinatorial driver test runner")
    group.addoption(
        "--suite-config",
        default=_DEFAULT_SUITE,
        help=(
            "Suite selector: an existing file path, or a regex fullmatched "
            "against each suite's file name or search-root-relative path. "
            "Candidates are the *.yaml files found recursively under "
            "CECE_SUITE_CONFIG_SEARCH_PATH (os.pathsep-separated) plus the "
            "built-in suite directory; every match runs — several matches "
            "run as one multi-suite session."
        ),
    )
    group.addoption(
        "--combo-output-root",
        default=None,
        help=(
            "Root artifact directory; relative paths resolve against /work in the "
            "container. Default: a pytest-managed temporary directory."
        ),
    )
    group.addoption(
        "--combo-clean-root",
        action="store_true",
        help="Remove an existing output root before running (default: existing root is an error).",
    )
    group.addoption(
        "--dry-run",
        action="store_true",
        help=(
            "Generate every combination's config and the session artifacts "
            "(run.yaml, combos.csv, test-report.csv) but skip driver execution; "
            "every combo test skips."
        ),
    )
    group.addoption(
        "--cece-root-dir",
        default=None,
        help=(
            "Host path of the CECE repository root, mounted at /work in the "
            "driver container. Overrides CECE_ROOT_DIR; required (either form) "
            "to execute the driver."
        ),
    )
    group.addoption(
        "--run-examples",
        action="store_true",
        help=(
            "Run the CECE checkout's shipped example configs "
            "(examples/cece_config_ex*.yaml) verbatim in docker, "
            "fetching data via scripts/data_download/ first. Off by default; "
            "examples may legitimately fail — they are artifacts under test."
        ),
    )


_ROOT_DIR_SOURCES = "pass --cece-root-dir or set CECE_ROOT_DIR"


def pytest_sessionstart(session: pytest.Session) -> None:
    config = session.config
    # Init kwargs beat env vars in pydantic-settings, so the flag wins over
    # CECE_ROOT_DIR here — the single root_dir resolution point (Settings is
    # frozen).
    root_dir_option = config.getoption("--cece-root-dir")
    settings = (
        Settings() if root_dir_option is None else Settings(root_dir=root_dir_option)
    )
    configure_logging(settings.log_level)

    # Sessions always run against a checked-out CECE when a root is
    # configured: an unresolvable commit SHA is a fatal misconfiguration,
    # surfaced here before any work. No configured root records null.
    try:
        config.stash[_CECE_COMMIT] = settings.get_cece_commit_sha()
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc

    try:
        suite_paths = select_suites(
            config.getoption("--suite-config"),
            [*settings.suite_config_search_path, _BUILTIN_SUITE_DIR],
        )
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc

    # Every match runs: one suite is a single-suite session, several a
    # multi-suite session over the same flat output root.
    contexts: list[SuiteContext] = []
    seen_names: dict[str, Path] = {}
    for suite_path in suite_paths:
        try:
            suite = SuiteConfig.from_yaml(
                suite_path,
                config_search_path=settings.config_search_path,
                root_dir=settings.root_dir,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise pytest.UsageError(str(exc)) from exc
        if suite.name in seen_names:
            raise pytest.UsageError(
                f"suite name {suite.name!r} is defined by both "
                f"{seen_names[suite.name]} and {suite_path}; suite names join "
                "every session artifact and must be unique among the selected suites"
            )
        seen_names[suite.name] = suite_path

        # Selector validation happens here, against the loaded base config,
        # before any container runs.
        base_config = CeceConfig.from_yaml(suite.config_path)
        try:
            combos = enumerate_combos(suite.sweep, base_config)
            baselines = resolve_baseline_comparisons(suite.baseline_comparisons, combos)
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from exc
        contexts.append(SuiteContext(suite=suite, combos=combos, baselines=baselines))

    # One ULID per test run, generated at runtime only — never configuration.
    run_id = str(ULID())
    logger.info(
        "starting run %s (%s suite(s): %s)",
        run_id,
        len(contexts),
        ", ".join(context.suite.name for context in contexts),
    )
    config.stash[_RUN_ID] = run_id

    # An explicit output root is resolved and guarded here, before anything
    # runs. The default (pytest tmp) root is created lazily in combo_roots —
    # it is freshly created each session and can never pre-exist.
    option = config.getoption("--combo-output-root")
    if option is None:
        roots = None
    else:
        if settings.root_dir is None:
            raise pytest.UsageError(
                f"--combo-output-root resolves against the CECE repository root; {_ROOT_DIR_SOURCES}"
            )
        try:
            host_root, container_root = resolve_output_roots(option, settings.root_dir)
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from exc
        if host_root.exists():
            if config.getoption("--combo-clean-root"):
                shutil.rmtree(host_root)
            else:
                raise pytest.UsageError(
                    f"output root {host_root} already exists; move it aside or pass --combo-clean-root"
                )
        roots = ComboRoots(host=host_root, container=container_root, needs_mount=False)

    config.stash[_SETTINGS] = settings
    config.stash[_SUITE_CONTEXTS] = contexts
    config.stash[_EXPLICIT_ROOTS] = roots
    # test-report.csv rows, keyed by nodeid in execution order; filled by the
    # pytest_runtest_makereport wrapper below.
    config.stash[_REPORT_ROWS] = {}


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Fail fast when driver execution is coming but no CECE root is
    configured. Collection time (not sessionstart) so harness-only runs —
    which collect no driver-executing tests — need no environment; still
    before any test executes. Example tests don't request driver_run, so
    they carry their own condition, active only with --run-examples."""
    if config.getoption("--dry-run"):
        return
    needs_root = any(
        "driver_run" in getattr(item, "fixturenames", ()) for item in items
    )
    if config.getoption("--run-examples"):
        needs_root = needs_root or any(
            "example_yaml" in getattr(item, "fixturenames", ()) for item in items
        )
    if not needs_root:
        return
    settings = config.stash[_SETTINGS]
    if settings.root_dir is None:
        raise pytest.UsageError(
            f"driver execution requires the CECE repository root; {_ROOT_DIR_SOURCES}"
        )
    if not settings.root_dir.is_dir():
        raise pytest.UsageError(
            f"CECE root {settings.root_dir} is not an existing directory; "
            f"check --cece-root-dir / CECE_ROOT_DIR"
        )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Collect every combo-parameterized test's outcome for test-report.csv.
    A test's phases combine via worst_result (failed > skipped > passed), so
    fixture skips (dry run, failed driver) and teardown failures report
    truthfully. Non-combo tests are not reported."""
    report = yield
    callspec = getattr(item, "callspec", None)
    param = callspec.params.get("driver_run") if callspec is not None else None
    rows = item.config.stash.get(_REPORT_ROWS, None)
    if not isinstance(param, tuple) or rows is None:
        return report
    context, combo = param
    if not isinstance(context, SuiteContext) or not isinstance(combo, Combo):
        return report
    row = rows.get(item.nodeid)
    if row is None:
        row = TestReportRow(
            pytest_name=item.name,
            suite=context.suite.name,
            combo_id=combo.combo_id,
            combo=combo.name,
            result="passed",
        )
        rows[item.nodeid] = row
    row.result = worst_result(row.result, report.outcome)
    return report


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Session-end artifact pipeline, in order: test report -> stats concat ->
    per-suite overview plots -> comparison-stats concat -> per-suite bias
    plots. The output root is flat across suites; concatenated CSVs carry a
    suite column, and each suite's plots render from its own slice so color
    scales never mix suites. Bias plotting is independent of the overview
    plotting/stats gates: its scale derives from the comparison CSV and it
    is governed per comparison entry."""
    config = session.config
    contexts = config.stash.get(_SUITE_CONTEXTS, None)
    roots = config.stash.get(_REALIZED_ROOTS, None)
    if contexts is None or roots is None:
        return

    report_rows = config.stash.get(_REPORT_ROWS, {})
    if report_rows:
        write_test_report_csv(
            list(report_rows.values()), roots.host / "test-report.csv"
        )

    # Only combos whose suite enabled stats produced a CSV, so the glob is
    # already gated; per-suite plot gates apply to each suite's slice.
    combo_csvs = sorted(roots.host.glob("*/*-stats.csv"))
    if combo_csvs:
        stats = concatenate_stats_csvs(combo_csvs, roots.host / "descriptive_stats.csv")
        for context in contexts:
            if not context.suite.plotting.enabled:
                continue
            suite_stats = stats[stats["suite"] == context.suite.name]
            if not suite_stats.empty:
                render_all_plots(
                    roots.host,
                    suite_stats,
                    gif_enabled=context.suite.plotting.gif_enabled,
                )

    comparison_csvs = sorted(roots.host.glob("*/*-stats-comparison.csv"))
    if comparison_csvs:
        comparison_stats = concatenate_comparison_csvs(
            comparison_csvs, roots.host / "stats-comparison.csv"
        )
        settings = config.stash[_SETTINGS]
        baseline_root = settings.baseline_root_dir or Path.cwd()
        for context in contexts:
            resolved = context.baselines
            pairs = [
                (
                    roots.host / combo.combo_id,
                    baseline_root / resolved[combo.name].ulid,
                )
                for combo in context.combos
                if combo.name in resolved and resolved[combo.name].plot
            ]
            suite_comparisons = comparison_stats[
                comparison_stats["suite"] == context.suite.name
            ]
            if pairs and not suite_comparisons.empty:
                render_all_bias_plots(pairs, suite_comparisons)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "driver_run" in metafunc.fixturenames:
        contexts = metafunc.config.stash[_SUITE_CONTEXTS]
        multi = len(contexts) > 1

        def combo_part(context: SuiteContext, combo: Combo) -> str:
            # Suite-qualified only in multi-suite sessions, so single-suite
            # test ids stay exactly as they always were.
            return f"{context.suite.name}/{combo.name}" if multi else combo.name

        if "species_name" in metafunc.fixturenames:
            # Joint parametrization: combos pair only with their own suite's
            # configured species (a cross product would leak species across
            # suites). Hand-built ids preserve pytest's dash-joined format.
            triples = [
                ((context, combo), species)
                for context in contexts
                for combo in context.combos
                for species in sorted(context.suite.assertions.species or {})
            ]
            metafunc.parametrize(
                "driver_run,species_name",
                triples,
                ids=[
                    f"{combo_part(pair[0], pair[1])}-{species}"
                    for pair, species in triples
                ],
                indirect=["driver_run"],
            )
        else:
            pairs = [
                (context, combo) for context in contexts for combo in context.combos
            ]
            metafunc.parametrize(
                "driver_run",
                pairs,
                ids=[combo_part(context, combo) for context, combo in pairs],
                indirect=True,
            )
    if "example_yaml" in metafunc.fixturenames:
        # Discovery needs the CECE checkout; without a root there is nothing
        # to parametrize (the empty set collects as a single skipped item,
        # and the collection guard converts it to a UsageError when
        # --run-examples actually asks for execution).
        root_dir = metafunc.config.stash[_SETTINGS].root_dir
        examples = discover_examples(root_dir) if root_dir is not None else []
        metafunc.parametrize(
            "example_yaml", examples, ids=[path.stem for path in examples]
        )


@pytest.fixture(scope="session")
def settings(request: pytest.FixtureRequest) -> Settings:
    return request.config.stash[_SETTINGS]


def _owning_context(request: pytest.FixtureRequest) -> SuiteContext:
    """The SuiteContext owning the requesting item's driver_run param: how
    per-suite values reach tests without changing test signatures. Only
    meaningful for driver_run-parametrized items (function-scoped callers)."""
    callspec = getattr(request.node, "callspec", None)
    assert callspec is not None, "requires a driver_run-parametrized test"
    context, _combo = callspec.params["driver_run"]
    assert isinstance(context, SuiteContext)
    return context


@pytest.fixture()
def suite_assertions(request: pytest.FixtureRequest) -> Assertions:
    """The owning suite's assertions for this item's combination."""
    return _owning_context(request).suite.assertions


@pytest.fixture()
def suite_analysis(request: pytest.FixtureRequest) -> Analysis:
    """The owning suite's analysis switches for this item's combination."""
    return _owning_context(request).suite.analysis


@pytest.fixture()
def baseline_comparisons(
    request: pytest.FixtureRequest,
) -> dict[str, BaselineComparison]:
    """The owning suite's resolved baseline entries, keyed by combo name."""
    return _owning_context(request).baselines


@pytest.fixture()
def run_context(request: pytest.FixtureRequest) -> RunContext:
    return RunContext(
        run_id=request.config.stash[_RUN_ID],
        suite=_owning_context(request).suite.name,
    )


@pytest.fixture(scope="session")
def dask_client(settings: Settings) -> Iterator["Client"]:
    """Session distributed client for the analysis computations. Requested
    lazily (request.getfixturevalue) so disabled-analysis runs never pay
    cluster startup. dask_nworkers unset -> all available cores."""
    from dask.distributed import Client, LocalCluster

    cluster_kwargs: dict[str, object] = {"dashboard_address": None}
    if settings.dask_nworkers is not None:
        cluster_kwargs["n_workers"] = settings.dask_nworkers
    cluster = LocalCluster(**cluster_kwargs)
    client = Client(cluster)
    logger.info("started dask client with %s worker(s)", len(cluster.workers))
    yield client
    client.close()
    cluster.close()


@pytest.fixture(scope="session")
def combo_roots(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> ComboRoots:
    roots = request.config.stash[_EXPLICIT_ROOTS]
    if roots is None:
        # Default: all test-generated data goes to a pytest temp directory,
        # bind-mounted into the container at a fixed path.
        roots = ComboRoots(
            host=tmp_path_factory.mktemp("combo_runs"),
            container=_CONTAINER_TMP_ROOT,
            needs_mount=True,
        )
    # Stash the realized roots for pytest_sessionfinish (hooks cannot request
    # fixtures; the tmp-default root only exists once this fixture has run).
    request.config.stash[_REALIZED_ROOTS] = roots

    # Write the run manifest as soon as the root is realized, so even a run
    # that dies mid-way records what ran (combos.csv follows immediately
    # after config generation — its values come from the generated configs).
    roots.host.mkdir(parents=True, exist_ok=True)
    manifest = RunManifest(
        run_id=request.config.stash[_RUN_ID],
        cece_commit=request.config.stash[_CECE_COMMIT],
        suites=[context.suite for context in request.config.stash[_SUITE_CONTEXTS]],
    )
    manifest.to_yaml(roots.host / "run.yaml")
    return roots


@pytest.fixture(scope="session")
def generated_combos(
    request: pytest.FixtureRequest, combo_roots: ComboRoots
) -> dict[str, GeneratedCombo]:
    """Generate every suite's every combo config up front (flat root: combo
    ULIDs are unique across suites), then write combos.csv — the
    effective-parameter table reads the generated configs, and writing it
    here keeps the record complete before any driver executes."""
    generated: dict[str, GeneratedCombo] = {}
    entries: list[tuple[str, Combo, CeceConfig]] = []
    for context in request.config.stash[_SUITE_CONTEXTS]:
        for combo in context.combos:
            # Storage carries no semantics: directories and filenames are the
            # combo's runtime ULID; combos.csv dereferences them.
            combo_dir = combo_roots.host / combo.combo_id
            combo_dir.mkdir(parents=True)
            container_dir = combo_roots.container / combo.combo_id
            config = build_config(
                combo,
                output_directory=str(container_dir),
                config_path=context.suite.config_path,
            )
            config.to_yaml(combo_dir / f"{combo.combo_id}.yaml")
            generated[combo.combo_id] = GeneratedCombo(
                host_dir=combo_dir,
                container_yaml=container_dir / f"{combo.combo_id}.yaml",
                config=config,
            )
            entries.append((context.suite.name, combo, config))
    write_combos_csv(
        entries,
        run_id=request.config.stash[_RUN_ID],
        csv_path=combo_roots.host / "combos.csv",
    )
    return generated


@pytest.fixture(scope="session")
def driver_run(
    request: pytest.FixtureRequest,
    combo_roots: ComboRoots,
    generated_combos: dict[str, GeneratedCombo],
    settings: Settings,
) -> DriverRunResult:
    """Run the driver once per combination (session-scoped, parameterized on
    (suite context, combo) pairs) and capture the outcome without raising."""
    context, combo = request.param
    assert isinstance(context, SuiteContext) and isinstance(combo, Combo)
    # Effective timeout: the owning suite's value, capped by the settings
    # value when that is smaller.
    run_timeout_s = min(context.suite.timeout_s, settings.run_timeout_s)
    generated = generated_combos[combo.combo_id]
    out_path = generated.host_dir / f"{combo.combo_id}.out"

    if request.config.getoption("--dry-run"):
        # Everything up to here — enumeration, run.yaml, combos.csv, this
        # combo's generated config — happened for real; only execution is
        # skipped, and every dependent test skips with this reason.
        logger.info("dry run: skipping driver execution for combo %s", combo.name)
        pytest.skip("dry run: driver execution skipped")

    logger.info("running combo %s (timeout=%ss)", combo.name, run_timeout_s)
    start = time.monotonic()
    error: Exception | None = None
    try:
        run_driver(
            settings,
            container_yaml=generated.container_yaml,
            out_path=out_path,
            timeout_s=run_timeout_s,
            output_mount=combo_roots.output_mount,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        error = exc
    duration = time.monotonic() - start
    if error is None:
        logger.info("combo %s completed in %.1fs", combo.name, duration)
    else:
        logger.error(
            "combo %s FAILED after %.1fs: %s",
            combo.name,
            duration,
            type(error).__name__,
        )

    return DriverRunResult(
        combo=combo,
        combo_dir=generated.host_dir,
        out_path=out_path,
        config=generated.config,
        error=error,
    )
