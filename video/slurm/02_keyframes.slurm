#!/bin/bash
#SBATCH --job-name=av-02-keyframes
#SBATCH --partition=compute
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=16                # ffmpeg is cheap per-frame; scale out on CPU
#SBATCH --cpus-per-task=2
#SBATCH --time=04:00:00
#SBATCH --output=logs/02-keyframes-%j.out
#SBATCH --error=logs/02-keyframes-%j.err

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

python run_pipeline.py --config "${CONFIG}" --ranks "${SLURM_NTASKS}" --steps keyframes

echo "=== $(basename "$0") finished: $(date) ==="
