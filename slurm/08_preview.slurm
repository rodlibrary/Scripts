#!/bin/bash
#SBATCH --job-name=av-08-preview
#SBATCH --partition=compute
#SBATCH --nodes=1
#SBATCH --ntasks=1                          # not rank-aware; must run as a single process
#SBATCH --cpus-per-task=2
#SBATCH --time=00:30:00
#SBATCH --output=logs/08-preview-%j.out
#SBATCH --error=logs/08-preview-%j.err

# SLURM copies the submitted script into a per-job spool directory before
# running it, so "$0" / "dirname \"$0\"" does NOT point at this file's real
# location in your repo -- it points at that spool dir, and the common_env.sh
# lookup fails. SLURM_SUBMIT_DIR (the directory you ran sbatch/submit_pipeline.sh
# from) is reliable, so anchor the lookup there instead.
COMMON_ENV=""
for candidate in "${SLURM_SUBMIT_DIR}/slurm/common_env.sh" "${SLURM_SUBMIT_DIR}/common_env.sh"; do
    if [[ -f "${candidate}" ]]; then
        COMMON_ENV="${candidate}"
        break
    fi
done
if [[ -z "${COMMON_ENV}" ]]; then
    echo "ERROR: could not find common_env.sh under ${SLURM_SUBMIT_DIR} (looked in slurm/ and .). " \
         "Submit this job from your repo root, e.g.: cd your-repo && ./slurm/submit_pipeline.sh config.yaml" >&2
    exit 1
fi
source "${COMMON_ENV}" "$@"

# Builds the static HTML dashboard; no external API calls.
python run_pipeline.py --config "${CONFIG}" --no-mpi --steps preview

echo "=== $(basename "$0") finished: $(date) ==="
