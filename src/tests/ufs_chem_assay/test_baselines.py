"""baselines.py: publishing a run's compared combinations as baselines — the
gate, the plan, the probe/manifest/sync/cache/suite-edit sequence (the S3
wrapper mocked: no aws, no network), the resume rule, and the textual suite
repoint. Fixtures build a run the way a session leaves it: run.yaml,
combos.csv, test-report.csv, one directory per combination."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from applications.registry import load_suite
from baselines import (
    BASELINE_MANIFEST,
    BaselineManifest,
    PublishRecord,
    default_selector,
    load_run,
    plan,
    publish,
    publish_session,
    publishable,
    rewrite_suite_ulid,
)
from comparison import resolve_baseline_comparisons
from platforms import Platform, Runtime
from s3_sync import S3SyncConfig, S3SyncResult
from tests.ufs_chem_assay.baseline_runs import (
    COMMIT,
    HARNESS_COMMIT,
    HARNESS_VERSION,
    RUN_ID,
    Run,
    fabricate_run,
    fake_sync,
    report_rows,
)

_STORE = "s3://ufs-chem/baselines"


@pytest.fixture()
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
    assert ok and reason == "driver passed; no other test failed"


def test_gate_fails_without_the_driver_test(run: Run) -> None:
    combo = run.combos[0]
    rows = [r for r in report_rows("s", combo) if "driver" not in r.pytest_name]
    ok, reason = publishable(rows)
    assert not ok and "test_driver_execution" in reason and "did not run" in reason
    assert publishable([]) == (False, "test_driver_execution did not run")


def test_gate_fails_on_a_failed_driver_or_output_test(run: Run) -> None:
    combo = run.combos[0]
    ok, reason = publishable(report_rows("s", combo, failed=("test_driver_execution",)))
    assert not ok and "test_driver_execution failed" in reason
    ok, reason = publishable(report_rows("s", combo, failed=("test_nc_file_count",)))
    assert not ok and "test_nc_file_count" in reason


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
    assert bilinear.suite == "simple-maccity" and bilinear.suite_path == run.suite_path
    assert bilinear.ulid == run.combo("MACCITY.map-bilinear").combo_id
    assert bilinear.previous_ulid == "01KXNXCJ858EE6R4FPF39BC8V2"
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
    rewrite_suite_ulid(run.suite_path, "01KXNXCJ86E8Z2FKVAXRER5ND4", consd.combo_id)
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
    consd = run.combo("MACCITY.map-consd")
    rewrite_suite_ulid(run.suite_path, "01KXNXCJ86E8Z2FKVAXRER5ND4", consd.combo_id)
    edited = load_suite(run.suite_path)
    with pytest.raises(ValueError, match="simple-maccity.*baseline_comparisons"):
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
    sync = mocker.patch("baselines.sync", side_effect=fake_sync())
    before = datetime.now(timezone.utc)
    records = _publish(run, _plan(run))
    assert [r.action for r in records] == ["published"] * 3
    assert all(r.suite_updated for r in records)
    calls = [call.args[0] for call in sync.call_args_list]
    assert len(calls) == 6  # one probe + one upload per baseline

    bilinear = records[0]
    probe, upload = _probe_and_upload(calls, bilinear.ulid)
    assert probe.dry_run and isinstance(probe.destination, Path)
    assert upload.source == bilinear.source_dir
    assert upload.destination == bilinear.destination
    assert upload.only_show_errors and not upload.dry_run
    assert upload.exclude == [] and upload.include == [] and not upload.delete

    manifest = BaselineManifest.model_validate(
        yaml.safe_load((bilinear.source_dir / BASELINE_MANIFEST).read_text())
    )
    assert manifest.ulid == bilinear.ulid and manifest.store == _STORE
    assert manifest.application == "cece" and manifest.application_commit == COMMIT
    assert (
        manifest.suite == "simple-maccity" and manifest.combo == "MACCITY.map-bilinear"
    )
    assert manifest.run_id == RUN_ID and manifest.harness_version == HARNESS_VERSION
    assert manifest.harness_commit == HARNESS_COMMIT
    assert manifest.platform is Platform.LOCAL and manifest.runtime is Runtime.DOCKER
    assert manifest.published_at.tzinfo is not None and manifest.published_at >= before
    assert "@" in manifest.published_by
    assert manifest.superseded_ulid == "01KXNXCJ858EE6R4FPF39BC8V2"
    swept = [p for p in manifest.parameters if p.swept]
    assert [(p.target, p.field, p.value) for p in swept] == [
        ("MACCITY", "mapalgo", "bilinear")
    ]
    assert len(manifest.parameters) == 6
    paths = [f.path for f in manifest.files]
    assert paths == sorted(paths)
    assert f"{bilinear.ulid}.yaml" in paths and "cece.log" in paths
    assert "plots-overview/co.gif" in paths and BASELINE_MANIFEST not in paths
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
    mocker.patch("baselines.sync", side_effect=fake_sync())
    cache = tmp_path / "cache"
    records = _publish(run, _plan(run), baseline_root=cache)
    for record in records:
        cached = cache / record.ulid
        assert (cached / BASELINE_MANIFEST).is_file()
        assert (cached / "plots-overview" / "co.gif").is_file()
        assert sorted(p.name for p in cached.glob("*.nc")) == sorted(
            p.name for p in record.source_dir.glob("*.nc")
        )


def test_publish_refuses_an_occupied_prefix_on_a_fresh_publish(
    run: Run, mocker: MockerFixture
) -> None:
    records = _plan(run)
    occupied = frozenset({records[1].destination})
    sync = mocker.patch("baselines.sync", side_effect=fake_sync(occupied))
    results = _publish(run, records)
    assert [r.action for r in results] == ["published", "failed", "published"]
    assert "append-only" in results[1].detail
    assert not (results[1].source_dir / BASELINE_MANIFEST).exists()
    uploads = [
        c for c in (call.args[0] for call in sync.call_args_list) if not c.dry_run
    ]
    assert len(uploads) == 2  # the refused one was never uploaded
    text = run.suite_path.read_text()
    assert "01KXNXCJ86E8Z2FKVAXRER5ND4" in text  # the consd entry untouched
    assert records[0].previous_ulid not in text


def test_publish_resumes_when_the_directory_was_published_before(
    run: Run, mocker: MockerFixture
) -> None:
    # First publish, then the suite is reverted (git checkout): the prefix is
    # occupied but the directory's manifest names it — no upload, repoint only.
    mocker.patch("baselines.sync", side_effect=fake_sync())
    original = run.suite_path.read_text()
    first = _publish(run, _plan(run))
    run.suite_path.write_text(original)
    sync = mocker.patch(
        "baselines.sync",
        side_effect=fake_sync(frozenset(r.destination for r in first)),
    )
    manifest_before = (first[0].source_dir / BASELINE_MANIFEST).read_text()
    results = _publish(run, _plan(run))
    assert [r.action for r in results] == ["published"] * 3
    assert all("already in the store" in r.detail for r in results)
    calls = [call.args[0] for call in sync.call_args_list]
    assert len(calls) == 3 and all(c.dry_run for c in calls)  # probes only
    assert (first[0].source_dir / BASELINE_MANIFEST).read_text() == manifest_before
    assert all(f"ulid: {r.ulid}\n" in run.suite_path.read_text() for r in results)


def test_publish_fails_a_directory_carrying_another_manifest(
    run: Run, mocker: MockerFixture
) -> None:
    sync = mocker.patch("baselines.sync", side_effect=fake_sync())
    records = _plan(run)
    foreign = records[0].source_dir / BASELINE_MANIFEST
    foreign.write_text("ulid: 01KXNXCJ858EE6R4FPF39BC8V2\nstore: s3://elsewhere/b\n")
    results = _publish(run, records[:1])
    assert results[0].action == "failed" and "manifest for" in results[0].detail
    sync.assert_not_called()


def test_publish_dry_run_writes_nothing(
    run: Run, tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    sync = mocker.patch("baselines.sync", side_effect=fake_sync())
    original = run.suite_path.read_text()
    cache = tmp_path / "cache"
    with caplog.at_level("INFO"):
        results = _publish(run, _plan(run), baseline_root=cache, dry_run=True)
    assert [r.action for r in results] == ["would-publish"] * 3
    assert all(r.detail == "prefix empty; would upload" for r in results)
    assert not any(r.suite_updated for r in results)
    calls = [call.args[0] for call in sync.call_args_list]
    assert len(calls) == 6 and all(c.dry_run for c in calls)
    uploads = [c for c in calls if isinstance(c.source, Path)]
    assert all(not c.only_show_errors for c in uploads)
    assert not any((r.source_dir / BASELINE_MANIFEST).exists() for r in results)
    assert not cache.exists()
    assert run.suite_path.read_text() == original
    assert any("superseded_ulid" in rec.getMessage() for rec in caplog.records)


def test_publish_without_suite_update_leaves_the_file_alone(
    run: Run, mocker: MockerFixture
) -> None:
    mocker.patch("baselines.sync", side_effect=fake_sync())
    original = run.suite_path.read_text()
    results = _publish(run, _plan(run), suite_update=False)
    assert [r.action for r in results] == ["published"] * 3
    assert not any(r.suite_updated for r in results)
    assert run.suite_path.read_text() == original
    manifest = yaml.safe_load((results[0].source_dir / BASELINE_MANIFEST).read_text())
    assert manifest["superseded_ulid"] == "01KXNXCJ858EE6R4FPF39BC8V2"


def test_publish_sync_failure_is_recorded_not_raised(
    run: Run, mocker: MockerFixture
) -> None:
    records = _plan(run)
    target = records[1].destination

    def failing(config: S3SyncConfig) -> S3SyncResult:
        if not config.dry_run and str(config.destination) == target:
            raise subprocess.CalledProcessError(1, ["aws"], output="boom\nfatal: no\n")
        return fake_sync()(config)

    mocker.patch("baselines.sync", side_effect=failing)
    results = _publish(run, records)
    assert [r.action for r in results] == ["published", "failed", "published"]
    assert "fatal: no" in results[1].detail
    # The manifest stays as the record of the attempt (the next run resumes).
    assert (results[1].source_dir / BASELINE_MANIFEST).is_file()
    assert "01KXNXCJ86E8Z2FKVAXRER5ND4" in run.suite_path.read_text()


def test_publish_only_touches_would_publish_records(
    run: Run, mocker: MockerFixture
) -> None:
    sync = mocker.patch("baselines.sync", side_effect=fake_sync())
    records = _plan(run)
    skipped = records[0].model_copy(update={"action": "skipped", "detail": "x"})
    results = _publish(run, [skipped, *records[1:]])
    assert results[0] == skipped
    assert sync.call_count == 4


def test_publish_session_reads_run_yaml_and_publishes(
    run: Run, tmp_path: Path, mocker: MockerFixture
) -> None:
    sync = mocker.patch("baselines.sync", side_effect=fake_sync())
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
    assert all((cache / r.ulid / BASELINE_MANIFEST).is_file() for r in results)
    assert all(f"ulid: {r.ulid}\n" in run.suite_path.read_text() for r in results)


# ── rewrite_suite_ulid ────────────────────────────────────────────────────────


def test_rewrite_keeps_every_other_byte(run: Run) -> None:
    old = "01KXNXCJ86E8Z2FKVAXRER5ND4"
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
    text = run.suite_path.read_text().replace(
        "01KXNXCJ87190QGHQZV2913JAW", "01KXNXCJ86E8Z2FKVAXRER5ND4"
    )
    run.suite_path.write_text(text)
    with pytest.raises(ValueError, match="2 `ulid:` lines"):
        rewrite_suite_ulid(
            run.suite_path, "01KXNXCJ86E8Z2FKVAXRER5ND4", "01JNEWNEWNEWNEWNEWNEWNEWNE"
        )
    assert run.suite_path.read_text() == text


def test_rewrite_restores_the_file_when_the_reload_differs(run: Run) -> None:
    before = run.suite_path.read_text()
    with pytest.raises(ValueError, match="reload"):
        rewrite_suite_ulid(
            run.suite_path, "01KXNXCJ86E8Z2FKVAXRER5ND4", "not a ulid: ["
        )
    assert run.suite_path.read_text() == before
