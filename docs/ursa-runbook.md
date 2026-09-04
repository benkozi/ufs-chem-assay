# Ursa runbook: running the harness natively

Manual steps to build the CECE driver against its own Ursa modulefiles
and run `simple-maccity-suite.yaml` through the harness from a login
node, one Slurm job per driver call. `ufs-chem-assay run --config-file=config/ursa.yaml` automates the
same sequence (each stage renders to a script under `<root_dir>/scripts/`,
so the two are the same commands).

Set these once in your shell; every step below uses them:

```bash
export ROOT=<your scratch directory>/ufs-chem-assay   # holds ufs-chem-assay/ and CECE/
export HARNESS_REF=develop                             # harness branch or tag
export CECE_REF=<branch or SHA>                        # CECE ref to build (config/ursa.yaml: cece.ref)
```

Conventions:

- `epic` / `debug` / `u1-compute` are the Slurm account, QOS, and
  partition used in the examples; substitute your own. `debug` caps
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

## 2. The harness

```bash
git clone --branch "$HARNESS_REF" git@github.com:benkozi/ufs-chem-assay.git $ROOT/ufs-chem-assay
cd $ROOT/ufs-chem-assay
UV_PYTHON=3.13 uv sync --frozen        # downloads CPython 3.13 + wheels
uv run pytest src/tests/ufs_chem_assay # harness suite: hermetic, login-node safe
uv run pytest src/tests/test_driver_combos.py --dry-run   # no CECE needed
```

Python 3.13 rather than 3.14: every dependency has Linux wheels for
3.13 (cartopy included), so nothing compiles during the sync.

## 3. CECE source

```bash
git clone --recurse-submodules --branch "$CECE_REF" \
    git@github.com:benkozi/CECE.git $ROOT/CECE
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
- Add `--target all` (or drop `--target`) to also build the CECE unit
  tests; run them later from the batch job with
  `srun --ntasks=1 ctest --test-dir $ROOT/CECE/build --output-on-failure`.

## 5. Data

```bash
cd $ROOT/ufs-chem-assay
uv run --no-sync python $ROOT/CECE/examples/download-example-data.py --example ex3 --dst-dir $ROOT/CECE/data
```

`ex3` is the MACCity file, the only input `simple-maccity` reads. The
download runs under the harness venv's Python: CECE's examples tooling
needs 3.11 or newer, and after `module purge` the only `python3` left is
the OS one.

## 6. The harness script

`scripts/ursa-harness.sh` in the harness checkout runs pytest **on the
login node** and submits **one Slurm job per driver call** through
`scripts/cece-modules.sh`, the job script that loads the CECE modulefile
before the driver. It reads `ROOT` (required) plus optional `MODULEFILE`,
`SBATCH_ARGS` (default `-A epic -q debug -p u1-compute -N 1 -n 1 -c 8`),
`SUITE`, and `OUTPUT_ROOT`. Read it once before running.

Two environments, kept apart: the harness venv must never see the
modulefile (spack-stack sets `PYTHONPATH` to `python3.11` packages that
shadow the venv's numpy), so the script starts with `module purge` and
`unset PYTHONPATH`; the driver needs the modulefile's libraries, so each
job loads it. Never run steps 2, 5, or 6 from a shell with the modulefile
loaded without purging first. Each job's time limit is the suite's
`timeout_s` rounded up to whole minutes; queue time does not count.
Analysis (stats, plots) runs in the pytest process on the login node, so
`CECE_DASK_NWORKERS` is pinned to 2.

## 7. Run and watch

```bash
tmux new -s harness            # the session outlives your SSH connection
export ROOT=<your scratch directory>/ufs-chem-assay
bash $ROOT/ufs-chem-assay/scripts/ursa-harness.sh 2>&1 | tee $ROOT/logs/harness-$(date +%Y%m%d-%H%M%S).log
```

In another window, `squeue -u $USER` shows the per-combo jobs come and
go. Results land in `$ROOT/CECE/ufs-chem-assay-output/`: `run.yaml`
(with `cece_commit`, `platform: ursa`, `runtime: slurm`, `modulefile`),
`combos.csv`, `test-report.csv`, per-combo directories with the generated
config, the job's `.out`, `cece.log`, NetCDF, stats, and plots. The login
node has network, so the first plot fetches Natural Earth coastlines on
its own.

For an interactive alternative, run the session inside an allocation
with the driver as a direct process:

```bash
salloc -A epic -q debug -p u1-compute -N 1 -n 1 -c 8 -t 00:30:00
```

then the same exports with `CECE_RUNTIME=native` and
`CECE_LAUNCHER="srun --ntasks=1 $ROOT/ufs-chem-assay/scripts/cece-modules.sh"`
(the wrapper still loads the modulefile per driver run) and
`uv run --no-sync pytest src/tests/test_driver_combos.py -x -vs ...`.

## What to record after the first run

Worth capturing for whoever maintains the harness:

- `hostname` on a login node and inside the allocation (platform
  detection table).
- Whether `cece_ursa.intelllvm` loads on today's `/contrib`
  spack-stack, and the configure-log lines for MPI, netCDF, and Kokkos.
- Queue wait and wall time per driver job (`sacct -j <id>`), and whether
  the one-minute job limit (`timeout_s: 10` rounded up) ever trips.
- Whether the driver's MPI singleton needs the `I_MPI_FABRICS=shm` hint.
