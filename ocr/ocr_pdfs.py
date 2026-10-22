#!/usr/bin/env python3
"""
Batch OCR PDFs in a directory using ocrmypdf.

For every .pdf file in the input directory, this creates a searchable
copy named "<original_name>_ocr.pdf" in the output directory (defaults
to the same directory as the input).

Requirements:
    pip install ocrmypdf --break-system-packages
    (ocrmypdf also requires the Tesseract OCR engine and Ghostscript
    to be installed on the system, e.g. `apt install tesseract-ocr ghostscript`)

Usage:
    python ocr_pdfs.py /path/to/pdf_directory
    python ocr_pdfs.py /path/to/pdf_directory --output-dir /path/to/output
    python ocr_pdfs.py /path/to/pdf_directory --language eng+fra --force
"""

import argparse
import logging
import sys
from pathlib import Path

try:
    import ocrmypdf
except ImportError:
    print(
        "Error: the 'ocrmypdf' package is not installed.\n"
        "Install it with: pip install ocrmypdf --break-system-packages",
        file=sys.stderr,
    )
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("ocr_pdfs")


def find_pdfs(input_dir: Path, recursive: bool) -> list[Path]:
    """Return all PDF files in input_dir, skipping any already-OCR'd output."""
    pattern = "**/*.pdf" if recursive else "*.pdf"
    pdfs = []
    for path in sorted(input_dir.glob(pattern)):
        if path.stem.endswith("_ocr"):
            continue  # don't re-process files we already generated
        pdfs.append(path)
    return pdfs


def ocr_file(
    src: Path,
    output_dir: Path,
    language: str,
    force: bool,
    skip_text: bool,
) -> bool:
    """Run OCR on a single PDF. Returns True on success."""
    dest = output_dir / f"{src.stem}_ocr.pdf"

    if dest.exists() and not force:
        logger.info(f"SKIP (already exists): {dest.name}")
        return True

    logger.info(f"Processing: {src.name} -> {dest.name}")
    try:
        ocrmypdf.ocr(
            src,
            dest,
            language=language,
            deskew=True,
            skip_text=skip_text,  # keep existing text layers, OCR only image pages
            force_ocr=force,      # if True, re-OCR everything (rasterizes existing text)
            progress_bar=False,
        )
        logger.info(f"  Done: {dest}")
        return True
    except ocrmypdf.exceptions.PriorOcrFoundError:
        logger.warning(
            f"  Skipped: {src.name} already contains a text layer "
            f"(use --force to re-OCR, or --skip-text to keep it as-is)"
        )
        return False
    except Exception as e:
        logger.error(f"  Failed: {src.name} ({e})")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Batch OCR PDFs in a directory using ocrmypdf, "
        "producing searchable '<name>_ocr.pdf' copies."
    )
    parser.add_argument(
        "input_dir", type=Path, help="Directory containing PDF files to OCR"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write OCR'd PDFs to (default: same as input_dir)",
    )
    parser.add_argument(
        "--language",
        "-l",
        default="eng",
        help="Tesseract language code(s), e.g. 'eng' or 'eng+fra' (default: eng)",
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        help="Search subdirectories recursively",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-OCR even if a PDF already has a text layer "
        "(rasterizes and replaces existing text)",
    )
    parser.add_argument(
        "--skip-text",
        action="store_true",
        help="Leave pages that already have text alone; only OCR image-only pages",
    )
    args = parser.parse_args()

    if not args.input_dir.is_dir():
        logger.error(f"Not a directory: {args.input_dir}")
        sys.exit(1)

    output_dir = args.output_dir or args.input_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    pdfs = find_pdfs(args.input_dir, args.recursive)
    if not pdfs:
        logger.info("No PDF files found.")
        return

    logger.info(f"Found {len(pdfs)} PDF file(s) to process.\n")

    successes = 0
    for pdf in pdfs:
        if ocr_file(pdf, output_dir, args.language, args.force, args.skip_text):
            successes += 1

    logger.info(f"\nFinished: {successes}/{len(pdfs)} file(s) processed successfully.")


if __name__ == "__main__":
    main()
