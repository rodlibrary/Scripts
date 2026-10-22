#!/bin/bash
#SBATCH --job-name=av-01-transcribe
#SBATCH --partition=gpu                     # <-- GPU partition; Whisper large-v3 wants a GPU
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1                 # one Whisper model instance per GPU
#SBATCH --cpus-per-task=8
#SBATCH --time=06:00:00
#SBATCH --output=logs/01-transcribe-%j.out
#SBATCH --error=logs/01-transcribe-%j.err

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

# NOTE: set transcription.device: "cuda" in your config to actually use the GPU.
python run_pipeline.py --config "${CONFIG}" --ranks "${SLURM_NTASKS}" --steps transcribe

echo "=== $(basename "$0") finished: $(date) ==="
