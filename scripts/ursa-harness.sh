#!/bin/bash
# Manual Ursa run of the harness (docs/ursa-runbook.md, steps 6-7): pytest
# on this login node, one `sbatch --wait` job per driver call through
# scripts/cece-modules.sh. Run it under tmux; watch the jobs with
# `squeue -u $USER`. The same commands `ufs-chem-assay run` renders from
# config/ursa.yaml; keep the two in step.
#
# Environment:
#   ROOT         required: the directory holding ufs-chem-assay/ and CECE/
#   MODULEFILE   CECE modulefile the driver jobs load (default cece_ursa.intelllvm)
#   SBATCH_ARGS  per-driver job options (default "-A epic -q debug -p u1-compute -N 1 -n 1 -c 8")
#   SUITE        --suite-config selector (default simple-maccity-suite.yaml)
#   OUTPUT_ROOT  --combo-output-root, relative to the CECE checkout
#                (default ufs-chem-assay-output)
set -euo pipefail

ROOT="${ROOT:?set ROOT to the directory holding ufs-chem-assay/ and CECE/}"
MODULEFILE="${MODULEFILE:-cece_ursa.intelllvm}"
SBATCH_ARGS="${SBATCH_ARGS:--A epic -q debug -p u1-compute -N 1 -n 1 -c 8}"
SUITE="${SUITE:-simple-maccity-suite.yaml}"
OUTPUT_ROOT="${OUTPUT_ROOT:-ufs-chem-assay-output}"

# The harness venv must not see the module environment (spack-stack's
# PYTHONPATH shadows the venv's numpy): undo whatever this shell loaded.
if [ -n "${MODULESHOME:-}" ]; then source "$MODULESHOME/init/bash"; fi
module purge
unset PYTHONPATH

export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR="$ROOT/uv-cache"

# slurm runtime: one job per driver call; the job script loads the modulefile.
export CECE_ROOT_DIR="$ROOT/CECE"
export CECE_PLATFORM=ursa
export CECE_RUNTIME=slurm
export CECE_SBATCH_ARGS="$SBATCH_ARGS"
export CECE_MODULEFILE="$MODULEFILE"
export CECE_ENABLE_BASELINE_COMPARISONS=false  # no public baseline store yet
export CECE_RUN_TIMEOUT_S=300
export CECE_DASK_NWORKERS=2  # analysis runs here, on the login node: stay small
# Single-node MPI hints for the driver jobs (the ones CECE's own ctest uses).
export I_MPI_FABRICS=shm
export FI_PROVIDER=tcp

cd "$ROOT/ufs-chem-assay"
uv run --no-sync pytest src/tests/test_driver_combos.py \
    --suite-config="$SUITE" \
    --combo-output-root="$OUTPUT_ROOT" --combo-clean-root
