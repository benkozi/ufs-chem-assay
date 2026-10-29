"""Staging a suite's declared inputs into the application checkout's data
directory, through the S3 wrapper: one filtered `aws s3 sync` of the object's
parent prefix per missing file, verified by digest in a staging directory,
then moved into place. An input with no remote source (url null) is never
downloaded: absent, it is linked from the input search path (one basename
match under this machine's trees) and verified; present, it is verified;
with `record_digests`, a differing digest is recorded for the caller to
write into the suite (`rewrite_suite_digests`). Nothing here raises for a
transfer failure — every input is attempted and the outcome is returned; the
caller (the CLI's exit code) decides what a failure means. The pytest
session never calls this: it only checks presence (`missing_inputs`) and
fails fast.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import ConfigDict, Field
from ulid import ULID

from logs import get_logger
from models.base import StrictModel
from models.suite_config import InputFile, SuiteConfig
from platforms import Platform
from s3_sync import S3SyncConfig, check_reachable, sync

logger = get_logger("staging")

# Per-fetch staging directories live here, under the data directory, so the
# final move is a rename on one filesystem; a leftover from a killed run is
# removed at the start of the next staging pass.
STAGING_DIRNAME = ".staging"
_CHUNK = 1024 * 1024
ENV_INPUT_SEARCH_PATH = "ASSAY_INPUT_SEARCH_PATH"

Action = Literal[
    "skipped", "downloaded", "would-fetch", "linked", "would-link", "recorded", "failed"
]


class StagedInput(StrictModel):
    """What staging did (or would do) for one declared input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str | None = Field(
        description="The input's S3 object URI; null for one with no remote source"
    )
    path: Path = Field(description="Host path the input is staged at")
    action: Action = Field(
        description=(
            "skipped: present and verified (or present, no digest declared); "
            "downloaded: fetched, verified, moved into place; would-fetch: a dry "
            "run's plan; linked: a source-less input symlinked from the input "
            "search path and verified; would-link: that plan; recorded: a "
            "source-less input whose on-disk digest the caller is to write into "
            "the suite (sha256 holds it); failed: see detail"
        )
    )
    detail: str = Field(description="Why: the verification outcome or the error")
    sha256: str | None = Field(
        description="Digest of the file on disk when one was computed; null otherwise"
    )


def merge_inputs(suites: Sequence[SuiteConfig]) -> list[InputFile]:
    """Every selected suite's inputs, identical entries deduplicated (several
    suites of one configuration naturally declare the same file), in
    first-seen order. The same `dst` declared two different ways is a
    ValueError before any transfer."""
    merged: dict[PurePosixPath, tuple[InputFile, str]] = {}
    for suite in suites:
        for entry in suite.inputs:
            existing = merged.get(entry.dst)
            if existing is None:
                merged[entry.dst] = (entry, suite.name)
            elif existing[0] != entry:
                raise ValueError(
                    f"inputs: {str(entry.dst)!r} is declared differently by suites "
                    f"{existing[1]!r} and {suite.name!r} (url/public/sha256 must agree)"
                )
    return [entry for entry, _ in merged.values()]


def missing_inputs(inputs: Sequence[InputFile], data_dir: Path) -> list[Path]:
    """The declared inputs with no file at their destination (existence only —
    the session's fail-fast guard; digests are staging's business)."""
    return [
        data_dir / entry.dst for entry in inputs if not (data_dir / entry.dst).is_file()
    ]


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            sha.update(chunk)
    return sha.hexdigest()


def _present(entry: InputFile, target: Path) -> tuple[bool, str, str | None]:
    """(usable as-is, detail, digest-if-computed) for the file at target."""
    if not target.is_file():
        return False, "absent", None
    if entry.sha256 is None:
        if target.stat().st_size > 0:
            return True, "present, no digest declared", None
        return False, "present but empty", None
    digest = _digest(target)
    if digest == entry.sha256:
        return True, "sha256 verified", digest
    return False, f"present but sha256 {digest} != declared {entry.sha256}", digest


