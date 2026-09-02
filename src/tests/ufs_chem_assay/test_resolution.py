from pathlib import Path, PurePosixPath

import pytest

from resolution import resolve_output_roots, select_suites


def test_relative_output_root_maps_under_work() -> None:
    host, container = resolve_output_roots("combo_runs", Path("/host/cece"))
    assert host == Path("/host/cece/combo_runs")
    assert container == PurePosixPath("/work/combo_runs")


def test_absolute_output_root_under_work_is_mapped() -> None:
    host, container = resolve_output_roots("/work/nested/runs", Path("/host/cece"))
    assert host == Path("/host/cece/nested/runs")
    assert container == PurePosixPath("/work/nested/runs")


def test_absolute_output_root_outside_work_raises() -> None:
    with pytest.raises(ValueError, match="/work"):
        resolve_output_roots("/elsewhere/runs", Path("/host/cece"))


@pytest.fixture()
def suite_roots(tmp_path: Path) -> tuple[Path, Path]:
    """Two search roots: root_a with a nested suite, root_b with a same-named
    suite at its top level plus a distinct one."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    (root_a / "nightly").mkdir(parents=True)
    root_b.mkdir()
    (root_a / "nightly" / "smoke-suite.yaml").touch()
    (root_b / "smoke-suite.yaml").touch()
    (root_b / "regional-suite.yaml").touch()
    return root_a, root_b


def test_select_suites_literal_filename(suite_roots: tuple[Path, Path]) -> None:
    root_a, root_b = suite_roots
    selected = select_suites("regional-suite.yaml", [root_a, root_b])
    assert selected == [root_b / "regional-suite.yaml"]


def test_select_suites_finds_nested_files_recursively(
    suite_roots: tuple[Path, Path],
) -> None:
    root_a, _ = suite_roots
    selected = select_suites("smoke-suite.yaml", [root_a])
    assert selected == [root_a / "nightly" / "smoke-suite.yaml"]


def test_select_suites_relative_path_disambiguates(
    suite_roots: tuple[Path, Path],
) -> None:
    root_a, root_b = suite_roots
    selected = select_suites(r"nightly/smoke-suite\.yaml", [root_a, root_b])
    assert selected == [root_a / "nightly" / "smoke-suite.yaml"]


def test_select_suites_returns_every_match(suite_roots: tuple[Path, Path]) -> None:
    # Multiple matches are a multi-suite selection, not an error; ordered by
    # file name (then path) for determinism.
    root_a, root_b = suite_roots
    selected = select_suites(r".*-suite\.yaml", [root_a, root_b])
    assert selected == [
        root_b / "regional-suite.yaml",
        root_a / "nightly" / "smoke-suite.yaml",
        root_b / "smoke-suite.yaml",
    ]


def test_select_suites_no_match_lists_candidates(
    suite_roots: tuple[Path, Path],
) -> None:
    root_a, root_b = suite_roots
    with pytest.raises(ValueError, match="matches no suite") as excinfo:
        select_suites("absent-suite.yaml", [root_a, root_b])
    message = str(excinfo.value)
    assert "nightly/smoke-suite.yaml" in message
    assert "regional-suite.yaml" in message


def test_select_suites_overlapping_roots_deduplicate(
    suite_roots: tuple[Path, Path],
) -> None:
    root_a, _ = suite_roots
    # The same file via duplicate roots is one candidate, not an ambiguity.
    selected = select_suites("smoke-suite.yaml", [root_a, root_a])
    assert selected == [root_a / "nightly" / "smoke-suite.yaml"]


def test_select_suites_existing_file_bypasses_search(
    tmp_path: Path, suite_roots: tuple[Path, Path]
) -> None:
    root_a, _ = suite_roots
    outside = tmp_path / "outside.yaml"
    outside.touch()
    assert select_suites(str(outside), [root_a]) == [outside]


def test_select_suites_invalid_regex_raises(suite_roots: tuple[Path, Path]) -> None:
    root_a, _ = suite_roots
    with pytest.raises(ValueError, match=r"\*invalid\["):
        select_suites("*invalid[", [root_a])
