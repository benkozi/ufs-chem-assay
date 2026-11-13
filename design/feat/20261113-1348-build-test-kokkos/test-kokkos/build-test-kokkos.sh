#!/usr/bin/env bash
#
# Build and test CATChem (Kokkos + MUSICA + NUOPC) against the custom
# kokkos/kokkos-kernels spack-stack environment built for
# https://github.com/JCSDA/spack-stack/issues/2081, plus a standalone
# kokkos/kokkos-kernels smoke test.
#
# See README.md for usage and examples. Quick reference:
#
#   ./build-test-kokkos.sh [--platform <ursa|gaea-c6>] [--dry-run]
#                          [--build-only | --test-only]
#                          [catchem|cece|smoke|all]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# =============================================================================
# User-editable configuration
# =============================================================================

# Shared across platforms (the git URLs are per-platform overridable below —
# e.g. switch a platform to HTTPS if its login nodes block outbound SSH)
# feat/rework-benkozi now carries the core-nuopc-safety merge (see
# tasks/fix/20260914-0919); push it before Ursa runs. Switch back to the
# bbakernoaa lines once the PR into feature/core-nuopc-safety merges.
CATCHEM_GIT_URL="git@github.com:benkozi/CATChem.git"
CATCHEM_BRANCH="feature/spack-stack-wm-tweaks"
#CATCHEM_GIT_URL="git@github.com:bbakernoaa/CATChem.git"
#CATCHEM_BRANCH="feature/core-nuopc-safety"
CECE_GIT_URL="git@github.com:benkozi/CECE.git"
CECE_BRANCH="feat/prefer-find-package"
CMAKE_BUILD_TYPE="Debug"
BUILD_JOBS=8

# ---- platform: ursa ---------------------------------------------------------
# root_dir is the single output location: everything the script creates
# (clone, build dirs, logs) lands under it, so the script can be run from
# any directory. catchem_clone defaults inside root_dir; point it elsewhere
# to build an existing checkout instead.
ursa_root_dir="/scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos"
# Compiler axis (--compiler oneapi|gcc): env root, modulefile, and the
# toolchain probe list are one compiler-suite choice and switch together.
#ursa_oneapi_env_root="/scratch4/NCEPDEV/nems/Richard.Grubin/spack-stack/envs/ue-oneapi-2025.3.1"
#ursa_oneapi_env_root="/scratch4/NCEPDEV/nems/Richard.Grubin/spack-stack/envs/ue-oneapi-2025.3.1-cuda-arborx"
ursa_oneapi_env_root="/scratch4/NAGAPE/epic/role-epic/spack-stack/envs/ue-oneapi-2025.3.1"
# GCC: kokkos / kokkos-kernels / arborx -- without CUDA support
ursa_gcc_env_root="/scratch4/NAGAPE/epic/role-epic/spack-stack/envs/ue-gcc-12.4.0"
ursa_catchem_clone="${ursa_root_dir}/CATChem"
ursa_catchem_git_url="${CATCHEM_GIT_URL}"
ursa_cece_clone="${ursa_root_dir}/CECE"
ursa_cece_git_url="${CECE_GIT_URL}"
# Both Ursa envs carry an arborx install; require CECE/AXIS to consume it
# via find_package (do_cece checks the configure log).
ursa_oneapi_expect_arborx="yes"
ursa_gcc_expect_arborx="yes"
ursa_oneapi_modulefile="ufs_ursa.oneapi"
ursa_gcc_modulefile="ufs_ursa.gnu"
# How to reset the module environment before loading the modulefile.
ursa_module_init="purge"
# Compiler/tool names probed with `which` after module load.
ursa_oneapi_toolchain_tools="mpiicx mpiicpx mpiifx cmake ctest"
ursa_gcc_toolchain_tools="mpicc mpic++ mpifort cmake ctest"
# Env exported around the ctest phase (space-separated VAR=VAL list): CECE's
# multi-rank tests run `mpiexec -n 2/4` inside the harness's 1-task srun
# step, where both MPIs' default bootstrap reaches for Slurm and finds no
# tasks to place ranks on. Force local spawning within the step instead
# (the step's --cpus-per-task=8 covers np4). No OMP_PROC_BIND here: CECE's
# ctest registrations inject OMP_PROC_BIND=false themselves (ctest
# ENVIRONMENT_MODIFICATION, applied by tests/CMakeLists.txt).
ursa_oneapi_test_env="I_MPI_HYDRA_BOOTSTRAP=fork"
ursa_gcc_test_env="OMPI_MCA_plm=isolated"
# --direct-launch site policy for the sbatch test submissions. Job topology
# (nodes/ntasks/cpus/time) lives with the test suites: CECE's
# scripts/run-tests-slurm.sbatch, and CATCHEM_SBATCH_TOPOLOGY below for
# catchem's inline submission. These flags may also override topology
# directives (sbatch command line wins) if a platform ever needs to.
ursa_test_submit_flags="--account=epic"
# --direct-launch per-compiler mpiexec preflags, baked into the configure
# as -DMPIEXEC_PREFLAGS (fills TEST_LAUNCHER/add_test launch lines). Seed
# for the Ursa PMI experiment: set to "--mpi=pmi2" if srun-launched Intel
# MPI binaries need it.
ursa_oneapi_mpiexec_preflags=""
ursa_gcc_mpiexec_preflags=""
# --direct-launch MPI launcher per compiler. "srun" bakes srun in as the
# launcher so every MPI test runs as its own job step (the MPI must be able
# to bootstrap through Slurm's PMI — OpenMPI/PMIx does; Intel MPI 2021.17
# does not out of the box: PMI_Init returns 14 / slurmd logs "pmirank
# missing in fullinit command", and would need I_MPI_PMI_LIBRARY plus the
# --mpi=pmi2 preflag above). "mpiexec" leaves CECE's FindMPI default, so the
# MPI's own launcher forks the ranks inside the sbatch allocation, bounded by
# the sbatch --ntasks (this is the configuration that passed 577/577 on
# Ursa oneapi, 2026-09-25).
ursa_oneapi_direct_launch_launcher="mpiexec"
ursa_gcc_direct_launch_launcher="srun"
# Tests must run on a compute node — MPI is blocked on Ursa login nodes.
# Everything after the test command name is prefixed with this launcher
# (word-split); set it empty to run tests directly. Adjust account/qos/time
# as needed.
ursa_test_launcher="srun --account=epic --nodes=1 --ntasks=1 --cpus-per-task=8 --time=00:05:00"
# Used only with --srun-build: runs configure + build on a compute node to
# probe compiling without network access (the real UFS rt.sh constraint).
# Keep --cpus-per-task in sync with BUILD_JOBS.
ursa_build_launcher="srun --account=epic --nodes=1 --ntasks=1 --cpus-per-task=8 --time=00:10:00"
ursa_implemented="yes"

