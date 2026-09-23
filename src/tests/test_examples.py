"""Opt-in execution of the application's shipped examples (--run-examples).

Each example the adapter discovers in the checkout runs through the
checkout's own tooling (CECE: examples/run-example.py, wrapped in docker by
this suite; exit 0 = pass). Input data is fetched once per session through
the adapter's download support. Gating lives in the session download
fixture so nothing (downloads included) runs when disabled: no
--run-examples -> skip; --dry-run -> skip. Failures are honest — examples
are external artifacts under test, never masked, and the session writes
examples/examples-report.md under the output root.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from applications.base import Application, ApplicationSettings, ExamplesSupport
from examples import DownloadResult, ExampleRunResult, write_examples_report
from settings import Settings

if TYPE_CHECKING:
    # Type-only: a runtime import would re-execute the conftest module and
    # mint duplicate StashKeys; pytest injects the fixture by name.
    from tests.conftest import ComboRoots


@pytest.fixture(scope="session")
def examples_support(application: Application) -> ExamplesSupport:
    """The adapter's examples support; the collection guard already refused
    --run-examples for an application without it."""
    assert application.examples is not None
    return application.examples


@pytest.fixture(scope="session")
def example_downloads(
    request: pytest.FixtureRequest,
    settings: Settings,
    app_settings: ApplicationSettings,
    examples_support: ExamplesSupport,
) -> list[DownloadResult]:
    """Gate keeper + one download pass per session. Requested only by
    example tests, so combo-only sessions never pay for it."""
    if not request.config.getoption("--run-examples"):
        pytest.skip("examples disabled; pass --run-examples")
    if request.config.getoption("--dry-run"):
        pytest.skip("dry run: driver execution skipped")
    assert app_settings.root_dir is not None  # collection guard guarantees this
    return examples_support.download(
        app_settings.root_dir, timeout_s=settings.run_timeout_s
    )


@pytest.fixture(scope="session")
def examples_root(combo_roots: "ComboRoots") -> Path:
    """examples/ subdirectory of the output root — keeps combo-id
    directories unambiguous."""
    root = combo_roots.host / "examples"
    root.mkdir(exist_ok=True)
    return root


@pytest.fixture(scope="session")
def example_results(
    application: Application,
    examples_root: Path,
    example_downloads: list[DownloadResult],
) -> Iterator[list[ExampleRunResult]]:
    """Collects every execution outcome; teardown writes the session
    report once all example tests have run."""
    results: list[ExampleRunResult] = []
    yield results
    write_examples_report(
        application.name,
        example_downloads,
        results,
        examples_root / "examples-report.md",
    )


def test_example_execution(
    example_yaml: Path,
    settings: Settings,
    app_settings: ApplicationSettings,
    examples_support: ExamplesSupport,
    example_downloads: list[DownloadResult],
    examples_root: Path,
    example_results: list[ExampleRunResult],
) -> None:
    """The shipped example runs through the application's runner and exits 0."""
    assert app_settings.root_dir is not None
    stem = example_yaml.stem
    out_path = examples_root / f"{stem}.out"
    command = examples_support.run_command(
        settings, app_settings, examples_support.example_id(example_yaml)
    )

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
