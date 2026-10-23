# Ursa runbook: running the harness natively

Manual steps to build the target driver (CECE's `cece_standalone_driver`,
the application this runbook is written for) against the application's
own Ursa modulefiles and run `simple-maccity-suite.yaml` through the
harness from a login node, one Slurm job per driver call. The
gcafs-anthro configuration has its own runbook on top of this one:
[gcafs-anthro-runbook.md](gcafs-anthro-runbook.md).
`ufs-chem-assay run --config-file=config/ursa.yaml` automates the same
sequence (each stage renders to a script under `<root_dir>/scripts/`, so
the two are the same commands).

Set these once in your shell; every step below uses them:

```bash
export ROOT=<your scratch directory>/ufs-chem-assay   # holds ufs-chem-assay/ and CECE/
export HARNESS_REF=develop                             # harness branch or tag
export CECE_REF=<branch or SHA>                        # CECE ref to build (config/ursa.yaml: applications.cece.ref)
```

Conventions:

- `epic` / `debug` / `u1-compute` are the Slurm account, QOS, and
  partition used in the commands below; substitute your own. `debug` caps
  jobs at 30 minutes, which fits `simple-maccity`; use `batch` (8 h)
  for the exhaustive suites.
- Steps 1–5 run on a **login node**: editing, compiling, downloads,
  and job submission are the allowed uses there. Nothing that needs
  network runs in the batch job (compute nodes are network-restricted).
- `$HOME` is quota-limited: caches, interpreters, and clones go under
  `$ROOT`.

## 1. uv (once)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # installs ~/.local/bin/uv
export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR=$ROOT/uv-cache UV_PYTHON_INSTALL_DIR=$ROOT/uv-python
```

No root, no conda, no `rdhpcs-python` module needed. Put the three
`export`s in your shell profile (or a small `source`-able file under
`$ROOT`) so batch scripts and later logins see them.

### AWS CLI (once, for S3 data sync)

The harness syncs data with `aws s3 sync` (today: the `data_integration`
test against the private `ufs-chem` bucket). Check for a site-provided
binary first, then install v2 user-locally if there is none. Install
under `$HOME`, not `$ROOT`: a tool belongs with the other per-user
binaries (`~/.local/bin/uv` above), not in the run tree — change the two
directories if your layout differs:

```bash
which aws && aws --version                            # present? note the version and skip the install
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install --install-dir $HOME/aws-cli --bin-dir $HOME/bin
rm -rf /tmp/aws /tmp/awscliv2.zip
export PATH="$HOME/bin:$PATH"                         # beside the uv exports above
aws --version
```

Credentials are the CLI's own: `aws configure` (or `aws sso login`)
writes `~/.aws/credentials` and `~/.aws/config` with a region — the harness
reads nothing AWS-related itself, and nothing goes in `.env`. The login
node has network; compute nodes do not, so syncs never run inside a job.
Confirm the credentials from `$ROOT/ufs-chem-assay` once the harness is
set up (step 2):

```bash
uv run pytest -m data_integration src/tests/ufs_chem_assay/test_s3_sync.py -v
```

## 2. The harness

```bash
git clone --branch "$HARNESS_REF" git@github.com:benkozi/ufs-chem-assay.git $ROOT/ufs-chem-assay
cd $ROOT/ufs-chem-assay
UV_PYTHON=3.13 uv sync --frozen        # downloads CPython 3.13 + wheels
uv run pytest src/tests/ufs_chem_assay # harness suite: hermetic, login-node safe
uv run pytest src/tests/test_driver_combos.py --dry-run   # no CECE needed
```

Both commands run before CECE exists, and neither needs a checkout: the
harness suite never looks at one, and a dry run records
`application_commit: null` (with a warning when `CECE_ROOT_DIR` is already
exported but not yet cloned) — only a real run requires the configured
root to be a git checkout.

Python 3.13 rather than 3.14: every dependency has Linux wheels for
3.13 (cartopy included), so nothing compiles during the sync.

## 3. CECE source

```bash
git clone --recurse-submodules --branch "$CECE_REF" \
    git@github.com:ufs-community/CECE.git $ROOT/CECE
```

## 4. Driver build (native, modules from the checkout)

```bash
module purge
module use $ROOT/CECE/modulefiles
module load cece_ursa.intelllvm && module list
which mpiicx mpiicpx mpiifx cmake
mkdir -p $ROOT/CECE/build
cmake -S $ROOT/CECE -B $ROOT/CECE/build -DCMAKE_BUILD_TYPE=Release \
    2>&1 | tee $ROOT/CECE/build/configure.log