# ---- platform: gaea-c6 ------------------------------------------------------
# Custom env posted in https://github.com/JCSDA/spack-stack/issues/2081
# (oneAPI 2025.2.1 — differs from Ursa's 2025.3.1). Gaea is a Cray machine:
# `module reset` (purge would strip the craype/PrgEnv machinery) and the
# cc/CC/ftn compiler wrappers instead of Intel MPI's mpiicx/mpiicpx/mpiifx.
gaeac6_root_dir="/gpfs/f6/bil-fire3/scratch/Benjamin.Koziol/stmp/test-kokkos"
# oneapi only: upstream ufs-weather-model has no ufs_gaeac6.gnu.lua and no
# GCC C6 env exists — --compiler gcc fails fast on this platform.
gaeac6_oneapi_env_root="/gpfs/f6/epic/scratch/Richard.Grubin/spack-stack/envs/ue-oneapi-2025.2.1"
gaeac6_catchem_clone="${gaeac6_root_dir}/CATChem"
# SSH to github.com works from C6 login nodes (confirmed 2026-08-14); these
# per-platform URLs remain the override point if that ever changes.
gaeac6_catchem_git_url="${CATCHEM_GIT_URL}"
gaeac6_cece_clone="${gaeac6_root_dir}/CECE"
gaeac6_cece_git_url="${CECE_GIT_URL}"
# The C6 env carries no arborx today; AXIS's fallback stays tolerated.
gaeac6_oneapi_expect_arborx="no"
gaeac6_oneapi_modulefile="ufs_gaeac6.oneapi"
gaeac6_module_init="reset"
gaeac6_oneapi_toolchain_tools="cc CC ftn cmake ctest"
# TODO(gaea-c6): cray-mpich's local-spawn story differs from Intel MPI /
# OpenMPI — determine the right bootstrap env when multi-rank cece tests
# first run there. Empty = no env prefix. (--direct-launch moots this:
# srun is cray-mpich's native launcher.)
gaeac6_oneapi_test_env=""
# --direct-launch site policy / preflags (see the ursa comments).
gaeac6_test_submit_flags="--clusters=c6 --partition=batch --account=bil-fire3"
gaeac6_oneapi_mpiexec_preflags=""
# cray-mpich launches through srun natively.
gaeac6_oneapi_direct_launch_launcher="srun"
# C6 srun requires an explicit cluster and partition (confirmed 2026-08-14:
# without them, "Unable to allocate resources: No partition specified").
gaeac6_test_launcher="srun --clusters=c6 --partition=batch --account=bil-fire3 --nodes=1 --ntasks=1 --cpus-per-task=8 --time=00:05:00"
# Used only with --srun-build (see the ursa comment). Keep --cpus-per-task
# in sync with BUILD_JOBS.
gaeac6_build_launcher="srun --clusters=c6 --partition=batch --account=bil-fire3 --nodes=1 --ntasks=1 --cpus-per-task=8 --time=00:10:00"
gaeac6_implemented="yes"

# CATChem configure flags (per the task: Kokkos + MUSICA + NUOPC all ON)
CATCHEM_CMAKE_FLAGS=(
  -DCATCHEM_ENABLE_KOKKOS=ON
  -DCATCHEM_BUILD_MUSICA=ON
  -DCATCHEM_BUILD_NUOPC=ON
)

# --direct-launch job topology for catchem's inline sbatch --wrap (catchem
# has no repo-owned batch script; cece's topology lives in its
# scripts/run-tests-slurm.sbatch). All catchem tests are single-rank; the
# 8 CPUs cover its OpenMP Kokkos tests.
CATCHEM_SBATCH_TOPOLOGY="--job-name=catchem-tests --nodes=1 --ntasks=1 --cpus-per-task=8 --time=00:05:00"

# CECE configure flags (Kokkos + ESMF). The env-kokkos and env-esmf
# requirements are enforced by the configure-log checks, not flags —
# CECE_FETCH_DEPS=OFF would also demand system yaml-cpp/GTest/RapidCheck,
# which the env doesn't provide.
CECE_CMAKE_FLAGS=(
  -DENABLE_ESMF=ON
)
# Intel LLVM-specific flags for CECE (-fp-model=precise prevents precision-related
# test failures in CeceAxisDecode; unrecognized by GCC).
CECE_INTELLLVM_CMAKE_FLAGS=(
  -DCMAKE_CXX_FLAGS=-fp-model=precise
)

# =============================================================================
# End of user-editable configuration
# =============================================================================

