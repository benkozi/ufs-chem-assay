# Kokkos runbook: building and testing against a spack-stack environment

`config/ursa-kokkos.yaml` builds and tests three things against a
spack-stack environment that provides Kokkos, kokkos-kernels and ArborX,
with two compilers, from an Ursa login node:

- **CECE**: cloned, configured in a Debug build with ESMF, its unit tests
  (`ctest`) run as one Slurm job;
- **CATChem**: cloned with its submodules, configured in a Debug build
  with Kokkos, MUSICA and NUOPC, its unit tests run as one Slurm job;
- **the Kokkos smoke project** (`kokkos-smoke`): a small CMake project
  shipped with the harness that requires the environment's Kokkos and
  kokkos-kernels with no fallback, builds one executable and runs it.

Each is built the way the UFS builds: the harness's own UFS platform
modulefiles (`modulefiles/ufs_ursa.oneapi.lua`, `modulefiles/ufs_ursa.gnu.lua`,
and the `ufs_common.lua` they load) on top of the environment's
`modules/Core`. Each compiler suite is a **toolchain** of the run config
(`oneapi`, `gnu`) with its own checkouts, builds and results under
`$ROOT/<toolchain>/`.

This runbook builds on [ursa-runbook.md](ursa-runbook.md): do its
sections 1 and 2 first (uv, the harness checkout, `$ROOT`). Everything
below runs on a **login node**, from `$ROOT/ufs-chem-assay`.

## 1. Before the first run

Read `config/ursa-kokkos.yaml` and check four things:

- **The environments.** Each toolchain's `modulepath` ends with the
  environment's `modules/Core` directory. Confirm both exist, and see
  what they carry:

  ```bash
  module purge
  module use $ROOT/ufs-chem-assay/modulefiles
  module use <the oneapi toolchain's modules/Core path>
  module load ufs_ursa.oneapi && module list
  module avail kokkos kokkos-kernels arborx
  ```

  The modulefiles load the environment's default version of each
  package. To pin one, add it to the toolchain's `module_env`
  (`kokkos_ver`, `kokkos_kernels_ver`, `arborx_ver`, or any other
  `<name>_ver` the modulefiles read).
- **The Slurm account.** `slurm.account`, `slurm.qos` and
  `slurm.partition` are used three ways: the compile steps, the test
  jobs, and the driver jobs.
- **The refs.** `applications.cece` and `applications.catchem` name the
  `git_url` and `ref` to build.
- **ArborX.** `applications.cece.configure_gates` requires CECE to find
  the environment's ArborX. Remove that line for an environment that
  does not carry it. It is CECE's gate, not the toolchain's: a
  toolchain's gates apply to every application, and only CECE's
  configure prints the ArborX line.

## 2. Render and read the scripts

```bash
cd $ROOT/ufs-chem-assay
KOKKOS=(--config-file=config/ursa-kokkos.yaml --application all --toolchain all
        --stage source --stage build --stage test)
uv run ufs-chem-assay run "${KOKKOS[@]}" --dry-run
ls $ROOT/scripts/
less $ROOT/scripts/02-build-cece-oneapi.sh $ROOT/scripts/04-test-cece-gnu.sbatch
```

A dry run writes every script of every toolchain and executes nothing.
Per toolchain there is a source script for CECE and CATChem, a build
script for all three, and a test script with its job script
(`04-test-<application>-<toolchain>.sbatch`) for all three.

## 3. Run, watch, report

```bash
uv run ufs-chem-assay run "${KOKKOS[@]}"
uv run ufs-chem-assay watch --only tests
uv run ufs-chem-assay report --only tests
```

`run` goes one toolchain at a time: the whole stage list for `oneapi`,
then for `gnu`. A failed stage stops only what depends on it — a failed
build skips its own tests, a failed clone the rest of that application —
and everything else still runs, so the test jobs of the builds that
succeeded are submitted; `run` ends with one line per toolchain
(`oneapi: ok`, `gnu: failed at 02-build-catchem-gnu.sh (exit 1)`) and
the first failure's exit status. `--toolchain gnu` runs one of them;
`--application cece` one application; `--stage test` one stage, against
the build that is already there.

