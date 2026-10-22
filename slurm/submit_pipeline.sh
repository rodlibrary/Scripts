#!/bin/bash
#===============================================================================
# submit_pipeline.sh
#
# Submits every pipeline stage as its own SLURM job, chained with
# --dependency=afterok so each stage only starts once the previous one has
# succeeded. Each stage script requests its own resources (GPU for
# transcription, wide CPU fan-out for keyframes/ffmpeg, modest concurrency
# for the Gemini API steps), so you can tune them independently instead of
# sizing one job for the whole workflow.
#
# Usage:
#   ./submit_pipeline.sh [config.yaml]
#
# Skip stages (e.g. you already have transcripts and keyframes):
#   SKIP_STEPS="transcribe keyframes" ./submit_pipeline.sh config.yaml
#
# Run only a subset:
#   ONLY_STEPS="describe_frames summarize" ./submit_pipeline.sh config.yaml
#
# Partitions: the .slurm files ship with placeholder partition names
# (gpu / compute) that will NOT match your cluster. Find your real ones
# with `sinfo -s` or `scontrol show partition` on the login node, then
# either edit the #SBATCH lines directly, or override them here without
# touching any file:
#   CPU_PARTITION=batch GPU_PARTITION=gpu-a100 ./submit_pipeline.sh config.yaml
#
# No GPU nodes on your cluster at all? Run transcription on CPU instead
# (also set transcription.device: "cpu" in your config):
#   USE_GPU=0 CPU_PARTITION=batch ./submit_pipeline.sh config.yaml
#
# Memory/CPU: the .slurm files also ship with placeholder --mem values
# (64G/48G/16G/8G/4G depending on stage) that may not match any node size
# on your cluster ("Memory specification can not be satisfied" / "Requested
# node configuration is not available"). Check what your nodes actually
# have with `sinfo -o "%P %D %c %m %G"`, then override uniformly here —
# note this applies the SAME value to every stage in that resource class,
# so pick something that comfortably fits your smallest node and covers
# the heaviest stage (keyframes wants the most CPU memory by default):
#   CPU_MEM=32G GPU_MEM=48G ./submit_pipeline.sh config.yaml
# For tighter per-stage tuning, edit the #SBATCH --mem/--cpus-per-task
# lines in the individual NN_*.slurm files directly.
#
# NOTE: the .slurm files in this directory no longer set #SBATCH --mem or
# --gres by default. On some clusters memory isn't tracked as a schedulable
# resource at all (check with `scontrol show node <name>` — if RealMemory
# is a nonsense value like 1, any --mem request will be rejected no matter
# how small) and/or GPUs aren't registered as a Slurm generic resource
# (Gres=(null) on what should be GPU nodes). If that's your situation too,
# leave CPU_MEM/GPU_MEM unset and set USE_GPU=0 in cluster_env.sh — jobs
# will run unconstrained by Slurm's memory accounting either way.
#
# Tip: rather than retyping/pasting these overrides (multi-line commands
# with backslash continuations are fragile through copy-paste), copy
# cluster_env.example.sh to cluster_env.sh, fill in your real values, and
# source it first:
#   cp cluster_env.example.sh cluster_env.sh && $EDITOR cluster_env.sh
#   source cluster_env.sh && ./submit_pipeline.sh config.yaml
#===============================================================================

set -euo pipefail

CONFIG="${1:-config.yaml}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Leave these empty to keep whatever partition/memory is hardcoded in each
# .slurm file; set them to override on the command line, e.g.:
#   CPU_PARTITION=batch GPU_PARTITION=gpu-a100 CPU_MEM=32G GPU_MEM=48G ./submit_pipeline.sh config.yaml
CPU_PARTITION="${CPU_PARTITION:-}"
GPU_PARTITION="${GPU_PARTITION:-}"
CPU_MEM="${CPU_MEM:-}"
GPU_MEM="${GPU_MEM:-}"
USE_GPU="${USE_GPU:-1}"                     # set to 0 to submit transcribe on CPU_PARTITION, no --gres