usage() {
  cat <<EOF
Usage: ${0##*/} [options] [component]

Build and test CATChem and/or the kokkos smoke test against the spack-stack
environment from JCSDA/spack-stack#2081.

Components:
  catchem | cece | smoke | all   (default: all)

Options:
  --platform <name>  Target platform: ursa (default) or gaea-c6.
  --compiler <name>  Compiler suite: oneapi (default; alias: intelllvm) or
                     gcc. Selects the spack-stack env, modulefile, and
                     toolchain probes together. gcc is ursa-only (no
                     upstream gaea-c6 gnu modulefile or env).
  --dry-run          Print every command without executing anything. Safe to
                     run anywhere (including macOS).
  --build-only       Configure and build only; skip tests.
  --test-only        Run tests against an existing build dir; skip build.
  --reuse-build      Reuse an existing root/build dir if present (default: yes).
                     Reconfigures and builds incrementally.
  --no-reuse-build   Require root directory to not exist (fresh-root safety check).
  --root-dir <dir>   Override the platform root_dir output directory.
  --update-source    Sync the CATChem clone to the configured branch before
                     building (clone if missing; otherwise fetch + checkout +
                     fast-forward pull). Without this flag an existing clone
                     is used as-is. Incompatible with --test-only.
  --srun-build       Run the compile (configure + build) under the platform's
                     build launcher on a compute node (default: yes).
                     Build and tests stay separate srun commands; source
                     clone/fetch stays on the login node. Incompatible with
                     --test-only.
  --no-srun-build    Run the compile directly on the login node.
  --test-filter <re> Run only ctest tests matching the regex (ctest -R),
                     with the usual module/srun setup handled by the script.
                     Applies to the catchem/cece ctest phases; the smoke
                     executable has no ctest and ignores it. Default: the
                     full suite. Incompatible with --build-only.
  --direct-launch    Slurm-native rank placement: configure catchem/cece
                     with -DMPIEXEC_EXECUTABLE=srun so every MPI test runs
                     as its own right-sized job step, and submit the ctest
                     phases as sbatch jobs (cece via its repo-owned
                     scripts/run-tests-slurm.sbatch; catchem inline)
                     instead of wrapping ctest in a 1-task srun step. Per
                     compiler, <platform>_<compiler>_direct_launch_launcher
                     may select "mpiexec" instead (cece only): the MPI's own
                     launcher then forks ranks inside the allocation. The
                     launcher is baked in at configure time, so builds and
                     --test-only reruns must agree on this flag. The smoke
                     phase keeps the srun step launcher.
  --async            With --direct-launch: submit the sbatch test jobs
                     without --wait and return immediately, printing job
                     ids; results land in the jobs' log files (check with
                     sacct/tail). The harness cannot gate on pass/fail in
                     this mode.
  --test-jobs <n>    With --direct-launch: run up to n tests concurrently
                     (ctest -j n) inside the allocation. Safe rank
                     packing: every cece test declares PROCESSORS, so
                     ctest's budget counts ranks. Default: serial.
  -h, --help         Show this help.

--build-only and --test-only are mutually exclusive. Default is build + test.

The script may be run from any directory: inputs (modulefiles/, smoke/) are
resolved relative to the script's own location, and all output — the CATChem
clone, build dirs, and logs — lands under the per-platform root_dir
configured at the top of the script.

Safety model: build runs require root_dir to NOT exist yet, so every run is
clean across the board. This script never removes anything — to re-run,
remove or rename the root dir yourself (or point root_dir elsewhere).
--test-only is the exception: it reuses the existing root/build dir.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

note() {
  echo "# $*"
}

# Run a test command, prefixed with the platform's test launcher (e.g. srun)
# when one is configured — MPI is blocked on login nodes, so tests need a
# compute node. The launcher string is word-split into arguments.
run_tests() {
  if [[ -n "${TEST_LAUNCHER}" ]]; then
    local -a launcher
    read -r -a launcher <<< "${TEST_LAUNCHER}"
    run "${launcher[@]}" "$@"
  else
    run "$@"
  fi
}

# Run a component's ctest suite, honoring the optional --test-filter regex
# and the per-compiler test env. Default mode wraps ctest in the platform's
# step launcher (srun --ntasks=1; mpiexec forks ranks inside the step, which
# is what TEST_ENV's bootstrap values are for). --direct-launch submits an
# sbatch job instead: ctest runs on the compute node and every MPI test
# launches through its own right-sized `srun -n <np>` job step (requires a
# build configured with -DMPIEXEC_EXECUTABLE=srun — the harness does this
# unless the compiler's direct_launch_launcher is "mpiexec", in which case
# the MPI's own launcher forks the ranks inside the allocation).
# The optional second argument is a repo-owned sbatch script carrying the
# job topology (cece: scripts/run-tests-slurm.sbatch); without one the
# submission is composed inline with --wrap (catchem). The submitting
# shell's environment (modules) reaches the job via sbatch's default
# --export=ALL; the fork-bootstrap TEST_ENV is intentionally not applied
# in direct-launch mode (see below).
run_ctest() {
  local build_dir="$1" sbatch_script="${2:-}"

  if [[ "${DIRECT_LAUNCH}" != "yes" ]]; then
    local -a ctest_cmd=(ctest --test-dir "${build_dir}" --output-on-failure)
    if [[ -n "${TEST_FILTER}" ]]; then
      ctest_cmd+=(-R "${TEST_FILTER}")
    fi
    if [[ -n "${TEST_ENV}" ]]; then
      local -a env_prefix
      read -r -a env_prefix <<< "${TEST_ENV}"
      run_tests env "${env_prefix[@]}" "${ctest_cmd[@]}"
    else
      run_tests "${ctest_cmd[@]}"
    fi
    return
  fi

  # Extra ctest args pass through to the batch script / inline wrap
  # (--output-on-failure is supplied there).
  local -a extra_args=()
  if [[ -n "${TEST_FILTER}" ]]; then
    extra_args+=(-R "${TEST_FILTER}")
  fi
  if [[ -n "${TEST_JOBS}" ]]; then
    extra_args+=(-j "${TEST_JOBS}")
  fi

  local -a submit=(sbatch)
  if [[ "${ASYNC}" != "yes" ]]; then
    submit+=(--wait)
  fi
  if [[ -n "${TEST_SUBMIT_FLAGS}" ]]; then
    local -a submit_flags
    read -r -a submit_flags <<< "${TEST_SUBMIT_FLAGS}"
    submit+=("${submit_flags[@]}")
  fi
  local job_log="${LOG_DIR}/$(basename "${build_dir}")-tests-%j.log"
  submit+=(--output "${job_log}")

  if [[ -n "${sbatch_script}" ]]; then
    submit+=("${sbatch_script}" "${build_dir}" ${extra_args[@]+"${extra_args[@]}"})
  else
    local wrap_cmd arg
    wrap_cmd="ctest --test-dir $(printf '%q' "${build_dir}") --output-on-failure"
    for arg in ${extra_args[@]+"${extra_args[@]}"}; do
      wrap_cmd+=" $(printf '%q' "${arg}")"
    done
    local -a topology
    read -r -a topology <<< "${CATCHEM_SBATCH_TOPOLOGY}"
    submit+=("${topology[@]}" --wrap "${wrap_cmd}")
  fi

  # TEST_ENV is deliberately NOT applied here: its values are the
  # fork-bootstrap workaround for mpiexec-inside-a-step, which srun
  # placement replaces. Direct-launch env, if ever needed, belongs in the
  # platform's mpiexec preflags or submit flags (sbatch --export=...).
  run "${submit[@]}"

  if [[ "${ASYNC}" == "yes" ]]; then
    note "async submission — results land in ${job_log/\%j/<jobid>}" \
      "(job id printed above; watch with squeue/sacct or tail the log)"
  fi
}

# Run a compile-phase command (cmake configure/build), prefixed with the
# platform's build launcher when --srun-build is active — a separate srun
# command (and allocation) from the tests, never one spanning both.
run_build() {
  if [[ "${SRUN_BUILD}" == "yes" ]]; then
    local -a launcher
    read -r -a launcher <<< "${BUILD_LAUNCHER}"
    run "${launcher[@]}" "$@"
  else
    run "$@"
  fi
}

# Print (dry-run) or echo-and-execute a command.
run() {
  if [[ "${DRY_RUN}" == "yes" ]]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
  else
    echo "+ $*"
    "$@"
  fi
}

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------

PLATFORM="ursa"
COMPILER="oneapi"
DRY_RUN="no"
BUILD_ONLY="no"
TEST_ONLY="no"
REUSE_BUILD="yes"
ROOT_DIR_OVERRIDE=""
UPDATE_SOURCE="no"
SRUN_BUILD="yes"
TEST_FILTER=""
DIRECT_LAUNCH="no"
DIRECT_LAUNCH_LAUNCHER="srun"
ASYNC="no"
TEST_JOBS=""
COMPONENT="all"

while (($# > 0)); do
  case "$1" in
    --platform)
      [[ $# -ge 2 ]] || die "--platform requires an argument"
      PLATFORM="$2"
      shift
      ;;
    --platform=*)
      PLATFORM="${1#*=}"
      ;;
    --compiler)
      [[ $# -ge 2 ]] || die "--compiler requires an argument"
      COMPILER="$2"
      shift
      ;;
    --compiler=*)
      COMPILER="${1#*=}"
      ;;
    --dry-run)
      DRY_RUN="yes"
      ;;
    --build-only)
      BUILD_ONLY="yes"
      ;;
    --test-only)
      TEST_ONLY="yes"
      ;;
    --reuse-build)
      REUSE_BUILD="yes"
      ;;
    --no-reuse-build)
      REUSE_BUILD="no"
      ;;
    --root-dir)
      [[ $# -ge 2 ]] || die "--root-dir requires an argument"
      ROOT_DIR_OVERRIDE="$2"
      shift
      ;;
    --root-dir=*)
      ROOT_DIR_OVERRIDE="${1#*=}"
      ;;
    --update-source)
      UPDATE_SOURCE="yes"
      ;;
    --srun-build)
      SRUN_BUILD="yes"
      ;;
    --no-srun-build)
      SRUN_BUILD="no"
      ;;
    --test-filter)
      [[ $# -ge 2 ]] || die "--test-filter requires an argument"
      TEST_FILTER="$2"
      shift
      ;;
    --test-filter=*)
      TEST_FILTER="${1#*=}"
      ;;
    --direct-launch)
      DIRECT_LAUNCH="yes"
      ;;
    --async)
      ASYNC="yes"
      ;;
    --test-jobs)
      [[ $# -ge 2 ]] || die "--test-jobs requires an argument"
      TEST_JOBS="$2"
      shift
      ;;
    --test-jobs=*)
      TEST_JOBS="${1#*=}"
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    catchem | cece | smoke | all)
      COMPONENT="$1"
      ;;
    *)
      usage >&2
      die "unrecognized argument: $1"
      ;;
  esac
  shift