The build stage is the **offline compile probe**: each configure and
each build is its own `srun` step on a compute node, where nothing can
be fetched. CECE configures with `-DCECE_BUILD_TESTING=OFF` (its test
stack is fetched at configure time), and the stage then finishes the
same build directory on the login node with it back on, which is what
the tests need. CATChem is two builds of one checkout
(`applications.catchem.builds`): `compute`, with
`-DCATCHEM_BUILD_MUSICA=OFF`, fetches nothing and is built whole on the
compute node, tests included; `musica`, with MUSICA on, fetches at
configure time and is built on the login node. Each has its own build
directory (`build-compute`, `build-musica`), scripts and test job; the test job exports `OMP_PROC_BIND=false` (its `tests.env`), which keeps Kokkos's OpenMP backend from warning in every test without pinning threads. The configure log is checked before anything is
built: `CECE dependency Kokkos: system`, `CECE dependency ESMF: system`
and the ArborX line for CECE, `Using system Kokkos` for CATChem, both
packages found for the smoke project.

The test stage **submits and returns**: one job per application, `ctest`
directly on a compute node (each MPI test launches its own ranks: an
`srun` step under the `gnu` toolchain, an `mpiexec` fork under `oneapi` —
the toolchain's `mpi_launcher`). The job is recorded in
`$ROOT/<toolchain>/tests/<application>/tests.yaml`. `watch --only tests`
polls the queue and evaluates each job when it leaves it:

```
tests cece/oneapi: evaluated (exit 0), 577 passed / 0 failed / 0 skipped
tests kokkos-smoke/gnu: evaluated (exit 0), 1 passed / 0 failed / 0 skipped
```

`report --only tests` prints the same from the files, any time later;
for a failed run it names the failed tests and tails the job's output.
The job's files are `tests-<job id>.out` and `tests-<job id>.xml` (the
junit report) in the tests root.

To submit a test job again after a fix, run its stage again
(`--stage test`); a job still in the queue is refused, and

```bash
uv run ufs-chem-assay cancel --tests-root $ROOT/gnu/tests/cece
uv run ufs-chem-assay submit-tests --root-dir $ROOT --application cece --toolchain gnu --resubmit
```

cancel it, or cancel and resubmit in one step.

## 4. The same steps by hand

The scripts are these commands; when one fails, run it by hand. For CECE
under the `oneapi` toolchain:

```bash
# source (login node)
git clone --recurse-submodules --branch <ref> <git_url> $ROOT/oneapi/CECE

# module environment (login node; the srun steps inherit it)
module purge
module use $ROOT/ufs-chem-assay/modulefiles
module use <the oneapi toolchain's modules/Core path>
module load ufs_ursa.oneapi && module list
which mpiicx mpiicpx mpiifx cmake ctest

# the offline compile probe (compute node)
BUILD="srun -A epic -q debug -p u1-compute --nodes=1 --ntasks=1 --cpus-per-task=8 --time=10"
$BUILD cmake -S $ROOT/oneapi/CECE -B $ROOT/oneapi/CECE/build \
    -DCMAKE_BUILD_TYPE=Debug -DENABLE_ESMF=ON -DCMAKE_CXX_FLAGS=-fp-model=precise \
    -DCECE_BUILD_TESTING=OFF 2>&1 | tee $ROOT/oneapi/CECE/build/configure.log
grep -E "CECE dependency (Kokkos|ESMF): system|AXIS: ArborX" $ROOT/oneapi/CECE/build/configure.log
$BUILD cmake --build $ROOT/oneapi/CECE/build --target all --parallel 8

# the login-node finish: the test stack, into the same build directory
cmake -S $ROOT/oneapi/CECE -B $ROOT/oneapi/CECE/build \
    -DCMAKE_BUILD_TYPE=Debug -DENABLE_ESMF=ON -DCMAKE_CXX_FLAGS=-fp-model=precise \
    -DCECE_BUILD_TESTING=ON 2>&1 | tee $ROOT/oneapi/CECE/build/configure.log
cmake --build $ROOT/oneapi/CECE/build --target all --parallel 8

# the tests (the rendered job script; or, inside an allocation with the
# modules loaded: cd $ROOT/oneapi/CECE && ctest --test-dir build --output-on-failure)
sbatch $ROOT/scripts/04-test-cece-oneapi.sbatch
```

