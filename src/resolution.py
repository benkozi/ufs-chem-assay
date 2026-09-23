"""Pure suite-selection and output-root resolution rules.

Kept free of pytest so the rules are unit-testable; the integration conftest
converts errors to pytest.UsageError at the boundary.
"""

import re
from pathlib import Path, PurePosixPath

from platforms import Runtime


def resolve_output_roots(
    option: str,
    root_dir: Path,
    runtime: Runtime = Runtime.DOCKER,
    *,
    container_workdir: PurePosixPath,
) -> tuple[Path, PurePosixPath]:
    """Map an explicit --combo-output-root to (host path, driver-side path).

    docker: relative paths resolve against the application's container
    workdir (host: under root_dir, the checkout); absolute paths must lie
    under that workdir (ValueError otherwise), and the driver-side path is
    the container one.
    native / slurm: relative paths resolve against the checkout too — the
    same host location on every platform — and any absolute host path is
    accepted; the driver sees host paths, so both sides are the same path.
    """
    given = PurePosixPath(option)
    if runtime is not Runtime.DOCKER:
        host = Path(option) if given.is_absolute() else root_dir / option
        return host, PurePosixPath(host)
    if given.is_absolute():
        if not given.is_relative_to(container_workdir):
            raise ValueError(
                f"--combo-output-root must be relative or under {container_workdir}, got {option!r}"
            )
        relative = given.relative_to(container_workdir)
    else:
        relative = given
    return root_dir / relative, container_workdir / relative


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