def _sync_config(entry: InputFile, destination: Path, *, dry_run: bool) -> S3SyncConfig:
    # One object through the wrapper's one verb: the parent prefix, filtered
    # to the file name. A dry run keeps the per-file lines (they are the plan).
    return S3SyncConfig(
        source=entry.parent_prefix,
        destination=destination,
        exclude=["*"],
        include=[entry.filename],
        no_sign_request=entry.public,
        only_show_errors=not dry_run,
        dry_run=dry_run,
    )


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        output = (exc.output or "").strip()
        tail = output.splitlines()[-1] if output else ""
        return f"aws s3 sync exited {exc.returncode}: {tail}"
    return str(exc)


def _fetch(entry: InputFile, target: Path, staging_root: Path) -> StagedInput:
    staging_dir = staging_root / str(ULID())
    try:
        sync(_sync_config(entry, staging_dir, dry_run=False))
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        return StagedInput(
            url=entry.url,
            path=target,
            action="failed",
            detail=_error_detail(exc),
            sha256=None,
        )
    staged = staging_dir / entry.filename
    if not staged.is_file():
        shutil.rmtree(staging_dir, ignore_errors=True)
        return StagedInput(
            url=entry.url,
            path=target,
            action="failed",
            detail=f"not fetched: no such object {entry.filename!r} under {entry.parent_prefix}",
            sha256=None,
        )
    digest = _digest(staged)
    if entry.sha256 is not None and digest != entry.sha256:
        shutil.rmtree(staging_dir, ignore_errors=True)
        return StagedInput(
            url=entry.url,
            path=target,
            action="failed",
            detail=f"sha256 mismatch after download: got {digest}, declared {entry.sha256}",
            sha256=digest,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged, target)
    shutil.rmtree(staging_dir, ignore_errors=True)
    return StagedInput(
        url=entry.url,
        path=target,
        action="downloaded",
        detail="sha256 verified" if entry.sha256 else "downloaded, no digest declared",
        sha256=digest,
    )


def _find_source(name: str, search_path: Sequence[Path]) -> list[Path]:
    """Every file called `name` under the search-path directories (recursive,
    sorted): the one link target, or the ambiguity to report."""
    found: list[Path] = []
    for directory in search_path:
        if directory.is_dir():
            found += sorted(p for p in directory.rglob(name) if p.is_file())
    return found


def _link_source_less(
    target: Path,
    search_path: Sequence[Path],
    *,
    dry_run: bool,
    declared: PurePosixPath | None = None,
    platform: Platform | None = None,
) -> tuple[str | None, str]:
    """Link an absent source-less input: from its declared source for this
    platform when the suite names one (a missing declared file is a named
    failure, never a fallback), else from the one basename match under the
    search path. Returns (error, note): error set when nothing was (or
    would be) linked; note describes what happened for the result's detail."""
    if declared is not None:
        source = Path(declared)
        if not source.is_file():
            return (
                f"declared source for {platform} is missing: {declared}; "
                f"place or link the file at {target}"
            ), ""
    else:
        search = ", ".join(str(d) for d in search_path)
        if not search_path:
            return (
                f"absent and no input search path is set ({ENV_INPUT_SEARCH_PATH}); "
                f"place or link the file at {target}"
            ), ""
        found = _find_source(target.name, search_path)
        if not found:
            return (
                f"absent and not found under the input search path ({search}); "
                f"place or link the file at {target}"
            ), ""
        if len(found) > 1:
            return (
                f"absent and ambiguous under the input search path: "
                f"{', '.join(str(p) for p in found)}; place or link the file at {target}"
            ), ""
        (source,) = found
    if dry_run:
        return None, f"would link to {source}"
    if target.is_symlink():  # dangling: is_file() was False
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source.resolve())
    return None, f"linked to {source}"


