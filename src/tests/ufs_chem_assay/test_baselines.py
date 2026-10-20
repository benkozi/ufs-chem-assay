"""baselines.py: publishing a run's compared combinations as baselines — the
gate, the plan, the probe/manifest/sync/cache/suite-edit sequence (the S3
wrapper mocked: no aws, no network), the resume rule, and the textual suite
repoint. Fixtures build a run the way a session leaves it: run.yaml,
combos.csv, test-report.csv, one directory per combination."""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from applications.registry import load_suite
from baselines import (
    CONVENTIONS,
    BaselineManifest,
    PublishRecord,
    default_selector,
    ensure_baselines,
    load_run,
    plan,
    publish,
    publish_session,
    publishable,
    rewrite_suite_ulid,
)
from comparison import resolve_baseline_comparisons
from identity import HARNESS_NAME
from platforms import Platform, Runtime
from s3_sync import S3SyncConfig, S3SyncResult
from tests.ufs_chem_assay.baseline_runs import (
    COMMIT,
    HARNESS_COMMIT,
    HARNESS_VERSION,
    PREVIOUS_ULIDS,
    RUN_ID,
    Run,
    fabricate_run,
    fake_sync,
    report_rows,
)

_STORE = "s3://ufs-chem/baselines"


@pytest.fixture
def run(tmp_path: Path, suite_dir: Path) -> Run:
    return fabricate_run(tmp_path, suite_dir)


def _plan(fixture: Run, **overrides: object) -> list[PublishRecord]:
    kwargs: dict[str, object] = {
        "run": fixture.manifest,
        "suites": [fixture.suite_input],
        "rows": fixture.rows,
        "output_root": fixture.root,
        "store": _STORE,
    }
    kwargs.update(overrides)
    return plan(**kwargs)  # type: ignore[arg-type]


def _publish(
    fixture: Run, records: list[PublishRecord], **overrides: object
) -> list[PublishRecord]:
    kwargs: dict[str, object] = {
        "run": fixture.manifest,
        "output_root": fixture.root,
        "baseline_root": None,
        "dry_run": False,
        "suite_update": True,
    }
    kwargs.update(overrides)
    return publish(records, **kwargs)  # type: ignore[arg-type]


# ── the gate ──────────────────────────────────────────────────────────────────


def test_gate_passes_when_only_the_old_comparison_failed(run: Run) -> None:
    combo = run.combos[0]
    ok, reason = publishable(
        report_rows("s", combo, failed=("test_baseline_comparison",))
    )
    assert ok
    assert reason == "driver passed; no other test failed"


def test_gate_fails_without_the_driver_test(run: Run) -> None:
    combo = run.combos[0]
    rows = [r for r in report_rows("s", combo) if "driver" not in r.pytest_name]
    ok, reason = publishable(rows)
    assert not ok
    assert "test_driver_execution" in reason
    assert "did not run" in reason
    assert publishable([]) == (False, "test_driver_execution did not run")


def test_gate_fails_on_a_failed_driver_or_output_test(run: Run) -> None:
    combo = run.combos[0]
    ok, reason = publishable(report_rows("s", combo, failed=("test_driver_execution",)))
    assert not ok
    assert "test_driver_execution failed" in reason
    ok, reason = publishable(report_rows("s", combo, failed=("test_nc_file_count",)))
    assert not ok
    assert "test_nc_file_count" in reason


def test_gate_allows_skips(run: Run) -> None:
    combo = run.combos[0]
    rows = report_rows("s", combo)
    rows[4] = rows[4].model_copy(update={"result": "skipped"})  # stats off
    assert publishable(rows)[0]


# ── plan ──────────────────────────────────────────────────────────────────────


def test_plan_one_would_publish_record_per_entry(run: Run) -> None:
    records = _plan(run)
    assert [r.action for r in records] == ["would-publish"] * 3
    assert [r.combo for r in records] == [
        "MACCITY.map-bilinear",
        "MACCITY.map-consd",
        "MACCITY.map-passthrough",
    ]
    bilinear = records[0]
    assert bilinear.suite == "simple-maccity"
    assert bilinear.suite_path == run.suite_path
    assert bilinear.ulid == run.combo("MACCITY.map-bilinear").combo_id
    assert bilinear.previous_ulid == PREVIOUS_ULIDS[0]
    assert bilinear.source_dir == run.combo_dir("MACCITY.map-bilinear")
    assert bilinear.store == _STORE
    assert bilinear.destination == f"{_STORE}/{bilinear.ulid}/"
    assert not bilinear.suite_updated


