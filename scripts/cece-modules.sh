#!/bin/bash
# Job script for the slurm runtime — and a plain wrapper anywhere else.
#
#   sbatch --wait ... scripts/cece-modules.sh <command> [args...]
#   scripts/cece-modules.sh <command> [args...]
#
# When CECE_MODULEFILE is set, loads that modulefile from
# $CECE_ROOT_DIR/modulefiles (after `module purge`) so the driver or ctest
# runs in the environment it was built in; then execs the command. Without
# CECE_MODULEFILE it is a bare exec. The harness's own Python never runs
# through here: the module environment (its PYTHONPATH in particular)
# must not reach the venv.
set -euo pipefail
if [ -n "${CECE_MODULEFILE:-}" ]; then
  if [ -n "${MODULESHOME:-}" ]; then source "$MODULESHOME/init/bash"; fi
  module purge
  module use "${CECE_ROOT_DIR:?CECE_ROOT_DIR must point at the CECE checkout}/modulefiles"
  module load "$CECE_MODULEFILE"
fi
exec "$@"
