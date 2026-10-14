"""The download half: every ULID the selected suites pin, present under the
baseline root — a present directory is used as is (no network); an absent
one is synced from the store, S3 through the wrapper into a staging
directory and moved into place only when it holds NetCDF, a directory
store by copy. Called by `fetch` (the login node's pre-warm) and at
session start."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from baselines.models import CONVENTIONS, BaselineState, StagedBaseline
from baselines.store import error_detail, is_s3, location
from logs import get_logger
from models.suite_config import SuiteConfig
from s3_sync import S3SyncConfig, check_reachable, sync

logger = get_logger("baselines")


def _pinned(suites: Sequence[SuiteConfig]) -> list[tuple[str, str]]:
    """(suite name, ulid) for every pinned ULID, first suite wins."""
    seen: set[str] = set()
    pinned: list[tuple[str, str]] = []
    for suite in suites:
        for entry in suite.baseline_comparisons:
            if entry.ulid not in seen:
                seen.add(entry.ulid)
                pinned.append((suite.name, entry.ulid))
    return pinned


def _sync_into(store: str, ulid: str, staging_dir: Path) -> str | None:
    """Bring <store>/<ulid> into staging_dir; the error to report, or None."""
    if not is_s3(store):
        source = Path(store) / ulid
        if not source.is_dir():
            return f"{ulid} is not in the store {store}"
        shutil.copytree(source, staging_dir)
        return None
    try:
        sync(
            S3SyncConfig(
                source=location(store, ulid),
                destination=staging_dir,
                only_show_errors=True,
            )
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return error_detail(exc)
    if not any(staging_dir.glob("*.nc")):
        # aws exits 0 and downloads nothing for a prefix that does not exist.
        return f"{ulid} is not in the store {store} (nothing downloaded)"
    return None


class _Reachability:
    """The store probed once, lazily, only when a sync is needed."""

    def __init__(self, store: str) -> None:
        self.store = store
        self.error: str | None = None
        self._probed = not is_s3(store)  # a directory store needs no network

    def error_if_unreachable(self) -> str | None:
        if not self._probed:
            self._probed = True
            try:
                check_reachable(self.store)
            except ConnectionError as exc:
                self.error = f"{exc}; stage baselines from a node with network: ufs-chem-assay fetch"
        return self.error


def ensure_baselines(
    suites: Sequence[SuiteConfig],
    store: str,
    baseline_root: Path | None,
    *,
    dry_run: bool = False,
) -> list[StagedBaseline]:
    """One record per pinned ULID: local (present), synced (brought into
    place), would-sync (a dry run's plan), or failed with the reason. With
    no baseline root nothing is written (the harness never writes into the
    cwd) and an absent baseline fails naming the setting; no network fails
    every absent baseline naming `fetch`. Never raises for one baseline."""
    root = baseline_root if baseline_root is not None else Path.cwd()
    staging_root = root / CONVENTIONS.staging_dirname
    reachability = _Reachability(store)
    results: list[StagedBaseline] = []
    for suite_name, ulid in _pinned(suites):
        path = root / ulid
        state, detail = _stage_one(
            store,
            ulid,
            path,
            staging_root,
            reachability,
            root_set=baseline_root is not None,
            dry_run=dry_run,
        )
        (logger.error if state == "failed" else logger.info)(
            "baseline %s: %s (%s)", ulid, state, detail
        )
        results.append(
            StagedBaseline(
                suite=suite_name,
                ulid=ulid,
                source=location(store, ulid),
                path=path,
                state=state,
                detail=detail,
            )
        )
    if staging_root.exists():
        shutil.rmtree(staging_root, ignore_errors=True)
    return results


def _stage_one(
    store: str,
    ulid: str,
    path: Path,
    staging_root: Path,
    reachability: _Reachability,
    *,
    root_set: bool,
    dry_run: bool,
) -> tuple[BaselineState, str]:
    if path.is_dir():
        return "local", "present"
    if not root_set:
        return "failed", (
            f"baseline {ulid} is absent and ASSAY_BASELINE_ROOT_DIR is unset: "
            "set it to let the harness sync baselines there"
        )
    if dry_run:
        return "would-sync", "absent; would sync"
    unreachable = reachability.error_if_unreachable()
    if unreachable is not None:
        return "failed", unreachable
    staging_dir = staging_root / ulid
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.parent.mkdir(parents=True, exist_ok=True)
    error = _sync_into(store, ulid, staging_dir)
    if error is not None:
        shutil.rmtree(staging_dir, ignore_errors=True)
        return "failed", error
    path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging_dir, path)
    return "synced", f"synced from {store}"
