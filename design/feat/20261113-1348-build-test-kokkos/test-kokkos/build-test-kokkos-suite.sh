#!/usr/bin/env bash
#
# Orchestrated test suite runner for CATChem and CECE against the custom
# kokkos/kokkos-kernels spack-stack environment.
#
# Runs offline compute-node compilation probes (--srun-build) and Slurm
# direct-launch testing (--direct-launch) across compiler suites (oneapi, gcc),
# re-using build artifacts across phases.
#
# See README.md for details.
#

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_SCRIPT="${SCRIPT_DIR}/build-test-kokkos.sh"

# Default platform root dirs (matching build-test-kokkos.sh)
DEFAULT_URSA_ROOT_DIR="/scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos"
DEFAULT_GAEAC6_ROOT_DIR="/gpfs/f6/bil-fire3/scratch/Benjamin.Koziol/stmp/test-kokkos"

usage() {
  cat <<EOF
Usage: ${0##*/} [options] <application>
       ${0##*/} --application=<app> [options]

Run the automated test suite for CATChem and/or CECE across compiler suites
(oneapi, gcc) and execution phases (srun-build, direct-launch testing).

Each application and compiler combination executes in its own dedicated
subdirectory under the base root directory (e.g. <base-root>/<app>-<comp>).
Top-level suite execution logs are written to <base-root>/logs/.

Applications:
  catchem | cece | all   (required)

Options:
  --platform <name>      Target platform: ursa (default) or gaea-c6.
  --compiler <name>      Compiler suite: all (default; oneapi + gcc on Ursa),
                         oneapi (alias: intelllvm), or gcc.
  --reuse-build          Reuse existing combination build directory (default: yes).
  --no-reuse-build       Wipe combination directory before Phase 1.
  --srun-build           Run compute-node compile probe in Phase 1 (default: yes).
  --no-srun-build        Skip compute-node compile probe; only build tests and
                         run Slurm direct-launch (alias: --skip-srun-build).
  --skip-srun-build      Alias for --no-srun-build.
  --srun-build-only      Run compute-node compile probe only; skip test building
                         and test execution.
  --direct-launch        Use Slurm direct-launch for MPI tests (default: yes).
  --no-direct-launch     Disable Slurm direct-launch (use srun 1-task step wrapper).
  --test-filter <re>     Run only ctest tests matching regex in test phase.
  --test-jobs <n>        Run up to n tests concurrently inside test allocation.
  --async                Submit sbatch test jobs without --wait.
  --dry-run, -n          Print commands without executing. Safe to run anywhere.
  --update-source        Sync git clones before building.
  --upload-logs          Collect and upload all output logs to git artifacts after run.
  --root-dir <dir>       Override base output root directory.
  --scoped-roots         Retained for backwards compatibility (dedicated
                         subdirectories per combination under base root dir
                         are standard).
  -h, --help             Show this help.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

note() {
  echo "# $*"
}

format_duration() {
  local total_secs="$1"
  local mins=$((total_secs / 60))
  local secs=$((total_secs % 60))
  printf "%02dm %02ds" "${mins}" "${secs}"
}

# Color support for terminal output
if [[ -t 1 ]]; then
  C_RESET="\033[0m"
  C_GREEN="\033[32m"
  C_RED="\033[31m"
  C_YELLOW="\033[33m"
  C_BOLD="\033[1m"
else
  C_RESET=""
  C_GREEN=""
  C_RED=""
  C_YELLOW=""
  C_BOLD=""
fi

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------

APPLICATION=""
PLATFORM="ursa"
COMPILER="all"
DRY_RUN="no"
UPDATE_SOURCE="no"
SKIP_SRUN_BUILD="no"
SRUN_BUILD_ONLY="no"
ROOT_DIR_ARG=""
SCOPED_ROOTS="no"
REUSE_BUILD="yes"
DIRECT_LAUNCH="yes"
TEST_FILTER=""
TEST_JOBS=""
ASYNC="no"
UPLOAD_LOGS="no"

while (($# > 0)); do
  case "$1" in
    --application)
      [[ $# -ge 2 ]] || die "--application requires an argument"
      APPLICATION="$2"
      shift
      ;;
    --application=*)
      APPLICATION="${1#*=}"
      ;;
    -a)
      [[ $# -ge 2 ]] || die "-a requires an argument"
      APPLICATION="$2"
      shift
      ;;
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
    --reuse-build)
      REUSE_BUILD="yes"
      ;;
    --no-reuse-build)
      REUSE_BUILD="no"
      ;;
    --direct-launch)
      DIRECT_LAUNCH="yes"
      ;;
    --no-direct-launch)
      DIRECT_LAUNCH="no"
      ;;
    --test-filter)
      [[ $# -ge 2 ]] || die "--test-filter requires an argument"
      TEST_FILTER="$2"
      shift
      ;;
    --test-filter=*)
      TEST_FILTER="${1#*=}"
      ;;
    --test-jobs)
      [[ $# -ge 2 ]] || die "--test-jobs requires an argument"
      TEST_JOBS="$2"
      shift
      ;;
    --test-jobs=*)
      TEST_JOBS="${1#*=}"
      ;;
    --async)
      ASYNC="yes"
      ;;
    --dry-run | -n)
      DRY_RUN="yes"
      ;;
    --update-source)
      UPDATE_SOURCE="yes"
      ;;
    --upload-logs)
      UPLOAD_LOGS="yes"
      ;;
    --srun-build)
      SKIP_SRUN_BUILD="no"
      ;;
    --no-srun-build | --skip-srun-build)
      SKIP_SRUN_BUILD="yes"
      ;;
    --srun-build-only)
      SRUN_BUILD_ONLY="yes"
      ;;
    --root-dir)
      [[ $# -ge 2 ]] || die "--root-dir requires an argument"
      ROOT_DIR_ARG="$2"
      shift
      ;;
    --root-dir=*)
      ROOT_DIR_ARG="${1#*=}"
      ;;
    --scoped-roots)
      SCOPED_ROOTS="yes"
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    catchem | cece | all)
      if [[ -z "${APPLICATION}" ]]; then
        APPLICATION="$1"
      else
        die "application specified more than once: '${APPLICATION}' and '$1'"
      fi
      ;;
    *)
      usage >&2
      die "unrecognized argument: $1"
      ;;
  esac
  shift