def test_plan_skips_gated_out_combinations(run: Run) -> None:
    consd = run.combo("MACCITY.map-consd")
    rows = [
        r.model_copy(update={"result": "failed"})
        if r.combo == consd.name and "driver" in r.pytest_name
        else r
        for r in run.rows
    ]
    records = _plan(run, rows=rows)
    by_combo = {r.combo: r for r in records}
    assert by_combo["MACCITY.map-consd"].action == "skipped"
    assert "test_driver_execution failed" in by_combo["MACCITY.map-consd"].detail
    assert by_combo["MACCITY.map-bilinear"].action == "would-publish"


def test_plan_skips_an_already_pinned_ulid(run: Run) -> None:
    consd = run.combo("MACCITY.map-consd")
    rewrite_suite_ulid(run.suite_path, PREVIOUS_ULIDS[1], consd.combo_id)
    suite = load_suite(run.suite_path)
    suite_input = run.suite_input.model_copy(
        update={
            "suite": suite,
            "baselines": resolve_baseline_comparisons(
                run.app, suite.baseline_comparisons, run.combos
            ),
        }
    )
    records = _plan(run, suites=[suite_input])
    by_combo = {r.combo: r for r in records}
    assert by_combo["MACCITY.map-consd"].action == "skipped"
    assert by_combo["MACCITY.map-consd"].detail == "already published"
    assert by_combo["MACCITY.map-bilinear"].action == "would-publish"


def test_plan_fails_every_record_without_an_application_commit(run: Run) -> None:
    manifest = run.manifest.model_copy(update={"application_commit": None})
    records = _plan(run, run=manifest)
    assert {r.action for r in records} == {"failed"}
    assert all("application commit" in r.detail for r in records)


def test_plan_fails_a_missing_directory_and_skips_one_without_netcdf(run: Run) -> None:
    shutil.rmtree(run.combo_dir("MACCITY.map-bilinear"))
    for path in run.combo_dir("MACCITY.map-consd").glob("*.nc"):
        path.unlink()
    by_combo = {r.combo: r for r in _plan(run)}
    assert by_combo["MACCITY.map-bilinear"].action == "failed"
    assert "missing" in by_combo["MACCITY.map-bilinear"].detail
    assert by_combo["MACCITY.map-consd"].action == "skipped"
    assert "NetCDF" in by_combo["MACCITY.map-consd"].detail
    assert by_combo["MACCITY.map-passthrough"].action == "would-publish"


# ── load_run (the CLI's reader) ──────────────────────────────────────────────


def test_load_run_reads_the_output_root_and_joins_combos_csv(run: Run) -> None:
    manifest, suites, rows = load_run(run.root, run.app, [(run.suite_path, run.suite)])
    assert manifest == run.manifest
    (suite_input,) = suites
    assert suite_input.path == run.suite_path
    assert suite_input.combo_ids == {c.name: c.combo_id for c in run.combos}
    assert set(suite_input.baselines) == set(suite_input.combo_ids)
    assert len(rows) == len(run.rows)
    assert {r.combo_id for r in rows} == {c.combo_id for c in run.combos}
    # The plan from the file-backed inputs equals the in-memory one.
    records = plan(manifest, suites, rows, run.root, _STORE)
    assert records == _plan(run)


def test_load_run_refuses_a_suite_edited_since_the_run(run: Run) -> None:
    # A repointed ulid is not an edit (that is what publishing does — a
    # second publish of the same root must still load and skip); any other
    # change to the entries is.
    consd = run.combo("MACCITY.map-consd")
    rewrite_suite_ulid(run.suite_path, PREVIOUS_ULIDS[1], consd.combo_id)
    _, suites, _ = load_run(
        run.root, run.app, [(run.suite_path, load_suite(run.suite_path))]
    )
    assert suites[0].baselines["MACCITY.map-consd"].ulid == consd.combo_id
    text = run.suite_path.read_text().replace(
        f"    ulid: {consd.combo_id}\n", f"    ulid: {consd.combo_id}\n    atol: 0.5\n"
    )
    run.suite_path.write_text(text)
    edited = load_suite(run.suite_path)
    with pytest.raises(ValueError, match=r"simple-maccity.*baseline_comparisons"):
        load_run(run.root, run.app, [(run.suite_path, edited)])


def test_load_run_refuses_a_suite_the_run_did_not_execute(
    run: Run, suite_dir: Path
) -> None:
    other = load_suite(suite_dir / "exhaustive-maccity-run-only-suite.yaml")
    with pytest.raises(ValueError, match="exhaustive-maccity-run-only"):
        load_run(run.root, run.app, [(run.suite_path, run.suite), (Path("x"), other)])