def _stage_source_less(
    entry: InputFile,
    target: Path,
    search_path: Sequence[Path],
    *,
    dry_run: bool,
    record_digests: bool,
    platform: Platform | None,
) -> StagedInput:
    """The url-null path: present → verify (and record when asked); absent →
    link from the declared source for this platform or the search path,
    then verify. Never the network."""
    assert entry.sha256 is not None  # the model requires it without a url
    note = ""
    if not target.is_file():
        declared = (
            entry.sources.get(platform)
            if entry.sources is not None and platform is not None
            else None
        )
        error, note = _link_source_less(
            target, search_path, dry_run=dry_run, declared=declared, platform=platform
        )
        if error is not None:
            return StagedInput(
                url=None, path=target, action="failed", detail=error, sha256=None
            )
        if dry_run:
            return StagedInput(
                url=None, path=target, action="would-link", detail=note, sha256=None
            )
        note = f"{note}; "
    usable, detail, digest = _present(entry, target)
    if usable:
        return StagedInput(
            url=None,
            path=target,
            action="linked" if note else "skipped",
            detail=f"{note}{detail}",
            sha256=digest,
        )
    if record_digests and digest is not None:
        return StagedInput(
            url=None,
            path=target,
            action="recorded",
            detail=f"{note}sha256 recorded: {digest} (was {entry.sha256})",
            sha256=digest,
        )
    return StagedInput(
        url=None,
        path=target,
        action="failed",
        detail=f"{note}{detail}; no source to re-fetch from — replace the file at {target}",
        sha256=digest,
    )


def stage_inputs(
    inputs: Sequence[InputFile],
    data_dir: Path,
    *,
    dry_run: bool = False,
    search_path: Sequence[Path] = (),
    record_digests: bool = False,
    platform: Platform | None = None,
) -> list[StagedInput]:
    """Stage every input into data_dir: present-and-verified files are
    skipped without touching the network; the rest are fetched one by one
    (every input attempted even after a failure). An input with no remote
    source (url null) is never fetched: absent, it is symlinked from the
    suite's declared source for `platform` when there is one, else from the
    one basename match under `search_path` (none or several is a failure
    naming the path to place the file at), and then verified like any
    present file;
    with `record_digests`, a present file whose digest differs from the
    declared one is reported `recorded` with its digest instead of failed.
    A dry run classifies, plans links, and passes the wrapper's own dry run
    through for the files it would fetch; it writes nothing."""
    staging_root = data_dir / STAGING_DIRNAME
    if staging_root.exists():
        logger.info("removing stale staging directory %s", staging_root)
        shutil.rmtree(staging_root)
    results: list[StagedInput] = []
    # Fail fast without network: one probe per bucket, only once a fetch is
    # actually needed (present, verified files never touch the network).
    unreachable: dict[str, str] = {}
    for entry in inputs:
        target = data_dir / entry.dst
        if entry.url is None:
            result = _stage_source_less(
                entry,
                target,
                search_path,
                dry_run=dry_run,
                record_digests=record_digests,
                platform=platform,
            )
            log = logger.error if result.action == "failed" else logger.info
            log("%s: %s (%s)", target, result.action, result.detail)
            results.append(result)
            continue
        usable, detail, digest = _present(entry, target)
        if usable:
            logger.info("%s: skipped (%s)", target, detail)
            results.append(
                StagedInput(
                    url=entry.url,
                    path=target,
                    action="skipped",
                    detail=detail,
                    sha256=digest,
                )
            )
            continue
        logger.info("%s: %s; fetching %s", target, detail, entry.url)
        bucket = entry.url.split("/", 3)[2]
        if bucket not in unreachable:
            try:
                check_reachable(entry.url)
                unreachable[bucket] = ""
            except ConnectionError as exc:
                unreachable[bucket] = str(exc)
        if unreachable[bucket]:
            logger.error("%s: %s", target, unreachable[bucket])
            results.append(
                StagedInput(
                    url=entry.url,
                    path=target,
                    action="failed",
                    detail=unreachable[bucket],
                    sha256=None,
                )
            )
            continue
        if dry_run:
            try:
                sync(_sync_config(entry, staging_root / "dry-run", dry_run=True))
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                results.append(
                    StagedInput(
                        url=entry.url,
                        path=target,
                        action="failed",
                        detail=_error_detail(exc),
                        sha256=None,
                    )
                )
                continue
            results.append(
                StagedInput(
                    url=entry.url,
                    path=target,
                    action="would-fetch",
                    detail=detail,
                    sha256=None,
                )
            )
            continue
        result = _fetch(entry, target, staging_root)
        log = logger.error if result.action == "failed" else logger.info
        log("%s: %s (%s)", target, result.action, result.detail)
        results.append(result)
    if staging_root.exists():
        shutil.rmtree(staging_root, ignore_errors=True)
    return results