done

if [[ "${BUILD_ONLY}" == "yes" && "${TEST_ONLY}" == "yes" ]]; then
  usage >&2
  die "--build-only and --test-only are mutually exclusive"
fi

if [[ "${REUSE_BUILD}" == "yes" && "${TEST_ONLY}" == "yes" ]]; then
  usage >&2
  die "--reuse-build is incompatible with --test-only (use --test-only alone to skip building)"
fi

if [[ "${UPDATE_SOURCE}" == "yes" && "${TEST_ONLY}" == "yes" ]]; then
  usage >&2
  die "--update-source is incompatible with --test-only (nothing is built)"
fi

if [[ "${SRUN_BUILD}" == "yes" && "${TEST_ONLY}" == "yes" ]]; then
  usage >&2
  die "--srun-build is incompatible with --test-only (nothing is compiled)"
fi

if [[ -n "${TEST_FILTER}" && "${BUILD_ONLY}" == "yes" ]]; then
  usage >&2
  die "--test-filter is incompatible with --build-only (no tests run)"
fi

if [[ "${ASYNC}" == "yes" && "${DIRECT_LAUNCH}" != "yes" ]]; then
  usage >&2
  die "--async requires --direct-launch (only sbatch submissions can detach)"
fi

if [[ -n "${TEST_JOBS}" ]]; then
  if [[ "${DIRECT_LAUNCH}" != "yes" ]]; then
    usage >&2
    die "--test-jobs requires --direct-launch (packing needs per-test srun steps)"
  fi
  if [[ ! "${TEST_JOBS}" =~ ^[1-9][0-9]*$ ]]; then
    die "--test-jobs must be a positive integer (got '${TEST_JOBS}')"
  fi