done

if [[ -z "${APPLICATION}" ]]; then
  usage >&2
  die "required <application> argument missing (catchem, cece, all)"
fi

if [[ "${SKIP_SRUN_BUILD}" == "yes" && "${SRUN_BUILD_ONLY}" == "yes" ]]; then
  usage >&2
  die "--skip-srun-build and --srun-build-only are mutually exclusive"
fi

if [[ -n "${TEST_JOBS}" && ! "${TEST_JOBS}" =~ ^[1-9][0-9]*$ ]]; then
  die "--test-jobs must be a positive integer (got '${TEST_JOBS}')"
fi

# -----------------------------------------------------------------------------
# Configuration resolution
# -----------------------------------------------------------------------------

case "${PLATFORM}" in
  ursa)
    BASE_ROOT_DIR="${ROOT_DIR_ARG:-${DEFAULT_URSA_ROOT_DIR}}"
    ;;
  gaea-c6)
    BASE_ROOT_DIR="${ROOT_DIR_ARG:-${DEFAULT_GAEAC6_ROOT_DIR}}"
    ;;
  *)
    usage >&2
    die "unknown platform '${PLATFORM}' (recognized: ursa, gaea-c6)"
    ;;
esac

# Resolve compilers
declare -a COMPILERS=()
case "${COMPILER}" in
  all)
    if [[ "${PLATFORM}" == "ursa" ]]; then
      COMPILERS=("oneapi" "gcc")
    else
      COMPILERS=("oneapi")
    fi
    ;;
  oneapi | intelllvm)
    COMPILERS=("oneapi")
    ;;
  gcc)
    if [[ "${PLATFORM}" == "gaea-c6" ]]; then
      die "compiler 'gcc' is not supported on platform gaea-c6 (oneapi only)"
    fi
    COMPILERS=("gcc")
    ;;
  *)
    usage >&2
    die "unknown compiler '${COMPILER}' (recognized: all, oneapi, intelllvm, gcc)"
    ;;
