import shutil
import subprocess
import time
from collections.abc import Generator, Iterator
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, ConfigDict, InstanceOf

from analysis import RunContext, concatenate_stats_csvs
from baselines import SuiteInput, publish_session
from applications.base import Application, ApplicationSettings, DriverConfig
from combos import Combo, ParameterRow, enumerate_combos, write_combos_csv
from comparison import concatenate_comparison_csvs, resolve_baseline_comparisons
from identity import harness_commit, harness_version
from logs import configure_logging, get_logger
from models.suite_config import (
    Analysis,
    Assertions,
    BaselineComparison,
    RecordedInput,
    RunManifest,
    SuiteConfig,
)
from plotting import render_all_bias_plots, render_all_plots
from report import TestReportRow, worst_result, write_test_report_csv
from ulid import ULID
from platforms import Runtime
from resolution import resolve_output_roots
from selection import resolve_suites
from staging import missing_inputs
from runner import DriverRunResult, run_driver, write_job_script
from settings import Settings

if TYPE_CHECKING:
    from dask.distributed import Client

logger = get_logger("conftest")

# <repo root>/src/tests/conftest.py -> <repo root>/src/tests/
_TESTS_ROOT = Path(__file__).resolve().parent
# Container-side mount point for the default (pytest tmp) output root, which
# lives outside the application's checkout mount (docker runtime only).
_CONTAINER_TMP_ROOT = PurePosixPath("/combo_runs")


class ComboRoots(BaseModel):
    """The output root as the harness sees it (host) and as the driver sees
    it (driver): the container path under docker, the same host path
    natively."""

    model_config = ConfigDict(frozen=True)

    host: Path
    driver: PurePosixPath
    needs_mount: bool  # docker only: the host root is outside the checkout mount

    @property
    def output_mount(self) -> tuple[Path, PurePosixPath] | None:
        return (self.host, self.driver) if self.needs_mount else None


class GeneratedCombo(BaseModel):
    """A combination's generated driver config and where it lives."""

    model_config = ConfigDict(frozen=True)

    host_dir: Path
    driver_yaml: PurePosixPath  # the config path as the driver sees it
    config: InstanceOf[DriverConfig]


class SuiteContext(BaseModel):
    """One selected suite, fully resolved at sessionstart: the loaded suite,
    its enumerated combinations, and its resolved baseline entries. The
    session holds a list of these in selection order; every per-suite value a
    test needs derives from the combo's owning context."""

    model_config = ConfigDict(frozen=True)

    path: Path  # the suite file: what --publish-baselines repoints
    suite: InstanceOf[SuiteConfig]
    # InstanceOf: Combo is enumeration machinery (callables, enum members),
    # validated by isinstance rather than deep pydantic validation.
    combos: list[InstanceOf[Combo]]
    baselines: dict[str, InstanceOf[BaselineComparison]]


