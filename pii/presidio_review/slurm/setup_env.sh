#!/bin/bash
# One-time setup for running presidio_review on the cluster.
# Run on a LOGIN node (compute nodes often have no internet), from the
# presidio_review folder:
#
#     bash slurm/setup_env.sh
#
# Safe to re-run: it reuses what's already installed and upgrades nothing it
# doesn't have to. Settings (Python module, paths) are in slurm/env.sh.
#
# What it does:
#   1. creates the Python virtual environment at $VENV
#   2. installs requirements.txt and the spaCy model en_core_web_lg into it
#   3. finds Tesseract (module or PATH), or installs it into $TESSERACT_PREFIX
#      from conda-forge with micromamba (no root needed; skip with SKIP_TESSERACT=1)
#   4. creates $DATA/{docs,review,redacted,logs}, readable only by you
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
# shellcheck source=env.sh
source "$HERE/env.sh"

if [ -n "${SLURM_JOB_ID:-}" ]; then
    echo "NOTE: running inside a Slurm job. If this node has no internet access the"
    echo "      downloads below will fail; run on a login node instead."
fi

load_env

# --- 1. Virtual environment ---------------------------------------------------
PY="$(command -v python3 || true)"
[ -z "$PY" ] && { echo "ERROR: python3 not found. Set PYTHON_MODULE in slurm/env.sh." >&2; exit 1; }
if ! "$PY" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 1)'; then
    echo "ERROR: $PY is Python $("$PY" -c 'import sys; print(sys.version.split()[0])');" >&2
    echo "       Presidio needs 3.11-3.14. Find one with 'module avail python' and set" >&2
    echo "       PYTHON_MODULE in slurm/env.sh." >&2
    exit 1
fi

if [ ! -f "$VENV/bin/activate" ]; then
    echo "== Creating virtual environment $VENV with $PY"
    "$PY" -m venv "$VENV"
else
    echo "== Using existing virtual environment $VENV"
fi
activate_venv

# Keep pip's download cache out of the (often small) home directory quota.
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$(dirname "$VENV")/.pip_cache_$USER}"

# --- 2. Python packages and spaCy model ---------------------------------------
echo "== Installing Python packages"
python -m pip install --upgrade pip
python -m pip install -r "$REPO/requirements.txt"

if python -c 'import spacy, sys; sys.exit(0 if spacy.util.is_package("en_core_web_lg") else 1)'; then
    echo "== spaCy model en_core_web_lg already installed"
else
    echo "== Downloading spaCy model en_core_web_lg (~400 MB)"
    python -m spacy download en_core_web_lg
fi

# --- 3. Tesseract -------------------------------------------------------------
if command -v tesseract >/dev/null 2>&1; then
    echo "== Tesseract found: $(command -v tesseract)"
elif [ "${SKIP_TESSERACT:-0}" = "1" ]; then
    echo "== SKIP_TESSERACT=1: not installing Tesseract (use --ocr off when analyzing)"
else
    echo "== Tesseract not found; installing it into $TESSERACT_PREFIX from conda-forge"
    MAMBA_DIR="$(dirname "$TESSERACT_PREFIX")/.micromamba_$USER"
    MAMBA="$MAMBA_DIR/bin/micromamba"
    if [ ! -x "$MAMBA" ]; then
        mkdir -p "$MAMBA_DIR"
        curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
            | tar -xj -C "$MAMBA_DIR" bin/micromamba
    fi
    export MAMBA_ROOT_PREFIX="$MAMBA_DIR"
    "$MAMBA" create -y -p "$TESSERACT_PREFIX" -c conda-forge tesseract
    export PATH="$TESSERACT_PREFIX/bin:$PATH"
fi

# --- 4. Data folders ----------------------------------------------------------
mkdir -p "$DATA"/docs "$DATA"/review "$DATA"/redacted "$DATA"/logs
chmod 700 "$DATA"
echo "== Data folder: $DATA (permissions: owner only)"

# --- Check --------------------------------------------------------------------
echo "== Checking the installation"
preflight eng

echo
echo "Setup complete. Next:"
echo "  1. Copy documents into $DATA/docs"
echo "  2. From $REPO run:  sbatch slurm/analyze.script"
echo "  (Optional test on the sample files: sbatch slurm/analyze.script input=$REPO/sample)"