esac

# Resolve applications
declare -a APPS=()
case "${APPLICATION}" in
  catchem)
    APPS=("catchem")
    ;;
  cece)
    APPS=("cece")
    ;;
  all)
    APPS=("catchem" "cece")
    ;;
  *)
    usage >&2
    die "unknown application '${APPLICATION}' (recognized: catchem, cece, all)"
    ;;
esac

[[ -f "${BUILD_SCRIPT}" ]] || die "cannot find build script at ${BUILD_SCRIPT}"

# Persistent directory for suite logs
SUITE_LOG_DIR="${BASE_ROOT_DIR}/logs"

echo "===================================================================================================="
echo "build-test-kokkos-suite configuration"
echo "  platform:         ${PLATFORM}"
echo "  application(s):   ${APPS[*]}"
echo "  compiler(s):      ${COMPILERS[*]}"
echo "  base root dir:    ${BASE_ROOT_DIR}"
echo "  suite log dir:    ${SUITE_LOG_DIR}"
echo "  reuse build:      ${REUSE_BUILD}"
echo "  direct launch:    ${DIRECT_LAUNCH}"
echo "  test filter:      ${TEST_FILTER:-(none — full suite)}"
echo "  test jobs:        ${TEST_JOBS:-(serial)}"
echo "  async:            ${ASYNC}"
echo "  skip srun-build:  ${SKIP_SRUN_BUILD}"
echo "  srun-build only:  ${SRUN_BUILD_ONLY}"
echo "  update source:    ${UPDATE_SOURCE}"
echo "  dry-run:          ${DRY_RUN}"
echo "===================================================================================================="

# Data structures for tracking step results
declare -a REPORT_APPS=()
declare -a REPORT_COMPS=()
declare -a REPORT_PHASES=()
declare -a REPORT_STATUSES=()
declare -a REPORT_DURATIONS=()
declare -a REPORT_LOGS=()

SUITE_START_TIME=$(date +%s)

# Helper to execute or dry-run a shell command
run_cmd() {
  if [[ "${DRY_RUN}" == "yes" ]]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
  else
    echo "+ $*"
    "$@"
  fi
}

# Helper to clean combination root dir before fresh pipeline
clean_root_dir() {
  local target_root="$1"
  if [[ "${DRY_RUN}" == "yes" ]]; then
    note "clean combination directory: ${target_root}"
    run_cmd rm -rf "${target_root}"
    return
  fi

  if [[ -d "${target_root}" ]]; then
    echo "+ rm -rf ${target_root}"
    rm -rf "${target_root}"
  fi
}

# -----------------------------------------------------------------------------
# Pipeline Execution
# -----------------------------------------------------------------------------

