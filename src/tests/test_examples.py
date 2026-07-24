"""Opt-in execution of the CECE driver's shipped examples (--run-examples).

Each examples/config/cece_config_ex*.yaml in the CECE checkout runs via the
checkout's own examples/run-example.py entrypoint, wrapped in docker by this
suite (the entrypoint is container-agnostic; exit 0 = pass). Input data is
fetched once per session through examples/download-example-data.py, invoked
per example. Gating lives in the session download fixture so nothing
(downloads included) runs when disabled: no --run-examples -> skip;
--dry-run -> skip. Failures are honest — examples are external artifacts
under test, never masked, and the session writes examples/examples-report.md
under the output root.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from examples import (
    DownloadResult,
    ExampleRunResult,
    download_example_data,
    example_id,
    run_example_command,
    write_examples_report,
)
from settings import Settings

if TYPE_CHECKING:
    # Type-only: a runtime import would re-execute the conftest module and
    # mint duplicate StashKeys; pytest injects the fixture by name.
    from tests.conftest import ComboRoots


@pytest.fixture(scope="session")
def example_downloads(
    request: pytest.FixtureRequest, settings: Settings
) -> list[DownloadResult]:
    """Gate keeper + one download pass per session. Requested only by
    example tests, so combo-only sessions never pay for it."""
    if not request.config.getoption("--run-examples"):
        pytest.skip("examples disabled; pass --run-examples")
    if request.config.getoption("--dry-run"):
        pytest.skip("dry run: driver execution skipped")
    assert settings.root_dir is not None  # collection guard guarantees this
    return download_example_data(settings.root_dir, timeout_s=settings.run_timeout_s)


@pytest.fixture(scope="session")
def examples_root(combo_roots: "ComboRoots") -> Path:
    """examples/ subdirectory of the output root — keeps combo-id
    directories unambiguous."""
    root = combo_roots.host / "examples"
    root.mkdir(exist_ok=True)
    return root


@pytest.fixture(scope="session")
def example_results(
    examples_root: Path, example_downloads: list[DownloadResult]
) -> Iterator[list[ExampleRunResult]]:
    """Collects every execution outcome; teardown writes the session
    report once all example tests have run."""
    results: list[ExampleRunResult] = []
    yield results
    write_examples_report(
        example_downloads, results, examples_root / "examples-report.md"
    )


def test_example_execution(
    example_yaml: Path,
    settings: Settings,
    example_downloads: list[DownloadResult],
    examples_root: Path,
    example_results: list[ExampleRunResult],
) -> None:
    """The shipped example runs via run-example.py in docker and exits 0."""
    assert settings.root_dir is not None
    stem = example_yaml.stem
    out_path = examples_root / f"{stem}.out"
    command = run_example_command(settings, example_id(example_yaml))

    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=settings.run_timeout_s,
        )
        returncode = completed.returncode
        output = completed.stdout
    except subprocess.TimeoutExpired as exc:
        returncode = -1
        output = exc.output or b""

    out_path.write_bytes(output)
    example_results.append(
        ExampleRunResult(example=stem, returncode=returncode, out_path=out_path)
    )
    assert returncode == 0, f"example {stem} exited {returncode}; output at {out_path}"
