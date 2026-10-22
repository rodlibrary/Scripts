#!/usr/bin/env python3
"""
batch_ocr.py

Run Tesseract OCR over a directory of image files and produce, for each
input file, four output formats:
    - .txt        (plain text)
    - .pdf        (searchable PDF)
    - .hocr       (hOCR, saved with .hocr extension)
    - .xml        (ALTO XML)

Requirements:
    - Tesseract OCR must be installed and on PATH (https://github.com/tesseract-ocr/tesseract)
      e.g. on Debian/Ubuntu: sudo apt-get install tesseract-ocr
    - Optional: additional language packs, e.g. tesseract-ocr-fra

Usage:
    python batch_ocr.py -i /path/to/input_dir -o /path/to/output_dir
    python batch_ocr.py -i ./scans -o ./ocr_out -l eng+fra --dpi 300
    python batch_ocr.py -i ./scans -o ./ocr_out --workers 4

Notes:
    - Input files are matched by extension (see DEFAULT_EXTENSIONS below).
    - Multi-page TIFFs and PDFs are supported directly by Tesseract.
    - Each output format is written into its own subfolder under the
      output directory: txt/, pdf/, hocr/, alto/
"""

import argparse
import concurrent.futures
import logging
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".pnm", ".pgm",
    ".ppm", ".webp", ".pdf",
}

# Maps a human-friendly name to (tesseract configname, output file suffix, subfolder)
OUTPUT_FORMATS = {
    "txt":  {"config": "txt",  "suffix": ".txt",  "subdir": "txt"},
    "pdf":  {"config": "pdf",  "suffix": ".pdf",  "subdir": "pdf"},
    "hocr": {"config": "hocr", "suffix": ".hocr", "subdir": "hocr"},
    "alto": {"config": "alto", "suffix": ".xml",  "subdir": "alto"},
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("batch_ocr")


def check_tesseract_available():
    """Verify tesseract is installed and reachable on PATH."""
    if shutil.which("tesseract") is None:
        log.error(
            "Tesseract executable not found on PATH. Install it first, e.g.\n"
            "  Ubuntu/Debian: sudo apt-get install tesseract-ocr\n"
            "  macOS (Homebrew): brew install tesseract\n"
            "  Windows: https://github.com/UB-Mannheim/tesseract/wiki"
        )
        sys.exit(1)


def discover_input_files(input_dir: Path, extensions: set, recursive: bool):
    """Find candidate files in the input directory."""
    pattern = "**/*" if recursive else "*"
    files = [
        p for p in input_dir.glob(pattern)
        if p.is_file() and p.suffix.lower() in extensions
    ]
    return sorted(files)


def ensure_output_dirs(output_dir: Path):
    """Create one subdirectory per output format."""
    for fmt in OUTPUT_FORMATS.values():
        (output_dir / fmt["subdir"]).mkdir(parents=True, exist_ok=True)


def run_tesseract(
    input_file: Path,
    output_dir: Path,
    lang: str,
    dpi: int,
    oem: int,
    psm: int,
    extra_config: list,
):
    """
    Run a single tesseract invocation that produces all four output
    formats at once (tesseract supports multiple output config names
    in a single call).

    Tesseract's base output naming pattern is:
        tesseract <input> <outputbase> [options] [configfiles...]
    This produces <outputbase>.<ext> for each configfile requested
    (txt -> .txt, pdf -> .pdf, hocr -> .hocr, alto -> .xml).

    Because each format has its own subdirectory, we invoke tesseract
    once per format so files land directly in the right place. This is
    slightly more repeated OCR work but keeps things simple, avoids
    filename collisions, and lets one format's failure not block others.
    """
    stem = input_file.stem
    results = {}

    for name, fmt in OUTPUT_FORMATS.items():
        out_subdir = output_dir / fmt["subdir"]
        out_base = out_subdir / stem  # tesseract appends the correct extension

        cmd = ["tesseract", str(input_file), str(out_base)]
        cmd += ["-l", lang, "--dpi", str(dpi), "--oem", str(oem), "--psm", str(psm)]
        cmd += extra_config
        cmd += [fmt["config"]]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, check=False
            )
            expected_output = out_base.with_suffix(fmt["suffix"])
            if proc.returncode != 0 or not expected_output.exists():
                log.error(
                    "  [%s] FAILED for %s: %s",
                    name, input_file.name, proc.stderr.strip() or "unknown error"
                )
                results[name] = False
            else:
                results[name] = True
        except FileNotFoundError:
            log.error("Tesseract executable disappeared mid-run.")
            results[name] = False

    return input_file, results