def test_default_selector_names_the_runs_suites(run: Run) -> None:
    assert default_selector(run.manifest) == r"(?:simple\-maccity)-suite\.yaml"


# ── publish ───────────────────────────────────────────────────────────────────


def _probe_and_upload(
    calls: list[S3SyncConfig], ulid: str
) -> tuple[S3SyncConfig, S3SyncConfig]:
    probe = next(c for c in calls if c.dry_run and c.source == f"{_STORE}/{ulid}/")
    upload = next(
        c for c in calls if isinstance(c.source, Path) and ulid in str(c.destination)
    )
    return probe, upload


def test_publish_probes_writes_the_manifest_syncs_and_repoints(
    run: Run, mocker: MockerFixture
) -> None:
    sync = mocker.patch("baselines.publish.sync", side_effect=fake_sync())
    before = datetime.now(UTC)
    records = _publish(run, _plan(run))
    assert [r.action for r in records] == ["published"] * 3
    assert all(r.suite_updated for r in records)
    calls = [call.args[0] for call in sync.call_args_list]
    assert len(calls) == 6  # one probe + one upload per baseline

    bilinear = records[0]
    probe, upload = _probe_and_upload(calls, bilinear.ulid)
    assert probe.dry_run
    assert isinstance(probe.destination, Path)
    assert upload.source == bilinear.source_dir
    assert upload.destination == bilinear.destination
    assert upload.only_show_errors
    assert not upload.dry_run
    assert upload.exclude == []
    assert upload.include == []
    assert not upload.delete

    manifest = BaselineManifest.model_validate(
        yaml.safe_load((bilinear.source_dir / CONVENTIONS.manifest).read_text())
    )
    assert manifest.ulid == bilinear.ulid
    assert manifest.store == _STORE
    assert manifest.application == "cece"
    assert manifest.application_commit == COMMIT
    assert manifest.suite == "simple-maccity"
    assert manifest.combo == "MACCITY.map-bilinear"
    assert manifest.run_id == RUN_ID
    assert manifest.harness_version == HARNESS_VERSION
    assert manifest.harness_commit == HARNESS_COMMIT
    assert manifest.platform is Platform.LOCAL
    assert manifest.runtime is Runtime.DOCKER
    assert manifest.launch is None  # docker: no job geometry to record
    assert manifest.published_at.tzinfo is not None
    assert manifest.published_at >= before
    assert "@" in manifest.published_by
    assert manifest.superseded_ulid == PREVIOUS_ULIDS[0]
    swept = [p for p in manifest.parameters if p.swept]
    assert [(p.target, p.field, p.value) for p in swept] == [
        ("MACCITY", "mapalgo", "bilinear")
    ]
    assert len(manifest.parameters) == 6
    paths = [f.path for f in manifest.files]
    assert paths == sorted(paths)
    assert f"{bilinear.ulid}.yaml" in paths
    assert "cece.log" in paths
    assert "plots-overview/co.gif" in paths
    assert CONVENTIONS.manifest not in paths
    first = next(f for f in manifest.files if f.path == "cece_20100101_010000.nc")
    payload = (bilinear.source_dir / "cece_20100101_010000.nc").read_bytes()
    assert first.bytes == len(payload)
    assert first.sha256 == hashlib.sha256(payload).hexdigest()

    # The suite now pins the three new ULIDs and nothing else changed.
    text = run.suite_path.read_text()
    for record in records:
        assert f"    ulid: {record.ulid}\n" in text
        assert record.previous_ulid not in text
    assert "# Each entry's sweep_selector mirrors `sweep`" in text  # comments kept
    reloaded = load_suite(run.suite_path)
    assert [e.ulid for e in reloaded.baseline_comparisons] == [r.ulid for r in records]


