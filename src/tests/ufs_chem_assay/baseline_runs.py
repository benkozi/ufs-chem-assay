"""A fabricated finished run for the baseline-publishing tests (the module
and the CLI): the maccity suite copied beside its config into tmp_path (so
the file can be rewritten), run.yaml/combos.csv/test-report.csv as a
session writes them, and one directory per combination holding
NetCDF-named files, a log, and a plot subdirectory. Every comparison
failed (the republish case); everything else passed."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from applications.base import Application
from applications.registry import get_application, load_suite
from baselines import SuiteInput
from combos import Combo, enumerate_combos, write_combos_csv
from comparison import resolve_baseline_comparisons
from models.suite_config import RunManifest, SuiteConfig
from platforms import Platform, Runtime
from report import TestReportRow, write_test_report_csv
from s3_sync import S3SyncConfig, S3SyncResult

RUN_ID = "01JRUNRUNRUNRUNRUNRUNRUNRU"
COMMIT = "36da92e0f02ec9aa05d4261596927527350509b8"
HARNESS_VERSION = "0.1.0-rc.4"
HARNESS_COMMIT = "abc123-dirty"
TESTS = (
    "test_driver_execution",
    "test_nc_file_count",
    "test_nc_filenames",
    "test_species_attributes",
    "test_descriptive_stats",
    "test_baseline_comparison",
)


class Run(BaseModel):
    """One fabricated session: everything publication reads."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    root: Path
    suite_path: Path
    suite: SuiteConfig
    combos: list[Combo]
    manifest: RunManifest
    rows: list[TestReportRow]
    app: Application

    @property
    def suite_input(self) -> SuiteInput:
        return SuiteInput(
            path=self.suite_path,
            suite=self.suite,
            baselines=resolve_baseline_comparisons(
                self.app, self.suite.baseline_comparisons, self.combos
            ),
            combo_ids={combo.name: combo.combo_id for combo in self.combos},
        )

    def combo(self, name: str) -> Combo:
        return next(combo for combo in self.combos if combo.name == name)

    def combo_dir(self, name: str) -> Path:
        return self.root / self.combo(name).combo_id


def report_rows(
    suite: str, combo: Combo, failed: tuple[str, ...] = ()
) -> list[TestReportRow]:
    return [
        TestReportRow(
            pytest_name=f"{test}[{combo.name}]",
            application="cece",
            suite=suite,
            combo_id=combo.combo_id,
            combo=combo.name,
            result="failed" if test in failed else "passed",
        )
        for test in TESTS
    ]


def fabricate_run(
    tmp_path: Path, suite_dir: Path, *, name: str = "simple-maccity"
) -> Run:
    """See the module docstring. `name` renames the copied suite (file and
    `name:` key) so a test can select it beside the built-in one."""
    suites = tmp_path / "suites"
    suites.mkdir()
    shutil.copy(suite_dir / "maccity.yaml", suites / "maccity.yaml")
    suite_path = suites / f"{name}-suite.yaml"
    suite_path.write_text(
        (suite_dir / "simple-maccity-suite.yaml")
        .read_text()
        .replace("name: simple-maccity\n", f"name: {name}\n", 1)
    )
    app = get_application("cece")
    suite = load_suite(suite_path)
    base = app.config_model.from_yaml(suite.config_path)
    combos = enumerate_combos(app.dimensions(suite.sweep, base))
    root = tmp_path / "output"
    root.mkdir()
    manifest = RunManifest(
        run_id=RUN_ID,
        application="cece",
        application_commit=COMMIT,
        harness_version=HARNESS_VERSION,
        harness_commit=HARNESS_COMMIT,
        platform=Platform.LOCAL,
        runtime=Runtime.DOCKER,
        modulefile=None,
        suites=[suite],
    )
    manifest.to_yaml(root / "run.yaml")
    write_combos_csv(
        [
            (
                suite.name,
                combo,
                app.effective_parameters(
                    combo,
                    app.build_config(
                        combo, output_directory=".", config_path=suite.config_path
                    ),
                ),
            )
            for combo in combos
        ],
        run_id=RUN_ID,
        application="cece",
        csv_path=root / "combos.csv",
    )
    rows: list[TestReportRow] = []
    for combo in combos:
        rows += report_rows(suite.name, combo, failed=("test_baseline_comparison",))
        combo_dir = root / combo.combo_id
        (combo_dir / "plots-overview").mkdir(parents=True)
        for hour in (1, 2, 3):
            (combo_dir / f"cece_20100101_0{hour}0000.nc").write_bytes(
                f"nc {combo.name} {hour}".encode()
            )
        (combo_dir / f"{combo.combo_id}.yaml").write_text("driver: config\n")
        (combo_dir / "cece.log").write_text("log\n")
        (combo_dir / "plots-overview" / "co.gif").write_bytes(b"GIF89a")
    write_test_report_csv(rows, root / "test-report.csv")
    return Run(
        root=root,
        suite_path=suite_path,
        suite=suite,
        combos=combos,
        manifest=manifest,
        rows=rows,
        app=app,
    )


def fake_sync(
    occupied: frozenset[str] = frozenset(),
) -> Callable[[S3SyncConfig], S3SyncResult]:
    """aws, as far as publication can tell: a dry-run download of an occupied
    prefix lists one object; everything else is silent and succeeds."""

    def fake(config: S3SyncConfig) -> S3SyncResult:
        output = ""
        if config.dry_run and isinstance(config.source, str):
            if config.source in occupied:
                output = f"(dryrun) download: {config.source}x.nc to ./x.nc\n"
        return S3SyncResult(
            argv=config.argv, returncode=0, output=output, dry_run=config.dry_run
        )

    return fake
