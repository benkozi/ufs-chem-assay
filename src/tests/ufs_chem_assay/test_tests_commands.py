"""`ufs-chem-assay submit-tests` and `evaluate-tests`: the two commands the
rendered test stage calls, driven from the effective config a `run` left
behind. Slurm is mocked; the scripts, ledgers and reports are real files."""

import logging
import subprocess
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from benchmarks import SlurmAccounting, read_test_benchmarks_csv
from cli.main import COMMANDS, Command, main
from jobs import (
    JobRef,
    SlurmSubmissionError,
    path_of_test_ledger,
    read_test_ledger,
)
from logs import LOGGER_NAME
from models.conventions import CONVENTIONS
from platforms import Platform, Runtime
from tests.ufs_chem_assay.run_configs import run_config_file

_CLI_LOGGER = f"{LOGGER_NAME}.cli"
_TESTS_ENABLED: dict[str, object] = {
    "applications.cece.cmake_args": ["-DCMAKE_BUILD_TYPE=Debug", "-DENABLE_ESMF=ON"],
    "applications.cece.targets": ["all"],
    "applications.cece.tests": {"enabled": True, "ntasks": 4, "minutes": 7},
}
_PASSING = (
    "<testsuite>"
    '<testcase name="a" time="1.5"/><testcase name="b" time="0.5"/>'
    '<testcase name="c" status="notrun"><skipped/></testcase>'
    "</testsuite>"
)


def _render(
    tmp_path: Path,
    mocker: MockerFixture,
    template: str = "ursa.yaml",
    overrides: dict[str, object] | None = _TESTS_ENABLED,
) -> Path:
    """A `run --dry-run` at tmp_path/root: the effective config and the
    scripts on disk, nothing executed."""
    mocker.patch("cli.commands.run.run_bash")
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = run_config_file(
        tmp_path, template, overrides=overrides, root_dir=tmp_path / "root"
    )
    assert (
        main(["run", f"--config-file={config}", "--application=cece", "--dry-run"]) == 0
    )
    return tmp_path / "root"


@pytest.fixture
def rendered(tmp_path: Path, mocker: MockerFixture) -> Path:
    mocker.patch("apptests.submit_job", return_value=JobRef("4242", None))
    mocker.patch("apptests.queue_states", return_value={})
    mocker.patch("apptests.scancel")
    return _render(tmp_path, mocker)


def _submit(root: Path, *extra: str) -> int:
    return main(["submit-tests", f"--root-dir={root}", "--application=cece", *extra])


def _evaluate(root: Path, *extra: str) -> int:
    return main(["evaluate-tests", f"--root-dir={root}", "--application=cece", *extra])