A job submitted with `sbatch` by hand is not recorded in `tests.yaml`,
so `watch` and `report` do not see it; `submit-tests` (above) is the
tracked way. Under the `gnu` toolchain the module is `ufs_ursa.gnu`,
the probes are `mpicc mpic++ mpifort`, there is no `-fp-model` flag, and
each configure also carries the launcher —
`-DMPIEXEC_EXECUTABLE="$(command -v srun)" -DMPIEXEC_NUMPROC_FLAG=-n`.
CATChem's `compute` build is the same two `srun` steps with MUSICA off
and no login-node finish, into `build-compute`; its `musica` build is
one configure and one build on the login node, into `build-musica`. The
smoke project has one configure and one build, of
`$ROOT/ufs-chem-assay/src/applications/kokkos_smoke/cmake` into
`$ROOT/<toolchain>/kokkos-smoke/build`, and its test is
`./build/smoke`, which ends with `SMOKE TEST PASSED`.

## 5. When something fails

- **`module load` reports an unknown module.** The environment's
  `modules/Core` path is wrong, or the environment lacks what the UFS
  modulefile loads. `module avail` with the two `module use` lines of
  section 1 shows what is there.
- **`configure gate failed: …`.** The configure ran but did not use the
  environment's package: it fetched its own, or found none. The line
  the gate wants is named in the message; `configure.log` in the build
  directory shows what the configure reported instead.
- **The compute-node configure fails on a download.** Something reaches
  the network that the application does not turn off;
  `applications.<name>.build_node: login` builds on the login node
  while that is sorted out.
- **`no tests built`.** The test stage does not compile: the build
  stage builds the test stack, in its login-node finish. Run the build
  stage again and read its last configure.
- **A test job fails.** `report --only tests` names the failed tests;
  `tests-<job id>.out` has ctest's output for each. A job killed by its
  time limit has no junit report: raise `tests.minutes`. MPI tests that
  cannot start their ranks are a launcher question: the toolchain's
  `mpi_launcher`, `mpiexec_preflags` and `tests_env`.

## 6. The CECE driver sessions (optional)

The standard run stops at the tests. The driver sessions of the Ursa
runbook can run against one toolchain's build:

```bash
uv run ufs-chem-assay run --config-file=config/ursa-kokkos.yaml --application cece \
    --toolchain oneapi --stage data --stage harness --session simple-maccity
uv run ufs-chem-assay watch --toolchain oneapi --only sessions
uv run ufs-chem-assay report --toolchain oneapi --only sessions
```

This build is Debug, so the timings are not comparable with a Release
build's; the benchmark rows record the build type, and the template
leaves `harness.record_benchmarks` off.

## 7. Recording the test benchmarks

Every evaluated test run is one row of
`$ROOT/<toolchain>/tests/<application>/benchmarks.csv`: the toolchain,
the build type and configure arguments, the commits, the job's wall
time, queue time and peak memory, and the junit counts. To keep them:

```bash
uv run ufs-chem-assay record-benchmarks --tests-root $ROOT/oneapi/tests/cece --dry-run
uv run ufs-chem-assay record-benchmarks --tests-root $ROOT/oneapi/tests/cece
git -C $ROOT/ufs-chem-assay diff -- src/tests/config/cece/tests-benchmarks.csv
```

