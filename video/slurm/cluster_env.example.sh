#!/bin/bash
#===============================================================================
# cluster_env.example.sh
#
# Copy this to cluster_env.sh, fill in your cluster's real values (from
# `sinfo -s` and `sinfo -o "%P %D %c %m %G"`), then source it before
# submitting so you never have to retype/paste these on the command line:
#
#   cp cluster_env.example.sh cluster_env.sh
#   $EDITOR cluster_env.sh
#   source cluster_env.sh
#   ./submit_pipeline.sh config.yaml
#
# cluster_env.sh is for your local cluster settings only — gitignore it
# alongside logs/ and .secrets/ rather than committing it.
#===============================================================================

export CPU_PARTITION=batch          # <-- your CPU partition name
export GPU_PARTITION=gpu            # <-- your GPU partition name (only used if USE_GPU=1)
export CPU_MEM=32G                  # <-- must fit your smallest CPU node; covers keyframes (heaviest CPU stage)
export GPU_MEM=48G                  # <-- must fit your GPU node's memory
export USE_GPU=1                    # <-- set to 0 if your cluster has no GPU nodes

echo "Loaded cluster settings: partition=${CPU_PARTITION}/${GPU_PARTITION}, mem=${CPU_MEM}/${GPU_MEM}, USE_GPU=${USE_GPU}"
