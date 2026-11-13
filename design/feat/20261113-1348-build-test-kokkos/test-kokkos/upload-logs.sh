#!/usr/bin/env bash
#
# Collect all build-test-kokkos output logs and upload them to git under
# gcafs-v2/tasks/artifacts/<task>/<timestamp>-build-test-kokkos-logs.
#
# See usage() below for options and defaults.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

DEFAULT_URSA_ROOT_DIR="/scratch3/NCEPDEV/stmp/Benjamin.Koziol/my_stmp/test-kokkos"
DEFAULT_GAEAC6_ROOT_DIR="/gpfs/f6/bil-fire3/scratch/Benjamin.Koziol/stmp/test-kokkos"

DEFAULT_URSA_ARTIFACTS="${SCRIPT_DIR}/../../tasks/artifacts/20260813-1605-kokkos-spack-stack"
DEFAULT_GAEAC6_ARTIFACTS="${SCRIPT_DIR}/../../tasks/artifacts/20260814-1243-kokkos-spack-stack-gaeac6"

usage() {
  cat <<EOF
Usage: ${0##*/} [options]

Collect all build-test-kokkos output logs from the base root directory
(top-level suite logs, per-combination logs, Slurm test job logs, configure logs),
copy them into a timestamped directory under tasks/artifacts/, and push to git.

Options:
  --platform <name>      Target platform: ursa (default) or gaea-c6.
                         Auto-detected from hostname / filesystems if omitted.
  --root-dir <dir>       Base root directory containing logs to collect.
                         Defaults to platform scratch root.
  --dest-dir <dir>       Parent artifacts directory to upload to.
                         Defaults to platform-specific task artifact dir.
  --folder-name <name>   Destination folder name.
                         Defaults to: YYYYmmdd-HHMM-build-test-kokkos-logs.
  --message, -m <msg>    Git commit message.
  --no-push              Stage and commit to git, but do not push.
  --no-commit            Copy logs only; do not run git add/commit/push.
  --dry-run, -n          Print planned copy operations and git commands without
                         executing them.
  -h, --help             Show this help.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------

PLATFORM=""
ROOT_DIR_ARG=""
DEST_DIR_ARG=""
FOLDER_NAME=""
COMMIT_MSG=""
DO_PUSH="yes"
DO_COMMIT="yes"
DRY_RUN="no"

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
    --root-dir)
      [[ $# -ge 2 ]] || die "--root-dir requires an argument"
      ROOT_DIR_ARG="$2"
      shift
      ;;
    --root-dir=*)
      ROOT_DIR_ARG="${1#*=}"
      ;;
    --dest-dir)
      [[ $# -ge 2 ]] || die "--dest-dir requires an argument"
      DEST_DIR_ARG="$2"
      shift
      ;;
    --dest-dir=*)
      DEST_DIR_ARG="${1#*=}"
      ;;
    --folder-name)
      [[ $# -ge 2 ]] || die "--folder-name requires an argument"
      FOLDER_NAME="$2"
      shift
      ;;
    --folder-name=*)
      FOLDER_NAME="${1#*=}"
      ;;
    --message | -m)
      [[ $# -ge 2 ]] || die "$1 requires an argument"
      COMMIT_MSG="$2"
      shift
      ;;
    --message=* | -m=*)
      COMMIT_MSG="${1#*=}"
      ;;
    --no-push)
      DO_PUSH="no"
      ;;
    --no-commit)
      DO_COMMIT="no"
      DO_PUSH="no"
      ;;
    --dry-run | -n)
      DRY_RUN="yes"
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unrecognized argument: $1"
      ;;
  esac
  shift
done

# -----------------------------------------------------------------------------
# Configuration resolution
# -----------------------------------------------------------------------------

if [[ -z "${PLATFORM}" ]]; then
  if [[ -d "/scratch3/NCEPDEV/stmp" ]] || [[ "$(hostname)" =~ (ursa|ufe) ]]; then
    PLATFORM="ursa"
  elif [[ -d "/gpfs/f6/bil-fire3" ]] || [[ "$(hostname)" =~ (gaea|c6) ]]; then
    PLATFORM="gaea-c6"
  else
    PLATFORM="ursa"
  fi
fi

case "${PLATFORM}" in
  ursa)
    BASE_ROOT_DIR="${ROOT_DIR_ARG:-${DEFAULT_URSA_ROOT_DIR}}"
    BASE_ARTIFACTS_DIR="${DEST_DIR_ARG:-${DEFAULT_URSA_ARTIFACTS}}"
    ;;
  gaea-c6)
    BASE_ROOT_DIR="${ROOT_DIR_ARG:-${DEFAULT_GAEAC6_ROOT_DIR}}"
    BASE_ARTIFACTS_DIR="${DEST_DIR_ARG:-${DEFAULT_GAEAC6_ARTIFACTS}}"
    ;;
  *)
    usage >&2
    die "unknown platform '${PLATFORM}' (recognized: ursa, gaea-c6)"
    ;;
esac