grep -E "Found MPI|netCDF|Kokkos" $ROOT/CECE/build/configure.log
cmake --build $ROOT/CECE/build --target cece_standalone_driver --parallel 8
```

First-run checks:

- The modulefile's spack-stack environment must exist:
  `ls /contrib/spack-stack/spack-stack-1.9.2/envs/`. If `module load`
  reports an unknown module, the `cece_ursa.*` files need a CECE-side
  update; that is a CECE change, not something to work around here.
- Configure runs FetchContent (kokkos, yaml-cpp, googletest,
  rapidcheck) and therefore needs the login node's network.
- The `grep` should show `Found MPI` and a netCDF line; a missing
  netCDF line means the spack-stack `*_ROOT` variables were not picked
  up.

## 5. Data

```bash
cd $ROOT/ufs-chem-assay
CECE_ROOT_DIR=$ROOT/CECE uv run --no-sync ufs-chem-assay fetch --suite-config=simple-maccity-suite.yaml
```

The suite declares its inputs (`inputs:` in the suite file); `fetch`
stages them into `$ROOT/CECE/data` through the AWS CLI, anonymously for
public buckets (the MACCity file, the only input `simple-maccity` reads,
comes from the public `geos-chem` bucket — no AWS account needed). A file
already present with the declared `sha256` is skipped without touching the
network, so re-running is free. With `baselines.enabled: true` (or
`ASSAY_ENABLE_BASELINE_COMPARISONS=true`) and a `baselines.root_dir`,
`fetch` also pulls the baselines the suite pins from the store into that
root — the login node pre-warming what the session will compare against.
The data stage of the run config does the same.

## 6. Render and read the scripts

The shipped `config/ursa.yaml` runs as-is from a checkout laid out like
this runbook: the CLI derives `root_dir` as the parent of the harness
checkout (`$ROOT`), so `$ROOT/CECE` is the checkout it uses. Nothing in
the YAML needs editing; pass `--root-dir` only if your layout differs,
and override any other key from the command line instead of editing —
`--override slurm:account=<yours>` if your Slurm account is not `epic`,
`--override applications:cece:ref=<branch>` for another CECE ref. The
effective configuration (file plus overrides) is written to
`$ROOT/scripts/run-config.yaml` on every invocation.

```bash
cd $ROOT/ufs-chem-assay
uv run ufs-chem-assay run --config-file=config/ursa.yaml --dry-run
```

The dry run renders every stage to `$ROOT/scripts/<NN>-<stage>-<application>.sh`
(`05-harness-cece.sh` for the harness stage) and executes nothing; read
`05-harness-cece.sh` before running it. It runs pytest
**on the login node**, and pytest submits **one Slurm job per driver
call**: each combo gets a rendered `<combo_id>.sbatch` beside its
`.yaml` and `.out` under the output root, with the `#SBATCH` directives
(`-N 1 -n <slurm.ntasks> -c <slurm.cpus>` plus `slurm.extra_args`), the
module load, and the `srun` launch (`--ntasks=<slurm.ntasks>` plus
`slurm.srun_args`) spelled out, bracketed by `tic`/`toc` timestamps and
followed by `report-mem`, so the `.out` records the job's wall time and
memory. The job's time limit is the suite's `timeout_s` rounded up to
whole minutes; queue time does not count.

