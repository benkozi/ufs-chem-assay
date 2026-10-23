# gcafs-anthro runbook: the global anthropogenic use case on Ursa

The `gcafs-anthro` configuration (`src/tests/config/cece/gcafs-anthro/`)
is CECE's global anthropogenic emissions case: a 0.25° global grid, 25
hourly steps from 2021-12-20T11Z, 10 species stacked from 32 streams over
seven input files (CEDS monthly inventories, EDGAR hour-of-day and
CAMS-TEMPO daily/weekly/hourly weights), SO2/OC/BC written every step.
It runs on Ursa only: the inputs are not in any bucket, and one run takes
hours. This runbook assumes the [Ursa runbook](ursa-runbook.md) steps 1–4
are done (`uv`, the harness, the CECE source, the driver build) and uses
the same `$ROOT`.

Two things differ from the maccity runs:

- **The inputs are linked in place, not fetched.** The suite declares
  its seven files with `url: null`, their digests, and — under
  `sources:` — where each lives on Ursa; `ufs-chem-assay fetch` links
  them from there, verifies each by `sha256`, and never downloads one.
  Record the digests once (step 1).
- **The run has its own run config.** `config/ursa-gcafs-anthro.yaml`
  is `config/ursa.yaml` with the use case baked in — the suite, a raised
  timeout cap, and the author's known-good geometry (one task on one
  bound core, single-threaded, the whole node's memory). It runs as
  shipped; the two later runs flip one key each.
- **The first job is meant to time out.** The suite's `timeout_s` is 30
  minutes, deliberately short of the author's up-to-3 h runs, so the
  first attempt shows what the time limit does before a long window is
  requested; raising it is one line in the suite (and the QOS follows,
  see step 2).

## 1. Link the inputs in place and record their digests

