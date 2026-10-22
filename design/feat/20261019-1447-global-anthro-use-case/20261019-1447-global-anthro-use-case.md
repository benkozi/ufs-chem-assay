# Feature: the global anthropogenic use case — CECE's `cece_config_v5` becomes the harness's second carried configuration (`gcafs-anthro`), run on Ursa, with its inputs declared, its baseline published, and a cold start proven

Refined 2026-10-19 from https://github.com/benkozi/ufs-chem-assay/issues/33 ("Integrate global anthropogenic use case", labels `enhancement`, `CECE`), the configuration file at `/Users/bkoziol/Library/CloudStorage/Dropbox/rlps/rsandbox/benkozi-work/transfer/cece_config_v5.yml` (a copy of `/scratch3/NCEPDEV/naqfc/Zachary.Moon/cece-gcafs/cece_config_v5.yml` on Ursa), and the run notes in the requirements (single-task `srun --cpu-bind=cores`, the single-thread exports). Built on the conventions already in place: a suite names a configuration carried under `src/tests/config/<application>/<configuration>/` and declares the inputs it reads (`design/feat/20261007-1555-retire-examples/`), the slurm runtime renders one job script per driver call (`design/feat/20260903-1453-run-on-rdhpc/`), and baselines are published to and synced from `s3://ufs-chem/baselines/<ulid>/` (`design/feat/20261008-1433-baseline-sync/`). Ursa only for now — the issue says so, and the inputs are private.

# session

- **Session ID**: `c30fb022-0fff-43a5-9908-1cfbe2cb2d88`
- **Resume Command**: `agy --resume c30fb022-0fff-43a5-9908-1cfbe2cb2d88`

## Goal