Two environments, kept apart: the harness venv must never see the
modulefile (spack-stack sets `PYTHONPATH` to `python3.11` packages that
shadow the venv's numpy), so the harness script starts with
`module purge` and `unset PYTHONPATH`; the driver needs the modulefile's
libraries, so each job loads it. Never run steps 2, 5, or 7 from a shell
with the modulefile loaded without purging first. Analysis (stats,
plots) runs in the pytest process on the login node, so the template
pins `dask_nworkers` to 2.

## 7. Run and watch

```bash
tmux new -s harness            # the session outlives your SSH connection
cd $ROOT/ufs-chem-assay
uv run ufs-chem-assay run --config-file=config/ursa.yaml --stage harness
```

The CLI logs to `$ROOT/logs/05-harness-cece-<timestamp>.log` as it
runs. In another window, `squeue -u $USER` shows the per-combo jobs
(`ufs-chem-assay-<combo_id>`) come and go. Results land in
`$ROOT/CECE/ufs-chem-assay-output/`: `run.yaml` (with `application:
cece`, `application_commit`, the harness's own `harness_version` and
`harness_commit`, `platform: ursa`, `runtime: slurm`, `modulefile`,
`launch`: the directives, srun options and job environment),
`combos.csv`,
`test-report.csv`, and per combo the generated config, the job script,
the job's `.out`, `cece.log`, NetCDF, stats, and plots. The login node
has network, so the first plot fetches Natural Earth coastlines on its
own.

**Triage.** A failed combo is reproducible by hand:

```bash
cd $ROOT/CECE && sbatch --wait $ROOT/CECE/ufs-chem-assay-output/<combo_id>/<combo_id>.sbatch
```

then read the `.out` it rewrites. Edit the script in place to
experiment (a different modulefile, an extra export); the harness
regenerates it on the next run.

**Publishing baselines.** Once a run is the one you want to compare
future runs against, publish its compared combinations from the login
node (the AWS CLI step above; `AWS_PROFILE` as you configured it) —
dry-run first, then for real:

```bash
cd $ROOT/ufs-chem-assay
AWS_PROFILE=<profile> uv run ufs-chem-assay publish-baselines \
    --output-root=$ROOT/CECE/ufs-chem-assay-output --dry-run
AWS_PROFILE=<profile> uv run ufs-chem-assay publish-baselines \
    --output-root=$ROOT/CECE/ufs-chem-assay-output
```

Each combination the suite compares against goes to
`s3://ufs-chem/baselines/<ulid>/` with a `baseline.yaml` manifest, and
the suite file's `ulid:` lines are repointed in your harness checkout —
commit that change. Set `baselines.root_dir` in the run config (or
`ASSAY_BASELINE_ROOT_DIR`) to also keep a local copy the next run reads;
`harness.publish_baselines: true` does all of this at the end of the
harness stage instead — the harness stage runs on the login node under
the slurm runtime, which has network. A session run *inside* an
allocation (a compute node, no network) refuses `--publish-baselines` at
start: run without the flag, then publish the output root from a login
or data-transfer node as above. `--store=/path/to/dir` (or `baselines.store`)
publishes into a local directory instead of the bucket — no credentials,
but only reachable on this machine.

**Benchmarks.** The session appended one row per combination to
`src/tests/config/cece/maccity/benchmarks.csv` in your harness checkout
(`config/ursa.yaml` sets `harness.record_benchmarks: true`): timings, Slurm's
elapsed, queue wait and peak memory (`sacct`), the verdicts, and the
baseline links. Review and commit it with the repoint. A row never changes
once written; for a run that was not recorded:

```bash
cd $ROOT/ufs-chem-assay
uv run --no-sync ufs-chem-assay record-benchmarks --output-root=$ROOT/CECE/ufs-chem-assay-output
```

**Uploading a run.** To keep or share the whole output root (every
combination's NetCDF, `.out`, `cece.log`, plots, job script), copy it to
`s3://ufs-chem/assay-output/<run ULID>/` from the login node — resumable,
re-runnable after a publish, never deleting:

```bash
cd $ROOT/ufs-chem-assay
AWS_PROFILE=<profile> uv run --no-sync ufs-chem-assay upload-output --output-root=$ROOT/CECE/ufs-chem-assay-output --dry-run
AWS_PROFILE=<profile> uv run --no-sync ufs-chem-assay upload-output --output-root=$ROOT/CECE/ufs-chem-assay-output
```

`harness.upload_output: true` in the run config does it at the end of the
harness stage instead (off in every template; the login node has network).
On any machine with the bucket credentials, `download-output --ulid <run
ULID> --dest <dir>` brings the root back to `<dir>/<run ULID>/`; the ULID
is `run_id` in `run.yaml` and the output root's name in the harness log.

The `native` runtime (the driver as a direct host process) is not a
supported path on Ursa: the harness venv and the driver need conflicting
environments, and only a per-job script reconciles them.

## What to record after the first run

Worth capturing for whoever maintains the harness:

- `hostname` on a login node and inside the allocation (platform
  detection table).
- Whether `cece_ursa.intelllvm` loads on today's `/contrib`
  spack-stack, and the configure-log lines for MPI, netCDF, and Kokkos.
- Queue wait and wall time per driver job (`sacct -j <id>`), and whether
  the one-minute job limit (`timeout_s: 10` rounded up) ever trips.
- Whether the driver's MPI singleton needs the `I_MPI_FABRICS=shm` hint.