fi

if [[ "${ASYNC}" == "yes" && "${BUILD_ONLY}" == "yes" ]]; then
  usage >&2
  die "--async is incompatible with --build-only (no tests are submitted)"
fi

# -----------------------------------------------------------------------------
# Platform/compiler resolution
# -----------------------------------------------------------------------------

case "${COMPILER}" in
  oneapi | intelllvm | gcc) ;;
  *)
    usage >&2
    die "unknown compiler '${COMPILER}' (recognized: oneapi, intelllvm, gcc)"
    ;;
esac

case "${PLATFORM}" in
  ursa)
    ROOT_DIR="${ROOT_DIR_OVERRIDE:-${ursa_root_dir}}"
    if [[ -n "${ROOT_DIR_OVERRIDE:-}" ]]; then
      CATCHEM_CLONE="${ROOT_DIR}/CATChem"
      CECE_CLONE="${ROOT_DIR}/CECE"
    else
      CATCHEM_CLONE="${ursa_catchem_clone}"
      CECE_CLONE="${ursa_cece_clone}"
    fi
    CATCHEM_GIT_URL="${ursa_catchem_git_url}"
    CECE_GIT_URL="${ursa_cece_git_url}"
    MODULE_INIT="${ursa_module_init}"
    TEST_LAUNCHER="${ursa_test_launcher}"
    BUILD_LAUNCHER="${ursa_build_launcher}"
    TEST_SUBMIT_FLAGS="${ursa_test_submit_flags}"
    IMPLEMENTED="${ursa_implemented}"
    case "${COMPILER}" in
      oneapi | intelllvm)
        ENV_ROOT="${ursa_oneapi_env_root}"
        MODULEFILE="${ursa_oneapi_modulefile}"
        TOOLCHAIN_TOOLS="${ursa_oneapi_toolchain_tools}"
        EXPECT_ARBORX="${ursa_oneapi_expect_arborx}"
        TEST_ENV="${ursa_oneapi_test_env}"
        MPIEXEC_PREFLAGS_CFG="${ursa_oneapi_mpiexec_preflags}"
        DIRECT_LAUNCH_LAUNCHER="${ursa_oneapi_direct_launch_launcher}"
        ;;
      gcc)
        ENV_ROOT="${ursa_gcc_env_root}"
        MODULEFILE="${ursa_gcc_modulefile}"
        TOOLCHAIN_TOOLS="${ursa_gcc_toolchain_tools}"
        EXPECT_ARBORX="${ursa_gcc_expect_arborx}"
        TEST_ENV="${ursa_gcc_test_env}"
        MPIEXEC_PREFLAGS_CFG="${ursa_gcc_mpiexec_preflags}"
        DIRECT_LAUNCH_LAUNCHER="${ursa_gcc_direct_launch_launcher}"
        ;;
    esac
    ;;
  gaea-c6)
    if [[ "${COMPILER}" != "oneapi" && "${COMPILER}" != "intelllvm" ]]; then
      die "compiler '${COMPILER}' is not supported on platform gaea-c6 — \
upstream ufs-weather-model has no ufs_gaeac6 modulefile for it and no \
matching C6 env exists (oneapi/intelllvm only)"
    fi
    ROOT_DIR="${ROOT_DIR_OVERRIDE:-${gaeac6_root_dir}}"
    if [[ -n "${ROOT_DIR_OVERRIDE:-}" ]]; then
      CATCHEM_CLONE="${ROOT_DIR}/CATChem"
      CECE_CLONE="${ROOT_DIR}/CECE"
    else
      CATCHEM_CLONE="${gaeac6_catchem_clone}"
      CECE_CLONE="${gaeac6_cece_clone}"
    fi
    ENV_ROOT="${gaeac6_oneapi_env_root}"
    CATCHEM_GIT_URL="${gaeac6_catchem_git_url}"
    CECE_GIT_URL="${gaeac6_cece_git_url}"
    EXPECT_ARBORX="${gaeac6_oneapi_expect_arborx}"
    TEST_ENV="${gaeac6_oneapi_test_env}"
    MPIEXEC_PREFLAGS_CFG="${gaeac6_oneapi_mpiexec_preflags}"
    DIRECT_LAUNCH_LAUNCHER="${gaeac6_oneapi_direct_launch_launcher}"
    MODULEFILE="${gaeac6_oneapi_modulefile}"
    MODULE_INIT="${gaeac6_module_init}"
    TOOLCHAIN_TOOLS="${gaeac6_oneapi_toolchain_tools}"
    TEST_LAUNCHER="${gaeac6_test_launcher}"
    BUILD_LAUNCHER="${gaeac6_build_launcher}"
    TEST_SUBMIT_FLAGS="${gaeac6_test_submit_flags}"
    IMPLEMENTED="${gaeac6_implemented}"
    ;;
  *)
    usage >&2
    die "unknown platform '${PLATFORM}' (recognized: ursa, gaea-c6)"
    ;;
esac

if [[ "${IMPLEMENTED}" != "yes" ]]; then
  die "platform '${PLATFORM}' is not yet implemented — fill in its config \
block at the top of ${0##*/} and add modulefiles/${MODULEFILE}.lua, then \
set its _implemented variable to \"yes\""
fi

if [[ "${SRUN_BUILD}" == "yes" && -z "${BUILD_LAUNCHER}" ]]; then
  die "--srun-build requires a build launcher, but platform \
'${PLATFORM}' has an empty one configured"
fi

LOG_DIR="${ROOT_DIR}/logs"

