"""`record-benchmarks --transfer DIR`: every tests root a transfer directory
holds (<toolchain>/tests/<application>[/<build>] with its ledger and its
benchmarks.csv), in sorted order, recorded as --tests-root records one — on
the laptop, from what `transfer` copied. The transfer is its zipped payload
(<name>.zip, unzipping to <name>/) or that directory unzipped. One root
failing does not stop the others."""

import logging
import shutil
import zipfile
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from benchmarks import read_test_benchmarks_csv
from cli.main import main
from logs import LOGGER_NAME
from models.conventions import CONVENTIONS
from tests.ufs_chem_assay.test_stage_scope import _evaluated

_CLI_LOGGER = f"{LOGGER_NAME}.cli"


@pytest.fixture
def committed(tmp_path: Path, mocker: MockerFixture) -> Path:
    """The committed per-application files, in tmp_path instead of config/."""
    path = tmp_path / "config"
    mocker.patch("cli.commands.record_benchmarks.BUILTIN_CONFIG_DIR", path)
    return path


@pytest.fixture
def transfer(tmp_path: Path) -> Path:
    """A transfer directory: CATChem's two builds and CECE evaluated, and a
    tests root that holds output but no ledger (never recorded)."""
    top = tmp_path / "transfer" / "assay-x"
    (top / "scripts").mkdir(parents=True)
    (top / "scripts" / CONVENTIONS.effective_config).write_text("{}\n")
    tests = top / "oneapi" / "tests"
    for build in ("compute", "musica"):
        root = tests / "catchem" / build
        root.mkdir(parents=True)
        _evaluated(root, "catchem", build)
    (tests / "cece").mkdir(parents=True)
    _evaluated(tests / "cece", "cece")
    (tests / "monetio").mkdir()
    (tests / "monetio" / "tests.out").write_text("never recorded\n")
    return top


def _record(caplog: pytest.LogCaptureFixture, *args: str) -> tuple[int, list[str]]:
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["record-benchmarks", *args])
    return code, [r.getMessage() for r in caplog.records if r.name == _CLI_LOGGER]


def test_every_tests_root_is_recorded_in_order(
    transfer: Path, committed: Path, caplog: pytest.LogCaptureFixture
) -> None:
    code, messages = _record(caplog, f"--transfer={transfer}")
    assert code == 0
    tests = transfer / "oneapi" / "tests"
    recorded = [m for m in messages if m.startswith("recording ")]
    assert recorded == [
        f"recording {tests / 'catchem' / 'compute'}",
        f"recording {tests / 'catchem' / 'musica'}",
        f"recording {tests / 'cece'}",
    ]
    assert (
        len(read_test_benchmarks_csv(committed / "catchem" / "tests-benchmarks.csv"))
        == 2
    )
    assert (
        len(read_test_benchmarks_csv(committed / "cece" / "tests-benchmarks.csv")) == 1
    )
    # Again: every run is already recorded.
    code, messages = _record(caplog, f"--transfer={transfer}")
    assert code == 0
    assert sum("0 appended" in m for m in messages) == 3


def test_a_dry_run_appends_nothing(
    transfer: Path, committed: Path, caplog: pytest.LogCaptureFixture
) -> None:
    code, messages = _record(caplog, f"--transfer={transfer}", "--dry-run")
    assert code == 0
    assert not committed.exists()
    assert sum(m.startswith("dry run: would append test run") for m in messages) == 3


def test_a_failing_root_does_not_stop_the_others(
    transfer: Path, committed: Path, caplog: pytest.LogCaptureFixture
) -> None:
    musica = transfer / "oneapi" / "tests" / "catchem" / "musica"
    (musica / CONVENTIONS.benchmarks).write_text("not,the,columns\n")
    code, messages = _record(caplog, f"--transfer={transfer}")
    assert code == 1
    assert (
        len(read_test_benchmarks_csv(committed / "catchem" / "tests-benchmarks.csv"))
        == 1
    )
    assert (
        len(read_test_benchmarks_csv(committed / "cece" / "tests-benchmarks.csv")) == 1
    )
    assert messages[-1] == f"record-benchmarks: 1 of 3 tests roots in {transfer} failed"