The seven files stay where they are: the suite names each one's Ursa
location (`sources: {ursa: …}` per input, the author's staging area), so
on Ursa `fetch` links every absent input into
`$ROOT/CECE/data/gcafs-anthro/` from there and, with `--record-digests`,
writes each file's real `sha256` into the suite in place of the shipped
all-zero placeholders:

```bash
cd $ROOT/ufs-chem-assay
CECE_ROOT_DIR=$ROOT/CECE uv run --no-sync ufs-chem-assay fetch --suite-config=gcafs-anthro-suite.yaml --record-digests
git diff src/tests/config/cece/gcafs-anthro/gcafs-anthro-suite.yaml   # seven sha256 lines
```

Each input reports `recorded` with `linked to <original>` and the digest
it wrote; the summary line counts them. Review the diff and commit it —
the digests are the contract every later `fetch` verifies against, the
same way the baseline ULIDs are. From then on a plain `fetch` (and the
run's data stage) reports seven `skipped (sha256 verified)` through the
links — one digest pass over the originals each time, no network, no
credentials.

The links point into another project's scratch area. If a file is purged
or moved, `fetch` says `declared source for ursa is missing: <path>`; the
link, if one exists, dangles and counts as absent. Then either update the
`sources:` line in the suite, or set `ASSAY_INPUT_SEARCH_PATH` (the run
config's `data.input_search_path`) to the directory trees the files now
live under — searched recursively, by basename, each file found exactly
once — which is also how another machine without `sources:` entries
links them. The recorded digest tells whether a replacement is the same
file. The seven basenames:

- `BC-em-anthro_input4MIPs_emissions_CMIP_CEDS-CMIP-2024-11-25_gn_202101-202112_processed.nc`
- `OC-em-anthro_input4MIPs_emissions_CMIP_CEDS-CMIP-2024-11-25_gn_202101-202112_processed.nc`
- `SO2-em-anthro_input4MIPs_emissions_CMIP_CEDS-CMIP-2024-11-25_gn_202101-202112_processed.nc`
- `EDGAR_hourly_NOxScal.nc`
- `CAMS-GLOB-TEMPO_Glb_0.1x0.1_tmp_weights_v3.1_daily.nc`
- `CAMS-GLOB-TEMPO_Glb_0.1x0.1_tmp_weights_v3.1_weekly_fixtime.nc`
- `CAMS-GLOB-TEMPO_Glb_0.1x0.1_tmp_weights_v3.1_hourly_fixtime_utc.nc`

## 2. The run config

`config/ursa-gcafs-anthro.yaml` is the Ursa template with these values
changed, each commented in the file: `harness.suite_config:
gcafs-anthro-suite.yaml`; `harness.run_timeout_s: 28800` (the Ursa
template's 300 s cap would otherwise cut every suite to five minutes — the
suite's own `timeout_s` is the job's time limit, 1800 s = `--time=30` for
now); `slurm.qos: debug`, which fits a 30-minute job and schedules fast
(when `timeout_s` grows past 30 minutes, set `qos: batch` and
`queue_wait_s: 14400`, since batch waits can be hours); one MPI task on
one core (`ntasks: 1`, `cpus: 1`), bound with `srun_args:
--cpu-bind=cores`, and `extra_args: --mem=0` (the whole node's memory —
the per-core default was not enough); the seven single-thread exports
added to `harness.env` beside `I_MPI_FABRICS` and `FI_PROVIDER`. The
account is `epic`; `--override slurm:account=<yours>` if it differs.
Nothing in the file names a path: the inputs' locations are the suite's.

## 3. Render and read

```bash
cd $ROOT/ufs-chem-assay
uv run ufs-chem-assay run --config-file=config/ursa-gcafs-anthro.yaml --dry-run
```

`$ROOT/scripts/run-config.yaml` shows the effective configuration;
`05-harness-cece.sh` exports
`ASSAY_SBATCH_ARGS='-A epic -q debug -p u1-compute -N 1 -n 1 -c 1 --mem=0'`,
`ASSAY_SRUN_ARGS='--ntasks=1 --cpu-bind=cores'`, `ASSAY_RUN_TIMEOUT_S=28800`
and the nine `ASSAY_JOB_ENV` pairs. The rendered job script
(`<combo_id>.sbatch` under the output root after the harness stage starts)
carries those directives, `--time=30`, the module block, the exports,
then `tic`, the `srun`, `toc`, and `report-mem`.

## 4. Run (one rank)

```bash
tmux new -s gcafs
cd $ROOT/ufs-chem-assay
uv run ufs-chem-assay run --config-file=config/ursa-gcafs-anthro.yaml --stage data --stage harness
```

The data stage re-verifies the inputs (and, with comparisons enabled,
pre-warms the baseline); the harness stage submits the one job and waits.
With the 30-minute `timeout_s`, expect the first job to be killed by
Slurm at `--time=30`: `test_driver_execution` fails with the job's
nonzero exit, the `.out` ends without `toc`, and `sacct -j <id> -o
Elapsed,State` says `TIMEOUT`. That is the measurement; then raise
`timeout_s` in the suite (and the QOS, step 2) and run again.
`squeue -u $USER` shows `ufs-chem-assay-<combo_id>`. Results land in
`$ROOT/CECE/ufs-chem-assay-output/<combo_id>/`: the generated
configuration, the job script, the `.out` (ending with `toc` and the
`report-mem` table), `cece.log`, 25 NetCDF files, stats, plots and GIFs.
`run.yaml` records `launch` (the directives, the srun options, the job
environment) beside the usual provenance, and `benchmarks.csv` (at the
output root and in the combination directory) the run's timings and
Slurm's accounting (step 5a). `test_baseline_comparison`
skips on the first run — comparisons are off in the run config, and even
with them on the suite's all-zero placeholder ULID means "not published
yet" (`run.yaml`: `state: unpublished`); everything else must pass.

## 5. Publish the baseline

From the login node, with the AWS CLI configured as in the Ursa runbook
(`AWS_PROFILE` as you set it up) — dry-run first, then for real:

```bash
cd $ROOT/ufs-chem-assay
AWS_PROFILE=<profile> uv run ufs-chem-assay publish-baselines \
    --output-root=$ROOT/CECE/ufs-chem-assay-output --dry-run
AWS_PROFILE=<profile> uv run ufs-chem-assay publish-baselines \
    --output-root=$ROOT/CECE/ufs-chem-assay-output
```

One baseline goes to `s3://ufs-chem/baselines/<combo_id>/` with its
`baseline.yaml` (which now carries `launch`), and the suite's placeholder
`ulid` is replaced in your harness checkout. Commit that repoint together
with what the first run tells you: the species attribute dictionaries the
driver wrote (into `assertions.species`, `exact: true`) and a tightened
`timeout_s` (about twice the `tic`→`toc` wall time in the `.out`).
Publish before editing the suite — the publish refuses a suite changed
since the run.

## 5a. Benchmarks: the rows this run left

The run config records every run (`harness.record_benchmarks: true`), so
the session appended one row to
`src/tests/config/cece/gcafs-anthro/benchmarks.csv` in your harness
checkout — the first rows are the point of the 30-minute experiment:
`driver_result: timeout`, `slurm_state: TIMEOUT`, `wall_s` empty (no
`toc`), `elapsed_s` about 1800, `queue_s` and `max_rss_kb` from `sacct`,
and the launch columns saying one task, one bound core, the single-thread
exports. The same row sits in the combination directory as
`benchmarks.csv` (so it travels with the baseline when published), and the
run's own copy is `$ROOT/CECE/ufs-chem-assay-output/benchmarks.csv`.

```bash
git diff src/tests/config/cece/gcafs-anthro/benchmarks.csv
```

Commit the file with the repoint. A row never changes once written; for a
run that was not recorded (a run config with `record_benchmarks: false`,
or a session that died before its end), `record-benchmarks` adds its rows
afterwards:

```bash
cd $ROOT/ufs-chem-assay
uv run --no-sync ufs-chem-assay record-benchmarks --output-root=$ROOT/CECE/ufs-chem-assay-output
```

Raise `timeout_s`, run again, and the next row lands beside the first;
that pair is the measurement.

## 6. Cold start: the baseline comes back from the store

Prove the download half: with no local copy of the baseline (none exists
yet — the run config sets no `baselines.root_dir`, so the first run cached
nothing), run with comparisons on and a baseline root — the two keys the
run config leaves for this step:

```bash
uv run ufs-chem-assay run --config-file=config/ursa-gcafs-anthro.yaml --stage harness \
    --override baselines:enabled=true baselines:root_dir=$ROOT/baselines
```

Skipping the data stage lets the pytest session itself sync the baseline
at start (the login node has network); `run.yaml` records it as `synced`
from the store, and `test_baseline_comparison` passes — same machine,
same build, bit-for-bit. Running the data stage too pre-warms it instead
(`local` at session start), which is the shape for a session that may run
without network.

## 7. Four ranks

The same run config with the task count changed:

```bash
uv run ufs-chem-assay run --config-file=config/ursa-gcafs-anthro.yaml --stage harness \
    --override slurm:ntasks=4 baselines:enabled=true baselines:root_dir=$ROOT/baselines
```

`-c` is per task, so this asks for four cores. The driver splits the
latitude rows across the ranks and gathers them for output; the `.out`
banner says `MPI tasks 4`. The comparison against the one-rank baseline is
the test: a pass confirms rank invariance (record it, with `run.yaml`'s
`launch`); a failure is a CECE issue — attach both `.out` banners, the
comparison CSV and bias plots, and the configuration — not a reason to
loosen `rtol`.

## What to record after the first run

- `tic`→`toc` wall time and the `report-mem` table from the `.out`
  (`sacct -j <id> -o Elapsed,MaxRSS` agrees); whether `--mem=0` can
  become a `--mem=<n>g`.
- The seven input sizes (`ls -lL $ROOT/CECE/data/gcafs-anthro/` — through the links).
- The species attributes the driver wrote (`ncdump -h` of one file).
- The driver's warnings in `cece.log` about year fields on profile and
  series streams — expected, from the configuration's own settings.