1. **A second carried configuration, `gcafs-anthro`** — `src/tests/config/cece/gcafs-anthro/gcafs-anthro.yaml`, the v5 configuration made portable (cwd-relative data paths under `data/gcafs-anthro/`, the configuration history pruned) and validated by the harness's strict CECE model, beside **its suite** `gcafs-anthro-suite.yaml`: no sweep (the base configuration is the single combination), the seven input files declared with their digests and no remote source yet, the file-count / filename / dimension assertions at their defaults (25 hourly files, `cece_{YYYY}{MM}{DD}_{HH}{mm}.nc`), stats and plots on, one `baseline_comparisons` entry.
2. **The CECE config model catches up with `develop`** (`applications/cece/config.py`): `grid.nz`, the three driver-level AMIO input knobs the driver reads (`amio_staging_buffer_count`, `amio_staging_buffer_capacity_bytes`, `amio_prefetch_depth`), the stream `time_label`, and the cadence values `series`, `daily`, `stepwise`. Two keys the v5 file sets but the `develop` driver never reads (`driver.amio_read_timeout_s`, `driver.amio_staging_timeout_ms`) are **not** added: the strict model rejects them, which is the point — see Decisions and Open question 1.
3. **Inputs placed, not fetched** (user decision 2026-10-19: nothing but baselines goes to the bucket for now): the seven files already on Ursa are copied under `$ROOT/CECE/data/gcafs-anthro/` and declared in the suite with their digests and **no remote source** (`url: null`, a small `InputFile` extension), so `fetch` verifies them by digest, never touches the network, and names the missing file and its destination when one is absent; the session's fail-fast presence check and `run.yaml`'s input record work unchanged. The fetch path (a one-time upload to `s3://ufs-chem/inputs/cece/gcafs-anthro/`, the `url` lines filled in) is deferred with the digests already pinned.
4. **The run geometry is a run-config concern, not a configuration concern**: the driver decomposes the grid across MPI ranks from the communicator size alone (latitude bands, gathered for output), so the YAML under test never changes with the task count. The slurm section gains `ntasks` and `srun_args` (and `extra_args` for `--mem`-style directives), the job template stops hard-coding `srun --ntasks=1`, the single-thread geometry from the requirements (one task, one core, `--cpu-bind=cores`, the seven exports) is applied by overrides for this suite's runs only — `config/ursa.yaml` keeps its defaults (user decision 6) — and `run.yaml` records the launch (`sbatch_args`, `srun_args`, `job_env`) so a baseline's provenance includes how many ranks and threads produced it.
5. **The baseline in the bucket, then a cold start**: the first Ursa run (one rank) publishes its single combination with the existing `publish-baselines` (the suite is repointed, the user commits); the baseline directory is then deleted on Ursa and a second run must sync it back from the store at session start and pass the comparison.
6. **A TODO carried to the pull request** (user, 2026-10-19): whether to open a CECE issue for the two unread keys (CECE's development is fast; they may be read by then), and the four-rank run against the one-rank baseline — rank invariance confirmed, or a CECE issue opened. See "TODO (carried to the pull request)".

## Pre-design audit (fresh facts, 2026-10-19)

Read from the harness checkout (`feature/gcafs-anthro-use-case` at `7edd5bd`, 0.1.0-rc.8), the local CECE checkout (`/Users/bkoziol/sandbox/git-benkozi/CECE`; first at `2b1cfde`, 2026-10-15, then re-verified at `origin/develop` `35f2bc4`, 2026-10-14, after the user checked out `develop` — every fact below holds at both), and the v5 file.

1. **The v5 configuration.** `driver`: 2021-12-20T11:00 → 2021-12-21T12:00, hourly (25 steps; the comments say 11z is chosen "to get 12z data" and the end "for shift"), `amio_read_timeout_s: 300`, `amio_staging_timeout_ms: 60000`, `amio_staging_buffer_count: 32`, `amio_staging_buffer_capacity_bytes: 67108864`, `amio_prefetch_depth: 1`, `grid` 1440×720 (0.25°) with `nz: 1`, explicit edges −180/180, −90/90. `species`: 10 species (SO2, SO2_elev, SO2_ship, SO4_ship, OC, OC_elev, OC_ship, BC, BC_elev, BC_ship), 25 entries, every entry `operation: add` with `scale_fields` naming one or two temporal-weight streams (EDGAR hour-of-day, CAMS daily/weekly/hourly). `cece_data`: 32 streams over **seven distinct files** — three CEDS monthly files (`<SP>-em-anthro_input4MIPs_emissions_CMIP_CEDS-CMIP-2024-11-25_gn_202101-202112_processed.nc`, one per BC/OC/SO2, read by eight, eight, and nine streams, all `cadence: monthly`, `time_label: start`, `tintalgo: nearest`, `mapalgo: consd`), `EDGAR_hourly_NOxScal.nc` (`cadence: hourly`, no `time_label`), the CAMS-TEMPO daily file (`cadence: series`, `time_label: start`, yearFirst 2011 / yearLast 2020 / yearAlign 2011), the CAMS weekly file (two streams, `cadence: weekly`), and the CAMS hourly file (three streams, `cadence: hourly`). The `SO4_shp` stream maps file variable `SO2_shp` to model field `SO4_shp` with `scale: 0.077` on the species entry. `output`: every step, `fields: [SO2, OC, BC]` (plain strings, no configured attributes), pattern `cece_{YYYY}{MM}{DD}_{HH}{mm}.nc`, directory `./out_cece-helm17` (the seventeenth of a commented-out series — history, not configuration). Absolute paths on Ursa: `/scratch4/NCEPDEV/stmp/Wei.K.Li/RUNDIRS/C96_gcafs_cycled_noDA_catchem/gcdas.2021122012/prep_emissions.2816259/INPUT/{CEDS/v2025-01/<SP>/,EDGARv42/v2015-02/NO/,CAMS-TEMPO/v3.1-2021/}` for six files and `/scratch3/NCEPDEV/naqfc/Zachary.Moon/cece-gcafs/input/` for the CAMS hourly file (a commented alternative for the daily file lives there too).
2. **What the strict harness model rejects today** (`applications/cece/config.py`, `extra="forbid"` everywhere): `driver.amio_read_timeout_s`, `driver.amio_staging_timeout_ms`, `driver.amio_staging_buffer_count`, `driver.amio_staging_buffer_capacity_bytes`, `driver.amio_prefetch_depth`, `grid.nz`, stream `time_label`, and `cadence: series` (the `Cadence` enum has `hourly`, `weekly`, `monthly`). Everything else in the file validates (`scale_fields`, `scale`, the variable mapping, `filename_pattern` without `{ss}`).
3. **What the `develop` driver reads** (`src/core/cece_config_parser.cpp` 300–350, `src/driver/cece_driver_facade.cpp` 150–210, `src/driver/cece_time_indexing.cpp` 74–125): `driver.{start_time,end_time,timestep_seconds,gridspec_file,stacking_refresh_interval_seconds,amio_worker_threads,amio_staging_buffer_count,amio_staging_buffer_capacity_bytes,amio_prefetch_depth}` and `grid.{nx,ny,nz,lon_min,lon_max,lat_min,lat_max}`; per stream `file, mapalgo, cadence, yearFirst, yearLast, yearAlign, taxmode, time_label, tintalgo, time_var, time_units, calendar, data_model, source, variables`. **Not read anywhere: `amio_read_timeout_s` and a driver-level `amio_staging_timeout_ms`** — the AMIO manifest the facade writes hard-codes `read_timeout_s: 120` and `staging_timeout_ms: 30000` (facade 365–380), the very "defaults" the v5 comments cite. `amio_staging_timeout_ms` is a key of the **`output:`** block only (parser 296–301, range [1, 60000]), alongside `output.amio_worker_threads`, `output.amio_staging_buffer_count` ([1, 4096]), `output.amio_staging_buffer_capacity_bytes` — none of which v5 uses. The parser ignores unknown keys silently (the only "unknown key" error is the `interpolation` → `tintalgo` rename), so v5 ran with those two settings having no effect. Validated enums: cadence `series|daily|monthly|hourly|weekly|stepwise` (`step` an alias; absent = `series` — the model's comment that an absent cadence "disables the mechanism" is stale), taxmode `cycle|extend|limit`, tintalgo `linear|nearest`, time_label `auto|start|center|end`. `CECE/docs/configuration.md` documents all of these (`nz` default 1; `cadence` table; `time_label` section; the output AMIO table).
4. **Warnings v5 will log, harmlessly**: for profile cadences (`hourly`, `weekly`) the driver warns that `taxmode/yearAlign/yearFirst/yearLast` are ignored (v5 sets them on EDGAR and CAMS weekly/hourly streams); for series kinds it warns that `yearFirst/yearLast` are only consulted when the axis cannot be decoded. The harness model requires `yearFirst`, `yearLast`, `yearAlign`, `taxmode` on every stream, so the carried configuration keeps them (the driver's own defaults — 0 and empty — are not expressible today; Future work).
5. **MPI and threads in the driver.** `main.cpp` calls `MPI_Init_thread(MPI_THREAD_MULTIPLE)` then `Kokkos::initialize`; `cece_band_decomposition.hpp` partitions the destination latitude rows into contiguous per-rank bands as a pure function of `(ny_global, comm size)` — "the single source of truth … the ingest, regrid, core-import, compute, and output stages all agree on" — and the standalone writer gathers bands with `MPI_Gatherv` in a rank-invariant field order (`cece_standalone_writer.cpp` 790–830); a single rank, or no MPI, is the whole-grid short-circuit. **Nothing in the YAML names the rank count.** Threads: `OMP_NUM_THREADS` is read at Kokkos initialization ("not set — using all available threads", `cece_core_initialize_p1.cpp` 87–102); `cece_run_info.cpp` prints `MPI tasks` and the OMP setting in the banner.
6. **How the harness launches on Ursa.** `src/templates/driver-job.sbatch.j2`: directives (`--job-name`, `--output`, `--chdir=<checkout>`, `--time=<suite timeout in minutes>`, then one per token of `ASSAY_SBATCH_ARGS`), the module block, `export`s from `ASSAY_JOB_ENV`, then **`srun --ntasks=1 {{ command }}`** — hard-coded. `SlurmSection.sbatch_args` is `-A {account} -q {qos} -p {partition} -N 1 -n 1 -c {cpus}` (`cli/run_config.py` 189–192); `config/ursa.yaml` ships `qos: debug` (30 min cap), `cpus: 8`, `queue_wait_s: 3600`, `harness.env: {I_MPI_FABRICS: shm, FI_PROVIDER: tcp}` (→ `ASSAY_JOB_ENV`), `run_timeout_s: 300` (**caps every suite's `timeout_s`**), `dask_nworkers: 2`, `baselines.enabled: false`, `baselines.root_dir` unset. `Settings` (`settings.py`): `sbatch_args`, `slurm_queue_wait_s`, `job_env`, `launcher` (native only). `RunManifest` records `platform`, `runtime`, `modulefile` — not the directives, not the job env.
7. **Inputs.** `InputFile` = `{url (s3 object), public, dst (relative, clean), sha256 | null}`; `stage_inputs` (`staging.py`) treats a present file as usable when its digest matches (or, with no digest declared, when non-empty), fetches otherwise through one filtered `aws s3 sync` of the parent prefix into `<data>/.staging/<ulid>/` and `os.replace`s it into place (`target.parent.mkdir(parents=True)`, so a `dst` with subdirectories works); the session only checks presence (`missing_inputs`) and fails at collection naming the `fetch` command; `run.yaml` records presence and size. A present *symlink* passes `is_file()`; nothing hashes a directory. `public: false` means the CLI's own credential chain — on Ursa `AWS_PROFILE` as the runbook configures it (the user's note: the SSO profile `NOAA_OAR_Developer-567295733163` locally; `default` is broken).
8. **Baselines.** The selector of a `baseline_comparisons` entry must match exactly one enumerated combination (`comparison.resolve_baseline_comparisons`); `CeceSweepSelector` with no fields (`sweep_selector: {}`) matches every combination, which for a sweep-less suite is the one. `ensure_baselines` runs at session start only when comparisons are enabled; with `ASSAY_BASELINE_ROOT_DIR` unset an absent baseline is `failed` and the comparison test fails with that reason; the publish gate (`baselines/plan.py`) wants `test_driver_execution` passed and nothing else failed, comparison excepted, skips allowed — so a configuration with **no baseline yet** runs with comparisons off, publishes post hoc, and is repointed. A baseline is the whole combination directory: here 25 NetCDF × 3 float64 fields × 1440×720 ≈ 25 MB each ≈ **0.6 GB of NetCDF per baseline**, plus stats, 75 PNGs and 3 GIFs, per publish, append-only.
9. **Assertions and analysis for this shape.** `expected_output_count` = `(end − start) / timestep // frequency_steps` = 25; filenames from the pattern at `start + k·interval`, k = 1..25 (`render_filename_pattern` renders the tokens present; `{ss}` absent is fine); `validate_dimensions` wants `(time, lev, lat, lon)` on each output variable — `nz: 1` gives a length-1 `lev`, still four-dimensional. Species attribute expectations are per-suite and unknown until the `develop` writer has run this configuration (v5 configures none; the writer adds its own — `coordinates` at least, by the maccity evidence). Stats run in the pytest process (login node, 2 dask workers); plots render one PNG per (file, variable) at 1440×720 through cartopy.
10. **Discovery and tests.** Suites are found by the `*-suite.yaml` rule under `src/tests/config/` (recursively; `harness.suite_config` is a regex over file names); `test_dry_run.py` drives the default selector (`simple-maccity-suite.yaml`) through a real pytest subprocess; `test_user_docs.py` pins the two run-config templates and forbids user-specific strings (`Benjamin.Koziol`, `my_stmp`, `NCEPDEV/stmp`) in `docs/` and `config/` — **so the Ursa scratch paths from the v5 file may appear in this design document and nowhere user-facing**. Harness coverage is gated at 100 % in CI; `vulture`, ruff `ARG`, `mypy --strict` run in pre-commit.
11. **The author's job script** (`cece_job.sh`, supplied 2026-10-19; a copy at `/Users/bkoziol/Library/CloudStorage/Dropbox/rlps/rsandbox/benkozi-work/transfer/cece_job.sh`): `#SBATCH -J cece-helm -A arl -t 03:00:00 -N 1 -n 1 -c 1 --mem=0` — the commented history shows `-t 30`/`-q debug`/`-t 90` before the 3 h limit, `-n 48/32/24/12` before one task, and `--mem-per-cpu=6G/9G/12G` before `--mem=0` (the whole node's memory: with `-c 1` the per-core default was not enough). Modules: `module use $repo/modulefiles && module load cece_ursa.intelllvm && export I_MPI_F90=ifx`, plus `intel-oneapi-vtune/2025.4.0` for the commented VTune lines (`hotspots`, `threading`, `io`, `hpc-performance` collections around the same `srun`). The seven single-thread exports as in the requirements. The driver is `$repo/build-intel-rel-g/cece_standalone_driver` (a RelWithDebInfo-style build; the harness builds `build/` with `-DCMAKE_BUILD_TYPE=Release`), launched as `srun --cpu-bind=cores $cece cece_config.yml` (no `--ntasks`: inherited from `-n`), bracketed by `echo "tic: $(date -u)"` / `echo "toc: $(date -u)"`, and followed by `report-mem` (the RDHPCS site command that prints the job's memory use). Everything the harness renders already covers the structure — directives, module block, exports, `srun` — except `--mem=0`, the timestamps, and `report-mem`.
12. **The issue's three bullets map onto existing machinery**: "freshly stage / use data staged on Ursa" → `fetch`'s present-and-verified rule plus a one-time upload; "upload CECE baselines to the S3 sandbox" → `publish-baselines`; "delete baselines on Ursa and attempt a cold start" → `ensure_baselines` at session start with a baseline root. The new work is the configuration, the model, the geometry settings, and the documentation.

## Design

### 0. Decisions

1. **The configuration is carried faithfully, minus what is history or inert.** Same window (25 hourly steps, crossing a day boundary — that exercises the daily series bracket, the weekly day-of-week profile, and the hourly profiles across midnight, which a shorter window would not), same grid, same species, same streams, same AMIO input tuning, same output fields and pattern. Dropped: the commented-out alternatives, the `out_cece-helm<N>` directory history (`output.directory: .` — `build_config` overrides it per combination anyway), and the two driver keys the driver does not read (fact 3). Changed: the seven absolute scratch paths become `data/gcafs-anthro/<basename>` (cwd-relative, like `data/MACCity_4x5.nc`), and `output.enabled` stays `true`. The two dropped keys are a pull-request TODO (user, 2026-10-19): re-check CECE `develop` then, add them to the model if it reads them by that time, otherwise decide whether to open a CECE issue — the author's configuration relies on them, so they are worth raising.
2. **The model grows only by what `develop` reads**, with the driver's own validation mirrored (`ge=1` on the three AMIO knobs and `nz`; `TimeLabel` and the full `Cadence` as canonical lowercase enums) and the stale cadence comment corrected. `output.amio_*` keys, `taxmode: limit`, `time_var`/`time_units`/`calendar`, `stacking_refresh_interval_seconds`, `gridspec_file` are not needed by this configuration and stay out (Future work lists them; `limit` in particular would widen the exhaustive sweeps).
3. **No sweep, one baseline entry with an empty selector.** The use case is one configuration; `sweep_selector: {}` is the documented way to select the single combination of a sweep-less suite. A later suite may sweep `mapalgo` on the CEDS streams or `tintalgo` on the CAMS daily stream; that is a second suite file beside this one, not a change to it.
4. **Inputs are placed and declared without a source** (user decision 3, 2026-10-19: no input data goes to the bucket at this point — only baselines). The suite declares each file with its `dst` and digest and `url: null`; `InputFile.url` becomes optional for exactly this case, and `fetch`'s rule for such an entry is: present and verified → skipped; present and wrong → failed, naming the digest mismatch and that there is no source to re-fetch from; absent → failed, naming the destination to place the file at. Nothing is ever downloaded for it. On Ursa the files **stay where they are** (user decision 2026-10-22: no copies): `fetch` links them into `$ROOT/CECE/data/gcafs-anthro/` from a per-machine search path and records their digests into the suite on request — section 4a. The purge risk of another user's scratch is accepted and visible: a dangling link is "absent" and `fetch` says so. The fetch path — upload once, fill in the `url` lines — is Future work; the digests recorded now are what it will verify against.
5. **Geometry lives in the run config and the job script; the configuration under test never names it.** `slurm.ntasks` (sbatch `-n`, srun `--ntasks`), `slurm.srun_args` (`--cpu-bind=cores`), `slurm.extra_args` (free-form directives, e.g. `--mem=64g`), one new setting `ASSAY_SRUN_ARGS` carrying the rendered `--ntasks=<n> <srun_args>` into the job template. The author's single-thread geometry — `-n 1 -c 1`, `srun --cpu-bind=cores`, and the seven exports (`OMP_NUM_THREADS=1`, `OMP_PROC_BIND=spread`, `OMP_PLACES=threads`, `KOKKOS_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `OPENBLAS_DEFAULT_NUM_THREADS=1`) — is what this suite runs under initially, applied as `--override` entries for this suite only (user decision 6, 2026-10-19); `config/ursa.yaml` keeps `cpus: 8`, the two MPI hints, `qos: debug`, and the 300 s cap, so maccity's jobs are byte-for-byte what they were. The first run is one rank (user decision 4); the four-rank run (`slurm:ntasks=4`) is a pull-request TODO whose test is the comparison against the one-rank baseline — the band decomposition claims rank invariance, and a difference is a CECE issue, not a tolerance change. `run.yaml` records the launch.
6. **The first run publishes; the second proves the cold start.** No bootstrap tooling: the entry ships with the all-zero placeholder ULID and a comment; comparisons are off in `config/ursa.yaml` as shipped; `publish-baselines --output-root` from the login node repoints the suite; the user commits. The cold start is a verification step with explicit overrides (`baselines:enabled=true baselines:root_dir=$ROOT/baselines`), not a code path.
7. **Timeouts are the batch QOS's business.** The suite's `timeout_s` starts at 14400 (4 h — the author's script settled on a 3 h limit after 30 and 90 minutes, so the wall time is under 3 h; 4 h is the ceiling until measured); `config/ursa.yaml`'s `run_timeout_s` cap (300 s) and `qos: debug` stay as shipped (maccity's 30 s jobs turn around fastest there), and the gcafs-anthro run overrides both: `slurm:qos=batch harness:run_timeout_s=28800` (the `batch` QOS's 8 h cap — the cap is a safety net, the suite's own value is the limit). The first run's `sacct` wall time tightens `timeout_s` (a pull-request TODO).
8. **Species attributes are pinned after the first run, not guessed.** The suite ships without an `assertions.species` block; the first run's NetCDF supplies the exact dictionaries, which then go into the suite (`exact: true`) in the same commit as the repoint.
9. **Nothing user-facing carries the scratch paths.** The v5 provenance (who staged the files, where) lives in this document and in a comment at the top of the carried configuration naming only the issue and the author; the runbook refers to "the seven files" by basename.
10. **The name is `gcafs-anthro`** (user, 2026-10-19), after the GCAFS campaign the inputs were staged for (`C96_gcafs_cycled_noDA_catchem`, `cece-gcafs`); "global anthropogenic use case" stays the prose description. The window is the author's 25 hours (user decision 5).

### 1. The carried configuration — `src/tests/config/cece/gcafs-anthro/gcafs-anthro.yaml`

A header comment: provenance (issue #33, @zmoon's `cece_config_v5.yml`, carried 2026-10-19 with the paths made cwd-relative and the two unread driver keys dropped), the shape in one line (0.25° global, 25 hourly steps from 2021-12-20T11, 10 species over 32 streams and 7 files, SO2/OC/BC written). Then the configuration, keys in the model's order:

```yaml
driver:
  start_time: '2021-12-20T11:00:00'  # 11z start: the first write is 12z (frequency_steps 1)
  end_time: '2021-12-21T12:00:00'    # 25 hourly steps, crossing a day boundary
  timestep_seconds: 3600
  amio_staging_buffer_count: 32          # driver default 8; the 0.1-degree CAMS files need the headroom
  amio_staging_buffer_capacity_bytes: 67108864  # 64 MiB (driver default 32 MiB)
  amio_prefetch_depth: 1                 # driver default 2
  grid:
    nz: 1
    nx: 1440
    ny: 720
    lon_min: -180.0  # cell edges, not centers (HEMCO convention)
    lon_max: 180.0
    lat_min: -90.0
    lat_max: 90.0
species:
  SO2:
    - field: SO2_agr
      operation: add
      scale: 1.0
      scale_fields: [EDGAR_TODNOX]
    # … the 25 entries exactly as v5, in v5's order
cece_data:
  streams:
    - name: BC_agr
      file: data/gcafs-anthro/BC-em-anthro_input4MIPs_emissions_CMIP_CEDS-CMIP-2024-11-25_gn_202101-202112_processed.nc
      yearFirst: 2021
      yearLast: 2021
      yearAlign: 2021
      taxmode: cycle
      tintalgo: nearest
      mapalgo: consd
      cadence: monthly
      time_label: start
      variables:
        - file: BC_agr
          model: BC_agr
    # … 32 streams; CAMS_DAILY_RES keeps cadence: series, time_label: start,
    # yearFirst 2011 / yearLast 2020 / yearAlign 2011, taxmode cycle
output:
  enabled: true
  directory: .
  filename_pattern: cece_{YYYY}{MM}{DD}_{HH}{mm}.nc
  frequency_steps: 1
  fields: [SO2, OC, BC]
```

The seven basenames (the `dst` of each input, under `gcafs-anthro/`): `BC-em-anthro_input4MIPs_emissions_CMIP_CEDS-CMIP-2024-11-25_gn_202101-202112_processed.nc`, the `OC-…` and `SO2-…` siblings, `EDGAR_hourly_NOxScal.nc`, `CAMS-GLOB-TEMPO_Glb_0.1x0.1_tmp_weights_v3.1_daily.nc`, `CAMS-GLOB-TEMPO_Glb_0.1x0.1_tmp_weights_v3.1_weekly_fixtime.nc`, `CAMS-GLOB-TEMPO_Glb_0.1x0.1_tmp_weights_v3.1_hourly_fixtime_utc.nc`. Basenames are unique, so the data directory is flat. No `meteorology`, `diagnostics`, `temporal_profiles` blocks (v5 has none; the weights come through `scale_fields`). The v5 `TODO: investigate bilinear since coarse` on EDGAR is kept as a comment — a candidate sweep, not a change.

### 2. The CECE config model — `applications/cece/config.py`

- `Cadence`: `hourly`, `daily`, `weekly`, `monthly`, `series`, `stepwise` (canonical lowercase; the driver's `step` alias and case-insensitivity are not values). Docstring corrected: an absent cadence means `series` (the file's time axis is decoded), `stepwise` is the old index-walking behaviour; `hourly`/`weekly` are profiles that ignore the year fields.
- `TimeLabel` (new enum): `auto`, `start`, `center`, `end`.
- `Grid.nz: int | None = Field(None, ge=1, description="Number of vertical levels; None means the driver default (1)")`.
- `Driver`: `amio_staging_buffer_count: int | None = Field(None, ge=1, …)`, `amio_staging_buffer_capacity_bytes: int | None = Field(None, ge=1, …)`, `amio_prefetch_depth: int | None = Field(None, ge=1, …)`, each description naming the driver default (8, 33554432, 2) and that values < 1 are rejected here because the driver rejects them too (unlike `amio_worker_threads`, which the driver corrects silently — the existing comment stays accurate for that field only). A module comment above the three: `amio_read_timeout_s` and `amio_staging_timeout_ms` are deliberately absent from `Driver` — the driver reads neither at this level (facade hard-codes 120 s / 30000 ms); `amio_staging_timeout_ms` is an `output:` key, not modelled until a configuration needs it.
- `Stream.time_label: TimeLabel | None = Field(None, description="Where each time coordinate sits in its record's interval (auto|start|center|end); None means the driver's auto")`.
- `to_yaml` is unchanged (`exclude_none`), so `maccity.yaml` round-trips byte-identically in meaning and the generated combination configs for `gcafs-anthro` carry exactly the keys above.

Sweeps and selectors (`applications/cece/suite.py`, `combos.py`) are untouched: no new swept dimension. `effective_parameters` rows are unchanged, so `combos.csv` for a 32-stream configuration has 32 × 3 stream rows + 25 × 3 species rows per combination — fine.

### 3. The suite — `src/tests/config/cece/gcafs-anthro/gcafs-anthro-suite.yaml`

```yaml
# The global anthropogenic use case (issue #33): one configuration, no sweep —
# the base config is the single combination. Ursa only: the inputs are private
# and the run takes hours; run it through config/ursa.yaml with
#   --override harness:suite_config=gcafs-anthro-suite.yaml slurm:qos=batch
name: gcafs-anthro
config_path: gcafs-anthro.yaml
timeout_s: 14400  # per combination; the first Ursa run's wall time tightens this (design, Verification 8)
# Inputs the base config reads, expected at <CECE_ROOT_DIR>/data/gcafs-anthro/.
# No remote source yet (url: null): the files are copied there from the
# existing staging area on Ursa, and `uv run ufs-chem-assay fetch
# --suite-config=gcafs-anthro-suite.yaml` verifies each by sha256 and names any
# that is missing — it never downloads these. Filling in the url lines (after
# a one-time upload) makes them fetchable; the digests stay.
inputs:
  - url: null
    dst: gcafs-anthro/BC-em-anthro_input4MIPs_emissions_CMIP_CEDS-CMIP-2024-11-25_gn_202101-202112_processed.nc
    sha256: <recorded on Ursa at implementation; see the plan>
  # … seven entries
# assertions: defaults — file count derived (25), filenames from the pattern,
# every output variable on (time, lev, lat, lon). The species attribute
# dictionaries are pinned after the first run (design decision 8).
analysis:
  compute_descriptive_stats: true
plotting:
  enabled: true
  gif_enabled: true
# One entry: the empty selector matches the single combination of a sweep-less
# suite. The all-zero ULID is the bootstrap placeholder — no baseline is in the
# store yet; run with comparisons off, publish, and the repoint replaces it.
baseline_comparisons:
  - sweep_selector: {}
    ulid: 00000000000000000000000000
    rtol: 1.0e-15
```

`rtol: 1.0e-15` for the same reason as maccity (the one-ULP cross-build difference), harmless on the same machine. The digests are required: a source-less input with no digest would mean "any non-empty file", which the model refuses (section 4).

### 4. Inputs — placed on Ursa, declared without a source; `models/suite_config.py`, `staging.py`, `cli/main.py`

**The model.** `InputFile.url: str | None = Field(None, pattern=S3_OBJECT_URI_PATTERN, description="S3 object URI the file is fetched from; null declares an input with no remote source yet — it must be placed at dst by hand and is only ever verified")`. A validator: `url: null` requires `sha256` (a source-less, digest-less entry would accept any non-empty file). `public` keeps its meaning for a URL and is refused with `url: null` (`public: true` on nothing is a contradiction worth catching). `parent_prefix`/`filename` assert a URL (callers only reach them for one). `RecordedInput.url: str | None` likewise, so `run.yaml` records the entry as declared. `merge_inputs` is unchanged (identical entries dedupe, conflicting ones refuse).

**Staging** (`stage_inputs`): for an entry without a source — present and verified → `skipped (sha256 verified)`; present and wrong → `failed: present but sha256 … != declared …; no source to fetch from — replace the file at <path>`; absent → `failed: absent and no source declared — place the file at <path>`. No bucket probe, no `.staging/`, no network for these entries; the exit code of `fetch` reflects the failures as today. The session's presence check (`missing_inputs`) and its message are unchanged.

**On Ursa** (runbook step 5a):

```bash
mkdir -p $ROOT/CECE/data/gcafs-anthro
cp <the seven source files> $ROOT/CECE/data/gcafs-anthro/     # ~minutes; copy, not ln -s (purge policy)
cd $ROOT/ufs-chem-assay
CECE_ROOT_DIR=$ROOT/CECE uv run --no-sync ufs-chem-assay fetch --suite-config=gcafs-anthro-suite.yaml
```

`fetch` reports every file `skipped (sha256 verified)` — one digest pass over the files, no network, no credentials. The digests themselves come from the same files (`sha256sum *.nc` in that directory) and go into the suite file before the first run — implementation plan step 6.

**Deferred (user decision 3): the fetch path.** When the inputs are published — `aws s3 sync` of the directory to `s3://ufs-chem/inputs/cece/gcafs-anthro/` (a prefix convention beside `baselines/` that nothing in the code knows), then the seven `url:` lines filled in with `public: false` — any fresh checkout fetches them with the login node's credentials, and the pinned digests verify the download. File sizes decide whether that is practical (the CAMS 0.1° daily file over ten years may be tens of GB); the first run records them. A `publish-inputs` command is Future work.

### 4a. In-place inputs: linked by `fetch` from a search path, digests recorded by `fetch --record-digests` (user, 2026-10-22)

**The setting.** `ASSAY_INPUT_SEARCH_PATH` (`Settings.input_search_path`, `os.pathsep`-separated directories, the same `NoDecode` + split as `suite_config_search_path`), mirrored by the run config's `data.input_search_path: []` and exported by the shared stage exports when non-empty (so the data stage's `fetch` and a by-hand `fetch` see the same value). The directories are per-machine facts and never appear in a suite: on Ursa they are the two trees the originals live in, passed as an override (`data:input_search_path='[<INPUT tree>, <input dir>]'`) or exported in the shell. The templates list the key empty.

**Linking.** For a source-less input (`url: null`) absent at `dst`, `fetch` searches each directory of the search path recursively for the file's **basename**: exactly one match → a symlink `dst → <absolute match>` (action `linked`, then the usual digest verification of the linked file); no match → `failed: absent and not found under the input search path (<dirs>); place or link the file at <path>` (an empty search path says so); several → `failed`, listing them (ambiguity is never guessed). A dangling symlink at `dst` counts as absent and is replaced; a present regular file or a valid link is never touched. Linking never happens for a URL input (it is fetched) and never in a dry run (the plan says `would-link` with the match). The session is unchanged: it only checks presence, and `is_file()` follows links.

**Recording digests.** `fetch --record-digests`: for every source-less input that is present (placed or just linked) and whose on-disk digest differs from the declared one — the all-zero placeholder, or a changed file — the result is `recorded` (`sha256 recorded: <new> (was <old>)`) and the CLI rewrites that entry's `sha256:` line in every selected suite file that declares the `dst` (identical entries across suites, by `merge_inputs`'s rule): one textual substitution of the `sha256:` line that follows the matching `dst:` line within the entry, every other byte kept, verified by reloading the suite and comparing it to the previous model with only those digests changed — the ULID repoint's pattern (`baselines/suite.py`), in `staging.py`. The user reviews the diff and commits, exactly as with the ULIDs. A URL input is never recorded (its digest came with its declaration from the bucket's side; a mismatch there means a wrong file, not a missing record). `--record-digests` with `--dry-run` is a usage error (recording writes the suite). A present file that verifies is `skipped` as before, so running with the flag again is idempotent.

**Why a search path and not a per-entry source path.** The suite must stay portable (no scratch paths, the `test_user_docs` rule applied to configuration files too); where the originals live is the run's knowledge, like the account and the QOS. The basename lookup is what makes the seven entries machine-independent: the suite names the files, the machine says where they are.

**Runbook step 1 becomes**: export the search path (or put it in the override list), `fetch --record-digests --suite-config=gcafs-anthro-suite.yaml`, read the seven `linked` + `recorded` lines, `git diff` the suite, commit. Re-running `fetch` afterwards is seven `skipped (sha256 verified)` through the links (one digest pass over the originals each time — a size/mtime shortcut is Future work if that cost bites).

### 5. Process geometry — `settings.py`, `cli/run_config.py`, `cli/stages.py`, `templates/driver-job.sbatch.j2`, `models/suite_config.py`, `config/ursa.yaml`

- **`Settings.srun_args: str = Field("--ntasks=1", description="slurm runtime: options for the srun that launches the driver inside each job, word-split like a shell (e.g. '--ntasks=4 --cpu-bind=cores'); the harness never derives the task count from sbatch_args")`**. The template line becomes `srun {{ srun_args }} {{ command }}` (rendered from the setting; StrictUndefined keeps a missing value a render failure). The default reproduces today's launch line exactly.
- **Timestamps and memory in the job output** (from the author's script): the template brackets the launch with `echo "tic: $(date -u)"` / `echo "toc: $(date -u)"` and ends with `command -v report-mem >/dev/null 2>&1 && report-mem` (the RDHPCS site command; the guard keeps the template valid on any Slurm machine and under the harness's own template tests). The `.out` — which a published baseline carries — then records the driver's wall time and the job's memory on its own, independent of `sacct`; TODO 3 reads them from there. This changes every rendered job script's text (three lines), so the exact-text tests in `test_slurm_runtime`/`test_stages` are updated once; nothing about submission changes.
- **`SlurmSection`**: `ntasks: int = Field(1, gt=0, description="sbatch -n and srun --ntasks: MPI tasks per driver job (the driver decomposes latitude bands across them)")`, `srun_args: str = Field("", description="Extra srun options for the driver launch, e.g. --cpu-bind=cores")`, `extra_args: str = Field("", description="Extra sbatch directives appended verbatim, e.g. --mem=64g or --exclusive")`. `sbatch_args` becomes `-A … -q … -p … -N 1 -n {ntasks} -c {cpus} {extra_args}`.strip(); a new property `srun_args_value` = `--ntasks={ntasks} {srun_args}`.strip(). `_exports` adds `ASSAY_SRUN_ARGS` under the slurm runtime; `SETTINGS_NOT_MIRRORED` gains `srun_args`. `-N 1` stays fixed: multi-node is Future work.
- **`RunManifest.launch: LaunchRecord | None`** (`models/suite_config.py`, a new frozen `StrictModel`): `sbatch_args: str`, `srun_args: str`, `job_env: dict[str, str]`, written by the session under the slurm runtime and `null` otherwise — so `run.yaml` says how many ranks, which binding, which thread exports produced the run. `BaselineManifest` copies `run.yaml`'s fields already; it gains the same optional `launch` block (one line in `_manifest`), because a baseline's provenance should include its geometry.
- **`config/ursa.yaml`**: unchanged in values (user decision 6); the new `slurm.ntasks`, `slurm.srun_args`, `slurm.extra_args` keys are listed with their defaults and comments, as every key is. The template's geometry stays "one task, eight cores, the driver's own thread count"; the gcafs-anthro geometry is the override list of section 6. `config/local.yaml` is untouched (no slurm section).
- **`native` runtime**: unchanged — `ASSAY_LAUNCHER` already carries an arbitrary `srun …` prefix inside an allocation.
- Why this and not sweeping the task count as a suite dimension: the task count is not a property of the configuration under test (fact 5), a suite's combinations are configuration diffs, and the comparison against one baseline already answers "does the rank count change the output" (the four-rank TODO) without multiplying combinations.

### 6. The run on Ursa — overrides, not a new template

`config/ursa.yaml` runs as shipped for maccity. The gcafs-anthro run is the same template plus one override list — the suite, the batch QOS and cap, and the author's single-thread geometry — set once in the shell (runbook step 7a):

```bash
GCAFS=(harness:suite_config=gcafs-anthro-suite.yaml slurm:qos=batch harness:run_timeout_s=28800
       slurm:ntasks=1 slurm:cpus=1 slurm:srun_args=--cpu-bind=cores slurm:extra_args=--mem=0
       harness:env:OMP_NUM_THREADS=1 harness:env:OMP_PROC_BIND=spread harness:env:OMP_PLACES=threads
       harness:env:KOKKOS_NUM_THREADS=1 harness:env:MKL_NUM_THREADS=1
       harness:env:OPENBLAS_NUM_THREADS=1 harness:env:OPENBLAS_DEFAULT_NUM_THREADS=1)
cd $ROOT/ufs-chem-assay
uv run ufs-chem-assay run --config-file=config/ursa.yaml --dry-run --override "${GCAFS[@]}"
uv run ufs-chem-assay run --config-file=config/ursa.yaml --stage data --stage harness --override "${GCAFS[@]}"
```

(`harness:env:<NAME>=<value>` sets one key in the template's existing `env` mapping, so `I_MPI_FABRICS`/`FI_PROVIDER` stay; `$ROOT/scripts/run-config.yaml` shows the merged result. `tmux` as before; `queue_wait_s` is an hour — add `slurm:queue_wait_s=14400` when `batch` is busy.) The job script for the combination reads `#SBATCH -A epic -q batch -p u1-compute -N 1 -n 1 -c 1 --mem=0`, `--time=240`, the module block, the nine exports, `tic`, `srun --ntasks=1 --cpu-bind=cores ./build/cece_standalone_driver <combo>.yaml`, `toc`, `report-mem` — the author's `cece_job.sh` with the harness's naming, output, and chdir, reproducible by hand with `sbatch --wait` as today. `--mem=0` is the author's answer to `-c 1` (audit fact 11: per-cpu 6–12 G were not enough); the account is the run config's `epic` (confirmed by the user 2026-10-19; the author's script charges `arl`, which is not carried over). The four-rank run (the TODO) is the same list with `slurm:ntasks=4` (and `slurm:cpus` still 1: `-c` is per task).

### 7. Baselines — publish, then the cold start

1. First run, comparisons off (the template default): `test_baseline_comparison` skips; everything else must pass.
2. From the login node: `AWS_PROFILE=<profile> uv run ufs-chem-assay publish-baselines --output-root=$ROOT/CECE/ufs-chem-assay-output --dry-run`, then for real. One baseline, `s3://ufs-chem/baselines/<combo ulid>/` with `baseline.yaml` (now carrying `launch`), the suite's placeholder ULID rewritten. The user commits the repoint together with the species attributes (decision 8) and the tightened `timeout_s` (decision 7).
3. Cold start: `rm -rf $ROOT/baselines/<ulid>` if a cache exists (none does — `baselines.root_dir` is unset in the template, so the first run cached nothing), then

   ```bash
   uv run ufs-chem-assay run --config-file=config/ursa.yaml --stage data --stage harness \
     --override "${GCAFS[@]}" baselines:enabled=true baselines:root_dir=$ROOT/baselines
   ```

   The data stage's `fetch` pre-warms the baseline (`synced from s3://ufs-chem/baselines`, ~0.7 GB) and the session finds it `local`; to prove the *session's* download half instead, skip the data stage (`--stage harness` only) and let `pytest_sessionstart` sync it. `run.yaml` records `state: synced`; `test_baseline_comparison` passes (same machine, same build: bit-for-bit, `rtol` idle).

### 8. Documentation

- **README**: the configurations the harness carries (`maccity`, `gcafs-anthro` — what each is, where it runs); `inputs` entries with `url: null` (placed by hand, verified, never fetched) beside the fetched kind; the slurm section's `ntasks` / `srun_args` / `extra_args`; `ASSAY_SRUN_ARGS` in the harness-wide env table; `run.yaml`'s `launch`; the "Running on RDHPC" paragraph says `srun` with the configured task count instead of `--ntasks=1`.
- **`docs/gcafs-anthro-runbook.md`** (user, at implementation: a dedicated runbook rather than steps 5a/7a in the Ursa one): linking the seven files from the search path and recording the digests with one `fetch --record-digests` (section 4a), the `GCAFS` override list explained line by line, render and read, the one-rank run, the publish, the cold start, the four-rank run, and what to record. It assumes the Ursa runbook's steps 1–4 and `$ROOT`. `docs/ursa-runbook.md` points to it and describes the rendered `srun`/`tic`/`toc`/`report-mem` lines and `run.yaml`'s `launch`. No scratch paths, no user names (`test_user_docs`, which now covers both runbooks).
- **`design/design.md`** (implementation phase only): the "What is tested is a configuration" paragraph lists the second configuration; the Execution model's `srun --ntasks=1` becomes `srun ${ASSAY_SRUN_ARGS}`; the Baselines section notes `launch` in the manifest; a pointer to this document.
- **The carried configuration's header and the suite's comments** as in sections 1 and 3.
- **CHANGELOG** is semantic-release's; the PR title is `feat: integrate the global anthropogenic use case (gcafs-anthro)`.

### 9. Edge cases and guards (consistency scan)

- **A dry run anywhere** (laptop, CI, a checkout without the data): `--dry-run` never checks inputs or the store; the suite enumerates one combination, writes its config and job script (slurm) or command (docker), and skips every driver test. The `test_dry_run` subprocess covers the default selector; a new harness test runs the session dry against `gcafs-anthro-suite.yaml` as well (plan step 4).
- **`fetch` with a stale copy**: digest mismatch on a source-less input → `failed`, naming the mismatch and the path to replace; the file is never touched. (For a fetched input the existing rule applies: re-fetched into `.staging/`, replaced only after verification.) The session then fails at collection naming the command, as today.
- **A source-less input on a fresh checkout** (another Ursa user, a laptop): `fetch` says exactly which seven files to place where; nothing pretends to download. The runbook's step 5a is the answer until the inputs are published.
- **Comparisons enabled before the first publish**: `ensure_baselines` tries `<store>/00000000000000000000000000/`, downloads nothing, records `failed: not in the store (nothing downloaded)`; the comparison test fails with that reason; nothing else is affected. The placeholder's comment says so.
- **`publish-baselines` refuses a changed suite** ("edited since the run"): pin the species attributes and the timeout *after* publishing, in the same commit as the repoint — the order in section 7 step 2.
- **The slurm time limit**: `--time=240` from `timeout_s: 14400`; under `debug` the submission is rejected (30 min cap) and the `.out` carries sbatch's refusal — the override list says `batch`. The template's `run_timeout_s: 300` would cap the job to five minutes and kill it at six; the override list raises it, and `run-config.yaml` is where to confirm the effective value — the template comment on `run_timeout_s` names the interaction.
- **Memory with `-c 1`**: the per-core default was not enough for the author (6–12 G per cpu tried, then `--mem=0`), so the `GCAFS` list carries `--mem=0` from the start; `report-mem` in the `.out` says how much was actually used (TODO 3). `slurm.extra_args` defaults to empty, so maccity's jobs are unchanged.
- **`ntasks > 1` with `OMP_NUM_THREADS=1`**: valid (ranks × 1 thread); `cpus` is per task (`-c`), so `ntasks: 4, cpus: 1` asks for four cores. `srun --ntasks` and `sbatch -n` always agree because both render from `ntasks`.
- **maccity is untouched**: the template's values do not change, the default `ASSAY_SRUN_ARGS` renders the old `srun --ntasks=1` line byte for byte, and `run.yaml` merely gains `launch`.
- **The warnings in fact 4** appear in `cece.log` for every one of the 32 streams (six profile streams carry inert year fields, the 26 series streams set `yearFirst`/`yearLast`); they are the driver's and expected — not a harness assertion, not a README "known bug".
- **`exclude_none` dumps** keep `time_label`, `cadence`, `nz`, and the AMIO knobs only where the base configuration sets them; the maccity generated configs are unchanged, so its fingerprints and baselines are unaffected.
- **Two suites, one `dst` namespace**: `merge_inputs` dedupes identical entries and refuses conflicting ones; `gcafs-anthro/<basename>` cannot collide with `MACCity_4x5.nc`.

## Implementation plan (TDD, red → green → refactor)

1. **Model** (`applications/cece/config.py`; tests in `src/tests/ufs_chem_assay/applications/cece/test_cece_pipeline.py` or a new `test_cece_config.py`): red — a v5-shaped fragment fails on each of the eight keys/values; green — the enums and fields of section 2; a test that `amio_read_timeout_s` and `driver.amio_staging_timeout_ms` are *still* rejected (the decision, pinned); a round-trip of the new fields through `to_yaml`/`from_yaml`; `maccity.yaml` dumps unchanged.
2. **Source-less inputs** (`models/suite_config.py`, `staging.py`, `cli/main.py`; tests in `test_suite_config.py`, `test_staging.py`, `test_cli.py`): red — `InputFile(url=None, dst=…, sha256=…)` validates, `url=None` without a digest and `url=None, public=True` are refused, `stage_inputs` on a present-verified / present-wrong / absent source-less entry returns the three outcomes of section 4 without calling the wrapper or the probe, `RecordedInput` round-trips a null URL; green — the field, the validator, the branch. Fact 7's existing tests stay green (a URL entry behaves as before).
3. **The carried configuration and suite** (`src/tests/config/cece/gcafs-anthro/`): the files of sections 1 and 3 (digests filled in step 7; the placeholder ULID). Harness tests: the suite loads through `load_suite`, enumerates one combination, derives 25 expected files with the right names, the empty selector resolves to it; the suite is discovered by the `*-suite.yaml` rule and does **not** match the default selector; a dry-run session against it (subprocess, like `test_dry_run`) writes the config and job script and skips the driver tests.
4. **Geometry** (`settings.py`, `cli/run_config.py`, `cli/stages.py`, the template, `models/suite_config.py`, `baselines/models.py`+`publish.py`, `src/tests/conftest.py`): red — `render_job_script` with `srun_args="--ntasks=4 --cpu-bind=cores"` must emit that line, bracketed by the `tic`/`toc` echoes and followed by the guarded `report-mem`; `SlurmSection(ntasks=4, srun_args="--cpu-bind=cores", extra_args="--mem=64g").sbatch_args` and `srun_args_value`; `_exports` emits `ASSAY_SRUN_ARGS`; `RunManifest.launch` round-trips and is `null` under docker; the manifest carries it. Green — the fields, the template line, the export, the record. Existing tests on the exact script text (`test_slurm_runtime`, `test_stages`, `test_run_config`, `test_user_docs`) updated for the new default line (identical) and the template values.
4a. **In-place inputs** (section 4a; `settings.py`, `cli/run_config.py`, `cli/stages.py`, `staging.py`, `cli/main.py`; tests in `test_settings.py`, `test_run_config.py`, `test_stages.py`, `test_staging.py`, `test_cli.py`): red — the setting splits on `os.pathsep`; `data.input_search_path` exports `ASSAY_INPUT_SEARCH_PATH` only when set; `stage_inputs` links a unique basename match (absolute target), refuses none/several, replaces a dangling link, leaves present files alone, plans `would-link` in a dry run; `record_digests=True` turns a differing present source-less file into `recorded`; `rewrite_suite_digests` substitutes exactly the named entries' `sha256:` lines and restores the file when the reload differs; `fetch --record-digests` rewrites every selected suite declaring the dst and refuses `--dry-run`. Green — the code.
5. **`config/ursa.yaml`** and `config/local.yaml`: the three new slurm keys and `data.input_search_path: []` listed at their defaults with comments; `test_user_docs`/`run_configs.py` expectations for the template text.
6. **Docs** (section 8): README, runbook, the design pointer; `design.md` at the end.
7. **On Ursa** (the user's steps, recorded in Implementation notes as they happen): copy the seven files, `sha256sum` → the suite's digests (commit), `fetch` (verified, skipped), dry run of the run config with the `GCAFS` list, the real one-rank run (`batch`), `publish-baselines` (dry, then real), the species attributes and `timeout_s` from the run, commit; the cold start (section 7 step 3). The four-rank run is the pull-request TODO, not a step here.

## TODO (carried to the pull request)

Checked and resolved at pull-request time, each with its outcome recorded in the Implementation notes:

1. **The two unread keys** (`driver.amio_read_timeout_s`, `driver.amio_staging_timeout_ms`; audit fact 3). Re-check CECE `develop` at the PR's pin: if the parser reads them by then, add them to `Driver` and restore them to the carried configuration (the author's values, 300 s and 60000 ms); if not, decide whether to open a CECE issue — "the config documents AMIO timeouts the driver hard-codes" — with the facade lines as evidence. User's call at PR time (CECE's traffic is high; it may already be fixed).
2. **The four-rank run** (`slurm:ntasks=4` on the `GCAFS` list) against the one-rank baseline: `test_baseline_comparison` passes → rank invariance confirmed and recorded (`launch` in `run.yaml` says four tasks); it fails → a CECE issue with both `.out` banners (`MPI tasks 1` / `4`), the comparison CSV and bias plots, and the configuration; `rtol` is not loosened.
3. **`timeout_s` and memory** from the first run's `.out`: `timeout_s` tightened to ~2× the `tic`→`toc` wall time (the author's 3 h limit is the prior); `--mem=0` relaxed to a `--mem=<n>g` from `report-mem` if holding a whole node's memory matters on `u1-compute`.
4. **Species attributes** pinned from the first run's NetCDF (decision 8), in the repoint commit.
5. **Input file sizes** recorded (runbook "what to record"), for the deferred fetch path.

## Verification

```bash
# harness suite, type checks, hooks (100 % coverage gate)
uv run pytest src/tests/ufs_chem_assay -q
uv run pytest src/tests/ufs_chem_assay --cov
uv run pre-commit run --all-files

# every carried suite dry-runs with no data present
uv run pytest src/tests/test_driver_combos.py --dry-run --suite-config='.*-suite.yaml'
uv run pytest src/tests/test_driver_combos.py --dry-run --suite-config=gcafs-anthro-suite.yaml

# the integration suite still runs for real locally (docker) and in CI
uv run pytest src/tests/test_driver_combos.py --suite-config=simple-maccity-suite.yaml
```

On Ursa (login node, runbook order):

1. `fetch` after the copy: seven `skipped (sha256 verified)`, no network.
2. `run … --dry-run --override "${GCAFS[@]}"`: `05-harness-cece.sh` exports `ASSAY_SRUN_ARGS='--ntasks=1 --cpu-bind=cores'`, `ASSAY_SBATCH_ARGS='-A epic -q batch -p u1-compute -N 1 -n 1 -c 1 --mem=0'`, `ASSAY_RUN_TIMEOUT_S=28800`, the nine `ASSAY_JOB_ENV` pairs; `run-config.yaml` shows the merged `env`; the rendered `<combo>.sbatch` matches section 6. A plain `run … --dry-run` (no list) renders maccity's scripts exactly as before.
3. The real run: `test_driver_execution`, `test_nc_file_count` (25), `test_nc_filenames`, `test_nc_variable_dimensions` pass; `test_baseline_comparison` skipped; stats and 75 plots + 3 GIFs under the combo directory; `run.yaml` has `launch` and `inputs` with sizes; the `.out` ends with `toc` and the `report-mem` table.
4. `publish-baselines --dry-run` then real: one `published`, the suite's placeholder replaced, `baseline.yaml` in the store with `launch`.
5. The cold start (section 7 step 3): `run.yaml` baseline `state: synced`, `test_baseline_comparison` passed, `config_match` true.
6. Recorded from `sacct`: wall time and MaxRSS of the gcafs-anthro job (TODO 3); the seven input sizes (TODO 5).
7. The pull-request TODOs 1–2 (the unread keys, the four-rank run) before merge.

## Risks and notes

- **The author's driver is not necessarily `develop`.** The v5 comments cite defaults (120 s, 30000 ms) that `develop` hard-codes, which suggests the keys never took effect — but a branch may have read them. If the author's build differs, the harness's run on `develop` is a different experiment than theirs, and the baseline is the `develop` answer. TODO 1.
- **Wall time and memory are bounded, not measured.** The author's script says under 3 h on one core with the whole node's memory; 25 steps with 32 streams at 0.25° regridded from 0.1° inputs. The 4 h `timeout_s` and `--mem=0` are the ceilings; the first run's `tic`/`toc` and `report-mem` tighten both (TODO 3). A job killed by the time limit leaves the `.out` and a partial output directory; the harness reports `TimeoutExpired`.
- **Not the author's binary.** The author runs a `build-intel-rel-g` build (RelWithDebInfo-style, `-O2 -g`) with `I_MPI_F90=ifx` exported at module load; the harness builds `build/` as `Release` (`-O3`) through the same modulefile and has not needed the export. Optimization level can move floating-point bits, so the author's `out_cece-helm*` outputs are reference material, not the baseline — the harness's own one-rank run is. If a side-by-side is wanted, `applications:cece:cmake_args` and `applications:cece:driver_path` select the author's build type and directory.
- **Input sizes.** Irrelevant while the inputs are placed by hand (user decision 3), but the CAMS-TEMPO 0.1° daily weights file over 2011–2020 could be very large, which is what decides whether the deferred fetch path is practical; recorded at the first run (TODO 5).
- **Baseline size.** ~0.7 GB per publish, append-only; a handful of republishes is fine, a weekly one is not — `baselines.exclude` (Future work in the baseline-sync design) would trim plots. Not a blocker.
- **Login-node analysis** on 75 million-cell arrays: stats are quick with two dask workers; cartopy renders at this size take seconds each — minutes in total. Acceptable; `plotting.enabled: false` is the override if the login node objects.
- **Credentials on Ursa**: only the private store needs them now (the inputs are placed); the runbook's AWS CLI section covers it; `check_reachable` fails fast on a compute node, and the data stage runs on the login node.
- **Scratch purge**: copying the inputs under `$ROOT/CECE/data` ties them to the harness checkout's own retention; the bucket copy is the durable one.

## Open questions — resolved (user, 2026-10-19)

The six questions the first refinement left open, with the user's answers (the defaults they replace are in the appendix's conversational update):