# Ordered list of (step_name, script_file, resource_class) triples —
# step/script ordering must match run_pipeline.py's PIPELINE_CMDS.
# resource_class is "gpu" for the one stage that wants a GPU, "cpu" for
# everything else.
STEPS=(
    "transcribe:01_transcribe.slurm:gpu"
    "keyframes:02_keyframes.slurm:cpu"
    "describe_frames:03_describe_frames.slurm:cpu"
    "summarize:04_summarize.slurm:cpu"
    "tags:05_tags.slurm:cpu"
    "accessibility:06_accessibility.slurm:cpu"
    "collection_report:07_collection_report.slurm:cpu"
    "preview:08_preview.slurm:cpu"
    "quality_metrics:09_quality_metrics.slurm:cpu"
    "iiif:10_iiif.slurm:cpu"
    "catalog_export:11_catalog_export.slurm:cpu"
    "search_index:12_search_index.slurm:cpu"
    "clustering:13_clustering.slurm:cpu"
)

SKIP_STEPS="${SKIP_STEPS:-}"
ONLY_STEPS="${ONLY_STEPS:-}"

should_run () {
    local step="$1"
    if [[ -n "${ONLY_STEPS}" ]]; then
        [[ " ${ONLY_STEPS} " == *" ${step} "* ]] && return 0 || return 1
    fi
    if [[ -n "${SKIP_STEPS}" ]] && [[ " ${SKIP_STEPS} " == *" ${step} "* ]]; then
        return 1
    fi
    return 0
}

mkdir -p "${SCRIPT_DIR}/../logs"

prev_jobid=""
submitted_any=0

for entry in "${STEPS[@]}"; do
    IFS=':' read -r step script resource_class <<< "${entry}"

    if ! should_run "${step}"; then
        echo "Skipping ${step} (${script})"
        continue
    fi

    extra_flags=()
    if [[ "${resource_class}" == "gpu" ]]; then
        if [[ "${USE_GPU}" == "0" ]]; then
            # Run transcription on CPU: use CPU_PARTITION/CPU_MEM (or the
            # file's defaults) and strip the GPU resource request.
            extra_flags+=(--gres=none)
            [[ -n "${CPU_PARTITION}" ]] && extra_flags+=(--partition="${CPU_PARTITION}")
            [[ -n "${CPU_MEM}" ]] && extra_flags+=(--mem="${CPU_MEM}")
        else
            [[ -n "${GPU_PARTITION}" ]] && extra_flags+=(--partition="${GPU_PARTITION}")
            [[ -n "${GPU_MEM}" ]] && extra_flags+=(--mem="${GPU_MEM}")
        fi
    else
        [[ -n "${CPU_PARTITION}" ]] && extra_flags+=(--partition="${CPU_PARTITION}")
        [[ -n "${CPU_MEM}" ]] && extra_flags+=(--mem="${CPU_MEM}")
    fi

    cmd=(sbatch --parsable)
    [[ -n "${prev_jobid}" ]] && cmd+=(--dependency=afterok:"${prev_jobid}")
    cmd+=("${extra_flags[@]}" "${SCRIPT_DIR}/${script}" "${CONFIG}")

    echo "+ ${cmd[*]}" >&2
    jobid=$("${cmd[@]}")

    echo "Submitted ${step} (${script}) as job ${jobid}$( [[ -n "${prev_jobid}" ]] && echo " (after ${prev_jobid})")"
    prev_jobid="${jobid}"
    submitted_any=1
done

if [[ "${submitted_any}" -eq 0 ]]; then
    echo "No steps submitted — check ONLY_STEPS/SKIP_STEPS." >&2
    exit 1
fi

echo "Final job in chain: ${prev_jobid}"
echo "Track the whole chain with: squeue -u \$USER"
echo "Or watch it live with:      watch -n 30 squeue -u \$USER"