def main():
    parser = argparse.ArgumentParser(
        description="Batch OCR a directory of files with Tesseract, "
                     "producing .txt, .pdf, .hocr, and ALTO .xml outputs."
    )
    parser.add_argument("-i", "--input-dir", required=True, type=Path,
                         help="Directory containing files to OCR.")
    parser.add_argument("-o", "--output-dir", required=True, type=Path,
                         help="Directory to write output files into "
                              "(subfolders txt/, pdf/, hocr/, alto/ are created).")
    parser.add_argument("-l", "--lang", default="eng",
                         help="Tesseract language(s), e.g. 'eng' or 'eng+fra'. Default: eng")
    parser.add_argument("--dpi", type=int, default=300,
                         help="DPI hint for tesseract (useful for images without DPI metadata). Default: 300")
    parser.add_argument("--oem", type=int, default=3,
                         help="OCR Engine Mode (0-3). Default: 3 (default engine, LSTM+legacy).")
    parser.add_argument("--psm", type=int, default=3,
                         help="Page Segmentation Mode (0-13). Default: 3 (fully automatic).")
    parser.add_argument("--extensions", nargs="*", default=None,
                         help="Override list of file extensions to process, "
                              "e.g. --extensions .png .tif .pdf")
    parser.add_argument("--recursive", action="store_true",
                         help="Recurse into subdirectories of the input directory.")
    parser.add_argument("--workers", type=int, default=1,
                         help="Number of files to process in parallel. Default: 1 (sequential).")
    parser.add_argument("--config", nargs="*", default=[],
                         help="Extra tesseract config options, e.g. --config txt_config.txt")

    args = parser.parse_args()

    check_tesseract_available()

    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not input_dir.is_dir():
        log.error("Input directory does not exist: %s", input_dir)
        sys.exit(1)

    extensions = (
        {e.lower() if e.startswith(".") else f".{e.lower()}" for e in args.extensions}
        if args.extensions else DEFAULT_EXTENSIONS
    )

    files = discover_input_files(input_dir, extensions, args.recursive)
    if not files:
        log.warning("No matching files found in %s (extensions: %s)",
                     input_dir, sorted(extensions))
        sys.exit(0)

    log.info("Found %d file(s) to OCR.", len(files))
    ensure_output_dirs(output_dir)

    summary = {"ok": 0, "partial": 0, "failed": 0}

    if args.workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    run_tesseract, f, output_dir, args.lang, args.dpi,
                    args.oem, args.psm, args.config
                ): f
                for f in files
            }
            for future in concurrent.futures.as_completed(futures):
                f = futures[future]
                try:
                    _, results = future.result()
                    _tally(f, results, summary)
                except Exception as exc:
                    log.error("Unhandled error processing %s: %s", f.name, exc)
                    summary["failed"] += 1
    else:
        for f in files:
            log.info("Processing: %s", f.name)
            _, results = run_tesseract(
                f, output_dir, args.lang, args.dpi, args.oem, args.psm, args.config
            )
            _tally(f, results, summary)

    log.info(
        "Done. %d fully succeeded, %d partially succeeded, %d fully failed.",
        summary["ok"], summary["partial"], summary["failed"],
    )
    log.info("Outputs written under: %s", output_dir)


def _tally(f, results, summary):
    n_ok = sum(1 for v in results.values() if v)
    if n_ok == len(results):
        summary["ok"] += 1
        log.info("  -> %s: all formats OK", f.name)
    elif n_ok == 0:
        summary["failed"] += 1
        log.error("  -> %s: all formats FAILED", f.name)
    else:
        summary["partial"] += 1
        ok_list = [k for k, v in results.items() if v]
        log.warning("  -> %s: partial success (%s)", f.name, ", ".join(ok_list))


if __name__ == "__main__":
    main()
