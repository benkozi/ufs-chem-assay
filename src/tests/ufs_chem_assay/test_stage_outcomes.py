"""The stage outcome record: `run` writes what each script it ran (or
skipped) came to into <root_dir>/scripts/stages.yaml, and `report` and
`watch` read it — so a failed or skipped source, build or data stage is in
the report, a missing test run says why, and a test result older than the
latest build says so."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

import apptests
from cli.main import main
from cli.stage_outcomes import StageState, newest_stage_log, read_stage_record
from jobs import TestProvenance
from logs import LOGGER_NAME
from platforms import Platform, Runtime
from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR

_CLI_LOGGER = f"{LOGGER_NAME}.cli"
_KOKKOS = TEMPLATES_DIR / "ursa-kokkos.yaml"
_MUSICA_BUILD = "02-build-catchem-oneapi-musica.sh"
_PROVENANCE = TestProvenance(
    platform=Platform.URSA,
    runtime=Runtime.SLURM,
    modulefile="ufs_ursa.oneapi",
    build_type="Debug",
    cmake_args=None,
    application_commit=None,
    harness_version="0.1.0",
    harness_commit=None,
)


def _run(
    root: Path,
    mocker: MockerFixture,
    failing: dict[str, int],
    *args: str,
) -> int:
    """`run` of the Ursa Kokkos template's oneapi toolchain with bash
    stubbed: each script writes a log as run_bash would, and the named
    scripts fail with the given exit code."""

    def bash(path: Path, logs_dir: Path, **_kw: object) -> int:
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")  # run_bash's
        (logs_dir / f"{path.stem}-{stamp}.log").write_text(
            f">>> {path.name}\nconfiguring\nlast line of {path.stem}\n"
        )
        return failing.get(path.name, 0)

    mocker.patch("cli.commands.run.run_bash", side_effect=bash)
    return main(
        [
            "run",
            f"--config-file={_KOKKOS}",
            f"--root-dir={root}",
            "--application=all",
            "--toolchain=oneapi",
            *args,
        ]
    )


def _report(
    root: Path, caplog: pytest.LogCaptureFixture, *args: str
) -> tuple[int, list[str]]:
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["report", f"--root-dir={root}", *args])
    return code, [r.getMessage() for r in caplog.records if r.name == _CLI_LOGGER]


# ── the record ───────────────────────────────────────────────────────────────


def test_run_records_every_script_it_ran_or_skipped(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    root = tmp_path / "root"
    before = datetime.now(UTC)
    code = _run(root, mocker, {_MUSICA_BUILD: 2}, "--stage=build", "--stage=test")
    assert code == 2
    record = read_stage_record(root / "scripts")
    assert set(record.outcomes) == {
        "02-build-cece-oneapi.sh",
        "02-build-catchem-oneapi-compute.sh",
        _MUSICA_BUILD,
        "02-build-kokkos-smoke-oneapi.sh",
        "04-test-cece-oneapi.sh",
        "04-test-catchem-oneapi-compute.sh",
        "04-test-catchem-oneapi-musica.sh",
        "04-test-kokkos-smoke-oneapi.sh",
    }
    failed = record.outcomes[_MUSICA_BUILD]
    assert failed.state is StageState.FAILED
    assert failed.exit_status == 2
    assert (failed.application, failed.toolchain, failed.build) == (
        "catchem",
        "oneapi",
        "musica",
    )
    assert failed.stage.value == "build"
    assert failed.log is not None
    assert failed.log.parent == root / "logs"
    assert failed.log.name.startswith("02-build-catchem-oneapi-musica-")
    assert before <= failed.started_at <= failed.finished_at <= datetime.now(UTC)
    skipped = record.outcomes["04-test-catchem-oneapi-musica.sh"]
    assert skipped.state is StageState.SKIPPED
    assert skipped.exit_status is None
    assert skipped.cause == _MUSICA_BUILD
    assert skipped.log is None
    passed = record.outcomes["04-test-cece-oneapi.sh"]
    assert (passed.state, passed.exit_status, passed.build) == (
        StageState.PASSED,
        0,
        None,
    )
    # One run id for everything this run did.
    assert len({o.run_id for o in record.outcomes.values()}) == 1


def test_a_dry_run_records_nothing(tmp_path: Path, mocker: MockerFixture) -> None:
    root = tmp_path / "root"
    assert _run(root, mocker, {}, "--stage=build", "--dry-run") == 0
    assert not (root / "scripts" / "stages.yaml").exists()
    assert read_stage_record(root / "scripts").outcomes == {}


def test_a_later_run_replaces_only_what_it_ran(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    root = tmp_path / "root"
    _run(root, mocker, {_MUSICA_BUILD: 2}, "--stage=build")
    first = read_stage_record(root / "scripts").outcomes
    _run(root, mocker, {}, "--stage=build", "--build=musica")
    second = read_stage_record(root / "scripts").outcomes
    assert second[_MUSICA_BUILD].state is StageState.PASSED
    assert second[_MUSICA_BUILD].run_id != first[_MUSICA_BUILD].run_id
    # The compute build was not run again: its outcome stays the first run's.
    compute = "02-build-catchem-oneapi-compute.sh"
    assert second[compute] == first[compute]
    text = yaml.safe_load((root / "scripts" / "stages.yaml").read_text())
    assert list(text) == ["outcomes"]


# ── the report ───────────────────────────────────────────────────────────────


def test_the_report_names_a_failed_build_and_the_test_run_it_cost(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    _run(
        root,
        mocker,
        {_MUSICA_BUILD: 2},
        "--stage=source",
        "--stage=build",
        "--stage=test",
    )
    code, lines = _report(root, caplog, "--only", "tests")
    assert code == 1
    text = "\n".join(lines)
    # One stage line per source and build of the selected lanes, --only tests or not.
    assert any(
        line.startswith("build catchem/oneapi/musica") and "failed (exit 2)" in line
        for line in lines
    ), text
    assert any(
        line.startswith("build catchem/oneapi/compute") and "passed" in line
        for line in lines
    ), text
    assert any(
        line.startswith("source catchem/oneapi ") and "passed" in line for line in lines
    )
    assert "[stage-failed] 02-build-catchem-oneapi-musica.sh failed (exit 2)" in text
    assert "last line of 02-build-catchem-oneapi-musica" in text  # the log's tail
    assert f"see: {root}/logs/02-build-catchem-oneapi-musica-" in text
    # The test run the failed build cost says why it is missing.
    assert any(line.startswith("tests catchem/oneapi/musica") for line in lines), text
    assert "no test run: 02-build-catchem-oneapi-musica.sh failed (exit 2)" in text
    assert "report: " in lines[-1]
    assert lines[-1].endswith("exit 1")


def test_a_clean_run_reports_its_stages_without_issues(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    _run(root, mocker, {}, "--stage=build", "--application=cece")
    code, lines = _report(root, caplog, "--only", "tests", "--application", "cece")
    assert any(
        line.startswith("build cece/oneapi") and "passed" in line for line in lines
    )
    assert not any("[stage-" in line for line in lines)
    # The test stage was not part of the run, so no test run is reported as
    # missing: a clean build is a clean report.
    assert not any(line.startswith("tests ") for line in lines)
    assert code == 0


def test_a_skipped_build_is_reported_with_its_cause(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    _run(
        root,
        mocker,
        {"01-source-catchem-oneapi.sh": 128},
        "--stage=source",
        "--stage=build",
    )
    _, lines = _report(root, caplog)
    text = "\n".join(lines)
    assert "[stage-failed] 01-source-catchem-oneapi.sh failed (exit 128)" in text
    assert (
        "[stage-skipped] 02-build-catchem-oneapi-compute.sh was skipped: "
        "01-source-catchem-oneapi.sh failed (exit 128)"
    ) in text


def test_a_result_older_than_the_latest_build_is_stale(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # A test run recorded first; then a build of the same lane fails: the
    # report must not present the old result as this build's.
    root = tmp_path / "root"
    tests_root = root / "oneapi" / "tests" / "catchem" / "compute"
    apptests.record_local(
        tests_root,
        application="catchem",
        toolchain="oneapi",
        build="compute",
        provenance=_PROVENANCE,
    )
    compute = "02-build-catchem-oneapi-compute.sh"
    _run(root, mocker, {compute: 2}, "--stage=build")
    _, lines = _report(root, caplog, "--only", "tests", "--build", "compute")
    text = "\n".join(lines)
    assert "[tests-stale]" in text
    assert (
        "this test run predates the latest build (02-build-catchem-oneapi-compute.sh, "
        "failed"
    ) in text


def test_a_result_newer_than_the_build_is_not_stale(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    _run(root, mocker, {}, "--stage=build", "--build=compute")
    tests_root = root / "oneapi" / "tests" / "catchem" / "compute"
    apptests.record_local(
        tests_root,
        application="catchem",
        toolchain="oneapi",
        build="compute",
        provenance=_PROVENANCE,
    )
    _, lines = _report(root, caplog, "--only", "tests", "--build", "compute")
    assert not any("[tests-stale]" in line for line in lines)


def test_the_record_is_read_through_the_report_selection(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    _run(root, mocker, {_MUSICA_BUILD: 2}, "--stage=build")
    _, lines = _report(root, caplog, "--application", "cece")
    assert not any("catchem" in line for line in lines)
    _, lines = _report(root, caplog, "--build", "compute")
    assert not any("catchem/oneapi/musica" in line for line in lines)


def test_watch_ends_with_the_same_stage_lines(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # The compute build fails, and the compute test run (from before) is
    # evaluated, so watch has nothing to wait for and ends with its report.
    root = tmp_path / "root"
    compute = "02-build-catchem-oneapi-compute.sh"
    _run(root, mocker, {compute: 2}, "--stage=build")
    tests_root = root / "oneapi" / "tests" / "catchem" / "compute"
    apptests.record_local(
        tests_root,
        application="catchem",
        toolchain="oneapi",
        build="compute",
        provenance=_PROVENANCE,
    )
    (tests_root / "tests.out").write_text(
        "tic: 2026-11-20T12:00:00Z\ntoc: 2026-11-20T12:00:01Z\n"
    )
    apptests.evaluate(tests_root, exit_status=0)
    mocker.patch("cli.sessions.queue_states", return_value={})
    mocker.patch("cli.commands.watch.time.sleep")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        main(
            [
                "watch",
                f"--root-dir={root}",
                "--only",
                "tests",
                "--application",
                "catchem",
                "--build",
                "compute",
            ]
        )
    text = "\n".join(r.getMessage() for r in caplog.records if r.name == _CLI_LOGGER)
    assert "[stage-failed] 02-build-catchem-oneapi-compute.sh failed (exit 2)" in text


def test_the_stage_times_are_aware_and_ordered(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    root = tmp_path / "root"
    _run(root, mocker, {}, "--stage=build")
    outcome = read_stage_record(root / "scripts").outcomes["02-build-cece-oneapi.sh"]
    assert outcome.started_at.tzinfo is not None
    assert outcome.finished_at - outcome.started_at < timedelta(minutes=1)


def test_a_failed_test_stage_after_the_result_makes_it_stale(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # Built, then tested (the ledger), then the test stage alone fails to
    # submit again: the result on show is the earlier submission's.
    root = tmp_path / "root"
    _run(root, mocker, {}, "--stage=build", "--build=compute")
    tests_root = root / "oneapi" / "tests" / "catchem" / "compute"
    apptests.record_local(
        tests_root,
        application="catchem",
        toolchain="oneapi",
        build="compute",
        provenance=_PROVENANCE,
    )
    _run(
        root,
        mocker,
        {"04-test-catchem-oneapi-compute.sh": 1},
        "--stage=test",
        "--build=compute",
    )
    _, lines = _report(root, caplog, "--only", "tests", "--build", "compute")
    text = "\n".join(lines)
    assert (
        "this test run predates the latest test stage "
        "(04-test-catchem-oneapi-compute.sh, failed (exit 1)"
    ) in text


def test_an_unreadable_record_is_an_error_naming_it(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "root"
    _run(root, mocker, {}, "--stage=build", "--dry-run")  # the effective config
    record = root / "scripts" / "stages.yaml"
    record.write_text("outcomes: [not, a, map]\n")
    with pytest.raises(ValueError, match=r"stages\.yaml: not a stage record"):
        read_stage_record(root / "scripts")
    code, lines = _report(root, caplog)
    assert code == 1
    assert any("not a stage record" in line for line in lines)


def test_a_root_without_logs_has_no_stage_log(tmp_path: Path) -> None:
    # run always creates logs/ now (its transcript); an older or hand-made
    # root may still have none, and report reads it all the same.
    assert newest_stage_log(tmp_path / "logs", "02-build-cece") is None