echo "=============================================================="
echo "Resolved configuration"
echo "  platform:        ${PLATFORM}"
echo "  compiler:        ${COMPILER}"
echo "  spack-stack env: ${ENV_ROOT} (drives MODULEPATH via TEST_KOKKOS_ENV_ROOT)"
echo "  root dir:        ${ROOT_DIR} (all output lands here)"
echo "  catchem clone:   ${CATCHEM_CLONE} (branch ${CATCHEM_BRANCH})"
echo "  cece clone:      ${CECE_CLONE} (branch ${CECE_BRANCH})"
echo "  modulefile:      ${MODULEFILE} (after module ${MODULE_INIT})"
echo "  test launcher:   ${TEST_LAUNCHER:-(none — tests run directly)}"
echo "  component(s):    ${COMPONENT}"
echo "  build-only:      ${BUILD_ONLY}   test-only: ${TEST_ONLY}   reuse-build: ${REUSE_BUILD}"
echo "  update-source:   ${UPDATE_SOURCE}"
echo "  srun-build:      ${SRUN_BUILD}$( [[ "${SRUN_BUILD}" == "yes" ]] && echo "   (build launcher: ${BUILD_LAUNCHER})" )"
echo "  test filter:     ${TEST_FILTER:-(none — full suite)}"
echo "  test env:        $( [[ "${DIRECT_LAUNCH}" == "yes" ]] && echo "(ignored — fork-bootstrap env is moot under --direct-launch)" || echo "${TEST_ENV:-(none)}" )"
echo "  direct-launch:   ${DIRECT_LAUNCH}$( [[ "${DIRECT_LAUNCH}" == "yes" ]] && echo "   (submit flags: ${TEST_SUBMIT_FLAGS:-none}; launcher: ${DIRECT_LAUNCH_LAUNCHER}; mpiexec preflags: ${MPIEXEC_PREFLAGS_CFG:-none}; async: ${ASYNC}; test jobs: ${TEST_JOBS:-serial})" )"
echo "  dry-run:         ${DRY_RUN}"
echo "=============================================================="

# Fresh-root safety check: when --no-reuse-build is specified, require ROOT_DIR to
# not exist yet so the run starts completely clean. By default (--reuse-build),
# an existing ROOT_DIR is reused; if it does not exist, it is created.
if [[ "${TEST_ONLY}" != "yes" && "${REUSE_BUILD}" != "yes" ]]; then
  if [[ "${DRY_RUN}" == "yes" ]]; then
    note "precondition: ${ROOT_DIR} must not exist (--no-reuse-build specified)"
  elif [[ -e "${ROOT_DIR}" ]]; then
    die "root dir ${ROOT_DIR} already exists. --no-reuse-build requires a fresh \
root dir — remove or rename it manually (or point the ${PLATFORM} root_dir at \
a new location) and re-run."
  fi
fi

# -----------------------------------------------------------------------------
# Shared steps
# -----------------------------------------------------------------------------

load_modules() {
  # purge on Ursa; reset on Gaea (purge strips Cray's craype/PrgEnv setup).
  run module "${MODULE_INIT}"
  run module use "${SCRIPT_DIR}/modulefiles"
  # The platform modulefile derives its MODULEPATH from this variable, so
  # the env_root config actually selects the spack-stack env (works through
  # `run` because run executes builtins in the current shell).
  run export TEST_KOKKOS_ENV_ROOT="${ENV_ROOT}"
  run module load "${MODULEFILE}"
  run module list
}

print_toolchain() {
  note "resolved toolchain (env root: ${ENV_ROOT})"
  local tool
  # Intentionally word-split: per-platform space-separated tool list.
  for tool in ${TOOLCHAIN_TOOLS}; do
    run which "${tool}"
  done
}

# Sync an existing clone to its configured branch. Fast-forward only: a
# dirty tree or diverged branch fails loudly instead of discarding state.
# args: clone dir, branch
update_source() {
  local clone="$1" branch="$2"
  run git -C "${clone}" fetch origin
  run git -C "${clone}" checkout "${branch}"
  run git -C "${clone}" pull --ff-only origin "${branch}"
  run git -C "${clone}" submodule update --init --recursive
}

# args: display name, clone dir, branch, git URL, submodule guard path
# (relative to the clone; a path that only exists once submodules are
# initialized), guard submodule name for the error message
prepare_source() {
  local name="$1" clone="$2" branch="$3" url="$4" guard_rel="$5" guard_name="$6"
  if [[ "${DRY_RUN}" == "yes" ]]; then
    note "if ${clone} does not exist, it is cloned:"
    run git clone --recurse-submodules --branch "${branch}" \
      "${url}" "${clone}"
    if [[ "${UPDATE_SOURCE}" == "yes" ]]; then
      note "otherwise (--update-source) it is synced to ${branch}:"
      update_source "${clone}" "${branch}"
    else
      note "otherwise the existing clone is used as-is (no git mutations;" \
        "pass --update-source to sync it to ${branch})"
    fi
    return
  fi
  if [[ ! -d "${clone}/.git" ]]; then
    run git clone --recurse-submodules --branch "${branch}" \
      "${url}" "${clone}"
  elif [[ "${UPDATE_SOURCE}" == "yes" ]]; then
    update_source "${clone}" "${branch}"
  else
    note "using existing clone at ${clone} as-is (pass" \
      "--update-source to sync it to ${branch})"
  fi
  # An uninitialized submodule shows up much later as a confusing configure
  # error — fail fast instead.
  if [[ ! -e "${clone}/${guard_rel}" ]]; then
    die "the ${guard_name} submodule in ${clone} is not initialized — run \
'git -C ${clone} submodule update --init --recursive' (or re-run \
with --update-source)"
  fi
  note "building ${name} at commit:"
  git -C "${clone}" log -1 --oneline
}

# -----------------------------------------------------------------------------
# Components
# -----------------------------------------------------------------------------