for app in "${APPS[@]}"; do
  for comp in "${COMPILERS[@]}"; do
    combo="${app}-${comp}"
    echo
    echo "===================================================================="
    echo "Starting pipeline: application=${app} compiler=${comp} (${combo})"
    echo "===================================================================="

    local_root="${BASE_ROOT_DIR}/${combo}"
    srun_passed="no"

    # -------------------------------------------------------------------------
    # Phase 1: Compute-Node Compile Probe (--srun-build)
    # -------------------------------------------------------------------------
    if [[ "${SKIP_SRUN_BUILD}" != "yes" ]]; then
      step_name="srun-build"
      echo
      echo ">>> [${combo}] Phase 1: Compute-node compile probe (${step_name})"

      # Clean combination directory before starting fresh compile probe unless --reuse-build was specified
      if [[ "${REUSE_BUILD}" != "yes" ]]; then
        clean_root_dir "${local_root}"
      fi

      step_start=$(date +%s)
      cmd=(
        bash "${BUILD_SCRIPT}"
        "${app}"
        --platform "${PLATFORM}"
        --compiler "${comp}"
        --root-dir "${local_root}"
        --srun-build
      )
      if [[ "${DIRECT_LAUNCH}" == "yes" ]]; then
        cmd+=(--direct-launch)
      fi
      if [[ "${REUSE_BUILD}" == "yes" && -d "${local_root}" ]]; then
        cmd+=(--reuse-build)
      fi
      if [[ "${DRY_RUN}" == "yes" ]]; then
        cmd+=(--dry-run)
      fi
      if [[ "${UPDATE_SOURCE}" == "yes" ]]; then
        cmd+=(--update-source)
      fi

      step_log="${SUITE_LOG_DIR}/${combo}-srun-build.log"
      if [[ "${DRY_RUN}" == "yes" ]]; then
        run_cmd "${cmd[@]}"
        exit_code=0
      else
        mkdir -p "${SUITE_LOG_DIR}"
        "${cmd[@]}" 2>&1 | tee "${step_log}"
        exit_code="${PIPESTATUS[0]}"
      fi
      step_end=$(date +%s)
      duration=$((step_end - step_start))

      REPORT_APPS+=("${app}")
      REPORT_COMPS+=("${comp}")
      REPORT_PHASES+=("${step_name}")
      REPORT_DURATIONS+=("$(format_duration "${duration}")")
      REPORT_LOGS+=("${step_log}")

      if [[ ${exit_code} -eq 0 ]]; then
        REPORT_STATUSES+=("PASS")
        srun_passed="yes"
        echo ">>> [${combo}] Phase 1 PASSED ($(format_duration "${duration}"))"
      else
        REPORT_STATUSES+=("FAIL")
        srun_passed="no"
        echo ">>> [${combo}] Phase 1 FAILED with exit code ${exit_code} ($(format_duration "${duration}"))"
      fi
    else
      srun_passed="yes"
    fi

    # -------------------------------------------------------------------------
    # Phase 2: Login-Node Test Build & Direct Launch (--reuse-build)
    # -------------------------------------------------------------------------
    if [[ "${SRUN_BUILD_ONLY}" != "yes" ]]; then
      step_name="test-direct-launch"
      echo
      echo ">>> [${combo}] Phase 2: Login-node test build & direct-launch (${step_name})"

      if [[ "${srun_passed}" != "yes" ]]; then
        echo ">>> [${combo}] Phase 2 SKIPPED (Phase 1 failed)"
        REPORT_APPS+=("${app}")
        REPORT_COMPS+=("${comp}")
        REPORT_PHASES+=("${step_name}")
        REPORT_STATUSES+=("SKIPPED")
        REPORT_DURATIONS+=("00m 00s")
        REPORT_LOGS+=("(none — skipped due to Phase 1 failure)")
      else
        step_start=$(date +%s)
        cmd=(
          bash "${BUILD_SCRIPT}"
          "${app}"
          --platform "${PLATFORM}"
          --compiler "${comp}"
          --root-dir "${local_root}"
          --no-srun-build
        )
        if [[ "${DIRECT_LAUNCH}" == "yes" ]]; then
          cmd+=(--direct-launch)
        fi
        # If Phase 1 ran or reuse-build was asked, reuse the existing build dir; otherwise start clean
        if [[ "${SKIP_SRUN_BUILD}" != "yes" || "${REUSE_BUILD}" == "yes" ]]; then
          cmd+=(--reuse-build)
        else
          clean_root_dir "${local_root}"
        fi
        if [[ -n "${TEST_FILTER}" ]]; then
          cmd+=(--test-filter "${TEST_FILTER}")
        fi
        if [[ -n "${TEST_JOBS}" ]]; then
          cmd+=(--test-jobs "${TEST_JOBS}")
        fi
        if [[ "${ASYNC}" == "yes" ]]; then
          cmd+=(--async)
        fi
        if [[ "${DRY_RUN}" == "yes" ]]; then
          cmd+=(--dry-run)
        fi
        if [[ "${UPDATE_SOURCE}" == "yes" ]]; then
          cmd+=(--update-source)
        fi

        step_log="${SUITE_LOG_DIR}/${combo}-test-direct-launch.log"
        if [[ "${DRY_RUN}" == "yes" ]]; then
          run_cmd "${cmd[@]}"
          exit_code=0
        else
          mkdir -p "${SUITE_LOG_DIR}"
          "${cmd[@]}" 2>&1 | tee "${step_log}"
          exit_code="${PIPESTATUS[0]}"
        fi
        step_end=$(date +%s)
        duration=$((step_end - step_start))

        REPORT_APPS+=("${app}")
        REPORT_COMPS+=("${comp}")
        REPORT_PHASES+=("${step_name}")
        REPORT_DURATIONS+=("$(format_duration "${duration}")")
        REPORT_LOGS+=("${step_log}")

        if [[ ${exit_code} -eq 0 ]]; then
          REPORT_STATUSES+=("PASS")
          echo ">>> [${combo}] Phase 2 PASSED ($(format_duration "${duration}"))"
        else
          REPORT_STATUSES+=("FAIL")
          echo ">>> [${combo}] Phase 2 FAILED with exit code ${exit_code} ($(format_duration "${duration}"))"
        fi
      fi
    fi
  done
