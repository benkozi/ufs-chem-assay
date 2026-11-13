# test-kokkos: CATChem vs. the kokkos spack-stack environment

Build/test harness validating the custom spack-stack unified-dev environment
requested in [JCSDA/spack-stack#2081](https://github.com/JCSDA/spack-stack/issues/2081)
— Intel oneAPI 2025.3.1 with `kokkos` and `kokkos-kernels` (`+openmp +serial
~cuda`) — using Intel LLVM compilers (`mpiicx` / `mpiicpx` / `mpiifx`).

Three components:

- **catchem** — builds CATChem with `-DCATCHEM_ENABLE_KOKKOS=ON
  -DCATCHEM_BUILD_MUSICA=ON -DCATCHEM_BUILD_NUOPC=ON` and runs its ctest
  suite. The harness fails loudly unless CATChem's configure output shows
  `Using system Kokkos <version>` (proof the spack-stack Kokkos was consumed
  rather than a FetchContent fallback).
- **cece** — builds CECE (benkozi fork, `feat/prefer-find-package` branch)
  with its defaults and runs its gtest/ctest suite. That branch prefers a
  system Kokkos via `find_package` and logs its provenance; the harness
  fails loudly unless the configure output shows
  `CECE dependency Kokkos: system <version>` (the FetchContent fallback
  line is a failure). A second, independent consumer of the env's kokkos
  (C++20/gtest vs CATChem's Fortran/C++ mix). `ENABLE_ESMF` stays OFF
  (CECE's default) for now: the env's static `libesmf.a` bundles yaml-cpp
  objects, and an ESMF-enabled CECE would likely reproduce the
  duplicate-symbol clash CATChem fixed with its `YAML=CATChem_YAML`
  prefix — an ESMF variant is a follow-up after CECE grows the same
  defense. CECE still uses no kokkos-kernels; the smoke test remains the
  only direct validation of that package.
- **smoke** — a standalone CMake project (`smoke/`) that `find_package`s both
  `Kokkos` and `KokkosKernels` with **no fallback**, prints the Kokkos
  version/backends, and runs a small `parallel_for` + BLAS1 `axpby` check.
  This is the only piece that directly validates `kokkos-kernels` (CATChem
  does not use it).

CECE is intentionally **not** covered: it needs a CMake change (a
`find_package(Kokkos)`-first path) before it can consume the env's Kokkos —
that lands as a follow-on PR.

## How to run

```bash
# default: build + test everything on ursa
./build-test-kokkos.sh

# vet the command plan locally (macOS) before deploying
./build-test-kokkos.sh --dry-run

# build CATChem only, no tests
./build-test-kokkos.sh --build-only catchem

# build and test CECE only
./build-test-kokkos.sh cece

# re-run tests against an existing build
./build-test-kokkos.sh --test-only catchem

# run a single test (or regex subset) with all module/srun setup handled
./build-test-kokkos.sh --test-only --test-filter '^nuopc_contract_valid$' catchem

# smoke-test the env's kokkos/kokkos-kernels only
./build-test-kokkos.sh smoke

# sync the CATChem clone to the configured branch before building
./build-test-kokkos.sh --update-source catchem

# compile on a compute node (probes building without network access)
./build-test-kokkos.sh --srun-build catchem

# explicit platform selection (ursa is the default)
./build-test-kokkos.sh --platform ursa

# run on Gaea-C6 instead
./build-test-kokkos.sh --platform gaea-c6

# build with the GCC suite against the GCC env (ursa only)
./build-test-kokkos.sh --compiler gcc cece

# Slurm-native rank placement: per-test srun steps instead of forked ranks
./build-test-kokkos.sh --direct-launch cece

# ... submit the test job and return immediately (results in the job log)
./build-test-kokkos.sh --direct-launch --async --test-only cece

# ... run up to 4 tests concurrently inside the allocation
./build-test-kokkos.sh --direct-launch --test-jobs 4 cece

# run the automated test suite across compilers and phases (oneapi + gcc)
./build-test-kokkos-suite.sh cece

# ... or across all applications
./build-test-kokkos-suite.sh all

# ... dry-run to preview the plan
./build-test-kokkos-suite.sh --dry-run all
```

`--build-only` and `--test-only` are mutually exclusive; the default is build
**and** test. Components are `catchem`, `cece`, `smoke`, or `all` (default).
Platforms are `ursa` (default) and `gaea-c6`; each has its own config block
(root dir, env root, modulefile, module init, toolchain probe list, srun
launcher) at the top of the script. `--compiler` selects the suite —
`oneapi` (default) or `gcc` — switching the env root, modulefile, and
toolchain probes together (they are one compiler-suite choice; letting
them drift apart is exactly the Lmod "module(s) are unknown" failure).
gcc is ursa-only: upstream ufs-weather-model has no gaea-c6 gnu
modulefile and no GCC C6 env exists.

**Re-runs and cleanliness:** build runs require the platform `root_dir` to
**not exist** — the script bails out if it does, and it never removes
anything itself (there is no `rm` in the script). Every run is therefore a
from-scratch clean run by construction. To re-run a build, remove or rename
the root dir yourself first:

```bash
mv /scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos \
   /scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos.old
```

The exceptions are `--test-only` (which reuses the existing root/build dir to
re-run tests without rebuilding) and `--reuse-build` (which reconfigures and
builds tests on the login node into an existing build directory, e.g. reusing
object files from an earlier `--srun-build` pass).

**Compute-node compile (`--srun-build`, off by default):** normally the
compile runs on the login node, because CATChem's configure FetchContents
MUSICA's dependencies and login nodes have network. The real UFS workflow
(`rt.sh`) compiles on **network-restricted compute nodes**, though — so
`--srun-build` wraps the configure + build of each component in the
per-platform `<platform>_build_launcher` (srun, 10-minute walltime) to
probe that constraint directly. Build and tests remain **separate** srun
commands with separate allocations; source clone/fetch stays on the login
node. Interpreting the outcome:

- **Pass** — the env + toolchain compile the component offline on a
  compute node; strongest fidelity signal for spack-stack#2081.
- **catchem**: `--srun-build` automatically configures with
  `-DCATCHEM_BUILD_MUSICA=OFF` — MUSICA FetchContents its dependencies
  (micm, tuv-x, MechanismConfiguration) at configure time, which cannot
  work offline (confirmed by compute-node failures on 2026-09-14).
  Everything else in catchem is offline-safe (env kokkos, in-tree
  yaml-cpp, submodules cloned on login), so unlike cece the **test phase
  stays enabled**: the compute-node run proves the Fortran core, GOCART,
  the Kokkos C++ core, and the NUOPC cap compile and test offline. The
  musica-gated gaschem/photolysis tests exist only in login-node
  (default) builds, which keep MUSICA=ON. The durable fix — CATChem
  vendoring MUSICA's dependencies the way yaml-cpp is vendored — is a
  CATChem follow-up (mandatory eventually for CATChem-in-UFS with
  MUSICA, since UFS compiles on network-restricted nodes).
- The `smoke` component performs no fetches, so `--srun-build smoke`
  cleanly validates "env compiles offline" with no component caveats.
- **cece**: `--srun-build` automatically configures with
  `-DCECE_BUILD_TESTING=OFF` — the GTest/RapidCheck FetchContent happens
  at configure time and cannot work offline (a 2026-08-25 compute-node
  run died cloning googletest exactly this way; note `--build-only` alone
  does NOT prevent it, since the clone is part of configure, not ctest).
  With tests off, yaml-cpp vendored, and arborx from the env, the cece
  compile is fully offline-capable. The trade-off: the compute-node run
  proves libraries-compile-offline only; run a normal login-node `cece`
  pass for the test suite. The cece test phase is skipped with an
  explanatory note under `--srun-build`.

**Slurm-native test launch (`--direct-launch`, opt-in):** the default test
phase wraps ctest in a 1-task srun step and lets `mpiexec` fork multi-rank
tests inside it (which needs the per-compiler fork-bootstrap env below).
`--direct-launch` inverts this into the descriptive shape: catchem/cece are
configured with `-DMPIEXEC_EXECUTABLE=srun -DMPIEXEC_NUMPROC_FLAG=-n`
(plus the per-compiler `*_mpiexec_preflags` config, e.g. `--mpi=pmi2` if a
platform's PMI experiment demands it), and each ctest phase is submitted
as one `sbatch --wait` job — cece through its repo-owned
`scripts/run-tests-slurm.sbatch` (which carries the job topology,
`--ntasks=4` = the suite's max rank count; see CECE's `scripts/README.md`),
catchem through an inline `--wrap` with the `CATCHEM_SBATCH_TOPOLOGY`
config. Site policy (`--account`, Gaea's `--clusters`/`--partition`) comes
from the per-platform `*_test_submit_flags` config. Inside the allocation,
ctest runs on the compute node and every MPI test becomes its own
right-sized `srun -n <np>` job step (visible in `sacct`); the
fork-bootstrap env is ignored in this mode. The launcher is baked in at
configure time, so a direct-launch build needs a fresh root, and
`--test-only` reruns must repeat the flag. Two knobs: `--async` submits
without `--wait` and returns immediately (job id printed; results in
`logs/build-<comp>-tests-<jobid>.log`), and `--test-jobs <n>` runs up to n
tests concurrently (`ctest -j`) — safe because every cece test declares
`PROCESSORS`, so ctest packs 4x np1, 2x np2, or 1x np4 into the 4-task
allocation. The job inherits the submitting shell's module environment via
sbatch's default `--export=ALL`.

**Source handling** (same rules for the CATChem and CECE clones): if the
configured clone path doesn't exist, the script clones the repo's
configured URL @ branch with `--recurse-submodules` (CATChem vendors
MUSICA/GOCART; CECE vendors the HELM libraries at `extern/helm`). If it
does exist, the default is to use it exactly as-is — no fetch, checkout,
or pull — and log the commit being built. Pass `--update-source` to
instead sync an existing clone to the configured branch (`git fetch` +
`git checkout <branch>` + `git pull --ff-only` +
`git submodule update --init --recursive`). This is never destructive: a
dirty working tree or a locally diverged branch makes git fail (and the
script stop) rather than discarding local changes. `--update-source` with
`--test-only` is rejected, since nothing is built. Either way, the script
fails fast with instructions if a required submodule (musica, extern/helm)
is uninitialized in the clone it's about to build.

Every example above can be verified locally by appending `--dry-run` — it
prints the full command plan and exits 0 with no side effects (no `module`
command or HPC paths needed).

## Before the first real run on Ursa

1. **Review the config block** at the top of `build-test-kokkos.sh`:
   - `ursa_root_dir` — the single output location. Everything the script
     creates (the CATChem clone, `build-catchem/`, `build-smoke/`, `logs/`)
     lands under it, so the script can be invoked from any directory. It
     must not exist yet when a build run starts (see Re-runs above).
   - `CATCHEM_GIT_URL` / `CATCHEM_BRANCH` — what gets cloned if the clone
     path doesn't exist yet (an existing clone is used as-is unless
     `--update-source` is passed — see Source handling above).
   - `ursa_catchem_clone` — defaults to `<root_dir>/CATChem`; point it at an
     existing checkout (e.g. your sandbox clone) to build that instead.
   - `<platform>_<compiler>_test_env` — space-separated `VAR=VAL` list
     exported around the ctest phase. CECE's multi-rank tests run
     `mpiexec -n 2/4` inside the 1-task srun step, where both MPIs'
     default bootstrap reaches for Slurm and finds no tasks; these force
     local spawning instead (`I_MPI_HYDRA_BOOTSTRAP=fork` for oneapi,
     `OMPI_MCA_plm=isolated` for gcc; gaea-c6/cray-mpich TBD). The
     step's `--cpus-per-task=8` covers the np4 cases.
   - `ursa_test_launcher` — prefix for test commands (default
     `srun --account=epic ...`): MPI is **blocked on Ursa login nodes**, so
     ctest and the smoke executable run on a compute node via srun. Adjust
     the account/qos/time, or set it empty to run tests directly (e.g.
     inside an existing allocation). Builds run on the login node by
     default (network access for FetchContent/MUSICA) — see
     `--srun-build` above for the compute-node compile probe.
   - `ursa_build_launcher` — srun prefix used only with `--srun-build`
     (10-minute walltime; keep `--cpus-per-task` in sync with
     `BUILD_JOBS`).
   - `BUILD_JOBS`, `CMAKE_BUILD_TYPE` — parallelism and build type.
   - `<platform>_module_init` / `<platform>_toolchain_tools` — how the
     module environment is reset (`purge` on Ursa; `reset` on Gaea, where
     purge would strip the Cray craype/PrgEnv machinery) and which
     compiler/tool names get probed with `which` (`mpiicx mpiicpx mpiifx …`
     on Ursa; the Cray wrappers `cc CC ftn …` on Gaea).
   - `<platform>_catchem_git_url` — per-platform clone URL (defaults to the
     shared SSH URL; switch a platform to HTTPS if its login nodes block
     outbound SSH).
2. **Skim the modulefiles** (see next section) — in particular the
   `TODO(ursa)` note about pinning the kokkos/kokkos-kernels versions.
3. Run from a **login node**: CATChem's vendored MUSICA may fetch its own
   dependencies at configure time, and compute nodes are network-restricted.

## The modulefiles: real UFS files, not a curated list

This test asks "will the stack build CATChem **as part of the
ufs-weather-model**?", so the module environment is the actual UFS one —
no special per-library loading:

- `modulefiles/ufs_common.lua` — copy of
  [`ufs_common.lua`](https://github.com/gspetro-NOAA/ufs-weather-model/blob/feature/ss2.x/modulefiles/ufs_common.lua)
  from the ufs-weather-model `feature/ss2.x` branch (spack-stack 2.1.x
  style) with the **version pins removed** — each module loads the env's
  default version. A 2026-08-14 Ursa run showed the custom env carries
  different versions of jasper, libpng, and gftl-shared than the upstream
  pins (worth a mention in the issue report), and exact versions of these
  libraries aren't what this test validates.
- `modulefiles/ufs_ursa.oneapi.lua` — copy of
  [`ufs_ursa.oneapi.lua`](https://github.com/gspetro-NOAA/ufs-weather-model/blob/feature/ss2.x/modulefiles/ufs_ursa.oneapi.lua)
  with three local changes (called out in its header): the `MODULEPATH`
  points at the custom kokkos env instead of the `/contrib` production
  install; `kokkos` + `kokkos-kernels` loads are appended at the end — the
  packages under test, i.e. what the UFS platform file gains once
  spack-stack#2081 lands; and the cmake/nccmp pins are removed (the env
  lacks cmake/3.31.8; same run). The `stack-*` compiler/MPI pins are kept —
  they resolve in the env and select the toolchain under test.
- `modulefiles/ufs_ursa.gnu.lua` — the same recipe applied to
  [`ufs_ursa.gnu.lua`](https://github.com/gspetro-NOAA/ufs-weather-model/blob/feature/ss2.x/modulefiles/ufs_ursa.gnu.lua)
  for `--compiler gcc` (custom env `…/ue-gcc-12.4.0-arborx`, same env-name
  family as upstream's production install). Kept verbatim:
  `stack-gcc/12.4.0` + `stack-openmpi/4.1.6` pins and the `MPI_*`/`FC`
  setenvs — including upstream's choice to set no `CC`/`CXX` (CMake picks
  gcc/g++ from the stack; FindMPI supplies MPI), which is the UFS-gnu
  behavior under test.
- `modulefiles/ufs_gaeac6.oneapi.lua` — same three-change recipe applied to
  [`ufs_gaeac6.oneapi.lua`](https://github.com/gspetro-NOAA/ufs-weather-model/blob/feature/ss2.x/modulefiles/ufs_gaeac6.oneapi.lua)
  (custom env `/gpfs/f6/epic/scratch/Richard.Grubin/spack-stack/envs/ue-oneapi-2025.2.1`,
  same env name as production so the MODULEPATH swap is a pure path
  substitution). Kept verbatim: the `stack-intel-oneapi-compilers/2025.2.1`
  + `stack-cray-mpich/8.1.32` pins, the `darshan-runtime`/`cray-libsci`
  unloads, and the Cray `cc`/`CC`/`ftn` wrapper env vars.

The kokkos/kokkos-kernels loads are unversioned (env default) until the
versions in the custom env are known. To discover and pin them on Ursa:

```bash
env_root=/scratch4/NCEPDEV/nems/Richard.Grubin/spack-stack/envs/ue-oneapi-2025.3.1
module use ${env_root}/modules/Core
module load stack-intel-oneapi-compilers/2025.3.1 stack-intel-oneapi-mpi/2021.17
module avail kokkos kokkos-kernels
```

All version pins are overridable without editing files via the usual UFS
`<name>_ver` environment variables (e.g. `esmf_ver`, `kokkos_ver`; on
Gaea-C6 the compiler override is `stack_intel_ver`, matching upstream).

## First run on Gaea-C6

Gaea specifics to resolve before/at the first `--platform gaea-c6` run:

1. **Slurm topology** (resolved 2026-08-14): C6 `srun` needs explicit
   `--clusters=c6 --partition=batch` — already in `gaeac6_test_launcher`.
2. **Git protocol** (resolved 2026-08-14): outbound SSH to github.com works
   from C6 login nodes, as do MUSICA's configure-time HTTPS fetches; the
   default SSH URL stands. (`gaeac6_catchem_git_url` remains the override
   point if this ever changes.)
3. **Module pins**: discover the env's kokkos/kokkos-kernels versions
   (`module avail kokkos kokkos-kernels` after loading the stack modules)
   and fill the `TODO(gaea-c6)` pins in `ufs_gaeac6.oneapi.lua` if
   desired — unversioned default loads work meanwhile.
4. Confirm the clone tip carries the CATChem platform fixes on
   `feat/rework-benkozi`: `d162a74e` (yaml-cpp/ESMF symbol clash) and the
   GOCART2G fpp workaround for the IntelLLVM 2025.2.1 preprocessor bug
   ([ufs-community#3213](https://github.com/ufs-community/ufs-weather-model/issues/3213)) —
   the run log's "building CATChem at commit" line shows the tip.

## Logs and reporting

Real runs tee everything to timestamped per-component logs under
`<root_dir>/logs/` (e.g. `catchem-20260813-163000.log`), and the CATChem
configure output is additionally captured at
`<root_dir>/build-catchem/configure.log`. Each log starts with the resolved
configuration, `module list`, and `which` output for the toolchain, so a
results summary for issue #2081 can be assembled from:

- the `Using system Kokkos <version>` configure line,
- the smoke test's Kokkos version/backend printout and `SMOKE TEST PASSED`,
- the ctest summary at the end of the catchem log.