def _git_checkout(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        [
            *["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q"],
            *["--allow-empty", "-m", "initial"],
        ],
        cwd=path,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return head.stdout.strip()


def test_both_commands_are_registered() -> None:
    assert Command("submit-tests") in COMMANDS
    assert Command("evaluate-tests") in COMMANDS


def test_submit_tests_submits_the_rendered_job_and_records_it(rendered: Path) -> None:
    sha = _git_checkout(rendered / "CECE")
    cache = rendered / "CECE" / "build" / "CMakeCache.txt"
    cache.parent.mkdir()
    cache.write_text("CMAKE_BUILD_TYPE:STRING=Debug\n")
    assert _submit(rendered) == 0
    tests_root = rendered / "tests" / "cece"
    ledger = read_test_ledger(tests_root)
    assert ledger.application == "cece"
    assert ledger.toolchain is None
    assert ledger.job is not None
    # Everything comes from the effective config the run wrote: the job
    # script beside the stage script, the limit from tests.minutes, the
    # queue allowance from slurm.queue_wait_s.
    assert ledger.job.script == rendered / "scripts" / "04-test-cece.sbatch"
    assert ledger.job.timeout_s == 7 * 60
    assert ledger.job.queue_wait_s == 3600
    provenance = ledger.provenance
    assert provenance.platform is Platform.URSA
    assert provenance.runtime is Runtime.SLURM
    assert provenance.modulefile == "cece_ursa.intelllvm"
    assert provenance.cmake_args == "-DCMAKE_BUILD_TYPE=Debug -DENABLE_ESMF=ON"
    assert provenance.build_type == "Debug"
    assert provenance.application_commit == sha
    assert provenance.harness_version


def test_submit_tests_records_null_for_a_checkout_it_cannot_read(
    rendered: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=_CLI_LOGGER):
        assert _submit(rendered) == 0
    provenance = read_test_ledger(rendered / "tests" / "cece").provenance
    assert (provenance.application_commit, provenance.build_type) == (None, None)
    assert "application_commit: null" in caplog.text


def test_submit_tests_refuses_a_queued_job_unless_resubmit(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    assert _submit(rendered) == 0
    mocker.patch("apptests.queue_states", return_value={"4242": "PENDING"})
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert _submit(rendered) == 1
    assert "test job 4242 is still in the queue" in caplog.text
    assert "cancel --tests-root" in caplog.text
    mocker.patch("apptests.submit_job", return_value=JobRef("4300", None))
    assert _submit(rendered, "--resubmit") == 0
    ledger = read_test_ledger(rendered / "tests" / "cece")
    assert ledger.job is not None
    assert ledger.job.job_id == "4300"


def test_submit_tests_reports_a_refused_sbatch(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch(
        "apptests.submit_job",
        side_effect=SlurmSubmissionError(
            1, ["sbatch"], output="", stderr="Invalid qos"
        ),
    )
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert _submit(rendered) == 1
    assert "Invalid qos" in caplog.text
    assert not path_of_test_ledger(rendered / "tests" / "cece").exists()


def test_submit_tests_clean_errors(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    submit = mocker.patch("apptests.submit_job")

    def refused(root: Path, application: str = "cece") -> str:
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
            code = main(
                ["submit-tests", f"--root-dir={root}", f"--application={application}"]
            )
        assert code == 1
        return caplog.text

    # No run was rendered there.
    assert "ufs-chem-assay run" in refused(tmp_path / "nowhere")
    # Tests are off in the shipped template.
    plain = _render(tmp_path / "plain", mocker, overrides=None)
    assert "applications.cece.tests.enabled is false" in refused(plain)
    assert "not in the run config" in refused(plain, "monetio")
    # docker and native run the tests in place: there is no job to submit.
    local = _render(
        tmp_path / "local",
        mocker,
        "local.yaml",
        {
            "applications.cece.targets": ["all"],
            "applications.cece.tests": {"enabled": True},
        },
    )
    assert "submit-tests is the slurm runtime's" in refused(local)
    # The job script is rendered by `run`; without it there is nothing to submit.
    ursa = _render(tmp_path / "ursa", mocker)
    (ursa / "scripts" / "04-test-cece.sbatch").unlink()
    assert "04-test-cece.sbatch does not exist" in refused(ursa)
    submit.assert_not_called()


def _finish(root: Path, junit: str = _PASSING) -> None:
    tests_root = root / "tests" / "cece"
    (tests_root / "tests-4242.out").write_text(
        "tic: 2026-11-16T12:00:00Z\ntoc: 2026-11-16T12:01:00Z\n"
    )
    (tests_root / "tests-4242.xml").write_text(junit)


def test_evaluate_tests_judges_a_finished_job(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    assert _submit(rendered) == 0
    _finish(rendered)
    mocker.patch(
        "apptests.slurm_accounting",
        return_value=SlurmAccounting(
            job_id="4242",
            submit=None,
            start=None,
            end=None,
            elapsed_s=60.0,
            queue_s=5.0,
            max_rss_kb=1000,
            state="COMPLETED",
            exit_code=0,
        ),
    )
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert _evaluate(rendered) == 0
    tests_root = rendered / "tests" / "cece"
    ledger = read_test_ledger(tests_root)
    assert ledger.exit_status == 0
    assert ledger.evaluated_at is not None
    assert (
        "tests cece: evaluated (exit 0), 2 passed / 0 failed / 1 skipped" in caplog.text
    )
    (row,) = read_test_benchmarks_csv(tests_root / CONVENTIONS.benchmarks)
    assert row.wall_s == 60.0


def test_evaluate_tests_clean_errors(
    rendered: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert _evaluate(rendered) == 1  # nothing submitted yet
    assert "no test run recorded" in caplog.text
    assert _submit(rendered) == 0
    mocker.patch("apptests.queue_states", return_value={"4242": "RUNNING"})
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert _evaluate(rendered) == 1
    assert "still in the queue" in caplog.text


def test_evaluate_tests_records_a_run_in_place(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # docker: the stage script ran ctest in the image and passes its status.
    root = _render(
        tmp_path,
        mocker,
        "local.yaml",
        {
            "applications.cece.targets": ["all"],
            "applications.cece.tests": {"enabled": True},
        },
    )
    tests_root = root / "tests" / "cece"
    tests_root.mkdir(parents=True)
    (tests_root / "tests.out").write_text(
        "tic: 2026-11-16T12:00:00Z\ntoc: 2026-11-16T12:00:20Z\n"
    )
    (tests_root / "tests.xml").write_text(
        _PASSING.replace(
            '<testcase name="b" time="0.5"/>',
            '<testcase name="b"><failure/></testcase>',
        )
    )
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert _evaluate(root) == 1  # the status is the script's to pass
    assert "--exit-status" in caplog.text
    assert _evaluate(root, "--exit-status=8") == 0  # recorded; the stage exits 8
    ledger = read_test_ledger(tests_root)
    assert ledger.job is None
    assert ledger.exit_status == 8
    assert ledger.provenance.runtime is Runtime.DOCKER
    assert ledger.provenance.platform is Platform.LOCAL
    assert ledger.provenance.cmake_args is None  # the container script's build
    (row,) = read_test_benchmarks_csv(tests_root / CONVENTIONS.benchmarks)
    assert (row.exit_code, row.tests_failed, row.wall_s) == (8, 1, 20.0)


def test_evaluate_tests_refuses_an_application_whose_tests_are_off(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    plain = _render(tmp_path, mocker, overrides=None)
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        assert _evaluate(plain) == 1
    assert (
        "applications.cece.tests.enabled is false: no tests to evaluate" in caplog.text
    )


def test_submit_tests_records_the_pass_marker_of_a_reportless_suite(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("apptests.submit_job", return_value=JobRef("4242", None))
    mocker.patch("apptests.queue_states", return_value={})
    mocker.patch("cli.commands.run.run_bash")
    config = run_config_file(
        tmp_path,
        overrides={
            "applications.kokkos-smoke": {
                "modulefile": "ufs_ursa.oneapi",
                "tests": {"enabled": True},
            }
        },
        root_dir=tmp_path / "root",
    )
    assert (
        main(
            [
                "run",
                f"--config-file={config}",
                "--application=kokkos-smoke",
                "--dry-run",
            ]
        )
        == 0
    )
    root = tmp_path / "root"
    with caplog.at_level(logging.WARNING, logger=_CLI_LOGGER):
        assert (
            main(["submit-tests", f"--root-dir={root}", "--application=kokkos-smoke"])
            == 0
        )
    ledger = read_test_ledger(root / "tests" / "kokkos-smoke")
    assert ledger.passed_marker == "SMOKE TEST PASSED"
    assert ledger.job is not None
    assert ledger.job.script == root / "scripts" / "04-test-kokkos-smoke.sbatch"
    # The project is the harness's own: no checkout commit, and no warning
    # about one.
    assert ledger.provenance.application_commit is None
    assert "application_commit" not in caplog.text
    assert ledger.provenance.cmake_args == "-DCMAKE_BUILD_TYPE=Release"