TIMESTAMP=$(date '+%Y%m%d-%H%M')
FOLDER_NAME="${FOLDER_NAME:-${TIMESTAMP}-build-test-kokkos-logs}"
TARGET_DIR="${BASE_ARTIFACTS_DIR}/${FOLDER_NAME}"

echo "===================================================================================================="
echo "upload-logs configuration"
echo "  platform:          ${PLATFORM}"
echo "  base root dir:     ${BASE_ROOT_DIR}"
echo "  artifacts dir:     ${BASE_ARTIFACTS_DIR}"
echo "  destination:       ${TARGET_DIR}"
echo "  commit to git:     ${DO_COMMIT}"
echo "  push to git:       ${DO_PUSH}"
echo "  dry-run:           ${DRY_RUN}"
echo "===================================================================================================="

if [[ "${DRY_RUN}" != "yes" && ! -d "${BASE_ROOT_DIR}" ]]; then
  die "base root directory ${BASE_ROOT_DIR} does not exist — no logs to upload"
fi

if [[ "${DRY_RUN}" != "yes" ]]; then
  mkdir -p "${TARGET_DIR}"
fi

# -----------------------------------------------------------------------------
# Log collection
# -----------------------------------------------------------------------------

copied_count=0

# Helper to copy a log file into TARGET_DIR with deduplication prefix if needed
copy_log_file() {
  local src="$1"
  local dest_name="$2"
  local dest="${TARGET_DIR}/${dest_name}"

  if [[ "${DRY_RUN}" == "yes" ]]; then
    echo "+ cp ${src} ${dest}"
  else
    cp "${src}" "${dest}"
    echo "  copied: ${dest_name}"
  fi
  copied_count=$((copied_count + 1))
}

echo
echo "Collecting log files from ${BASE_ROOT_DIR}..."

# 1. Top-level suite logs: ${BASE_ROOT_DIR}/logs/*.log
if [[ -d "${BASE_ROOT_DIR}/logs" ]]; then
  for f in "${BASE_ROOT_DIR}/logs"/*.log; do
    if [[ -f "${f}" ]]; then
      copy_log_file "${f}" "$(basename "${f}")"
    fi
  done
fi

# 2. Per-combination logs: ${BASE_ROOT_DIR}/*/logs/*
for combo_dir in "${BASE_ROOT_DIR}"/*; do
  if [[ -d "${combo_dir}" && "$(basename "${combo_dir}")" != "logs" && "$(basename "${combo_dir}")" != "suite-logs" ]]; then
    combo="$(basename "${combo_dir}")"

    # Files inside ${combo_dir}/logs/
    if [[ -d "${combo_dir}/logs" ]]; then
      for f in "${combo_dir}/logs"/*; do
        if [[ -f "${f}" ]]; then
          fname="$(basename "${f}")"
          if [[ "${fname}" == "${combo}"* ]]; then
            dest_name="${fname}"
          else
            dest_name="${combo}-${fname}"
          fi
          copy_log_file "${f}" "${dest_name}"
        fi
      done
    fi

    # Any build-*/configure.log
    for f in "${combo_dir}"/build-*/configure.log; do
      if [[ -f "${f}" ]]; then
        dest_name="${combo}-configure.log"
        copy_log_file "${f}" "${dest_name}"
      fi
    done
  fi
done

echo "Total logs collected: ${copied_count}"

if [[ ${copied_count} -eq 0 ]]; then
  echo "WARNING: No log files were found under ${BASE_ROOT_DIR}."
  if [[ "${DRY_RUN}" != "yes" ]]; then
    rmdir "${TARGET_DIR}" 2>/dev/null || true
  fi
  exit 0
fi

# -----------------------------------------------------------------------------
# Git operations
# -----------------------------------------------------------------------------

if [[ "${DO_COMMIT}" != "yes" ]]; then
  echo
  echo "Logs copied to ${TARGET_DIR}. Git commit skipped (--no-commit)."
  exit 0
fi

echo
echo "Staging and committing logs to git..."

DEFAULT_MSG="add build-test-kokkos logs: ${FOLDER_NAME}"
MSG="${COMMIT_MSG:-${DEFAULT_MSG}}"

if [[ "${DRY_RUN}" == "yes" ]]; then
  echo "+ git -C ${REPO_ROOT} add ${TARGET_DIR}"
  echo "+ git -C ${REPO_ROOT} commit -m ${MSG@Q}"
  if [[ "${DO_PUSH}" == "yes" ]]; then
    echo "+ git -C ${REPO_ROOT} push"
  fi
else
  git -C "${REPO_ROOT}" add "${TARGET_DIR}"
  git -C "${REPO_ROOT}" commit -m "${MSG}"
  if [[ "${DO_PUSH}" == "yes" ]]; then
    echo
    echo "Pushing to git..."
    git -C "${REPO_ROOT}" push
  else
    echo "Logs committed locally. Push skipped (--no-push)."
  fi
fi

echo
echo "Done. Uploaded ${copied_count} logs to ${TARGET_DIR}."
