#!/usr/bin/env python3
"""
Batch OCR for .jp2 (JPEG 2000) images using pytesseract.

For every .jp2 image found (searched recursively), writes three output
files with the same base name:
    <name>.txt   - plain text OCR output
    <name>.xml   - ALTO XML OCR output (layout + word-level positions)
    <name>.pdf   - searchable PDF (original image + invisible text layer)

The directory structure of the input directory is mirrored under the
output directory, so images with the same filename in different
subdirectories won't collide.

Requirements:
    pip install pytesseract Pillow
    - Tesseract OCR engine must be installed on the system:
        Ubuntu/Debian: sudo apt-get install tesseract-ocr
        macOS (brew):  brew install tesseract
        Windows:       https://github.com/UB-Mannheim/tesseract/wiki
    - Pillow needs JPEG2000 support, which requires the OpenJPEG library
      (openjpeg / libopenjp2) to be present on the system. On Debian/Ubuntu:
        sudo apt-get install libopenjp2-7
    - ALTO XML output requires Tesseract 4.1+ (uses the built-in 'alto'
      configfile). PDF output requires Tesseract 3.05+.

Usage:
    python ocr_jp2.py /path/to/image_dir
    python ocr_jp2.py /path/to/image_dir --output-dir ocr_output
    python ocr_jp2.py /path/to/image_dir --lang eng --config "--psm 6"
"""

import argparse
import logging
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required. Install it with: pip install Pillow")

try:
    import pytesseract
except ImportError:
    sys.exit("pytesseract is required. Install it with: pip install pytesseract")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def find_jp2_files(directory: Path):
    """Recursively find all .jp2 files in the given directory."""
    files = directory.rglob("*.jp2")
    # Case-insensitive match on extension, sorted for deterministic order
    return sorted(
        {f for f in files if f.suffix.lower() == ".jp2"},
        key=lambda p: str(p).lower(),
    )


def ocr_image(path: Path, out_base: Path, lang: str = "eng", config: str = ""):
    """
    Run OCR on a single image and write .txt, .xml (ALTO), and .pdf
    outputs alongside the given out_base path (out_base has no extension).
    """
    with Image.open(path) as img:
        # Convert to RGB to avoid issues with palette/CMYK/etc. images
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")

        # Plain text
        text = pytesseract.image_to_string(img, lang=lang, config=config)
        out_base.with_suffix(".txt").write_text(text, encoding="utf-8")

        # ALTO XML
        alto_bytes = pytesseract.image_to_alto_xml(img, lang=lang, config=config)
        out_base.with_suffix(".xml").write_bytes(alto_bytes)

        # Searchable PDF
        pdf_bytes = pytesseract.image_to_pdf_or_hocr(
            img, lang=lang, config=config, extension="pdf"
        )
        out_base.with_suffix(".pdf").write_bytes(pdf_bytes)


def process_directory(
    input_dir: Path,
    output_dir: Path,
    lang: str = "eng",
    config: str = "",
):
    jp2_files = find_jp2_files(input_dir)

    if not jp2_files:
        log.warning("No .jp2 files found in %s (searched recursively)", input_dir)
        return

    log.info("Found %d .jp2 file(s) to process", len(jp2_files))

    successes, failures = 0, 0

    for i, path in enumerate(jp2_files, start=1):
        rel_path = path.relative_to(input_dir)
        log.info("[%d/%d] Processing %s", i, len(jp2_files), rel_path)

        out_base = output_dir / rel_path.with_suffix("")
        out_base.parent.mkdir(parents=True, exist_ok=True)

        try:
            ocr_image(path, out_base, lang=lang, config=config)
        except Exception as exc:
            log.error("Failed to OCR %s: %s", path, exc)
            failures += 1
            continue

        successes += 1

    log.info("Output written to %s", output_dir)
    log.info("Done. Success: %d, Failed: %d", successes, failures)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Recursively run OCR on a directory of .jp2 images using "
            "pytesseract, writing .txt, .xml (ALTO), and .pdf output "
            "per image."
        )
    )
    parser.add_argument(
        "input_dir", type=Path, help="Directory containing .jp2 images"
    )
    parser.add_argument(
        "--output-dir", "-d", type=Path, default=None,
        help=(
            "Directory to write output files into (mirrors input "
            "subdirectory structure). Defaults to '<input_dir>/ocr_output'."
        ),
    )
    parser.add_argument(
        "--lang", "-l", default="eng",
        help="Tesseract language code, e.g. 'eng', 'fra', 'deu' (default: eng)",
    )
    parser.add_argument(
        "--config", "-c", default="",
        help="Extra tesseract config string, e.g. '--psm 6'",
    )

    args = parser.parse_args()

    if not args.input_dir.is_dir():
        sys.exit(f"Error: {args.input_dir} is not a valid directory")

    output_dir = args.output_dir or (args.input_dir / "ocr_output")
    output_dir.mkdir(parents=True, exist_ok=True)

    process_directory(
        input_dir=args.input_dir,
        output_dir=output_dir,
        lang=args.lang,
        config=args.config,
    )


if __name__ == "__main__":
    main()