def test_publish_primes_the_local_cache_when_configured(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("baselines.publish.sync", side_effect=fake_sync())
    cache = tmp_path / "cache"
    records = _publish(run, _plan(run), baseline_root=cache)
    for record in records:
        cached = cache / record.ulid
        assert (cached / CONVENTIONS.manifest).is_file()
        assert (cached / "plots-overview" / "co.gif").is_file()
        assert sorted(p.name for p in cached.glob("*.nc")) == sorted(
            p.name for p in record.source_dir.glob("*.nc")
        )


def test_publish_refuses_an_occupied_prefix_on_a_fresh_publish(
    run: Run, mocker: MockerFixture
) -> None:
    records = _plan(run)
    occupied = frozenset({records[1].destination})
    sync = mocker.patch("baselines.publish.sync", side_effect=fake_sync(occupied))
    results = _publish(run, records)
    assert [r.action for r in results] == ["published", "failed", "published"]
    assert "append-only" in results[1].detail
    assert not (results[1].source_dir / CONVENTIONS.manifest).exists()
    uploads = [
        c for c in (call.args[0] for call in sync.call_args_list) if not c.dry_run
    ]
    assert len(uploads) == 2  # the refused one was never uploaded
    text = run.suite_path.read_text()
    assert PREVIOUS_ULIDS[1] in text  # the consd entry untouched
    assert records[0].previous_ulid not in text


def test_publish_resumes_when_the_directory_was_published_before(
    run: Run, mocker: MockerFixture
) -> None:
    # First publish, then the suite is reverted (git checkout): the prefix is
    # occupied but the directory's manifest names it — no upload, repoint only.
    mocker.patch("baselines.publish.sync", side_effect=fake_sync())
    original = run.suite_path.read_text()
    first = _publish(run, _plan(run))
    run.suite_path.write_text(original)
    sync = mocker.patch(
        "baselines.publish.sync",
        side_effect=fake_sync(frozenset(r.destination for r in first)),
    )
    manifest_before = (first[0].source_dir / CONVENTIONS.manifest).read_text()
    results = _publish(run, _plan(run))
    assert [r.action for r in results] == ["published"] * 3
    assert all("already in the store" in r.detail for r in results)
    calls = [call.args[0] for call in sync.call_args_list]
    assert len(calls) == 3
    assert all(c.dry_run for c in calls)
    assert (first[0].source_dir / CONVENTIONS.manifest).read_text() == manifest_before
    assert all(f"ulid: {r.ulid}\n" in run.suite_path.read_text() for r in results)


def test_publish_fails_a_directory_carrying_another_manifest(
    run: Run, mocker: MockerFixture
) -> None:
    sync = mocker.patch("baselines.publish.sync", side_effect=fake_sync())
    records = _plan(run)
    foreign = records[0].source_dir / CONVENTIONS.manifest
    foreign.write_text("ulid: 01KXNXCJ858EE6R4FPF39BC8V2\nstore: s3://elsewhere/b\n")
    results = _publish(run, records[:1])
    assert results[0].action == "failed"
    assert "manifest for" in results[0].detail
    sync.assert_not_called()


def test_publish_dry_run_writes_nothing(
    run: Run, tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    sync = mocker.patch("baselines.publish.sync", side_effect=fake_sync())
    original = run.suite_path.read_text()
    cache = tmp_path / "cache"
    with caplog.at_level("INFO"):
        results = _publish(run, _plan(run), baseline_root=cache, dry_run=True)
    assert [r.action for r in results] == ["would-publish"] * 3
    assert all(r.detail == "store free; would upload" for r in results)
    assert not any(r.suite_updated for r in results)
    calls = [call.args[0] for call in sync.call_args_list]
    assert len(calls) == 6
    assert all(c.dry_run for c in calls)
    uploads = [c for c in calls if isinstance(c.source, Path)]
    assert all(not c.only_show_errors for c in uploads)
    assert not any((r.source_dir / CONVENTIONS.manifest).exists() for r in results)
    assert not cache.exists()
    assert run.suite_path.read_text() == original
    assert any("superseded_ulid" in rec.getMessage() for rec in caplog.records)


def test_publish_without_suite_update_leaves_the_file_alone(
    run: Run, mocker: MockerFixture
) -> None:
    mocker.patch("baselines.publish.sync", side_effect=fake_sync())
    original = run.suite_path.read_text()
    results = _publish(run, _plan(run), suite_update=False)
    assert [r.action for r in results] == ["published"] * 3
    assert not any(r.suite_updated for r in results)
    assert run.suite_path.read_text() == original
    manifest = yaml.safe_load(
        (results[0].source_dir / CONVENTIONS.manifest).read_text()
    )
    assert manifest["superseded_ulid"] == PREVIOUS_ULIDS[0]


def test_publish_sync_failure_is_recorded_not_raised(
    run: Run, mocker: MockerFixture
) -> None:
    records = _plan(run)
    target = records[1].destination

    def failing(config: S3SyncConfig) -> S3SyncResult:
        if not config.dry_run and str(config.destination) == target:
            raise subprocess.CalledProcessError(1, ["aws"], output="boom\nfatal: no\n")
        return fake_sync()(config)

    mocker.patch("baselines.publish.sync", side_effect=failing)
    results = _publish(run, records)
    assert [r.action for r in results] == ["published", "failed", "published"]
    assert "fatal: no" in results[1].detail
    # The manifest stays as the record of the attempt (the next run resumes).
    assert (results[1].source_dir / CONVENTIONS.manifest).is_file()
    assert PREVIOUS_ULIDS[1] in run.suite_path.read_text()


def test_publish_only_touches_would_publish_records(
    run: Run, mocker: MockerFixture
) -> None:
    sync = mocker.patch("baselines.publish.sync", side_effect=fake_sync())
    records = _plan(run)
    skipped = records[0].model_copy(update={"action": "skipped", "detail": "x"})
    results = _publish(run, [skipped, *records[1:]])
    assert results[0] == skipped
    assert sync.call_count == 4


def test_publish_session_reads_run_yaml_and_publishes(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    sync = mocker.patch("baselines.publish.sync", side_effect=fake_sync())
    cache = tmp_path / "cache"
    results = publish_session(
        run.root,
        [run.suite_input],
        run.rows,
        store=_STORE,
        baseline_root=cache,
        suite_update=True,
    )
    assert [r.action for r in results] == ["published"] * 3
    assert sync.call_count == 6
    assert all((cache / r.ulid / CONVENTIONS.manifest).is_file() for r in results)
    assert all(f"ulid: {r.ulid}\n" in run.suite_path.read_text() for r in results)


# ── a directory store ─────────────────────────────────────────────────────────


def test_publish_to_a_directory_store_copies_and_repoints(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    sync = mocker.patch("baselines.publish.sync")
    store = tmp_path / "dropbox-store"
    store.mkdir()
    records = _plan(run, store=str(store))
    assert records[0].destination == str(store / records[0].ulid)
    results = _publish(run, records, baseline_root=store)  # the store is the cache
    assert [r.action for r in results] == ["published"] * 3
    assert all(r.suite_updated for r in results)
    sync.assert_not_called()
    for record in results:
        published = store / record.ulid
        manifest = BaselineManifest.model_validate(
            yaml.safe_load((published / CONVENTIONS.manifest).read_text())
        )
        assert manifest.store == str(store)
        assert manifest.ulid == record.ulid
        assert (published / "plots-overview" / "co.gif").is_file()
        assert sorted(p.name for p in published.glob("*.nc")) == sorted(
            p.name for p in record.source_dir.glob("*.nc")
        )
    assert all(f"ulid: {r.ulid}\n" in run.suite_path.read_text() for r in results)


def test_directory_store_is_append_only_and_resumes(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("baselines.publish.sync")
    store = tmp_path / "store"
    store.mkdir()
    records = _plan(run, store=str(store))
    (store / records[1].ulid).mkdir()  # occupied by something else
    results = _publish(run, records, baseline_root=None)
    assert [r.action for r in results] == ["published", "failed", "published"]
    assert "append-only" in results[1].detail
    # A reverted suite: the directories' manifests name the store -> resume.
    original_text = run.suite_path.read_text()
    for record in results:
        if record.action == "published":
            run.suite_path.write_text(
                run.suite_path.read_text().replace(record.ulid, record.previous_ulid)
            )
    again = _publish(run, _plan(run, store=str(store)), baseline_root=None)
    assert [r.action for r in again] == ["published", "failed", "published"]
    assert all(
        "already in the store" in r.detail for r in again if r.action == "published"
    )
    assert run.suite_path.read_text() == original_text


def test_directory_store_must_exist_and_dry_run_writes_nothing(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("baselines.publish.sync")
    store = tmp_path / "absent"
    results = _publish(run, _plan(run, store=str(store)))
    assert {r.action for r in results} == {"failed"}
    assert all("does not exist" in r.detail for r in results)
    store.mkdir()
    original = run.suite_path.read_text()
    results = _publish(run, _plan(run, store=str(store)), dry_run=True)
    assert [r.action for r in results] == ["would-publish"] * 3
    assert all(r.detail == "store free; would copy" for r in results)
    assert list(store.iterdir()) == []
    assert run.suite_path.read_text() == original


# ── rewrite_suite_ulid ────────────────────────────────────────────────────────


def test_rewrite_keeps_every_other_byte(run: Run) -> None:
    old = PREVIOUS_ULIDS[1]
    new = "01JNEWNEWNEWNEWNEWNEWNEWNE"
    before = run.suite_path.read_text()
    rewrite_suite_ulid(run.suite_path, old, new)
    after = run.suite_path.read_text()
    assert after == before.replace(f"ulid: {old}", f"ulid: {new}")
    assert after.count(new) == 1


def test_rewrite_refuses_zero_and_ambiguous_matches(run: Run) -> None:
    with pytest.raises(ValueError, match="no `ulid:` line"):
        rewrite_suite_ulid(
            run.suite_path, "01JABSENTABSENTABSENTABSEN", "01JNEWNEWNEWNEWNEWNEWNEWNE"
        )
    text = run.suite_path.read_text().replace(PREVIOUS_ULIDS[2], PREVIOUS_ULIDS[1])
    run.suite_path.write_text(text)
    with pytest.raises(ValueError, match="2 `ulid:` lines"):
        rewrite_suite_ulid(
            run.suite_path, PREVIOUS_ULIDS[1], "01JNEWNEWNEWNEWNEWNEWNEWNE"
        )
    assert run.suite_path.read_text() == text


def test_rewrite_restores_the_file_when_the_reload_differs(run: Run) -> None:
    before = run.suite_path.read_text()
    with pytest.raises(ValueError, match="reload"):
        rewrite_suite_ulid(run.suite_path, PREVIOUS_ULIDS[1], "not a ulid: [")
    assert run.suite_path.read_text() == before


# ── the download half: ensure_baselines ──────────────────────────────────────


def _ulids(run: Run) -> list[str]:
    return [e.ulid for e in run.suite.baseline_comparisons]


def _store_dir_with(tmp_path: Path, ulids: list[str]) -> Path:
    store = tmp_path / "store"
    for ulid in ulids:
        (store / ulid / "plots-overview").mkdir(parents=True)
        (store / ulid / "cece_20100101_010000.nc").write_bytes(b"nc " + ulid.encode())
        (store / ulid / CONVENTIONS.manifest).write_text(f"ulid: {ulid}\nstore: x\n")
    return store


def test_present_baselines_are_local_and_never_touch_the_network(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    root = tmp_path / "root"
    for ulid in _ulids(run):
        (root / ulid).mkdir(parents=True)
    probe = mocker.patch("baselines.stage.check_reachable")
    sync = mocker.patch("baselines.stage.sync")
    results = ensure_baselines([run.suite], _STORE, root)
    assert [r.state for r in results] == ["local"] * 3
    assert [r.ulid for r in results] == _ulids(run)
    assert all(r.path == root / r.ulid and r.suite == "simple-maccity" for r in results)
    probe.assert_not_called()
    sync.assert_not_called()


def test_absent_baselines_are_synced_from_s3_into_place(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    root = tmp_path / "root"
    probe = mocker.patch("baselines.stage.check_reachable")

    def fake(config: S3SyncConfig) -> S3SyncResult:
        assert isinstance(config.destination, Path)
        assert config.destination.parent == root / ".staging"
        config.destination.mkdir(parents=True, exist_ok=True)
        (config.destination / "cece_a.nc").write_bytes(b"nc")
        (config.destination / CONVENTIONS.manifest).write_text("ulid: x\n")
        return S3SyncResult(argv=config.argv, returncode=0, output="", dry_run=False)

    sync = mocker.patch("baselines.stage.sync", side_effect=fake)
    results = ensure_baselines([run.suite], _STORE, root)
    assert [r.state for r in results] == ["synced"] * 3
    probe.assert_called_once_with(_STORE)
    calls = [call.args[0] for call in sync.call_args_list]
    assert [c.source for c in calls] == [f"{_STORE}/{u}/" for u in _ulids(run)]
    assert all(c.only_show_errors and not c.dry_run and not c.delete for c in calls)
    for result in results:
        assert (root / result.ulid / "cece_a.nc").read_bytes() == b"nc"
        assert result.source == f"{_STORE}/{result.ulid}/"
    assert not (root / ".staging").exists()


def test_a_ulid_pinned_by_two_suites_is_synced_once(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    root = tmp_path / "root"
    mocker.patch("baselines.stage.check_reachable")
    mocker.patch("baselines.stage.sync", side_effect=fake_sync())
    results = ensure_baselines([run.suite, run.suite], _STORE, root, dry_run=True)
    assert len(results) == 3


def test_missing_in_the_store_and_sync_failures_are_recorded(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    import subprocess

    root = tmp_path / "root"
    mocker.patch("baselines.stage.check_reachable")
    ulids = _ulids(run)

    def fake(config: S3SyncConfig) -> S3SyncResult:
        if ulids[1] in str(config.source):
            raise subprocess.CalledProcessError(1, ["aws"], output="x\nAccessDenied\n")
        # ulids[0] and [2]: nothing downloaded (no such prefix) -> empty dir
        return S3SyncResult(argv=config.argv, returncode=0, output="", dry_run=False)

    mocker.patch("baselines.stage.sync", side_effect=fake)
    results = ensure_baselines([run.suite], _STORE, root)
    assert [r.state for r in results] == ["failed"] * 3
    assert results[0].detail == f"not in the store {_STORE} (nothing downloaded)"
    assert "AccessDenied" in results[1].detail
    assert not any((root / u).exists() for u in ulids)
    assert not (root / ".staging").exists()


def test_no_baseline_root_means_nothing_is_written(
    run: Run, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    probe = mocker.patch("baselines.stage.check_reachable")
    sync = mocker.patch("baselines.stage.sync")
    results = ensure_baselines([run.suite], _STORE, None)
    assert [r.state for r in results] == ["failed"] * 3
    assert all("ASSAY_BASELINE_ROOT_DIR" in r.detail for r in results)
    assert all(r.path == tmp_path / r.ulid for r in results)
    probe.assert_not_called()
    sync.assert_not_called()
    assert not any((tmp_path / r.ulid).exists() for r in results)
    assert not (tmp_path / ".staging").exists()


def test_unreachable_store_fails_every_absent_baseline_naming_fetch(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    root = tmp_path / "root"
    (root / _ulids(run)[0]).mkdir(parents=True)
    mocker.patch(
        "baselines.stage.check_reachable", side_effect=ConnectionError("no network")
    )
    sync = mocker.patch("baselines.stage.sync")
    results = ensure_baselines([run.suite], _STORE, root)
    assert [r.state for r in results] == ["local", "failed", "failed"]
    assert all("no network" in r.detail and "fetch" in r.detail for r in results[1:])
    sync.assert_not_called()


def test_states_are_logged_once_grouped_by_state_and_reason(
    run: Run, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One line per (state, reason) naming every ULID in it — not one line
    per baseline (a suite with many baselines would flood the session start)
    — and a reason never repeats the ULID the record already carries."""
    ulids = _ulids(run)
    store = _store_dir_with(tmp_path, ulids[1:])
    root = tmp_path / "root"
    (root / ulids[0]).mkdir(parents=True)
    with caplog.at_level("INFO", logger=f"{HARNESS_NAME}.baselines"):
        results = ensure_baselines([run.suite], str(store), root)
    assert [r.state for r in results] == ["local", "synced", "synced"]
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        f"1 baseline local ({ulids[0]}): present",
        f"2 baselines synced ({ulids[1]}, {ulids[2]}): from {store}",
    ]
    assert all(r.levelno == logging.INFO for r in caplog.records)
    caplog.clear()
    with caplog.at_level("INFO", logger=f"{HARNESS_NAME}.baselines"):
        results = ensure_baselines([run.suite], _STORE, None)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert (
        errors[0]
        .getMessage()
        .startswith(
            f"3 baselines failed ({', '.join(ulids)}): absent and ASSAY_BASELINE_ROOT_DIR"
        )
    )
    assert not any(ulid in r.detail for r in results for ulid in ulids)


def test_dry_run_classifies_without_the_network(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    root = tmp_path / "root"
    (root / _ulids(run)[2]).mkdir(parents=True)
    probe = mocker.patch("baselines.stage.check_reachable")
    sync = mocker.patch("baselines.stage.sync")
    results = ensure_baselines([run.suite], _STORE, root, dry_run=True)
    assert [r.state for r in results] == ["would-sync", "would-sync", "local"]
    probe.assert_not_called()
    sync.assert_not_called()
    assert sorted(p.name for p in root.iterdir()) == [_ulids(run)[2]]


def test_directory_store_baselines_are_copied(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    ulids = _ulids(run)
    store = _store_dir_with(tmp_path, ulids[:2])  # the third is not in the store
    root = tmp_path / "root"
    probe = mocker.patch("baselines.stage.check_reachable")
    sync = mocker.patch("baselines.stage.sync")
    results = ensure_baselines([run.suite], str(store), root)
    assert [r.state for r in results] == ["synced", "synced", "failed"]
    assert results[2].detail == f"not in the store {store}"
    for ulid in ulids[:2]:
        assert (root / ulid / "plots-overview").is_dir()
        assert (root / ulid / CONVENTIONS.manifest).is_file()
    probe.assert_not_called()
    sync.assert_not_called()


def test_directory_store_that_is_the_root_is_local(run: Run, tmp_path: Path) -> None:
    store = _store_dir_with(tmp_path, _ulids(run))
    results = ensure_baselines([run.suite], str(store), store)
    assert [r.state for r in results] == ["local"] * 3


# ── publish: the remaining failure branches ──────────────────────────────────


def test_directory_store_copy_failure_is_recorded(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    store = tmp_path / "store"
    store.mkdir()
    mocker.patch("baselines.publish.shutil.copytree", side_effect=OSError("disk full"))
    results = _publish(run, _plan(run, store=str(store)))
    assert {r.action for r in results} == {"failed"}
    assert all("disk full" in r.detail for r in results)


def test_cache_already_present_and_cache_copy_failure(
    run: Run, tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("baselines.publish.sync", side_effect=fake_sync())
    cache = tmp_path / "cache"
    records = _plan(run)
    (cache / records[0].ulid).mkdir(
        parents=True
    )  # a fresh publish into an occupied cache
    with caplog.at_level("WARNING"):
        results = _publish(run, records[:1], baseline_root=cache)
    assert results[0].action == "published"
    assert any("already exists" in r.getMessage() for r in caplog.records)
    mocker.patch("baselines.publish.shutil.copytree", side_effect=OSError("ro"))
    with caplog.at_level("ERROR"):
        results = _publish(run, records[1:2], baseline_root=cache)
    assert (
        results[0].action == "published"
    )  # the store has it; the cache is best effort
    assert any("cache copy" in r.getMessage() for r in caplog.records)


def test_repoint_failure_keeps_the_baseline_published(
    run: Run, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("baselines.publish.sync", side_effect=fake_sync())
    records = _plan(run)
    # The entry's ulid line vanishes before the repoint: nothing to rewrite.
    run.suite_path.write_text(
        run.suite_path.read_text().replace(
            records[0].previous_ulid, "01JGONEGONEGONEGONEGONEGON"
        )
    )
    with caplog.at_level("ERROR"):
        results = _publish(run, records[:1])
    assert results[0].action == "published"
    assert not results[0].suite_updated
    assert any("was not repointed" in r.getMessage() for r in caplog.records)


def test_dry_run_reports_a_resume(run: Run, mocker: MockerFixture) -> None:
    mocker.patch("baselines.publish.sync", side_effect=fake_sync())
    first = _publish(run, _plan(run))
    run.suite_path.write_text(
        run.suite_path.read_text().replace(first[0].ulid, first[0].previous_ulid)
    )
    mocker.patch(
        "baselines.publish.sync",
        side_effect=fake_sync(frozenset({first[0].destination})),
    )
    results = _publish(run, _plan(run)[:1], dry_run=True)
    assert results[0].action == "would-publish"
    assert "would resume" in results[0].detail


def test_staged_baseline_records_itself(run: Run) -> None:
    from models.suite_config import RecordedBaseline

    staged = ensure_baselines([run.suite], _STORE, None, dry_run=True)[0]
    recorded = staged.recorded()
    assert isinstance(recorded, RecordedBaseline)
    assert recorded.ulid == staged.ulid
    assert recorded.state == staged.state


def test_load_run_refuses_a_combination_missing_from_combos_csv(run: Run) -> None:
    csv = run.root / "combos.csv"
    lines = csv.read_text().splitlines()
    first_id = run.combos[0].combo_id
    csv.write_text("\n".join(line for line in lines if first_id not in line) + "\n")
    with pytest.raises(ValueError, match=r"not in combos\.csv"):
        load_run(run.root, run.app, [(run.suite_path, run.suite)])


def test_sync_clears_a_leftover_staging_directory(run: Run, tmp_path: Path) -> None:
    store = _store_dir_with(tmp_path, _ulids(run)[:1])
    root = tmp_path / "root"
    leftover = root / ".staging" / _ulids(run)[0]
    leftover.mkdir(parents=True)
    (leftover / "stale.nc").write_bytes(b"old")
    results = ensure_baselines([run.suite], str(store), root)
    assert results[0].state == "synced"
    assert not (root / _ulids(run)[0] / "stale.nc").exists()


def test_rewrite_restores_when_the_reload_differs_beyond_the_ulid(run: Run) -> None:
    before = run.suite_path.read_text()
    # A value that reloads fine but changes a second field: refused, restored.
    with pytest.raises(ValueError, match="differs beyond"):
        rewrite_suite_ulid(
            run.suite_path,
            PREVIOUS_ULIDS[1],
            "01JNEWNEWNEWNEWNEWNEWNEWNE\n    atol: 0.5",
        )
    assert run.suite_path.read_text() == before