The rows are appended to the committed per-application file; review the
diff and commit it. From a transfer directory (`ufs-chem-assay
transfer`'s), `record-benchmarks --transfer <dir>` does this for every
tests root it holds, on the laptop (see `ops/README.md`).

## 8. Gaea C7

`config/gaea-c7.yaml` is the same run on Gaea C7, for CECE, CATChem and
one toolchain (`oneapi`: Intel's compilers with cray-mpich), from a C7
login node. Sections 1 to 5 and 7 apply with these differences:

- **The build is the compute-node build of section 3**, the same as on
  Ursa: two `srun` steps on a C7 compute node (they carry
  `--clusters=c7` from `slurm.extra_args`), then the login-node finish.
  The gates are Ursa's (`CECE dependency Kokkos: system`, the ArborX
  line) plus `CECE dependency yaml-cpp: system`: the C7 modulefile also
  loads the environment's `yaml-cpp`, which CECE then takes over its
  submodule. To build on the login node alone, add
  `--override applications:cece:build_node=login`.
- **CATChem is two builds**, as on Ursa (`compute` and `musica`, section
  3), and both take the environment's yaml-cpp
  (`-DCATCHEM_USE_SYSTEM_YAML_CPP=ON`): after each configure the build
  fails if CATChem fell back to its vendored copy, and prints the
  `yaml-cpp_DIR` it used. The Kokkos smoke project is not in the C7
  template.
- **The module block starts with `module reset`**, never a purge, and the
  modulefile swaps the site's default oneAPI and cray-mpich for the ones
  the environment was built with. `module_env` pins `esmf_ver` and
  `mapl_ver`: the environment's default ESMF is a later beta than the one
  its MAPL is built against.
- **Every job names its cluster**: `slurm.extra_args: --clusters=c7`. The
  scheduler moves single-node jobs to the `batch-shared` partition and
  says so when the test job is submitted; that message is expected.
- **MPI tests launch through `srun`** with no extra flags, and compute
  nodes have no network: CECE's test job exports `PIP_NO_INDEX=1` so the
  pip steps of its Python tests return at once.

CATChem's vendored yaml-cpp exists because some ESMF builds carry their
own yaml-cpp in the same `YAML::` namespace. Before the first CATChem
build on C7, count the `YAML::` symbols the environment's ESMF exports
(0 means no clash; anything else, record it and set
`-DCATCHEM_USE_SYSTEM_YAML_CPP=OFF` in both builds):

```bash
( module reset; export esmf_ver=9.0.0b17 mapl_ver=2.69.1-esmf-9.0.0b17; module use $ROOT/ufs-chem-assay/modulefiles; module use /ncrc/proj/epic/spack-stack/c7/spack-stack-2.2.0/envs/ue-oneapi-2025.3.1/modules/Core; module load ufs_gaeac7.oneapi; L=$(dirname $ESMFMKFILE); ls $L/libesmf*; nm -DC --defined-only $L/libesmf.so 2>/dev/null | grep -c ' YAML::' )
```

For the everyday loop, a personal run config extending
`config/gaea-c7.yaml` names the root, the selection and the transfer
directory; `ops/assay-run.sh <run config>` runs it and then `watch`, with a
transcript in `<root>/logs`, and `ops/assay transfer --config-file <run
config>` sends the root's logs, records and report to the transfer repo
(`ops/assay` keeps uv's cache and Pythons in the checkout's `.uv/`; see
`ops/README.md` and `ops/c7-runbook.md`).
The same steps by hand:

```bash
cd $ROOT/ufs-chem-assay
C7=(--config-file=config/gaea-c7.yaml --application all)
uv run ufs-chem-assay run "${C7[@]}" --stage source --stage build --stage test --dry-run
uv run ufs-chem-assay run "${C7[@]}" --stage source --stage build --stage test
uv run ufs-chem-assay watch
uv run ufs-chem-assay report
```

To pull a newer commit of a ref into the existing clone and rebuild
only what changed, add `--override applications:cece:update_source=true`
(or `applications:catchem:…`) to the `run` command; `--build compute` or
`--build musica` runs one CATChem build.

## What to record after the first run

- What `module avail kokkos kokkos-kernels arborx` shows in each
  environment, and whether `kokkos_ver` / `kokkos_kernels_ver` pins are
  wanted in `module_env`.
- Whether the module environment's `ctest` writes a junit report
  (`--output-junit` needs CMake 3.21 or newer).
- Queue waits for the compile steps and the test jobs under the `debug`
  QOS, and whether `tests.minutes: 5` is enough for each suite.
- Whether `report-mem` prints inside the test job (its output ends the
  job's `.out`).
- Whether Intel MPI's tests can launch through `srun`
  (`mpi_launcher: srun` with `mpiexec_preflags: --mpi=pmi2` for the
  `oneapi` toolchain) or need `mpiexec`, as shipped.
- The pass counts per toolchain, and any test that fails under one
  compiler only.
