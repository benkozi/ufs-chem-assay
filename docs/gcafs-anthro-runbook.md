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

- **The inputs are placed, not fetched.** The suite declares its seven
  files with `url: null` and their digests; `ufs-chem-assay fetch`
  verifies each by `sha256` and never downloads one. Copy them in once
  (step 1).
- **The run geometry is an override list.** The shipped
  `config/ursa.yaml` keeps its defaults (the debug QOS, eight cores per
  task, a 300 s timeout cap); this configuration needs the batch QOS, a
  raised cap, and the author's known-good geometry — one task on one core,
  bound, single-threaded, with the whole node's memory. Set the list once
  per shell (step 2) and pass it to every `run`.

## 1. Place the inputs and record their digests

Copy (do not link — the staging areas they come from are scratch, subject
to purge, and a baseline's inputs must outlive them) the seven files into
the checkout's data directory, under the configuration's own
subdirectory:

```bash
mkdir -p $ROOT/CECE/data/gcafs-anthro
cp <the seven source files> $ROOT/CECE/data/gcafs-anthro/
ls $ROOT/CECE/data/gcafs-anthro/
```

The seven basenames the suite expects:

- `BC-em-anthro_input4MIPs_emissions_CMIP_CEDS-CMIP-2024-11-25_gn_202101-202112_processed.nc`
- `OC-em-anthro_input4MIPs_emissions_CMIP_CEDS-CMIP-2024-11-25_gn_202101-202112_processed.nc`
- `SO2-em-anthro_input4MIPs_emissions_CMIP_CEDS-CMIP-2024-11-25_gn_202101-202112_processed.nc`
- `EDGAR_hourly_NOxScal.nc`
- `CAMS-GLOB-TEMPO_Glb_0.1x0.1_tmp_weights_v3.1_daily.nc`
- `CAMS-GLOB-TEMPO_Glb_0.1x0.1_tmp_weights_v3.1_weekly_fixtime.nc`
- `CAMS-GLOB-TEMPO_Glb_0.1x0.1_tmp_weights_v3.1_hourly_fixtime_utc.nc`

The suite ships with all-zero placeholder digests. Record the real ones
and put them into the suite file (`sha256:` per `dst`), then commit that
change — the digests are the contract every later `fetch` verifies
against:

```bash
cd $ROOT/CECE/data/gcafs-anthro && sha256sum *.nc
```

Then verify (no network, no credentials; one digest pass over the files):

```bash
cd $ROOT/ufs-chem-assay
CECE_ROOT_DIR=$ROOT/CECE uv run --no-sync ufs-chem-assay fetch --suite-config=gcafs-anthro-suite.yaml
```

Every file reports `skipped (sha256 verified)`. A mismatch names the file
and says there is no source to re-fetch from; an absent file names the
path to place it at. The pytest session itself only checks presence and
fails at collection naming this command.

## 2. The override list

```bash
GCAFS=(harness:suite_config=gcafs-anthro-suite.yaml slurm:qos=batch harness:run_timeout_s=28800
       slurm:ntasks=1 slurm:cpus=1 slurm:srun_args=--cpu-bind=cores slurm:extra_args=--mem=0
       harness:env:OMP_NUM_THREADS=1 harness:env:OMP_PROC_BIND=spread harness:env:OMP_PLACES=threads
       harness:env:KOKKOS_NUM_THREADS=1 harness:env:MKL_NUM_THREADS=1
       harness:env:OPENBLAS_NUM_THREADS=1 harness:env:OPENBLAS_DEFAULT_NUM_THREADS=1)
```

Line by line: the suite; the `batch` QOS (8 h cap — `debug` rejects a
4 h job) and the harness's timeout cap raised to match (the suite's own
`timeout_s`, 14400, is the job's time limit; the template's 300 s cap
would kill the job at six minutes); one MPI task on one core, bound with
`--cpu-bind=cores`, and `--mem=0` (the whole node's memory — the per-core
default was not enough); the seven single-thread exports, each added to
the template's existing `harness.env` mapping (so `I_MPI_FABRICS` and
`FI_PROVIDER` stay). Add `slurm:queue_wait_s=14400` when `batch` is busy.
The account is the template's `epic`; `slurm:account=<yours>` if it
differs.

## 3. Render and read

```bash
cd $ROOT/ufs-chem-assay
uv run ufs-chem-assay run --config-file=config/ursa.yaml --dry-run --override "${GCAFS[@]}"
```

`$ROOT/scripts/run-config.yaml` shows the merged configuration (check the
`env` mapping and the `slurm` section); `05-harness-cece.sh` exports
`ASSAY_SBATCH_ARGS='-A epic -q batch -p u1-compute -N 1 -n 1 -c 1 --mem=0'`,
`ASSAY_SRUN_ARGS='--ntasks=1 --cpu-bind=cores'`, `ASSAY_RUN_TIMEOUT_S=28800`
and the nine `ASSAY_JOB_ENV` pairs. The rendered job script
(`<combo_id>.sbatch` under the output root after the harness stage starts)
carries those directives, `--time=240`, the module block, the exports,
then `tic`, the `srun`, `toc`, and `report-mem`.

## 4. Run (one rank)

```bash
tmux new -s gcafs
cd $ROOT/ufs-chem-assay
uv run ufs-chem-assay run --config-file=config/ursa.yaml --stage data --stage harness --override "${GCAFS[@]}"
```

The data stage re-verifies the inputs (and, with comparisons enabled,
pre-warms the baseline); the harness stage submits the one job and waits.
`squeue -u $USER` shows `ufs-chem-assay-<combo_id>`. Results land in
`$ROOT/CECE/ufs-chem-assay-output/<combo_id>/`: the generated
configuration, the job script, the `.out` (ending with `toc` and the
`report-mem` table), `cece.log`, 25 NetCDF files, stats, plots and GIFs.
`run.yaml` records `launch` (the directives, the srun options, the job
environment) beside the usual provenance. Baseline comparisons are off in
the template, so `test_baseline_comparison` skips on the first run;
everything else must pass.

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

## 6. Cold start: the baseline comes back from the store

Prove the download half: with no local copy of the baseline (none exists
yet — the template sets no `baselines.root_dir`, so the first run cached
nothing), run with comparisons on and a baseline root:

```bash
uv run ufs-chem-assay run --config-file=config/ursa.yaml --stage harness \
    --override "${GCAFS[@]}" baselines:enabled=true baselines:root_dir=$ROOT/baselines
```

Skipping the data stage lets the pytest session itself sync the baseline
at start (the login node has network); `run.yaml` records it as `synced`
from the store, and `test_baseline_comparison` passes — same machine,
same build, bit-for-bit. Running the data stage too pre-warms it instead
(`local` at session start), which is the shape for a session that may run
without network.

## 7. Four ranks

The same list with the task count changed:

```bash
uv run ufs-chem-assay run --config-file=config/ursa.yaml --stage harness \
    --override "${GCAFS[@]}" slurm:ntasks=4 baselines:enabled=true baselines:root_dir=$ROOT/baselines
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
- The seven input sizes (`ls -l $ROOT/CECE/data/gcafs-anthro/`).
- The species attributes the driver wrote (`ncdump -h` of one file).
- The driver's warnings in `cece.log` about year fields on profile and
  series streams — expected, from the configuration's own settings.