do_catchem() {
  local build_dir="${ROOT_DIR}/build-catchem"
  local configure_log="${build_dir}/configure.log"

  load_modules
  print_toolchain

  if [[ "${TEST_ONLY}" != "yes" ]]; then
    # CATChem vendors MUSICA (and GOCART) as git submodules.
    prepare_source "CATChem" "${CATCHEM_CLONE}" "${CATCHEM_BRANCH}" \
      "${CATCHEM_GIT_URL}" "src/external/musica/CMakeLists.txt" "musica"

    # The build dir must exist before tee opens configure.log inside it.
    run mkdir -p "${build_dir}"

    # Under --srun-build, MUSICA is disabled: it FetchContents its own
    # dependencies (micm, tuv-x, MechanismConfiguration) at configure time,
    # which cannot work on the network-restricted compute node. Everything
    # else in catchem is offline-safe (env kokkos, in-tree yaml-cpp,
    # submodules cloned on login), so — unlike cece — the test phase stays
    # enabled; the musica-gated tests simply don't exist in that build.
    # MUSICA coverage remains on login-node (default) builds.
    local -a catchem_flags=("${CATCHEM_CMAKE_FLAGS[@]}")
    if [[ "${SRUN_BUILD}" == "yes" ]]; then
      catchem_flags=("${catchem_flags[@]/-DCATCHEM_BUILD_MUSICA=ON/-DCATCHEM_BUILD_MUSICA=OFF}")
    fi
    # --direct-launch bakes srun in as the MPI test launcher at configure
    # time (FindMPI only defaults these cache variables; explicit -D wins).
    if [[ "${DIRECT_LAUNCH}" == "yes" ]]; then
      catchem_flags+=(
        -DMPIEXEC_EXECUTABLE="$(command -v srun || echo srun)"
        -DMPIEXEC_NUMPROC_FLAG=-n
      )
      if [[ -n "${MPIEXEC_PREFLAGS_CFG}" ]]; then
        catchem_flags+=(-DMPIEXEC_PREFLAGS="${MPIEXEC_PREFLAGS_CFG}")
      fi
    fi

    # Configure. Output is captured so the system-Kokkos check below can
    # verify the spack-stack Kokkos was consumed (not a FetchContent
    # fallback). Built as an array so the --srun-build launcher prefix and
    # the tee'd capture compose.
    local -a configure_cmd=(
      cmake -S "${CATCHEM_CLONE}" -B "${build_dir}"
      -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" "${catchem_flags[@]}"
    )
    if [[ "${SRUN_BUILD}" == "yes" ]]; then
      local -a build_launcher
      read -r -a build_launcher <<< "${BUILD_LAUNCHER}"
      configure_cmd=("${build_launcher[@]}" "${configure_cmd[@]}")
    fi
    if [[ "${DRY_RUN}" == "yes" ]]; then
      run "${configure_cmd[@]}"
      note "then verify 'Using system Kokkos' appears in the configure output"
    else
      echo "+ ${configure_cmd[*]}"
      "${configure_cmd[@]}" 2>&1 | tee "${configure_log}"
      if ! grep -q "Using system Kokkos" "${configure_log}"; then
        die "CATChem configure did NOT find the spack-stack Kokkos (no \
'Using system Kokkos' in ${configure_log}) — it fell back to FetchContent. \
Check the kokkos module in ${MODULEFILE}."
      fi
      grep "Using system Kokkos" "${configure_log}"
    fi

    run_build cmake --build "${build_dir}" --parallel "${BUILD_JOBS}"
  fi

  if [[ "${BUILD_ONLY}" != "yes" ]]; then
    if [[ "${DRY_RUN}" != "yes" && ! -d "${build_dir}" ]]; then
      die "no CATChem build dir at ${build_dir} — run without --test-only \
first"
    fi
    run_ctest "${build_dir}"
  fi
}

