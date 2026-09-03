# Ursa runbook: running the harness natively

Manual steps to build the CECE driver against its own Ursa modulefiles
and run `simple-maccity-suite.yaml` through the harness inside one Slurm
job. `ufs-chem-assay run --config-file=scripts/ursa.yaml` automates
exactly this sequence (each stage renders to a script under
`<root_dir>/scripts/`, so the two must stay the same commands); the
design record is `design/feat/20260903-1453-run-on-rdhpc.md`.

Conventions:

- `ROOT` is one scratch directory holding everything this runbook
  creates: `ROOT=/scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/ufs-chem-assay`.
- `epic` / `debug` / `u1-compute` are the Slurm account, QOS, and
  partition used in the examples; substitute your own. `debug` caps
  jobs at 30 minutes, which fits `simple-maccity`; use `batch` (8 h)
  for the exhaustive suites.
- Steps 1–6 run on a **login node**: editing, compiling, downloads,
  and job submission are the allowed uses there. Nothing that needs
  network runs in the batch job (compute nodes are network-restricted).
- `$HOME` is quota-limited: caches, interpreters, and clones go under
  `ROOT`.

## 1. uv (once)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # installs ~/.local/bin/uv
export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR=$ROOT/uv-cache UV_PYTHON_INSTALL_DIR=$ROOT/uv-python
```

No root, no conda, no `rdhpcs-python` module needed. Put the three
`export`s in your shell profile (or a small `source`-able file under
`ROOT`) so batch scripts and later logins see them.

## 2. The harness

```bash
git clone git@github.com:benkozi/ufs-chem-assay.git $ROOT/ufs-chem-assay
cd $ROOT/ufs-chem-assay && git checkout feat/run-on-rdhpc
UV_PYTHON=3.13 uv sync --frozen        # downloads CPython 3.13 + wheels
uv run pytest src/tests/ufs_chem_assay # harness suite: hermetic, login-node safe
uv run pytest src/tests/test_driver_combos.py --dry-run   # no CECE needed
```

Python 3.13 rather than 3.14: every dependency has Linux wheels for
3.13 (cartopy included), so nothing compiles during the sync.

## 3. CECE source

```bash
git clone --recurse-submodules -b fix/all-examples-pass \
    git@github.com:benkozi/CECE.git $ROOT/CECE
```

The branch is the one the integration workflow uses
(`.github/workflows/integration.yaml`, `CECE_REF`).

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

## 6. Batch script

Save as `$ROOT/scripts/harness.sbatch` (create `$ROOT/scripts` and
`$ROOT/logs` first):

```bash
#!/bin/bash
#SBATCH -A epic -q debug -p u1-compute -N 1 -n 1 -c 8 -t 00:30:00
#SBATCH -J ufs-chem-assay -o /scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/ufs-chem-assay/logs/slurm-%j.out
set -euo pipefail
ROOT=/scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/ufs-chem-assay
source "$MODULESHOME/init/bash"
module purge; module use $ROOT/CECE/modulefiles; module load cece_ursa.intelllvm
export PATH="$HOME/.local/bin:$PATH" UV_CACHE_DIR=$ROOT/uv-cache UV_OFFLINE=1
export CECE_ROOT_DIR=$ROOT/CECE CECE_PLATFORM=ursa CECE_RUNTIME=native
export CECE_LAUNCHER="srun --ntasks=1" CECE_ENABLE_BASELINE_COMPARISONS=false
export CECE_DASK_NWORKERS=${SLURM_CPUS_PER_TASK} I_MPI_FABRICS=shm FI_PROVIDER=tcp
cd $ROOT/ufs-chem-assay
uv run --no-sync pytest src/tests/test_driver_combos.py \
    --suite-config=simple-maccity-suite.yaml \
    --combo-output-root=ufs-chem-assay-output --combo-clean-root
```

What the exports do: `UV_OFFLINE=1` and `--no-sync` keep uv from
touching the network; `CECE_RUNTIME=native` runs the driver as a host
process with `CECE_LAUNCHER` as its prefix (each combo becomes an
`srun` job step inside the allocation); `CECE_DASK_NWORKERS` matches
the stats cluster to the allocated cores on a shared node; the two
MPI variables are the single-node hints CECE's own ctest uses;
baselines are off because the baseline store has no public home yet.
Environment variables beat a `.env` file, so a laptop `.env` copied
along cannot silently win.

## 7. Submit and watch

```bash
sbatch $ROOT/scripts/harness.sbatch
squeue -u $USER
less $ROOT/logs/slurm-<jobid>.out
```

Results land in `$ROOT/CECE/ufs-chem-assay-output/`: `run.yaml`
(with `cece_commit` and, once the native runtime lands, `platform:
ursa`), `combos.csv`, `test-report.csv`, per-combo directories with
the generated config, `.out`, `cece.log`, NetCDF, stats, and plots.
Plots may warn about missing coastlines the first time: Natural Earth
data cannot download on a compute node, and the maps degrade to
data-only. Warm the cache once on a login node from the harness venv:

```bash
uv run python -c "import cartopy.io.shapereader as s; [s.natural_earth(resolution=r, category='physical', name='coastline') for r in ('110m', '50m')]"
```

For an interactive loop instead of `sbatch`:

```bash
salloc -A epic -q debug -p u1-compute -N 1 -n 1 -c 8 -t 00:30:00
```

then the same `module` and `export` lines as the batch script and
`uv run --no-sync pytest src/tests/test_driver_combos.py -x -vs ...`.

## What to record after the first run

These feed the implementation notes of the design doc:

- `hostname` on a login node and inside the allocation (platform
  detection table).
- Whether `cece_ursa.intelllvm` loads on today's `/contrib`
  spack-stack, and the configure-log lines for MPI, netCDF, and Kokkos.
- Driver wall time per combo under `srun` (whether
  `simple-maccity`'s `timeout_s: 10` needs raising on Ursa).
- Whether the driver's MPI singleton needs the `I_MPI_FABRICS=shm` hint.
