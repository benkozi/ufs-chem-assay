"""`run --watch`: after its stages, `run` watches the same root in-process with
watch's defaults — whatever the stages returned, since a failed build still
leaves the other builds' test jobs submitted — and exits with the stages'
status when they failed, else watch's. Skipped under --dry-run and when no
selected stage leaves anything to wait for."""

import logging
import re
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

import logs
from cli.main import main
from logs import LOGGER_NAME
from tests.ufs_chem_assay.run_configs import TEMPLATES_DIR

_CLI_LOGGER = f"{LOGGER_NAME}.cli"


def _daily(tmp_path: Path, stages: list[str]) -> Path:
    path = tmp_path / "c7.local.yaml"
    data = {
        "extends": str(TEMPLATES_DIR / "gaea-c7.yaml"),
        "root_dir": str(tmp_path / "root"),
        "selected_applications": ["catchem"],
        "selected_stages": stages,
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _run(
    config: Path,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
    *args: str,
    bash: int | list[int] = 0,
) -> tuple[int, list[str]]:
    if isinstance(bash, list):
        mocker.patch("cli.commands.run.run_bash", side_effect=bash)
    else:
        mocker.patch("cli.commands.run.run_bash", return_value=bash)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_CLI_LOGGER):
        code = main(["run", f"--config-file={config}", *args])
    return code, [r.getMessage() for r in caplog.records if r.name == _CLI_LOGGER]


def test_run_watches_its_root_after_the_stages(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    watch = mocker.patch("cli.commands.run.watch_root", return_value=0)
    config = _daily(tmp_path, ["build", "test"])
    code, messages = _run(config, mocker, caplog, "--watch")
    assert code == 0
    watch.assert_called_once_with(tmp_path / "root")
    assert messages[-1] == "run: stages exit 0, watch exit 0"


def test_watch_runs_after_a_failed_stage_and_the_stages_status_wins(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    watch = mocker.patch("cli.commands.run.watch_root", return_value=1)
    config = _daily(tmp_path, ["build", "test"])
    # compute's build fails; musica's builds and its test is submitted.
    code, messages = _run(config, mocker, caplog, "--watch", bash=[2, 0, 0])
    assert code == 2
    watch.assert_called_once()
    assert messages[-1] == "run: stages exit 2, watch exit 1"


def test_watchs_status_is_the_runs_when_the_stages_pass(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("cli.commands.run.watch_root", return_value=1)
    code, _ = _run(_daily(tmp_path, ["test"]), mocker, caplog, "--watch")
    assert code == 1


def test_no_stage_to_wait_for_skips_watch(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    watch = mocker.patch("cli.commands.run.watch_root")
    code, messages = _run(
        _daily(tmp_path, ["source", "build"]), mocker, caplog, "--watch"
    )
    assert code == 0
    watch.assert_not_called()
    assert messages[-1] == (
        "run: stages exit 0, watch skipped: no test, harness or resume stage"
    )


def test_a_dry_run_skips_watch(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    watch = mocker.patch("cli.commands.run.watch_root")
    code, messages = _run(
        _daily(tmp_path, ["test"]), mocker, caplog, "--watch", "--dry-run"
    )
    assert code == 0
    watch.assert_not_called()
    assert messages[-1] == "run: dry run, watch skipped"


def test_without_watch_nothing_is_watched(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    watch = mocker.patch("cli.commands.run.watch_root")
    code, messages = _run(_daily(tmp_path, ["test"]), mocker, caplog)
    assert code == 0
    watch.assert_not_called()
    assert not any(m.startswith("run: stages exit") for m in messages)


def test_the_real_watch_lands_in_the_transcript_under_its_own_tag(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # The test stage "ran" (bash stubbed) but submitted nothing: watch gives
    # up after its not-submitted checks and exits 1.
    mocker.patch("cli.sessions.queue_states", return_value={})
    mocker.patch("cli.commands.watch.time.sleep")
    code, messages = _run(_daily(tmp_path, ["test"]), mocker, caplog, "--watch")
    assert code == 1
    assert messages[-1] == "run: stages exit 0, watch exit 1"
    assert logs.current_command() == "run"  # restored after the watch
    (transcript,) = (tmp_path / "root" / "logs").glob("assay-run-*.log")
    lines = transcript.read_text().splitlines()
    assert any("[watch]" in line and "not submitted" in line for line in lines)
    assert re.search(r"\[run\]: run: stages exit 0, watch exit 1$", lines[-1])