# ── Recording digests into a suite file ───────────────────────────────────────

_DST_LINE = re.compile(
    r"^(?P<indent>[ \t]*)(?:-[ \t]+)?dst:[ \t]*(?P<dst>\S+)[ \t]*(?:#.*)?$"
)
_ITEM_START = re.compile(r"^[ \t]*-[ \t]+\w")
_SHA_LINE = re.compile(
    r"^(?P<head>[ \t]*(?:-[ \t]+)?sha256:[ \t]*)(?P<quote>['\"]?)(?P<value>[^'\"#\s]+)(?P=quote)(?P<tail>[ \t]*(?:#.*)?)$"
)


def _entry_sha_line(lines: list[str], dst: PurePosixPath) -> int:
    """The index of the `sha256:` line of the inputs entry whose `dst:` is
    `dst`: the entry runs from its `- ` item start to the next item start
    or the next less-indented line."""
    dst_index = next(
        (
            i
            for i, line in enumerate(lines)
            if (m := _DST_LINE.match(line)) and m["dst"].strip("'\"") == str(dst)
        ),
        None,
    )
    if dst_index is None:
        raise ValueError(f"no `dst: {dst}` entry")
    start = dst_index
    while start > 0 and not _ITEM_START.match(lines[start]):
        start -= 1
    item_indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and (
            _ITEM_START.match(line) or len(line) - len(line.lstrip()) <= item_indent
        ):
            break
        end += 1
    for i in range(start, end):
        if _SHA_LINE.match(lines[i]):
            return i
    raise ValueError(f"the `dst: {dst}` entry has no `sha256:` line")


def rewrite_suite_digests(path: Path, updates: dict[PurePosixPath, str]) -> None:
    """Rewrite the `sha256:` line of each named inputs entry in place, every
    other byte kept (the suite files are hand-commented, which a YAML dump
    would lose), verified by reloading: the suite must equal the previous
    one with exactly those digests changed, or the original text is
    restored. The ULID repoint's pattern (baselines/suite.py)."""
    from applications.registry import load_suite

    original = path.read_text()
    expected = load_suite(path).model_dump()
    lines = original.splitlines(keepends=True)
    for dst, digest in updates.items():
        index = _entry_sha_line(lines, dst)
        match = _SHA_LINE.match(lines[index])
        assert match is not None  # _entry_sha_line found it by this pattern
        lines[index] = (
            f"{match['head']}{match['quote']}{digest}{match['quote']}{match['tail']}"
            + ("\n" if lines[index].endswith("\n") else "")
        )
        for entry in expected["inputs"]:
            if PurePosixPath(entry["dst"]) == dst:
                entry["sha256"] = digest
    path.write_text("".join(lines))
    try:
        after = load_suite(path).model_dump()
    except (
        ValueError,
        OSError,
        yaml.YAMLError,
    ) as exc:  # ValidationError is a ValueError
        path.write_text(original)
        raise ValueError(
            f"{path}: reload after recording failed ({exc}); restored"
        ) from exc
    if after != expected:
        path.write_text(original)
        raise ValueError(
            f"{path}: reload differs beyond the recorded digests; restored"
        )
