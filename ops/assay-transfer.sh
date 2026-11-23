#!/usr/bin/env bash
# ops/assay-transfer.sh: copy a root's evidence into the transfer repo, with
# the harness's report, and commit and push it.
#
#   ops/assay-transfer.sh [<name>]   default name: assay-<YYYYmmdd-HHMM>
#
# The values come from ops/env.sh (an exported variable wins); ASSAY_ROOT must
# be the root ops/assay-run.sh ran.
set -euo pipefail
shopt -s nullglob
# shellcheck source=ops/env.sh
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

name="${1:-assay-$(date +%Y%m%d-%H%M)}"
dst="$ASSAY_TRANSFER_REPO/$name"

mkdir -p "$dst/scripts" "$dst/cmake"
cd "$ASSAY_ROOT"
cp -r logs "$dst/"                                               # stage logs and run transcripts
cp scripts/*.yaml scripts/*.sh scripts/*.sbatch "$dst/scripts/" # stages.yaml, run-config.yaml, rendered scripts
for t in */tests; do                                             # every toolchain's tests root
  mkdir -p "$dst/$(dirname "$t")" && cp -r "$t" "$dst/$t"
done
for c in */*/build*/CMakeCache.txt; do # each build's configured options
  mkdir -p "$dst/cmake/$(dirname "$c")" && cp "$c" "$dst/cmake/$c"
done
# The same environment as the run, so uv and its cache are found. report exits
# 1 when it finds issues: that is still the report.
(cd "$ASSAY_HARNESS" && uv run --no-sync ufs-chem-assay report --root-dir "$ASSAY_ROOT") \
  > "$dst/report.txt" 2>&1 || true

cd "$ASSAY_TRANSFER_REPO"
git pull
git add -f "$name"
git commit -m "$name"
git push
echo "pushed $dst ($(du -sh "$dst" | cut -f1))"
