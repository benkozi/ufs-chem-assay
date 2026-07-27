"""Pure suite-selection and output-root resolution rules.

Kept free of pytest so the rules are unit-testable; the integration conftest
converts errors to pytest.UsageError at the boundary.
"""

import re
from pathlib import Path, PurePosixPath

CONTAINER_WORK = PurePosixPath("/work")


def resolve_output_roots(option: str, cece_root: Path) -> tuple[Path, PurePosixPath]:
    """Map an explicit --combo-output-root to (host path, container path).

    Relative paths resolve against /work; absolute paths must lie under
    /work (ValueError otherwise).
    """
    given = PurePosixPath(option)
    if given.is_absolute():
        if not given.is_relative_to(CONTAINER_WORK):
            raise ValueError(
                f"--combo-output-root must be relative or under {CONTAINER_WORK}, got {option!r}"
            )
        relative = given.relative_to(CONTAINER_WORK)
    else:
        relative = given
    return cece_root / relative, CONTAINER_WORK / relative


def select_suites(option: str, search_paths: list[Path]) -> list[Path]:
    """Select every suite file matching --suite-config.

    An existing file path is used verbatim (escape hatch). Otherwise the
    option is a regex matched (fullmatch, like sweep regexes) against every
    *.yaml found recursively under the search roots — against each file's
    name and its posix path relative to its root; the same file reachable
    via overlapping roots counts once. Every match is selected — one match
    is a single-suite session, several a multi-suite session — ordered by
    file name (then path) for determinism. Zero matches raise ValueError
    listing the candidates.
    """
    if Path(option).is_file():
        return [Path(option)]
    try:
        pattern = re.compile(option)
    except re.error as exc:
        raise ValueError(
            f"--suite-config {option!r} is neither an existing file nor a "
            f"valid regex: {exc}"
        ) from exc

    seen: set[Path] = set()
    candidates: list[str] = []
    matches: list[Path] = []
    for root in search_paths:
        for path in sorted(root.rglob("*.yaml")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            relative = path.relative_to(root).as_posix()
            candidates.append(f"{relative}  [{root}]")
            if pattern.fullmatch(path.name) or pattern.fullmatch(relative):
                matches.append(path)

    if not matches:
        listing = "\n  ".join(candidates) if candidates else "<none>"
        raise ValueError(
            f"--suite-config {option!r} matches no suite; discovered suites:\n  {listing}"
        )
    return sorted(matches, key=lambda path: (path.name, path.as_posix()))