done

SUITE_END_TIME=$(date +%s)
TOTAL_SUITE_DURATION=$((SUITE_END_TIME - SUITE_START_TIME))

# -----------------------------------------------------------------------------
# Terminal Summary Report
# -----------------------------------------------------------------------------

pass_count=0
fail_count=0
skip_count=0
total_steps=${#REPORT_APPS[@]}

for status in "${REPORT_STATUSES[@]}"; do
  case "${status}" in
    PASS) pass_count=$((pass_count + 1)) ;;
    FAIL) fail_count=$((fail_count + 1)) ;;
    SKIPPED) skip_count=$((skip_count + 1)) ;;
  esac
done

echo
echo "===================================================================================================="
echo -e "${C_BOLD}                               build-test-kokkos-suite Summary Report${C_RESET}"
echo "===================================================================================================="
echo "Platform: ${PLATFORM}   Application: ${APPLICATION}   Compiler(s): ${COMPILERS[*]}"
echo "Date: $(date '+%Y-%m-%d %H:%M:%S')   Total Duration: $(format_duration "${TOTAL_SUITE_DURATION}")"
echo "----------------------------------------------------------------------------------------------------"
printf "%-3s  %-11s  %-9s  %-20s  %-8s  %-9s  %s\n" \
  "#" "Application" "Compiler" "Phase" "Status" "Duration" "Log File"
echo "----------------------------------------------------------------------------------------------------"

for ((i = 0; i < total_steps; i++)); do
  step_num=$((i + 1))
  app="${REPORT_APPS[i]}"
  comp="${REPORT_COMPS[i]}"
  phase="${REPORT_PHASES[i]}"
  status="${REPORT_STATUSES[i]}"
  dur="${REPORT_DURATIONS[i]}"
  log="${REPORT_LOGS[i]}"

  case "${status}" in
    PASS)    color_status="${C_GREEN}${status}${C_RESET}" ;;
    FAIL)    color_status="${C_RED}${status}${C_RESET}" ;;
    SKIPPED) color_status="${C_YELLOW}${status}${C_RESET}" ;;
    *)       color_status="${status}" ;;
  esac

  printf "%-3s  %-11s  %-9s  %-20s  %-17b  %-9s  %s\n" \
    "${step_num}" "${app}" "${comp}" "${phase}" "${color_status}" "${dur}" "${log}"
done

echo "----------------------------------------------------------------------------------------------------"
if [[ ${fail_count} -eq 0 ]]; then
  echo -e "Result: ${C_GREEN}${C_BOLD}${pass_count} PASSED${C_RESET}, ${fail_count} FAILED, ${skip_count} SKIPPED (Total: ${total_steps} steps)"
else
  echo -e "Result: ${pass_count} PASSED, ${C_RED}${C_BOLD}${fail_count} FAILED${C_RESET}, ${skip_count} SKIPPED (Total: ${total_steps} steps)"
fi
echo "===================================================================================================="

if [[ "${UPLOAD_LOGS}" == "yes" ]]; then
  echo
  upload_cmd=(bash "${SCRIPT_DIR}/upload-logs.sh" --platform "${PLATFORM}" --root-dir "${BASE_ROOT_DIR}")
  if [[ "${DRY_RUN}" == "yes" ]]; then
    upload_cmd+=(--dry-run)
  fi
  "${upload_cmd[@]}"
fi

if [[ ${fail_count} -gt 0 ]]; then
  exit 1
fi

exit 0
