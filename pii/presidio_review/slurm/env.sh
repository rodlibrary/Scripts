# shellcheck shell=bash
# Shared settings for the presidio_review Slurm scripts.
# Sourced by setup_env.sh, analyze.script and anonymize.script; not run directly.
#
# EDIT the four settings below for your cluster. Each can also be overridden for
# one run from the command line, e.g.  PYTHON_MODULE=python/3.12 bash slurm/setup_env.sh
#
# If your cluster's storage isn't /data/$USER, also change the #SBATCH --output and
# --error lines at the top of analyze.script and anonymize.script.

: "${USER:=$(id -un)}"

# Where documents, review CSVs, redacted copies and logs live. Must be storage the
# compute nodes can see, and approved for PII.
DATA="${DATA:-/data/$USER/presidio_review}"

# The Python virtual environment (created by setup_env.sh).
VENV="${VENV:-/data/$USER/presidio_review_venv}"

# Python module to load, if your cluster provides Python through modules
# (find it with `module avail python` or `module spider python`). Presidio needs
# Python 3.11-3.14. Leave empty to use the system python3.
PYTHON_MODULE="${PYTHON_MODULE:-}"

# Tesseract: either a module name (`module avail tesseract`), or leave empty and
# setup_env.sh installs Tesseract into TESSERACT_PREFIX without root access.
TESSERACT_MODULE="${TESSERACT_MODULE:-}"
TESSERACT_PREFIX="${TESSERACT_PREFIX:-/data/$USER/tesseract}"

# ---------------------------------------------------------------------------

load_env() {
    # Loads the Python and Tesseract modules (if set) and puts a self-installed
    # Tesseract on PATH. Does not activate the venv.
    if [ -n "$PYTHON_MODULE" ] || [ -n "$TESSERACT_MODULE" ]; then
        if ! command -v module >/dev/null 2>&1; then
            echo "ERROR: PYTHON_MODULE/TESSERACT_MODULE are set in slurm/env.sh but the" >&2
            echo "       'module' command isn't available on this node." >&2
            return 1
        fi
        [ -n "$PYTHON_MODULE" ] && { module load "$PYTHON_MODULE" || return 1; }
        [ -n "$TESSERACT_MODULE" ] && { module load "$TESSERACT_MODULE" || return 1; }
    fi
    if [ -z "$TESSERACT_MODULE" ] && [ -x "$TESSERACT_PREFIX/bin/tesseract" ]; then
        export PATH="$TESSERACT_PREFIX/bin:$PATH"
    fi
    return 0
}

activate_venv() {
    if [ ! -f "$VENV/bin/activate" ]; then
        echo "ERROR: no virtual environment at $VENV." >&2
        echo "       Run 'bash slurm/setup_env.sh' on a login node first." >&2
        return 1
    fi
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
}

preflight() {
    # Usage: preflight <ocr-langs or "off">
    # Fails fast, with a clear message, before any work starts.
    local ocr_langs="$1"
    echo "Python: $(command -v python)"
    python - "$VENV" <<'EOF' || return 1
import importlib, sys
if not ((3, 11) <= sys.version_info[:2] < (3, 15)):
    sys.exit(f"ERROR: Python {sys.version.split()[0]} found; Presidio needs 3.11-3.14. "
             "Set PYTHON_MODULE in slurm/env.sh and re-run setup_env.sh.")
needed = {                      # import name -> pip package name
    "presidio_analyzer": "presidio-analyzer",
    "presidio_anonymizer": "presidio-anonymizer",
    "presidio_structured": "presidio-structured",
    "spacy": "spacy",
    "pandas": "pandas",
    "pdfminer": "pdfminer.six",
    "pypdfium2": "pypdfium2",
    "PIL": "Pillow",
    "docx": "python-docx",
    "openpyxl": "openpyxl",
    "pytesseract": "pytesseract",
}
missing = []
for module, package in needed.items():
    try:
        importlib.import_module(module)
    except ImportError:
        missing.append(package)
if missing:
    print(f"ERROR: not installed in {sys.argv[1]}: {', '.join(missing)}", file=sys.stderr)
    sys.exit("       Re-run 'bash slurm/setup_env.sh' on a login node.")
import spacy
if not spacy.util.is_package("en_core_web_lg"):
    sys.exit("ERROR: spaCy model en_core_web_lg is not installed. On a login node run:\n"
             f"       source {sys.argv[1]}/bin/activate && python -m spacy download en_core_web_lg")
print(f"Python {sys.version.split()[0]}; all required packages found.")
EOF
    if [ "$ocr_langs" = "off" ]; then
        echo "OCR: off (Tesseract not checked)"
        return 0
    fi
    if ! command -v tesseract >/dev/null 2>&1; then
        echo "ERROR: tesseract not found. Set TESSERACT_MODULE in slurm/env.sh, or run" >&2
        echo "       'bash slurm/setup_env.sh' to install it into $TESSERACT_PREFIX." >&2
        echo "       (Or pass --ocr off to skip scans and images.)" >&2
        return 1
    fi
    local have lang
    have="$(tesseract --list-langs 2>/dev/null | tail -n +2)"
    for lang in osd ${ocr_langs//+/ }; do
        if ! grep -qx "$lang" <<< "$have"; then
            echo "ERROR: Tesseract language data '$lang' is missing. See 'Tesseract languages'" >&2
            echo "       in HPC.md. Installed: $(echo $have)" >&2
            return 1
        fi
    done
    echo "Tesseract: $(tesseract --version 2>&1 | head -1); languages: $(echo $have)"
}

ocr_langs_from_args() {
    # Prints "off" if the arguments contain --ocr off, otherwise the --ocr-lang value
    # (default eng).
    local langs="eng" prev=""
    for a in "$@"; do
        case "$prev" in
            --ocr) [ "$a" = "off" ] && { echo off; return; } ;;
            --ocr-lang) langs="$a" ;;
        esac
        case "$a" in
            --ocr=off) echo off; return ;;
            --ocr-lang=*) langs="${a#--ocr-lang=}" ;;
        esac
        prev="$a"
    done
    echo "$langs"
}
