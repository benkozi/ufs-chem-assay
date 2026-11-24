"""`ufs-chem-assay transfer --config-file FILE`: a root's evidence — logs,
rendered scripts and records, tests roots, CMake caches, and the report —
zipped into one payload, <transfer_dir>/<name>.zip (unzipping it gives
<name>/), then pulled, added, committed and pushed in the transfer
repository. The root and the destination come from the run config; an
existing name is refused."""

import logging
import re
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from pytest_mock import MockerFixture

from cli.main import main
from logs import LOGGER_NAME
from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR

_CLI_LOGGER = f"{LOGGER_NAME}.cli"


def _config(tmp_path: Path, transfer: bool = True) -> Path:
    data: dict[str, object] = {
        "extends": str(TEMPLATES_DIR / "gaea-c7.yaml"),
        "root_dir": str(tmp_path / "root"),
        "selected_applications": ["catchem"],
        "selected_stages": ["source", "build", "test"],
    }
    if transfer:
        data["transfer_dir"] = str(tmp_path / "transfer")
    path = tmp_path / "c7.local.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


@pytest.fixture
def root(tmp_path: Path, mocker: MockerFixture) -> Path:
    """A root a run rendered (its scripts, records and transcript), with a
    tests root, a CMake cache, and a source file that is not evidence."""
    mocker.patch("cli.commands.run.run_bash", return_value=0)
    assert main(["run", f"--config-file={_config(tmp_path)}", "--dry-run"]) == 0
    root = tmp_path / "root"
    tests = root / "oneapi" / "tests" / "catchem" / "compute"
    tests.mkdir(parents=True)
    (tests / "tests.out").write_text("ok\n")
    build = root / "oneapi" / "CATChem" / "build-compute"
    build.mkdir(parents=True)
    (build / "CMakeCache.txt").write_text("KOKKOS:BOOL=ON\n")
    (root / "oneapi" / "CATChem" / "src.F90").write_text("! not evidence\n")
    (tmp_path / "transfer").mkdir()
    return root


def _git(mocker: MockerFixture, failing: str | None = None) -> MagicMock:
    def run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[bytes]:
        code = 1 if failing is not None and argv[1] == failing else 0
        return subprocess.CompletedProcess(argv, code)

    return mocker.patch("cli.commands.transfer.subprocess.run", side_effect=run)


def _transfer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, *args: str
) -> tuple[int, list[str]]:
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["transfer", f"--config-file={_config(tmp_path)}", *args])
    return code, [r.getMessage() for r in caplog.records if r.name == _CLI_LOGGER]


def _members(archive: Path) -> list[str]:
    """The archive's files, under its one top directory, which is checked."""
    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
    tops = {name.split("/", 1)[0] for name in names}
    assert tops == {archive.stem}
    return sorted(name.split("/", 1)[1] for name in names)


