#!/usr/bin/env bash
# ops/assay-benchmarks.sh: append one root's test benchmarks, from its
# transfer directory, to this checkout's committed tests-benchmarks.csv files.
#
#   ops/assay-benchmarks.sh [--dry-run] <transfer dir>
#
# Run on the laptop, after ops/assay-transfer.sh, on the directory it wrote.
# Runs already recorded are skipped. It does not commit: review the diff it
# prints and commit it. It does not source ops/env.sh, whose values (uv's
# directories under /gpfs) are the run machine's.
set -euo pipefail
shopt -s nullglob

harness="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
usage() {
  echo "usage: ops/assay-benchmarks.sh [--dry-run] <transfer dir>" >&2
  echo "  <transfer dir>: one root's directory as ops/assay-transfer.sh wrote it" >&2
  exit 2
}

dry_run=""
transfer=""
count=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run="--dry-run" ;;
    -h | --help) usage ;;
    -*) usage ;;
    *) transfer="$1" count=$((count + 1)) ;;
  esac
  shift
done
if [ "$count" -ne 1 ]; then usage; fi
if [ ! -f "$transfer/scripts/run-config.yaml" ]; then
  echo "assay-benchmarks: $transfer is not a transfer directory (no scripts/run-config.yaml)" >&2
  usage
fi
transfer="$(cd "$transfer" && pwd)"

# The tests roots, where assay-transfer.sh puts them; a run's has its ledger.
roots="$(
  for dir in "$transfer"/*/tests/*/ "$transfer"/*/tests/*/*/; do
    dir="${dir%/}"
    if [ -f "$dir/tests.yaml" ] && [ -f "$dir/benchmarks.csv" ]; then echo "$dir"; fi
  done | sort
)"
if [ -z "$roots" ]; then
  echo "assay-benchmarks: no test run in $transfer"
  exit 0
fi

failed=0
while IFS= read -r root; do
  uv run --project "$harness" ufs-chem-assay record-benchmarks \
    --tests-root "$root" ${dry_run:+"$dry_run"} < /dev/null || {
    echo "assay-benchmarks: record-benchmarks failed for $root"
    failed=1
  }
done <<< "$roots"

git -C "$harness" diff --stat -- 'src/tests/config/*/tests-benchmarks.csv'
if [ -n "$dry_run" ]; then
  echo "assay-benchmarks: dry run; nothing appended"
else
  echo "assay-benchmarks: review the diff above and commit src/tests/config/*/tests-benchmarks.csv"
fi
exit "$failed"
