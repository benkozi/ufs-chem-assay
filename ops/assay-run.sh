#!/usr/bin/env bash
# ops/assay-run.sh: what only a shell can do before the harness starts
# (module reset, git pull), then `ufs-chem-assay run --watch` on one run
# config, through ops/assay (uv's directories in this checkout only).
# Everything about the run is in that file: its root, its selection, where
# its transfer goes (extends: the shared template).
#
#   ops/assay-run.sh <run config yaml>   e.g. config/c7.local.yaml
#
# run writes its transcript, <root>/logs/assay-run-<YYYYmmdd-HHMMSS>.log; the
# exit status is run's.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: ops/assay-run.sh <run config yaml>" >&2
  exit 2
fi
if [ ! -f "$1" ]; then
  echo "assay-run: no run config $1" >&2
  exit 2
fi
config="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")" # absolute, before the cd below
cd "$(dirname "${BASH_SOURCE[0]}")/.."                   # the harness checkout

# The stage scripts reset their own modules; this one is for git and uv here.
set +u # module init scripts read unset variables
if [ -n "${MODULESHOME:-}" ] && [ -f "$MODULESHOME/init/bash" ]; then
  # shellcheck source=/dev/null
  source "$MODULESHOME/init/bash"
fi
if command -v module > /dev/null 2>&1; then
  module reset
else
  echo "assay-run: no module command; no module reset"
fi
set -u

git pull
exec ops/assay run --watch --config-file "$config"