@pytest.mark.usefixtures("root")
def test_the_evidence_is_copied_with_the_report(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _git(mocker)
    code, messages = _transfer(tmp_path, caplog, "--name=assay-x")
    assert code == 0
    archive = tmp_path / "transfer" / "assay-x.zip"
    # One payload, and nothing else in the transfer directory.
    assert sorted(p.name for p in (tmp_path / "transfer").iterdir()) == ["assay-x.zip"]
    files = _members(archive)
    assert "scripts/run-config.yaml" in files
    assert any(
        re.fullmatch(r"scripts/\d\d-build-catchem-oneapi-compute\.sh", f) for f in files
    )
    assert any(re.fullmatch(r"logs/assay-run-\d{8}-\d{6}\.log", f) for f in files)
    assert "oneapi/tests/catchem/compute/tests.out" in files
    assert "cmake/oneapi/CATChem/build-compute/CMakeCache.txt" in files
    assert not any(f.endswith("src.F90") for f in files)
    with zipfile.ZipFile(archive) as zf:
        assert zf.getinfo("assay-x/report.txt").compress_type == zipfile.ZIP_DEFLATED
        assert "[report]" in zf.read("assay-x/report.txt").decode()
        tests_out = zf.read("assay-x/oneapi/tests/catchem/compute/tests.out")
    assert tests_out == b"ok\n"
    assert re.fullmatch(
        rf"transfer: pushed {archive} \(\d+\.\d [KMG]?B, \d+\.\d [KMG]?B unzipped\)",
        messages[-1],
    )


@pytest.mark.usefixtures("root")
def test_the_transfer_repository_is_pulled_added_committed_and_pushed(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    git = _git(mocker)
    assert _transfer(tmp_path, caplog, "--name=assay-x")[0] == 0
    calls = [(c.args[0], c.kwargs["cwd"]) for c in git.call_args_list]
    transfer = tmp_path / "transfer"
    assert calls == [
        (["git", "pull"], transfer),
        (["git", "add", "-f", "assay-x.zip"], transfer),
        (["git", "commit", "-m", "assay-x"], transfer),
        (["git", "push"], transfer),
    ]


@pytest.mark.usefixtures("root")
def test_a_failing_git_command_stops_the_transfer_and_keeps_the_archive(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    git = _git(mocker, failing="commit")
    code, messages = _transfer(tmp_path, caplog, "--name=assay-x")
    assert code == 1
    assert [c.args[0][1] for c in git.call_args_list] == ["pull", "add", "commit"]
    archive = tmp_path / "transfer" / "assay-x.zip"
    assert archive.is_file()
    assert messages[-1] == (
        f"transfer: `git commit -m assay-x` failed (exit 1) in {tmp_path / 'transfer'}; "
        f"the archive stays in {archive}"
    )


@pytest.mark.usefixtures("root")
@pytest.mark.parametrize("taken", ["assay-x.zip", "assay-x"])
def test_an_existing_name_is_refused(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture, taken: str
) -> None:
    # The archive, or a directory of that name (one unzipped, or a transfer
    # from before the payload was zipped): unzipping would collide.
    git = _git(mocker)
    existing = tmp_path / "transfer" / taken
    if taken.endswith(".zip"):
        existing.write_bytes(b"earlier")
    else:
        existing.mkdir()
    code, messages = _transfer(tmp_path, caplog, "--name=assay-x")
    assert code == 1
    git.assert_not_called()
    assert messages[-1] == (f"transfer: {existing} already exists: pass another --name")
    assert sorted(p.name for p in (tmp_path / "transfer").iterdir()) == [taken]


@pytest.mark.usefixtures("root")
def test_the_default_name_is_the_minute(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _git(mocker)
    assert _transfer(tmp_path, caplog)[0] == 0
    (archive,) = (tmp_path / "transfer").iterdir()
    assert re.fullmatch(r"assay-\d{8}-\d{4}\.zip", archive.name)
    assert _members(archive)  # its top directory is the name, as checked there


@pytest.mark.usefixtures("root")
def test_a_dry_run_archives_and_pushes_nothing(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    git = _git(mocker)
    code, messages = _transfer(tmp_path, caplog, "--name=assay-x", "--dry-run")
    assert code == 0
    git.assert_not_called()
    assert not any((tmp_path / "transfer").iterdir())
    assert any(
        "would archive" in m
        and m.endswith("as assay-x/cmake/oneapi/CATChem/build-compute/CMakeCache.txt")
        for m in messages
    )
    assert f"dry run: would write {tmp_path / 'transfer' / 'assay-x.zip'}" in messages
    assert "dry run: would run `git add -f assay-x.zip`" in messages
    assert messages[-1] == "transfer: dry run; nothing archived or pushed"


@pytest.mark.usefixtures("root")
def test_the_reports_issues_do_not_fail_the_transfer(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _git(mocker)

    def report_to(_root: Path, path: Path) -> int:
        path.write_text("report: 1 issue; exit 1\n")  # as report_to writes it
        return 1

    mocker.patch("cli.commands.transfer.report_to", side_effect=report_to)
    assert _transfer(tmp_path, caplog, "--name=assay-x")[0] == 0
    with zipfile.ZipFile(tmp_path / "transfer" / "assay-x.zip") as zf:
        assert zf.read("assay-x/report.txt") == b"report: 1 issue; exit 1\n"


def test_without_transfer_dir_nothing_happens(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    git = _git(mocker)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["transfer", f"--config-file={_config(tmp_path, transfer=False)}"])
    assert code == 1
    git.assert_not_called()
    assert any("transfer_dir" in r.getMessage() for r in caplog.records)


def test_a_root_without_a_run_is_refused(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    git = _git(mocker)
    (tmp_path / "transfer").mkdir()
    code, messages = _transfer(tmp_path, caplog)
    assert code == 1
    git.assert_not_called()
    assert "run-config.yaml" in messages[-1]


def test_a_config_that_does_not_load_is_an_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"extends": "config/no-such.yaml"}))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        assert main(["transfer", f"--config-file={bad}"]) == 1
    assert any("no-such.yaml" in r.getMessage() for r in caplog.records)


@pytest.mark.usefixtures("root")
def test_a_failed_archive_leaves_no_payload(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # Written beside its name and renamed when complete: a write that fails
    # midway never looks like a finished payload, and nothing is pushed.
    git = _git(mocker)
    mocker.patch(
        "cli.commands.transfer.zipfile.ZipFile.write",
        side_effect=OSError(28, "No space left"),
    )
    code, messages = _transfer(tmp_path, caplog, "--name=assay-x")
    assert code == 1
    git.assert_not_called()
    assert not any((tmp_path / "transfer").iterdir())
    assert messages[-1] == (
        f"transfer: cannot write {tmp_path / 'transfer' / 'assay-x.zip'}: No space left"
    )
