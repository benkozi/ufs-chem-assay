#!/usr/bin/env bash
# ops/assay-transfer.sh: the daily loop's second step, read like the first:
# `ufs-chem-assay transfer` on one run config, through ops/assay. It zips the
# config's root into <transfer_dir>/<name>.zip and commits and pushes it.
#
#   ops/assay-transfer.sh <run config yaml>   e.g. config/c7.local.yaml
#
# No module reset and no git pull of the harness (ops/assay-run.sh did both).
# For --name or --dry-run: ops/assay transfer --config-file <file> [...].
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: ops/assay-transfer.sh <run config yaml>" >&2
  exit 2
fi
if [ ! -f "$1" ]; then
  echo "assay-transfer: no run config $1" >&2
  exit 2
fi
config="$(cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1")" # absolute, before the cd below
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."                      # the harness checkout (its .env)
exec ops/assay transfer --config-file "$config"
