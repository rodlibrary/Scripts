#!/bin/bash
#===============================================================================
# common_env.sh
#
# Sourced (not executed) by every stage script in this directory. Keeps
# module loads, environment activation, and credential handling in one
# place instead of thirteen. Adjust module names / env activation for your
# cluster.
#===============================================================================

set -euo pipefail

module purge
module load openmpi/gcc/64/4.1.8  # confirmed via `module avail openmpi`
# ffmpeg is at /usr/bin/ffmpeg (a standard system path) — already on PATH,
# no module needed.
# No conda/miniconda module on this cluster — activate the venv directly.

source /data/hartwigd/pve/bin/activate

# --- API credentials ---------------------------------------------------------
# Never hardcode secrets. Store the key in a file only you can read.
if [[ -f "${HOME}/.secrets/google_api_key" ]]; then
    export GOOGLE_API_KEY
    GOOGLE_API_KEY="$(cat "${HOME}/.secrets/google_api_key")"
else
    echo "ERROR: ${HOME}/.secrets/google_api_key not found. Set GOOGLE_API_KEY before submitting." >&2
    exit 1
fi

cd "${SLURM_SUBMIT_DIR}"
mkdir -p logs

CONFIG="${1:-config.yaml}"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"

echo "=== $(basename "$0") starting: $(date) ==="
echo "Config file:  ${CONFIG}"
echo "Job ID:       ${SLURM_JOB_ID:-n/a}"
echo "Nodes/tasks:  ${SLURM_NNODES:-1} nodes x ${SLURM_NTASKS_PER_NODE:-1} tasks/node = ${SLURM_NTASKS:-1} ranks"
echo "Threads/rank: ${OMP_NUM_THREADS}"
