"""cli/shell.py: a rendered script really run with bash, its output streamed
and logged under a timestamped file; the exit code returned."""

from pathlib import Path

import pytest

from cli.shell import run_bash, write_script
from cli.stages import ShellScript


def test_run_bash_streams_logs_and_returns_the_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = write_script(
        ShellScript(
            name="probe-stub", text="#!/bin/bash\necho hello\necho oops >&2\nexit 3\n"
        ),
        tmp_path / "scripts",
        9,
    )
    assert script.name == "09-probe-stub.sh"
    code = run_bash(script, tmp_path / "logs")
    assert code == 3
    (log,) = (tmp_path / "logs").glob("09-probe-stub-*.log")
    assert log.read_text() == "hello\noops\n"
    assert "hello" in capsys.readouterr().out