do_cece() {
  local build_dir="${ROOT_DIR}/build-cece"
  local configure_log="${build_dir}/configure.log"

  load_modules
  print_toolchain

  if [[ "${TEST_ONLY}" != "yes" ]]; then
    # CECE vendors the HELM micro-libraries as a git submodule (extern/helm).
    prepare_source "CECE" "${CECE_CLONE}" "${CECE_BRANCH}" \
      "${CECE_GIT_URL}" "extern/helm/libs" "extern/helm"

    # The build dir must exist before tee opens configure.log inside it.
    run mkdir -p "${build_dir}"

    # Configure. Output is captured so the check below can verify the
    # spack-stack Kokkos was consumed: CECE logs its provider as
    # "CECE dependency Kokkos: system <ver> (<dir>)" vs
    # "CECE dependency Kokkos: FetchContent (...)".
    # Under --srun-build, test building is disabled: GTest/RapidCheck are
    # FetchContent'd at configure time, which cannot work on the
    # network-restricted compute node. With them off (and yaml-cpp vendored,
    # arborx from the env), the cece configure is fully offline-capable.
    local -a cece_flags=(${CECE_CMAKE_FLAGS[@]+"${CECE_CMAKE_FLAGS[@]}"})
    if [[ "${COMPILER}" == "oneapi" || "${COMPILER}" == "intelllvm" ]]; then
      cece_flags+=(${CECE_INTELLLVM_CMAKE_FLAGS[@]+"${CECE_INTELLLVM_CMAKE_FLAGS[@]}"})
    fi
    if [[ "${SRUN_BUILD}" == "yes" ]]; then
      cece_flags+=(-DCECE_BUILD_TESTING=OFF)
    else
      cece_flags+=(-DCECE_BUILD_TESTING=ON)
    fi
    # --direct-launch bakes srun in as the MPI test launcher at configure
    # time (FindMPI only defaults these cache variables; explicit -D wins).
    # With DIRECT_LAUNCH_LAUNCHER=mpiexec the MPI's own launcher (CECE's
    # FindMPI default) forks ranks inside the sbatch allocation instead.
    if [[ "${DIRECT_LAUNCH}" == "yes" && "${DIRECT_LAUNCH_LAUNCHER}" != "mpiexec" ]]; then
      cece_flags+=(
        -DMPIEXEC_EXECUTABLE="$(command -v srun || echo srun)"
        -DMPIEXEC_NUMPROC_FLAG=-n
      )
      if [[ -n "${MPIEXEC_PREFLAGS_CFG}" ]]; then
        cece_flags+=(-DMPIEXEC_PREFLAGS="${MPIEXEC_PREFLAGS_CFG}")
      fi
    fi
    local -a configure_cmd=(
      cmake -S "${CECE_CLONE}" -B "${build_dir}"
      -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}"
      ${cece_flags[@]+"${cece_flags[@]}"}
    )
    if [[ "${SRUN_BUILD}" == "yes" ]]; then
      local -a build_launcher
      read -r -a build_launcher <<< "${BUILD_LAUNCHER}"
      configure_cmd=("${build_launcher[@]}" "${configure_cmd[@]}")
    fi
    if [[ "${DRY_RUN}" == "yes" ]]; then
      run "${configure_cmd[@]}"
      note "then verify 'CECE dependency Kokkos: system' appears in the" \
        "configure output"
      if [[ "${EXPECT_ARBORX}" == "yes" ]]; then
        note "and verify 'AXIS: ArborX ... found via find_package' appears" \
          "(this platform's env is expected to provide arborx)"
      fi
      if grep -q -- "-DENABLE_ESMF=ON" <<< "${cece_flags[*]}"; then
        note "and verify 'CECE dependency ESMF: system' appears in the" \
          "configure output"
      fi
    else
      echo "+ ${configure_cmd[*]}"
      "${configure_cmd[@]}" 2>&1 | tee "${configure_log}"
      if ! grep -q "CECE dependency Kokkos: system" "${configure_log}"; then
        die "CECE configure did NOT find the spack-stack Kokkos (no \
'CECE dependency Kokkos: system' in ${configure_log} — it reports \
'$(grep -m1 "CECE dependency Kokkos:" "${configure_log}" || echo "no Kokkos \
dependency line at all")'). Check the kokkos module in ${MODULEFILE}."
      fi
      grep "CECE dependency Kokkos: system" "${configure_log}"
      # AXIS logs ArborX provenance; when the platform env provides arborx,
      # anything but the find_package hit (vendored copy or a quiet
      # login-node FetchContent) is a failure.
      if [[ "${EXPECT_ARBORX}" == "yes" ]]; then
        if ! grep -q "AXIS: ArborX .* found via find_package" \
          "${configure_log}"; then
          die "CECE/AXIS did NOT consume the env's ArborX via find_package \
(expected on ${PLATFORM}). Check the arborx module in ${MODULEFILE} and \
that ENV_ROOT (${ENV_ROOT}) is the arborx-carrying env."
        fi
        grep "AXIS: ArborX .* found via find_package" "${configure_log}"
      fi
      if grep -q -- "-DENABLE_ESMF=ON" <<< "${cece_flags[*]}"; then
        if ! grep -q "CECE dependency ESMF: system" "${configure_log}"; then
          die "CECE configure did NOT find the spack-stack ESMF (no \
'CECE dependency ESMF: system' in ${configure_log} — it reports \
'$(grep -m1 "CECE dependency ESMF:" "${configure_log}" || echo "no ESMF \
dependency line at all")'). Check the esmf module in ${MODULEFILE}."
        fi
        grep "CECE dependency ESMF: system" "${configure_log}"
      fi
    fi

    run_build cmake --build "${build_dir}" --parallel "${BUILD_JOBS}"
  fi

  if [[ "${BUILD_ONLY}" != "yes" ]]; then
    if [[ "${SRUN_BUILD}" == "yes" ]]; then
      note "cece tests were not built (--srun-build sets \
CECE_BUILD_TESTING=OFF — the GTest/RapidCheck fetches need network); run a \
login-node 'cece' pass for the test suite"
    else
      if [[ "${DRY_RUN}" != "yes" && ! -d "${build_dir}" ]]; then
        die "no CECE build dir at ${build_dir} — run without --test-only \
first"
      fi
      run_ctest "${build_dir}" "${CECE_CLONE}/scripts/run-tests-slurm.sbatch"
    fi
  fi
}

do_smoke() {
  local build_dir="${ROOT_DIR}/build-smoke"
  local smoke_exe="${build_dir}/smoke"

  load_modules
  print_toolchain

  if [[ "${TEST_ONLY}" != "yes" ]]; then
    run_build cmake -S "${SCRIPT_DIR}/smoke" -B "${build_dir}" \
      -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}"
    run_build cmake --build "${build_dir}" --parallel "${BUILD_JOBS}"
  fi

  if [[ "${BUILD_ONLY}" != "yes" ]]; then
    if [[ "${DRY_RUN}" != "yes" && ! -x "${smoke_exe}" ]]; then
      die "no smoke executable at ${smoke_exe} — run without --test-only \
first"
    fi
    run_tests "${smoke_exe}"
  fi
}

# Run a component, teeing its output to a timestamped log file (real runs
# only; dry runs just print the plan).
run_component() {
  local name="$1"
  echo
  echo ">>> component: ${name}"
  if [[ "${DRY_RUN}" == "yes" ]]; then
    "do_${name}"
  else
    mkdir -p "${LOG_DIR}"
    local log_file
    log_file="${LOG_DIR}/${name}-$(date +%Y%m%d-%H%M%S).log"
    echo ">>> logging to ${log_file}"
    "do_${name}" 2>&1 | tee "${log_file}"
  fi
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

case "${COMPONENT}" in
  catchem)
    run_component catchem
    ;;
  cece)
    run_component cece
    ;;
  smoke)
    run_component smoke
    ;;
  all)
    run_component catchem
    run_component cece
    run_component smoke
    ;;
esac

echo
echo "done (platform=${PLATFORM}, component=${COMPONENT}, dry-run=${DRY_RUN})"
