#!/bin/bash
#===============================================================================
# cluster_env.sh — settings for this cluster (defq / gpuq)
#
# This cluster does not track memory as a schedulable Slurm resource
# (every node reports RealMemory=1) and does not register GPUs as a Slurm
# generic resource on the gpuq nodes (Gres=(null)), so CPU_MEM/GPU_MEM are
# left unset (no --mem is sent at all) and USE_GPU=0 (transcription runs
# on defq like everything else, with no --gres request).
#===============================================================================

export CPU_PARTITION=defq
export GPU_PARTITION=gpuq     # unused while USE_GPU=0, kept here in case that changes
export CPU_MEM=               # intentionally empty — see note above
export GPU_MEM=               # intentionally empty — see note above
export USE_GPU=0              # gpuq nodes have no Slurm-visible Gres; run transcribe on CPU

echo "Loaded cluster settings: partition=${CPU_PARTITION} (GPU disabled — USE_GPU=${USE_GPU}), no --mem requested"
