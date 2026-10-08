"""`ufs-chem-assay run`: rendering, stage and application selection,
overrides, dry-run, the effective-config artifact — through the CLI's
main() in-process and through `python -m cli` once. The CLI speaks through
the harness logger (`ufs-chem-assay.cli`), never print."""

import logging
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from applications.base import Application
from applications.registry import REGISTRY
from cli.main import main
from logs import LOGGER_NAME
from s3_sync import S3SyncConfig
from staging import Action, StagedInput
from tests.ufs_chem_assay.baseline_runs import Run, fabricate_run, fake_sync
from tests.ufs_chem_assay.stubs import stub_application
from tests.ufs_chem_assay.run_configs import run_config_file

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLI_LOGGER = f"{LOGGER_NAME}.cli"
_CECE_SCRIPTS = [
    "01-source-cece.sh",
    "02-build-cece.sh",
    "03-data-cece.sh",
    "05-harness-cece.sh",
]


def _config(
    tmp_path: Path,
    template: str = "ursa.yaml",
    overrides: dict[str, object] | None = None,
) -> Path:
    return run_config_file(
        tmp_path, template, overrides=overrides, root_dir=tmp_path / "root"
    )


def _scripts(tmp_path: Path) -> list[str]:
    return sorted(
        p.name for p in (tmp_path / "root" / "scripts").iterdir() if p.suffix == ".sh"
    )


@pytest.fixture()
def stub(monkeypatch: pytest.MonkeyPatch) -> Application:
    app = stub_application()
    monkeypatch.setitem(REGISTRY, "stub", app)
    return app


def test_dry_run_renders_every_script_and_executes_nothing(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["run", f"--config-file={_config(tmp_path)}", "--dry-run"])
    assert code == 0
    assert _scripts(tmp_path) == _CECE_SCRIPTS
    run_bash.assert_not_called()
    messages = [record.getMessage() for record in caplog.records]
    assert any("dry run" in m for m in messages)
    # Nothing but scripts/ under the root; the effective config beside them.
    assert sorted(p.name for p in (tmp_path / "root").iterdir()) == ["scripts"]
    assert (tmp_path / "root" / "scripts" / "run-config.yaml").is_file()