# Session state on pytest.Config, typed end to end via config.stash — written
# once at sessionstart (realized roots: once in combo_roots), read everywhere
# else. StashKey is the sanctioned pattern for hanging state on the config.
_RUN_ID = pytest.StashKey[str]()
_APPLICATION = pytest.StashKey[Application]()
_APP_SETTINGS = pytest.StashKey[ApplicationSettings]()
_APPLICATION_COMMIT = pytest.StashKey[str | None]()
_HARNESS_VERSION = pytest.StashKey[str]()
_HARNESS_COMMIT = pytest.StashKey[str | None]()
_SETTINGS = pytest.StashKey[Settings]()
_SUITE_CONTEXTS = pytest.StashKey[list[SuiteContext]]()
_EXPLICIT_ROOTS = pytest.StashKey[ComboRoots | None]()
_REALIZED_ROOTS = pytest.StashKey[ComboRoots]()
_REPORT_ROWS = pytest.StashKey[dict[str, TestReportRow]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("combo", "combinatorial driver test runner")
    group.addoption(
        "--application",
        default=None,
        help=(
            "Run only suites of this application (registry name, e.g. cece); "
            "overrides ASSAY_APPLICATION. Without it the application is inferred "
            "from the selected suites, which must agree. A bare --application "
            "runs that application's default suite."
        ),
    )
    group.addoption(
        "--suite-config",
        default=None,
        help=(
            "Suite selector: an existing file path, or a regex fullmatched "
            "against each suite's file name or search-root-relative path. "
            "Candidates are the *-suite.yaml files found recursively under "
            "ASSAY_SUITE_CONFIG_SEARCH_PATH (os.pathsep-separated) plus the "
            "built-in config directory; every match runs — several matches "
            "run as one multi-suite session. Default: the application's "
            "default suite (simple-maccity-suite.yaml for cece)."
        ),
    )
    group.addoption(
        "--combo-output-root",
        default=None,
        help=(
            "Root artifact directory; relative paths resolve against the "
            "application checkout (its container mount under docker). Default: "
            "a pytest-managed temporary directory."
        ),
    )
    group.addoption(
        "--combo-clean-root",
        action="store_true",
        help=(
            "Remove an existing output root before running (default: existing root "
            "is an error). Only a previous harness root — one with run.yaml — is removed."
        ),
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
        "--publish-baselines",
        action="store_true",
        help=(
            "At session end, publish every compared combination whose driver "
            "run passed (and no test but the old comparison failed) as a new "
            "baseline under ASSAY_BASELINE_STORE, and repoint the suite files "
            "at the new ULIDs. Needs the AWS CLI and credentials it can find."
        ),
    )
    group.addoption(
        "--no-suite-update",
        action="store_true",
        help="With --publish-baselines: publish, but leave the suite files alone.",
    )


def _root_dir_source(app: Application) -> str:
    return f"set {app.env_prefix}ROOT_DIR"


def pytest_sessionstart(session: pytest.Session) -> None:
    config = session.config
    # Init kwargs beat env vars in pydantic-settings, so the flag wins over
    # ASSAY_APPLICATION here (Settings is frozen).
    application_option = config.getoption("--application")
    settings = (
        Settings()
        if application_option is None
        else Settings(application=application_option)
    )
    configure_logging(settings.log_level)
    if config.getoption("--no-suite-update") and not config.getoption(
        "--publish-baselines"
    ):
        raise pytest.UsageError(
            "--no-suite-update has no effect without --publish-baselines"
        )

    # Selection, application, suite loading: the same sequence the CLI's
    # fetch runs (selection.resolve_suites); every failure is a usage error.
    try:
        resolved = resolve_suites(settings, config.getoption("--suite-config"))
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    app, app_settings = resolved.application, resolved.app_settings

    # Sessions always run against a checked-out application when a root is
    # configured: an unresolvable commit SHA is a fatal misconfiguration,
    # surfaced here before any work. No configured root records null.
    try:
        config.stash[_APPLICATION_COMMIT] = app_settings.commit_sha()
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    config.stash[_HARNESS_VERSION] = harness_version()
    config.stash[_HARNESS_COMMIT] = harness_commit()

    # Every match runs: one suite is a single-suite session, several a
    # multi-suite session over the same flat output root.
    contexts: list[SuiteContext] = []
    for suite_path, suite in resolved.suites:
        # Selector validation happens here, against the loaded base config,
        # before any container runs.
        base_config = app.config_model.from_yaml(suite.config_path)
        try:
            combos = enumerate_combos(app.dimensions(suite.sweep, base_config))
            baselines = resolve_baseline_comparisons(
                app, suite.baseline_comparisons, combos
            )
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from exc
        contexts.append(
            SuiteContext(
                path=suite_path, suite=suite, combos=combos, baselines=baselines
            )
        )

    # One ULID per test run, generated at runtime only — never configuration.
    run_id = str(ULID())
    logger.info(
        "starting run %s (application %s; %s suite(s): %s)",
        run_id,
        app.name,
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
        if app_settings.root_dir is None:
            raise pytest.UsageError(
                "--combo-output-root resolves against the application checkout; "
                + _root_dir_source(app)
            )
        try:
            host_root, driver_root = resolve_output_roots(
                option,
                app_settings.root_dir,
                settings.runtime,
                container_workdir=app.container_workdir,
            )
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from exc
        if host_root.exists():
            if not config.getoption("--combo-clean-root"):
                raise pytest.UsageError(
                    f"output root {host_root} already exists; move it aside or pass --combo-clean-root"
                )
            # Only a previous harness output root (run.yaml at its top) is
            # ever removed: an absolute output root under native/slurm can
            # point anywhere, and --combo-clean-root must not be a way to
            # delete an arbitrary directory.
            if not (host_root / "run.yaml").is_file():
                raise pytest.UsageError(
                    f"output root {host_root} exists but is not a previous harness "
                    "output root (no run.yaml); refusing --combo-clean-root — move it aside"
                )
            shutil.rmtree(host_root)
        roots = ComboRoots(host=host_root, driver=driver_root, needs_mount=False)

    config.stash[_SETTINGS] = settings
    config.stash[_APPLICATION] = app
    config.stash[_APP_SETTINGS] = app_settings
    config.stash[_SUITE_CONTEXTS] = contexts
    config.stash[_EXPLICIT_ROOTS] = roots
    # test-report.csv rows, keyed by nodeid in execution order; filled by the
    # pytest_runtest_makereport wrapper below.
    config.stash[_REPORT_ROWS] = {}


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Fail fast when driver execution is coming but no application root is
    configured. Collection time (not sessionstart) so harness-only runs —
    which collect no driver-executing tests — need no environment; still
    before any test executes."""
    if config.getoption("--dry-run"):
        return
    needs_root = any(
        "driver_run" in getattr(item, "fixturenames", ()) for item in items
    )
    if not needs_root:
        return
    app = config.stash[_APPLICATION]
    app_settings = config.stash[_APP_SETTINGS]
    if app_settings.root_dir is None:
        raise pytest.UsageError(
            f"driver execution requires the {app.name} checkout root; "
            + _root_dir_source(app)
        )
    if not app_settings.root_dir.is_dir():
        raise pytest.UsageError(
            f"{app.name} root {app_settings.root_dir} is not an existing directory; "
            f"check {app.env_prefix}ROOT_DIR"
        )
    # The suites' declared inputs must be present: the session never
    # downloads (that is the CLI's `fetch`), it fails fast and says how.
    data_dir = app.data_dir(app_settings.root_dir)
    missing: list[Path] = []
    for context in config.stash[_SUITE_CONTEXTS]:
        for path in missing_inputs(context.suite.inputs, data_dir):
            if path not in missing:
                missing.append(path)
    if missing:
        selector = config.getoption("--suite-config") or app.default_suite
        raise pytest.UsageError(
            "declared inputs are missing: "
            + ", ".join(str(path) for path in missing)
            + f"; stage them with: uv run ufs-chem-assay fetch --suite-config={selector}"
        )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
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
            application=item.config.stash[_APPLICATION].name,
            suite=context.suite.name,
            combo_id=combo.combo_id,
            combo=combo.name,
            result="passed",
        )
        rows[item.nodeid] = row
    row.result = worst_result(row.result, report.outcome)
    return report


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Session-end artifact pipeline, in order: test report -> stats concat ->
    per-suite overview plots -> comparison-stats concat -> per-suite bias
    plots. The output root is flat across suites; concatenated CSVs carry
    application and suite columns, and each suite's plots render from its
    own slice so color scales never mix suites. Bias plotting is independent
    of the overview plotting/stats gates: its scale derives from the
    comparison CSV and it is governed per comparison entry."""
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

    # Publication right after the report is on disk and before the plots (a
    # plotting failure must not cost a publish). Never raises into pytest:
    # the session's exit status is the tests' verdict.
    if config.getoption("--publish-baselines"):
        settings = config.stash[_SETTINGS]
        if not settings.enable_baseline_comparisons:
            logger.info(
                "baseline comparisons are disabled; publishing anyway (the gate "
                "saw no comparison test)"
            )
        try:
            publish_session(
                roots.host,
                [
                    SuiteInput(
                        path=context.path,
                        suite=context.suite,
                        baselines=context.baselines,
                        combo_ids={
                            combo.name: combo.combo_id for combo in context.combos
                        },
                    )
                    for context in contexts
                ],
                list(report_rows.values()),
                store=settings.baseline_store,
                baseline_root=settings.baseline_root_dir,
                suite_update=not config.getoption("--no-suite-update"),
            )
        except Exception as exc:  # the hook must not fail the session
            logger.error("publishing baselines failed: %s", exc)

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


@pytest.fixture(scope="session")
def settings(request: pytest.FixtureRequest) -> Settings:
    return request.config.stash[_SETTINGS]


@pytest.fixture(scope="session")
def application(request: pytest.FixtureRequest) -> Application:
    """The session's application adapter."""
    return request.config.stash[_APPLICATION]


@pytest.fixture(scope="session")
def app_settings(request: pytest.FixtureRequest) -> ApplicationSettings:
    """The session application's settings (checkout, image, driver)."""
    return request.config.stash[_APP_SETTINGS]


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
        application=request.config.stash[_APPLICATION].name,
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
        # Default: all test-generated data goes to a pytest temp directory —
        # bind-mounted into the container at a fixed path under docker, seen
        # directly by the driver natively.
        host = tmp_path_factory.mktemp("combo_runs")
        if request.config.stash[_SETTINGS].runtime is Runtime.DOCKER:
            roots = ComboRoots(host=host, driver=_CONTAINER_TMP_ROOT, needs_mount=True)
        else:  # native / slurm: the driver sees the host filesystem
            roots = ComboRoots(host=host, driver=PurePosixPath(host), needs_mount=False)
    # Stash the realized roots for pytest_sessionfinish (hooks cannot request
    # fixtures; the tmp-default root only exists once this fixture has run).
    request.config.stash[_REALIZED_ROOTS] = roots

    # Write the run manifest as soon as the root is realized, so even a run
    # that dies mid-way records what ran (combos.csv follows immediately
    # after config generation — its values come from the generated configs).
    roots.host.mkdir(parents=True, exist_ok=True)
    settings = request.config.stash[_SETTINGS]
    app = request.config.stash[_APPLICATION]
    root_dir = request.config.stash[_APP_SETTINGS].root_dir
    # Inputs as found at session start — presence and size, never a digest
    # (hashing is fetch's job). Without a checkout the path is recorded
    # relative to the data directory the inputs would be staged in.
    data_dir = (
        app.data_dir(root_dir) if root_dir is not None else Path(app.data_dirname)
    )
    inputs: list[RecordedInput] = []
    for context in request.config.stash[_SUITE_CONTEXTS]:
        for entry in context.suite.inputs:
            path = data_dir / entry.dst
            inputs.append(
                RecordedInput(
                    suite=context.suite.name,
                    url=entry.url,
                    path=path,
                    sha256=entry.sha256,
                    bytes=path.stat().st_size if path.is_file() else None,
                )
            )
    manifest = RunManifest(
        run_id=request.config.stash[_RUN_ID],
        application=app.name,
        application_commit=request.config.stash[_APPLICATION_COMMIT],
        harness_version=request.config.stash[_HARNESS_VERSION],
        harness_commit=request.config.stash[_HARNESS_COMMIT],
        platform=settings.platform,
        runtime=settings.runtime,
        modulefile=request.config.stash[_APP_SETTINGS].modulefile,
        suites=[context.suite for context in request.config.stash[_SUITE_CONTEXTS]],
        inputs=inputs,
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
    entries: list[tuple[str, Combo, list[ParameterRow]]] = []
    settings = request.config.stash[_SETTINGS]
    app = request.config.stash[_APPLICATION]
    app_settings = request.config.stash[_APP_SETTINGS]
    for context in request.config.stash[_SUITE_CONTEXTS]:
        for combo in context.combos:
            # Storage carries no semantics: directories and filenames are the
            # combo's runtime ULID; combos.csv dereferences them.
            combo_dir = combo_roots.host / combo.combo_id
            combo_dir.mkdir(parents=True)
            driver_dir = combo_roots.driver / combo.combo_id
            config = app.build_config(
                combo,
                output_directory=str(driver_dir),
                config_path=context.suite.config_path,
            )
            # The adapter writes the generated configuration (one file for
            # CECE; a directory for an application whose configuration is
            # one) and names the driver's argument.
            driver_yaml = driver_dir / app.write_config(
                config, combo_dir, combo.combo_id
            )
            if settings.runtime is Runtime.SLURM and app_settings.root_dir is not None:
                # The job script is a recorded artifact like the yaml: written
                # up front (dry runs included), rewritten identically before
                # submission. A checkout-less dry run has no job to describe
                # (nothing to --chdir into) and records none.
                write_job_script(
                    settings,
                    app_settings,
                    driver_yaml,
                    combo_dir / f"{combo.combo_id}.out",
                    timeout_s=min(context.suite.timeout_s, settings.run_timeout_s),
                )
            generated[combo.combo_id] = GeneratedCombo(
                host_dir=combo_dir, driver_yaml=driver_yaml, config=config
            )
            entries.append(
                (context.suite.name, combo, app.effective_parameters(combo, config))
            )
    write_combos_csv(
        entries,
        run_id=request.config.stash[_RUN_ID],
        application=app.name,
        csv_path=combo_roots.host / "combos.csv",
    )
    return generated


@pytest.fixture(scope="session")
def driver_run(
    request: pytest.FixtureRequest,
    combo_roots: ComboRoots,
    generated_combos: dict[str, GeneratedCombo],
    settings: Settings,
    application: Application,
    app_settings: ApplicationSettings,
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
            application,
            app_settings,
            driver_yaml=generated.driver_yaml,
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
