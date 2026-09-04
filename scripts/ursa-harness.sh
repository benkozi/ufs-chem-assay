#!/bin/bash
# Manual Ursa batch job for the harness (docs/ursa-runbook.md, steps 6-7):
# loads CECE's modulefile, exports the native-runtime settings, and runs
# one suite as a Slurm job. The same commands `ufs-chem-assay run` renders
# from config/ursa.yaml; keep the two in step.
#
# Directives below are defaults; override on the command line. The log
# path is given there too (#SBATCH -o cannot expand variables):
#
#   sbatch --export=ALL,ROOT=$ROOT -o $ROOT/logs/slurm-%j.out scripts/ursa-harness.sh
#
# Environment (via --export or exported before sbatch):
#   ROOT         required: the directory holding ufs-chem-assay/ and CECE/
#   MODULEFILE   CECE modulefile to load (default cece_ursa.intelllvm)
#   SUITE        --suite-config selector (default simple-maccity-suite.yaml)
#   OUTPUT_ROOT  --combo-output-root, relative to the CECE checkout
#                (default ufs-chem-assay-output)
#SBATCH -A epic
#SBATCH -q debug
#SBATCH -p u1-compute
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -c 8
#SBATCH -t 00:30:00
#SBATCH -J ufs-chem-assay
set -euo pipefail

ROOT="${ROOT:?set ROOT to the directory holding ufs-chem-assay/ and CECE/}"
MODULEFILE="${MODULEFILE:-cece_ursa.intelllvm}"
SUITE="${SUITE:-simple-maccity-suite.yaml}"
OUTPUT_ROOT="${OUTPUT_ROOT:-ufs-chem-assay-output}"

# The module environment the driver was built in (batch shells start clean).
if [ -n "${MODULESHOME:-}" ]; then source "$MODULESHOME/init/bash"; fi
module purge
module use "$ROOT/CECE/modulefiles"
module load "$MODULEFILE"
module list

# uv from the standalone installer; no network on compute nodes.
export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR="$ROOT/uv-cache"
export UV_OFFLINE=1

# Native runtime: the driver as a host process, one srun job step per
# combination inside this allocation.
export CECE_ROOT_DIR="$ROOT/CECE"
export CECE_PLATFORM=ursa
export CECE_RUNTIME=native
export CECE_LAUNCHER="srun --ntasks=1"
export CECE_ENABLE_BASELINE_COMPARISONS=false  # no public baseline store yet
export CECE_RUN_TIMEOUT_S=300
export CECE_DASK_NWORKERS="${SLURM_CPUS_PER_TASK}"
# Single-node MPI hints (the ones CECE's own ctest uses).
export I_MPI_FABRICS=shm
export FI_PROVIDER=tcp

cd "$ROOT/ufs-chem-assay"
uv run --no-sync pytest src/tests/test_driver_combos.py \
    --suite-config="$SUITE" \
    --combo-output-root="$OUTPUT_ROOT" --combo-clean-root