@pytest.mark.usefixtures("committed")
def test_a_transfer_without_a_test_run_records_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    top = tmp_path / "assay-y"
    (top / "scripts").mkdir(parents=True)
    (top / "scripts" / CONVENTIONS.effective_config).write_text("{}\n")
    code, messages = _record(caplog, f"--transfer={top}")
    assert code == 0
    assert messages == [f"{top} holds no test run (no tests root with a ledger)"]


@pytest.mark.usefixtures("committed")
def test_a_directory_transfer_did_not_write_is_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    code, messages = _record(caplog, f"--transfer={tmp_path}")
    assert code == 1
    assert "not a transfer directory" in messages[-1]


def test_transfer_excludes_the_other_roots() -> None:
    with pytest.raises(SystemExit) as usage:
        main(["record-benchmarks", "--transfer=a", "--tests-root=b"])
    assert usage.value.code == 2


def _zipped(top: Path) -> Path:
    """The payload `transfer` writes: top's files under <top name>/."""
    archive = top.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(top.rglob("*")):
            if path.is_file():
                zf.write(path, f"{top.name}/{path.relative_to(top)}")
    shutil.rmtree(top)
    return archive


def test_the_zipped_payload_is_read_as_its_directory(
    transfer: Path, committed: Path, caplog: pytest.LogCaptureFixture
) -> None:
    archive = _zipped(transfer)
    code, messages = _record(caplog, f"--transfer={archive}")
    assert code == 0
    recorded = [m for m in messages if m.startswith("recording ")]
    assert recorded == [
        f"recording {archive}:assay-x/oneapi/tests/catchem/compute",
        f"recording {archive}:assay-x/oneapi/tests/catchem/musica",
        f"recording {archive}:assay-x/oneapi/tests/cece",
    ]
    assert (
        len(read_test_benchmarks_csv(committed / "catchem" / "tests-benchmarks.csv"))
        == 2
    )
    assert (
        len(read_test_benchmarks_csv(committed / "cece" / "tests-benchmarks.csv")) == 1
    )
    assert archive.is_file()  # read, never changed or left unpacked beside it
    assert sorted(p.name for p in archive.parent.iterdir()) == ["assay-x.zip"]


@pytest.mark.usefixtures("committed")
def test_a_zip_that_is_not_one_payload_is_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    archive = tmp_path / "two.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a/scripts/run-config.yaml", "{}\n")
        zf.writestr("b/scripts/run-config.yaml", "{}\n")
    code, messages = _record(caplog, f"--transfer={archive}")
    assert code == 1
    assert messages[-1] == (
        f"{archive} is not a transfer payload: it must hold one top directory "
        "(it holds a, b)"
    )


@pytest.mark.usefixtures("committed")
def test_a_file_that_is_not_a_zip_is_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    archive = tmp_path / "broken.zip"
    archive.write_text("not a zip\n")
    code, messages = _record(caplog, f"--transfer={archive}")
    assert code == 1
    assert messages[-1] == f"{archive} is not a zip file"


def test_a_transfer_of_a_root_without_toolchains_is_recorded(
    tmp_path: Path, committed: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Without toolchains the tests roots are tests/<application>[/<build>],
    # with no toolchain directory above them.
    top = tmp_path / "transfer" / "assay-y"
    (top / "scripts").mkdir(parents=True)
    (top / "scripts" / CONVENTIONS.effective_config).write_text("{}\n")
    (top / "tests" / "cece").mkdir(parents=True)
    _evaluated(top / "tests" / "cece", "cece")
    (top / "tests" / "catchem" / "compute").mkdir(parents=True)
    _evaluated(top / "tests" / "catchem" / "compute", "catchem", "compute")
    code, messages = _record(caplog, f"--transfer={top}")
    assert code == 0
    tests = top / "tests"
    assert [m for m in messages if m.startswith("recording ")] == [
        f"recording {tests / 'catchem' / 'compute'}",
        f"recording {tests / 'cece'}",
    ]
    assert (
        len(read_test_benchmarks_csv(committed / "cece" / "tests-benchmarks.csv")) == 1
    )