1. **The two unread keys**: keep them out of the model and the carried configuration for now; carry the question to the pull request as TODO 1 (CECE moves fast; it may be fixed by then, and if not, an issue is the likely outcome).
2. **The name**: `gcafs-anthro` (not the first refinement's `global-anthro`).
3. **Inputs in the bucket**: none at this point — only baselines are published. Hence section 4's source-less inputs; the fetch path is Future work.
4. **Ranks**: the first run is one rank; the four-rank run is TODO 2, judged by the comparison against the one-rank baseline — rank invariance confirmed, or a CECE issue.
5. **The run window**: the author's 25 hours, as designed.
6. **The template's geometry**: `config/ursa.yaml` keeps its defaults; the author's single-threaded setup is applied by overrides for this suite only (section 6's `GCAFS` list).

## Future work

- **The fetch path for this configuration** (deferred by user decision 3): upload the seven files once to `s3://ufs-chem/inputs/cece/gcafs-anthro/`, fill in the `url:` lines (`public: false`), keep the digests — then any fresh checkout fetches them. Behind it, a `ufs-chem-assay publish-inputs --suite-config=…` command: digest, upload to the declared URIs, write the `url`/`sha256` lines — the upload half of `fetch`, mirroring `publish-baselines`.
- Optional `yearFirst`/`yearLast`/`yearAlign`/`taxmode` on `Stream` (the driver's defaults), so profile-cadence streams need not carry inert year fields; `taxmode: limit`; `time_var`/`time_units`/`calendar`; `stacking_refresh_interval_seconds`; the `output.amio_*` block; `gridspec_file`. Each when a carried configuration needs it.
- Sweeps over this configuration: `mapalgo` on the CEDS streams (`consd` vs `bilinear`, the EDGAR TODO), `tintalgo` on the CAMS daily stream — a second suite file; and a short `gcafs-anthro-smoke` suite (a few steps, `debug` QOS) if quick turnaround is wanted — the user kept the full window for this one.
- A profiling mode: the author's commented VTune lines (`srun --cpu-bind=cores vtune -collect hotspots -result-dir … -- $cece …` after `module load intel-oneapi-vtune`) are a launch *wrapper* plus an extra module — a `slurm.launch_wrapper` (or a wrapper form of `srun_args`) and a per-job extra module list would render them; the result directory would land in the combination directory like any other artifact.
- Multi-node (`-N`) and GPU-partition geometry in `SlurmSection`; `launch` as a comparison criterion (a baseline produced with four ranks compared by a one-rank run is a legitimate comparison today and should stay one — but the report could say so).
- Running the use case locally under docker once the inputs are fetchable (the item above, plus sizes) — the issue scopes it to Ursa.
- `baselines.exclude` for plot-free baselines when the store grows.
- A size-plus-mtime shortcut in `fetch`'s verification: every data stage re-hashes every present input with a declared digest, which is a one-time cost for maccity and tens of GB per run for gcafs-anthro through its links.

## Implementation notes (2026-10-19)

Implemented in the design's order, red → green at each step; the user's one addition at implementation time was a **dedicated runbook** for the use case (`docs/gcafs-anthro-runbook.md`) instead of extra steps in the Ursa runbook.

**Files.** Model: `src/applications/cece/config.py` (`Cadence` + `daily`/`series`/`stepwise` with the corrected comment, `TimeLabel`, `Grid.nz`, the three `Driver` AMIO knobs with the comment naming the two deliberately absent keys, `Stream.time_label`). Source-less inputs: `src/models/suite_config.py` (`InputFile.url: str | None` with the digest-required / public-refused validator and `_url()` behind `parent_prefix`/`filename`; `RecordedInput.url` nullable; `LaunchRecord`; `RunManifest.launch`), `src/staging.py` (the url-less branch: verified → skipped, wrong → failed "no source to re-fetch from — replace the file at …", absent → failed "place the file at …"; no probe, no `.staging/`), `StagedInput.url` nullable. The configuration: `src/tests/config/cece/gcafs-anthro/gcafs-anthro.yaml` (generated from the v5 file through the strict model — the two unread keys and the output-directory history dropped, paths rewritten, header comment added) and `gcafs-anthro-suite.yaml` (seven `url: null` inputs with quoted all-zero placeholder digests, `timeout_s: 14400`, the empty-selector entry with a quoted all-zero placeholder ULID, `rtol: 1.0e-15`). Geometry: `src/settings.py` (`srun_args`, default `--ntasks=1`), `src/cli/run_config.py` (`SlurmSection.ntasks`/`srun_args`/`extra_args`, `sbatch_args` with `-n {ntasks}` and the extras, `srun_args_value`), `src/cli/stages.py` (`ASSAY_SRUN_ARGS` exported under slurm; `srun_args` not mirrored), `src/templates/driver-job.sbatch.j2` (`srun {{ srun_args }} …` between `tic`/`toc`, then `if command -v report-mem >/dev/null 2>&1; then report-mem || true; fi`), `src/runner.py` (`render_job_script(srun_args=…)`, `write_job_script` passes the setting), `src/tests/conftest.py` (`launch` recorded under slurm), `src/baselines/models.py` + `publish.py` (`BaselineManifest.launch` copied from `run.yaml`). `config/ursa.yaml`: the three new keys at their defaults, commented; values unchanged. Docs: README (the two configurations, `url: null`, the RDHPC paragraph, the `ASSAY_SRUN_ARGS` row, the pointer to the new runbook), `docs/gcafs-anthro-runbook.md`, `docs/ursa-runbook.md`, `design/design.md` (configuration paragraph, execution model, inputs, manifest, layout).

**Tests** (673 passed, coverage 100 %, hooks green, mypy clean): `test_cece_config.py` (the eight new behaviours, the two rejected keys pinned, the round trip, `cadence: daily` moved from the non-canonical cases to the valid ones), `test_gcafs_anthro.py` (the carried shape, one sweep-less combination with placed inputs and the empty selector, 25 expected files, a checkout-less dry-run subprocess), `test_suite_config.py` (source-less inputs, `LaunchRecord` round trip), `test_staging.py` (the three url-less outcomes, dry run included), `test_settings.py`, `test_run_config.py` (geometry defaults, the override rendering, `ntasks` ≥ 1, `srun_args` in the not-mirrored set), `test_stages.py` (the export under slurm only), `test_slurm_runtime.py` (golden script with `tic`/`toc`/`report-mem`, the four-rank script), `test_dry_run.py` (`launch` in `run.yaml` under slurm, null under docker), `test_baselines.py`, `test_user_docs.py` (both runbooks). `test_cli.py`'s merged-inputs test selected `.*-suite\.yaml`, which now includes the seven gcafs-anthro inputs; it selects its two fixture suites instead.

**Departures and details worth knowing.** (1) The placeholder digests and ULID are all zeros and must be **quoted** in YAML — unquoted, the loader reads them as the integer 0 and the strict model rejects them; the suite's comment says so, and the `ulid:` repoint regex (`\S+`) replaces the quoted token whole. (2) `report-mem` is guarded with an `if` rather than `&&` so a machine without it exits 0 under `set -e`, and `|| true` keeps a cosmetic failure from failing the job. (3) `render_job_script` takes `srun_args` as a required keyword (StrictUndefined would otherwise fail the render silently later); the default setting reproduces the old `srun --ntasks=1` line. (4) `TimeLabel.center` shadows `str.center` and carries the same `type: ignore[assignment]` as `Operation.replace`. (5) The gcafs-anthro dry-run test runs checkout-less with `--basetemp` (an explicit `--combo-output-root` needs `CECE_ROOT_DIR`), which is also the shape any machine without the data can run.

**Verification run.** `uv run pytest src/tests/ufs_chem_assay --cov` → 673 passed, 100 %; `pre-commit run --all-files` green (ruff-format reflowed three test files on the first pass); `pytest src/tests/test_driver_combos.py --dry-run --suite-config='.*-suite.yaml'` → every carried suite, 2907 skipped; `pytest src/tests/test_driver_combos.py --suite-config=simple-maccity-suite.yaml` under docker → 21 passed (the three bucket baselines local, bit-for-bit within `rtol`). Not run here: anything on Ursa — the digests, the first run, the publish, the cold start and the four-rank run are the runbook's steps 1–7 and the pull-request TODOs.

**Remaining (the user's steps, then the PR TODOs).** On Ursa: place the files, `sha256sum` → the suite's digests (commit), `fetch`, the dry run and the one-rank run with the `GCAFS` list, `publish-baselines` (dry, then real), the species attributes and `timeout_s` into the suite with the repoint (commit), the cold start, the four-rank run. At PR time: TODOs 1–5.

## Implementation notes, second pass (2026-10-22): in-place inputs

Section 4a implemented red → green on top of the committed first pass (`2887407`).

**Files.** `src/settings.py` (`input_search_path`, the `os.pathsep` split shared with `suite_config_search_path`), `src/cli/run_config.py` (`DataSection.input_search_path`), `src/cli/stages.py` (`ASSAY_INPUT_SEARCH_PATH` exported by both shared stages when non-empty; the field not mirrored by `harness:`), `src/staging.py` (`_find_source` — recursive basename lookup, sorted; `_link_source_less` — the four outcomes, absolute symlink target, a dangling link replaced; `_stage_source_less` — link, then verify, then `recorded` under `record_digests`; the `linked`/`would-link`/`recorded` actions; `rewrite_suite_digests` with `_entry_sha_line` — the entry is the `- ` item containing the `dst:` line, its `sha256:` line is substituted keeping quotes and comment, the reload must equal the previous model with exactly those digests changed or the original text is restored), `src/cli/main.py` (`fetch --record-digests`: refused with `--dry-run`; after staging, every selected suite declaring a recorded `dst` is rewritten, each digest and a "review the diff and commit" line logged; a failed rewrite is exit 1; the summary counts the new actions), both run-config templates (`data.input_search_path: []`), README, `design.md`, and the gcafs runbook's step 1 (export the search path or put it in the override list, one `fetch --record-digests`, `git diff`, commit; the purge/dangling-link behaviour; `ls -lL` for sizes).

**Tests** (696 passed, coverage 100 %, hooks green, every suite dry-runs — 2907 skipped —, simple-maccity under docker 21 passed): `test_settings.py` (the new path setting), `test_run_config.py` (the data key, parsed from an override flow list; the not-mirrored set), `test_stages.py` (exported under both stages only when set), `test_staging.py` (linked and verified with an absolute target; wrong digest keeps the link and fails; not found names the search path, and an empty search path says so; ambiguity lists the matches; a dangling link is replaced; a present file is never relinked; a dry run plans `would-link` and creates nothing; URL inputs are never linked; `record_digests` records a differing file, also after linking, and leaves verified and URL inputs alone; `rewrite_suite_digests` rewrites only the named entries keeping every comment, refuses an unknown `dst` and an entry without a `sha256:` line, restores on a failed reload and on a reload that differs beyond the digests), `test_cli.py` (the setting passed through; a real end-to-end `--record-digests` on a fixture suite, idempotent on the second run; `--dry-run` refused; a failed rewrite reported), `test_user_docs.py` (the new README and runbook needles).

**Details worth knowing.** (1) The link target is `source.resolve()`, so a link never points at another link. (2) Linking happens before verification, so a linked file with a wrong digest stays linked and `failed` — the file is the user's to judge, and `--record-digests` is the way to accept it. (3) `yamlfix` reformatted the two gcafs-anthro YAML files on their first tracked `--all-files` run (list indentation, `url: null` → `url:` — the same null; and `#33` inside a comment became `# 33`, so the comments now say "issue 33"); the files still validate identically. (4) `rewrite_suite_digests` reads the entry's `sha256:` line by regex that captures its quoting, so the quoted all-zero placeholders are replaced in place as `'<digest>'`.

**Remaining.** On Ursa: `export ASSAY_INPUT_SEARCH_PATH=<tree>:<tree>` (or `data:input_search_path=` in the `GCAFS` list), `fetch --suite-config=gcafs-anthro-suite.yaml --record-digests`, review and commit the seven digests, then the runbook's steps 2–7 and the pull-request TODOs.

# appendix: original notes

## always do

- include updating design.md as part of the implementation
- update the harness tests (`src/tests/ufs_chem_assay`) in addition to any changes to test_driver_combos.py
- update README.md with any necessary documentation changes in case of an api adjustment
- use pydantic models as opposed to dataclasses
  - all pydantic fields should include a description like `... = Field(description="<description content here>", ...`
- do *not* add driver bugs to known bugs in `README.md` unless explicitly told to do so
- use a test-driven development, red-green-refactor approach for all fixes and features (when possible)
- maintain original design sections when refining design docs - create an appendix
  - summarize conversational updates in the appendix following original refinement target
  - refinements never create files. they only edit the target document that is being refined.
  - `design.md` may be updated during the _implementation_ phase only.
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code or use git actions that perform updates - the user always updates
- always prefer the harness's logging implementation over raw python print statements
- no need for wrapping when writing to the `./design` folder. wrapping will be handled by the user's ide
- persist the agy session id and resume command in any design/refined files for resumability

## session

- **Session ID**: `c30fb022-0fff-43a5-9908-1cfbe2cb2d88`
- **Resume Command**: `agy --resume c30fb022-0fff-43a5-9908-1cfbe2cb2d88`

### testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- refine from https://github.com/benkozi/ufs-chem-assay/issues/33
- configuration file is here: /Users/bkoziol/Library/CloudStorage/Dropbox/rlps/rsandbox/benkozi-work/transfer/cece_config_v5.yml
- updates to cece's pydantic configuration may be required
- consider different run scenarios for this configuration. we will want to run it under mpi. how does this affect the configuration?

```
Lately for this I've been running CECE with srun --cpu-bind=cores in a -N 1 -n 1 -c 1 job. After it became not terribly slow, I haven't yet re-experimented with using multiple MPI workers or OMP threads.

Also these env vars, trying to really keep it single-threaded.
export OMP_NUM_THREADS=1
export OMP_PROC_BIND=spread
export OMP_PLACES=threads
export KOKKOS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export OPENBLAS_DEFAULT_NUM_THREADS=1
```

Issue #33 ("Integrate global anthropogenic use case", labels `enhancement`, `CECE`, open, no comments), as read on 2026-10-19:

- Add @zmoon's CECE global anthropogenic use case to the harness. It will run on Ursa only initially.
- `/scratch3/NCEPDEV/naqfc/Zachary.Moon/cece-gcafs/cece_config_v5.yml`
- Investigate how to freshly stage the data, but expect to use data staged on Ursa already. In other words, both paths will need to be supported eventually
- Upload CECE baselines to the S3 sandbox
- Delete baselines on Ursa and attempt a cold start run where baselines are downloaded on demand

## conversational updates

- 2026-10-19 (refinement, no user input yet): the issue's three bullets and the requirements' two questions became the Goal and Design sections 1–9. Findings that shaped it beyond the issue text, all read from the code: (1) the strict CECE model rejects eight things in the v5 file, six of which the `develop` driver reads (`grid.nz`, three driver-level AMIO input knobs, stream `time_label`, `cadence: series`) and two of which it does not read at all (`driver.amio_read_timeout_s`, `driver.amio_staging_timeout_ms` — the facade hard-codes 120 s and 30000 ms, the "defaults" the v5 comments cite; `amio_staging_timeout_ms` is an `output:` key), so the model gains the six and deliberately not the two (Open question 1 for the author); (2) the driver's rank count comes from the communicator alone (latitude-band decomposition, rank-invariant gathers), so MPI changes the run config and the job script, never the configuration under test — the job template's hard-coded `srun --ntasks=1` becomes a rendered `ASSAY_SRUN_ARGS`, the slurm section gains `ntasks`/`srun_args`/`extra_args`, the requirements' single-thread exports become `config/ursa.yaml`'s documented default geometry, and `run.yaml` and `baseline.yaml` record the launch; (3) both input paths already fit `fetch`'s present-and-verified rule — copy the seven files to the declared destinations on Ursa, upload them once to `s3://ufs-chem/inputs/cece/gcafs-anthro/` for fresh checkouts — so no staging code changes, only digests and a runbook step; (4) `publish-baselines` and the download half cover the issue's second and third bullets as verification steps with a placeholder ULID and an empty selector for the sweep-less suite. Decisions taken without the user, each with its default and alternative under Open questions: the configuration name and bucket prefix, dropping the two unread keys, the faithful 25-hour window, `timeout_s: 14400` with `run_timeout_s` raised to the `batch` cap, species attributes pinned after the first run, and the template's default geometry becoming one core / one thread for every suite. No files other than this document were created or changed.
- 2026-10-19 (user: answers to the six open questions): (1) keys — keep the two unread keys out, but carry the question to the pull request so an issue can be opened then if CECE has not already changed (TODO 1); (2) the name is `gcafs-anthro`, renamed throughout from `global-anthro` (the directory, both files, the `data/gcafs-anthro/` destination, the suite name); (3) no input data to the bucket at this point, only baselines — so the suite declares its inputs with digests and `url: null`, `InputFile.url` becomes optional for that case, `fetch` verifies or names the missing file and never downloads it (section 4 rewritten; the upload and the `url` fill-in moved to Future work); (4) one rank first, the four-rank run is TODO 2, judged against the one-rank baseline (rank invariance confirmed or a CECE issue); (5) the 25-hour window stays; (6) `config/ursa.yaml` keeps its defaults and the author's single-threaded geometry is applied by overrides for this suite only — the `GCAFS` override list in section 6 (`slurm:ntasks=1 slurm:cpus=1 slurm:srun_args=--cpu-bind=cores`, the seven `harness:env:<NAME>=` entries, `slurm:qos=batch`, `harness:run_timeout_s=28800`), which works because an override descends into the template's existing `env` mapping key by key. Replaced defaults: the bucket prefix for inputs, the template-wide one-core/one-thread geometry and its byte-identical maccity check, `run_timeout_s: 28800` in the template, the four-rank run as a verification step. A new "TODO (carried to the pull request)" section holds the five items the pull request resolves. Open questions are now "resolved" with the answers; Goal 6, decision 10, and plan steps 2 (source-less inputs) and 5 (template keys only) added. Nothing outside this document changed.
- 2026-10-19 (user: an example `cece_job.sh` to incorporate, at the transfer path recorded in audit fact 11): the script confirms the geometry already designed (`-N 1 -n 1 -c 1`, `srun --cpu-bind=cores`, the seven exports, the modulefile) and adds four things. `--mem=0` after failed `--mem-per-cpu` attempts → `slurm:extra_args=--mem=0` joins the `GCAFS` list from the start (section 6, edge cases) rather than waiting on `MaxRSS`; a 3 h time limit after 30 and 90 minutes → the 4 h `timeout_s` is now a ceiling under a known bound (decision 7, risks); `tic`/`toc` timestamps and `report-mem` around the launch → the job template gains both (guarded `report-mem`), so a baseline's `.out` carries its wall time and memory (section 5, plan step 4, Verification 3, TODO 3); the commented `-n 48/32/24/12` history is context for the four-rank TODO. Noted, not adopted: the author's `arl` account (the run config's own applies), `build-intel-rel-g` and `I_MPI_F90=ifx` (the harness's Release build is the baseline's build; a new risk entry says why the author's outputs are reference material), and the VTune collections (Future work: a profiling mode). Nothing outside this document changed.
- 2026-10-19 (user: the runs charge the `epic` project): recorded in section 6; `config/ursa.yaml`'s `slurm.account: epic` stands and no `slurm:account=` override is needed. The author's `arl` stays a fact about their script only.
- 2026-10-19 (user: implement, and add a dedicated runbook for the example): implemented as designed (see "Implementation notes"); the runbook is `docs/gcafs-anthro-runbook.md`, a file of its own on top of the Ursa runbook's first four steps, and section 8 records that. Every gate green locally; the Ursa steps are the user's.

- 2026-10-22 (user: leave the data in place — no copies — and have the harness create the symlinks; record the digests automatically, committed like the baseline ULIDs): section 4a. `fetch` links source-less inputs from `ASSAY_INPUT_SEARCH_PATH` (run config `data.input_search_path`, exported by the stages) by basename, refusing ambiguity, replacing dangling links, never touching present files; `fetch --record-digests` rewrites the differing `sha256:` lines of the selected suites textually with a reload check, the ULID repoint's pattern, and the user commits the diff. Decision 4 and the runbook paragraph updated; plan step 4a added; a verification-cost shortcut noted as Future work. Implemented in the same session (Implementation notes, second entry).