def test_effective_config_records_overrides(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(
            [
                "run",
                f"--config-file={_config(tmp_path)}",
                "--dry-run",
                "-o",
                "applications:cece:ref=develop",
                "slurm:qos=batch",
                "--override",
                "harness:suite_config=ex3-suite.yaml",
            ]
        )
    assert code == 0
    effective = yaml.safe_load(
        (tmp_path / "root" / "scripts" / "run-config.yaml").read_text()
    )
    assert effective["applications"]["cece"]["ref"] == "develop"
    assert effective["slurm"]["qos"] == "batch"
    assert effective["harness"]["suite_config"] == "ex3-suite.yaml"
    assert effective["root_dir"] == str(tmp_path / "root")
    harness = (tmp_path / "root" / "scripts" / "05-harness-cece.sh").read_text()
    assert "--suite-config=ex3-suite.yaml" in harness
    assert "-q batch" in harness
    messages = [record.getMessage() for record in caplog.records]
    assert any("overrides:" in m and "slurm:qos=batch" in m for m in messages)


def test_bad_override_is_a_clean_error(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(
            ["run", f"--config-file={_config(tmp_path)}", "--dry-run", "-o", "nonsense"]
        )
    assert code == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "nonsense" in errors[0]


def test_stage_selection_renders_only_those(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    code = main(
        [
            "run",
            f"--config-file={_config(tmp_path)}",
            "--stage",
            "build",
            "--stage",
            "harness",
            "--dry-run",
        ]
    )
    assert code == 0
    assert _scripts(tmp_path) == ["02-build-cece.sh", "05-harness-cece.sh"]


def test_all_stages_run_with_bash_in_order(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash", return_value=0)
    code = main(["run", f"--config-file={_config(tmp_path)}"])
    assert code == 0
    ran = [call.args[0].name for call in run_bash.call_args_list]
    assert ran == _CECE_SCRIPTS


def test_failed_stage_stops_the_run_and_logs_at_error(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash", side_effect=[0, 2])
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["run", f"--config-file={_config(tmp_path)}"])
    assert code == 2
    assert run_bash.call_count == 2
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "02-build-cece" in errors[0].getMessage()


def test_local_config_runs_the_same_stages(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash", return_value=0)
    code = main(["run", f"--config-file={_config(tmp_path, 'local.yaml')}"])
    assert code == 0
    ran = [call.args[0].name for call in run_bash.call_args_list]
    assert ran == _CECE_SCRIPTS


def test_harness_keeps_slot_05_without_a_04(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    # Slot 04 is held for the application-tests stage of issue #9.
    mocker.patch("cli.main.run_bash")
    assert main(["run", f"--config-file={_config(tmp_path)}", "--dry-run"]) == 0
    assert _scripts(tmp_path) == _CECE_SCRIPTS


def test_platform_flag_overrides_file(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch("cli.main.run_bash")
    path = _config(tmp_path)
    assert (
        main(["run", f"--config-file={path}", "--platform", "local", "--dry-run"]) == 0
    )
    # The exports follow the overridden platform (local -> docker runtime).
    harness = tmp_path / "root" / "scripts" / "05-harness-cece.sh"
    assert "export ASSAY_RUNTIME=docker" in harness.read_text()


def test_python_m_cli_entrypoint(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli",
            "run",
            f"--config-file={_config(tmp_path)}",
            "--dry-run",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(_REPO_ROOT / "src")},
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "root" / "scripts" / "01-source-cece.sh").is_file()


def test_bad_stage_name_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["run", f"--config-file={_config(tmp_path)}", "--stage", "bogus"])
    assert excinfo.value.code == 2


def test_uncreatable_root_dir_is_a_clean_error(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    """A bad root_dir must not end in a traceback: one ERROR line naming
    the root, exit 1, nothing executed."""
    mocker.patch("cli.main.run_bash")
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    path = run_config_file(tmp_path, root_dir=blocker / "root")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["run", f"--config-file={path}", "--dry-run"])
    assert code == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and str(blocker / "root") in errors[0]
    assert "root_dir" in errors[0]


def test_root_dir_flag_overrides_the_derived_root(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    config = run_config_file(tmp_path)  # no root_dir in the template
    root = tmp_path / "elsewhere"
    code = main(["run", f"--config-file={config}", f"--root-dir={root}", "--dry-run"])
    assert code == 0
    harness = root / "scripts" / "05-harness-cece.sh"
    assert harness.is_file()
    assert f"export CECE_ROOT_DIR={root}/CECE" in harness.read_text()


# ── Several applications in one run ──────────────────────────────────────────


@pytest.mark.usefixtures("stub")
def test_comprehensive_run_renders_stage_major_per_application(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    run_bash = mocker.patch("cli.main.run_bash", return_value=0)
    path = _config(
        tmp_path, overrides={"applications.stub": {"git_url": "u", "ref": "main"}}
    )
    assert main(["run", f"--config-file={path}"]) == 0
    ran = [call.args[0].name for call in run_bash.call_args_list]
    assert ran == [
        "01-source-cece.sh",
        "01-source-stub.sh",
        "02-build-cece.sh",
        "02-build-stub.sh",
        "03-data-cece.sh",
        "03-data-stub.sh",
        "05-harness-cece.sh",
        "05-harness-stub.sh",
    ]
    scripts = tmp_path / "root" / "scripts"
    assert "natural_earth" in (scripts / "03-data-cece.sh").read_text()
    assert "natural_earth" not in (scripts / "03-data-stub.sh").read_text()
    assert (
        "--combo-output-root=ufs-chem-assay-output/cece"
        in (scripts / "05-harness-cece.sh").read_text()
    )


@pytest.mark.usefixtures("stub")
def test_application_flag_narrows_a_comprehensive_run(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    path = _config(
        tmp_path, overrides={"applications.stub": {"git_url": "u", "ref": "main"}}
    )
    assert (
        main(["run", f"--config-file={path}", "--application", "stub", "--dry-run"])
        == 0
    )
    assert _scripts(tmp_path) == [
        "01-source-stub.sh",
        "02-build-stub.sh",
        "03-data-stub.sh",
        "05-harness-stub.sh",
    ]


def test_unconfigured_application_is_a_clean_error(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("cli.main.run_bash")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(
            [
                "run",
                f"--config-file={_config(tmp_path)}",
                "--application",
                "catchem",
                "--dry-run",
            ]
        )
    assert code == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "catchem" in errors[0] and "['cece']" in errors[0]


# ── `ufs-chem-assay fetch` ───────────────────────────────────────────────────

_MACCITY_URL = "s3://geos-chem/HEMCO/MACCITY/v2014-07/MACCity_4x5.nc"
_MACCITY_SHA = "ef70975af53499ab45f620a778043ff6fd29ec4e7e2777f3fd1f2db59a12aab9"


@pytest.fixture()
def fetch_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cece_config_path: Path
) -> Path:
    """A neutral cwd (the repo .env out of scope), a fabricated checkout as
    CECE_ROOT_DIR, and a suite root with two suites declaring inputs."""
    monkeypatch.chdir(tmp_path)
    for key in ("CECE_ROOT_DIR", "ASSAY_SUITE_CONFIG_SEARCH_PATH", "ASSAY_APPLICATION"):
        monkeypatch.delenv(key, raising=False)
    checkout = tmp_path / "checkout"
    (checkout / "data").mkdir(parents=True)
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "one-suite.yaml").write_text(
        f"name: one\nconfig_path: {cece_config_path}\ntimeout_s: 5\ninputs:\n"
        f"  - url: {_MACCITY_URL}\n    public: true\n    dst: MACCity_4x5.nc\n    sha256: {_MACCITY_SHA}\n"
    )
    (suites / "two-suite.yaml").write_text(
        f"name: two\nconfig_path: {cece_config_path}\ntimeout_s: 5\ninputs:\n"
        f"  - url: {_MACCITY_URL}\n    public: true\n    dst: MACCity_4x5.nc\n    sha256: {_MACCITY_SHA}\n"
        "  - url: s3://ufs-chem/private/extra.nc\n    dst: extra.nc\n"
    )
    monkeypatch.setenv("CECE_ROOT_DIR", str(checkout))
    monkeypatch.setenv("ASSAY_SUITE_CONFIG_SEARCH_PATH", str(suites))
    return checkout


def _staged(url: str, path: Path, action: Action) -> StagedInput:
    return StagedInput(url=url, path=path, action=action, detail="t", sha256=None)


def test_fetch_stages_the_merged_inputs_of_the_selected_suites(
    fetch_env: Path, mocker: MockerFixture
) -> None:
    stage = mocker.patch(
        "cli.main.stage_inputs",
        side_effect=lambda inputs, data_dir, **_: [
            _staged(i.url, data_dir / i.dst, "skipped") for i in inputs
        ],
    )
    code = main(["fetch", "--application=cece", r"--suite-config=.*-suite\.yaml"])
    assert code == 0
    stage.assert_called_once()
    inputs, data_dir = stage.call_args.args
    assert data_dir == fetch_env / "data"
    assert stage.call_args.kwargs == {"dry_run": False}
    # The shared file once, the private extra once: merged across both suites.
    assert [(str(i.dst), i.public) for i in inputs] == [
        ("MACCity_4x5.nc", True),
        ("extra.nc", False),
    ]


def test_fetch_default_selects_the_applications_default_suite(
    fetch_env: Path, mocker: MockerFixture
) -> None:
    stage = mocker.patch("cli.main.stage_inputs", return_value=[])
    assert main(["fetch"]) == 0
    inputs, data_dir = stage.call_args.args
    assert data_dir == fetch_env / "data"
    # The checked-in simple-maccity suite's own declaration.
    assert [str(i.dst) for i in inputs] == ["MACCity_4x5.nc"]


def test_fetch_exit_code_follows_the_results(
    fetch_env: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch(
        "cli.main.stage_inputs",
        return_value=[
            _staged("s3://bkt/a.nc", fetch_env / "data" / "a.nc", "downloaded"),
            _staged("s3://bkt/b.nc", fetch_env / "data" / "b.nc", "failed"),
        ],
    )
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["fetch", "--suite-config=one-suite.yaml"])
    assert code == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("b.nc" in m for m in errors)


@pytest.mark.usefixtures("fetch_env")
def test_fetch_dry_run_passes_through(mocker: MockerFixture) -> None:
    stage = mocker.patch("cli.main.stage_inputs", return_value=[])
    assert main(["fetch", "--suite-config=one-suite.yaml", "--dry-run"]) == 0
    assert stage.call_args.kwargs == {"dry_run": True}


@pytest.mark.usefixtures("fetch_env")
def test_fetch_without_the_application_root_is_a_clean_error(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("CECE_ROOT_DIR")
    stage = mocker.patch("cli.main.stage_inputs")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["fetch", "--suite-config=one-suite.yaml"])
    assert code == 1
    stage.assert_not_called()
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "CECE_ROOT_DIR" in errors[0]


@pytest.mark.usefixtures("fetch_env")
def test_fetch_no_matching_suite_is_a_clean_error(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    stage = mocker.patch("cli.main.stage_inputs")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["fetch", "--suite-config=absent-suite.yaml"])
    assert code == 1
    stage.assert_not_called()
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "matches no suite" in errors[0]


def test_fetch_conflicting_inputs_is_a_clean_error(
    fetch_env: Path,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
    cece_config_path: Path,
) -> None:
    (fetch_env.parent / "suites" / "three-suite.yaml").write_text(
        f"name: three\nconfig_path: {cece_config_path}\ntimeout_s: 5\ninputs:\n"
        f"  - url: s3://elsewhere/MACCity_4x5.nc\n    dst: MACCity_4x5.nc\n"
    )
    stage = mocker.patch("cli.main.stage_inputs")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["fetch", r"--suite-config=.*-suite\.yaml"])
    assert code == 1
    stage.assert_not_called()
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "declared differently" in errors[0]


# ── `ufs-chem-assay publish-baselines` ──────────────────────────────────────


@pytest.fixture()
def publish_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suite_dir: Path
) -> Run:
    """A neutral cwd, a fabricated run of a renamed copy of the maccity suite
    (so the selector finds it beside the built-in one), the suite root on
    the search path, no baseline cache, the S3 wrapper mocked."""
    monkeypatch.chdir(tmp_path)
    for key in (
        "CECE_ROOT_DIR",
        "ASSAY_SUITE_CONFIG_SEARCH_PATH",
        "ASSAY_APPLICATION",
        "ASSAY_BASELINE_ROOT_DIR",
        "ASSAY_BASELINE_STORE",
    ):
        monkeypatch.delenv(key, raising=False)
    run = fabricate_run(tmp_path, suite_dir, name="pub-test")
    monkeypatch.setenv("ASSAY_SUITE_CONFIG_SEARCH_PATH", str(run.suite_path.parent))
    return run


def _publish_calls(sync: object) -> list[S3SyncConfig]:
    return [call.args[0] for call in sync.call_args_list]  # type: ignore[attr-defined]


def test_publish_baselines_publishes_the_runs_suites_by_default(
    publish_env: Run, mocker: MockerFixture
) -> None:
    sync = mocker.patch("baselines.sync", side_effect=fake_sync())
    code = main(["publish-baselines", f"--output-root={publish_env.root}"])
    assert code == 0
    uploads = [c for c in _publish_calls(sync) if isinstance(c.source, Path)]
    assert len(uploads) == 3
    assert all(
        str(c.destination).startswith("s3://ufs-chem/baselines/") for c in uploads
    )
    text = publish_env.suite_path.read_text()
    for combo in publish_env.combos:
        assert f"ulid: {combo.combo_id}" in text


def test_publish_baselines_dry_run_store_and_no_suite_update(
    publish_env: Run, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync = mocker.patch("baselines.sync", side_effect=fake_sync())
    original = publish_env.suite_path.read_text()
    code = main(
        [
            "publish-baselines",
            f"--output-root={publish_env.root}",
            "--store=s3://other-bucket/prefix/",
            "--dry-run",
        ]
    )
    assert code == 0
    calls = _publish_calls(sync)
    assert calls and all(c.dry_run for c in calls)
    assert all(
        str(c.destination).startswith("s3://other-bucket/prefix/")
        for c in calls
        if isinstance(c.source, Path)
    )
    assert publish_env.suite_path.read_text() == original
    assert not any(
        (d / "baseline.yaml").exists() for d in publish_env.root.iterdir() if d.is_dir()
    )

    monkeypatch.setenv("ASSAY_BASELINE_STORE", "s3://env-bucket/p")
    code = main(
        ["publish-baselines", f"--output-root={publish_env.root}", "--no-suite-update"]
    )
    assert code == 0
    assert publish_env.suite_path.read_text() == original
    uploads = [
        c for c in _publish_calls(sync) if isinstance(c.source, Path) and not c.dry_run
    ]
    assert len(uploads) == 3
    assert all(str(c.destination).startswith("s3://env-bucket/p/") for c in uploads)


def test_publish_baselines_exit_code_follows_the_records(
    publish_env: Run, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    occupied = frozenset({f"s3://ufs-chem/baselines/{publish_env.combos[1].combo_id}/"})
    mocker.patch("baselines.sync", side_effect=fake_sync(occupied))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        code = main(["publish-baselines", f"--output-root={publish_env.root}"])
    assert code == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("append-only" in m for m in errors)
    assert any(
        "publish summary: 2 published, 1 failed" in r.getMessage()
        for r in caplog.records
    )


def test_publish_baselines_without_run_yaml_is_a_clean_error(
    publish_env: Run, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    sync = mocker.patch("baselines.sync")
    (publish_env.root / "run.yaml").unlink()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["publish-baselines", f"--output-root={publish_env.root}"])
    assert code == 1
    sync.assert_not_called()
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "run.yaml" in errors[0]


def test_publish_baselines_selector_must_cover_the_runs_suites(
    publish_env: Run, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    sync = mocker.patch("baselines.sync")
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(
            [
                "publish-baselines",
                f"--output-root={publish_env.root}",
                "--suite-config=simple-maccity-suite.yaml",
            ]
        )
    assert code == 1
    sync.assert_not_called()
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert (
        len(errors) == 1 and "pub-test" in errors[0] and "--suite-config" in errors[0]
    )


def test_publish_baselines_primes_the_cache_from_the_setting(
    publish_env: Run,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mocker.patch("baselines.sync", side_effect=fake_sync())
    cache = tmp_path / "cache"
    monkeypatch.setenv("ASSAY_BASELINE_ROOT_DIR", str(cache))
    assert main(["publish-baselines", f"--output-root={publish_env.root}"]) == 0
    for combo in publish_env.combos:
        assert (cache / combo.combo_id / "baseline.yaml").is_file()
