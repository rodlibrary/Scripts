#!/usr/bin/env python3
"""
Build a searchable PDF from images + matching transcription text files.

For a given directory, this script finds image files (.tif, .tiff, .jpg, .jpeg)
and pairs each one with a same-named .txt file (e.g. "page01.jpg" pairs with
"page01.txt") containing the handwriting transcription (e.g. from Gemini).
Each image becomes one PDF page, rendered visibly, with the transcription text
placed on top as an invisible (but selectable/searchable) text layer.

Note: because the source text has no per-word coordinates, the invisible text
is laid out as wrapped lines covering the page rather than aligned word-for-word
with the handwriting. It won't highlight perfectly when selected, but it is
fully searchable and copyable.

Requires: reportlab, Pillow (both already available in this environment).

Usage:
    python generate_searchable_pdf.py /path/to/directory [-o output.pdf]
"""

import argparse
import sys
import textwrap
from pathlib import Path

from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

IMAGE_EXTENSIONS = {".tiff", ".tif", ".jpg", ".jpeg"}

DEFAULT_DPI = 150          # assumed resolution if image has none embedded
MAX_PAGE_POINTS = 2200     # cap page dimensions so huge scans stay a sane PDF page size
FONT_NAME = "Helvetica"
FONT_SIZE = 10
LEADING = 12
MARGIN = 24


def load_image_as_rgb(image_path: Path) -> Image.Image:
    """Open an image (handles multi-frame TIFFs) and normalize to RGB."""
    img = Image.open(image_path)
    img.seek(0)  # first frame/page only, for multi-page TIFFs
    if img.mode in ("CMYK", "P", "LA", "RGBA"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img


def compute_page_size(img: Image.Image):
    """Compute a PDF page size (in points) from the image's pixel size/DPI."""
    dpi_x, dpi_y = img.info.get("dpi", (DEFAULT_DPI, DEFAULT_DPI))
    dpi_x = dpi_x or DEFAULT_DPI
    dpi_y = dpi_y or DEFAULT_DPI

    width_pt = img.width * 72.0 / dpi_x
    height_pt = img.height * 72.0 / dpi_y

    # Scale down proportionally if either dimension is too large
    scale = min(1.0, MAX_PAGE_POINTS / width_pt, MAX_PAGE_POINTS / height_pt)
    return width_pt * scale, height_pt * scale


def add_invisible_text_layer(c: canvas.Canvas, text: str, page_width: float, page_height: float):
    """Overlay wrapped, invisible (render mode 3) text so the page is searchable."""
    if not text.strip():
        return

    # Estimate characters per line from average character width at this font size
    avg_char_width = FONT_SIZE * 0.5
    usable_width = page_width - 2 * MARGIN
    chars_per_line = max(10, int(usable_width / avg_char_width))

    wrapped_lines = []
    for paragraph in text.splitlines():
        if paragraph.strip():
            wrapped_lines.extend(textwrap.wrap(paragraph, width=chars_per_line))
        else:
            wrapped_lines.append("")

    text_obj = c.beginText(MARGIN, page_height - MARGIN)
    text_obj.setFont(FONT_NAME, FONT_SIZE)
    text_obj.setLeading(LEADING)
    text_obj.setTextRenderMode(3)  # invisible text, still selectable/searchable

    for line in wrapped_lines:
        text_obj.textLine(line)

    c.drawText(text_obj)


def build_pdf(directory: Path, output_path: Path):
    image_files = sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )

    if not image_files:
        print(f"Warning: no image files found in {directory}", file=sys.stderr)
        return 0

    c = canvas.Canvas(str(output_path))

    pages_written = 0
    for image_path in image_files:
        txt_path = image_path.with_suffix(".txt")

        if not txt_path.exists():
            print(f"Warning: missing transcription for '{image_path.name}' "
                  f"(expected '{txt_path.name}') — page added with image only",
                  file=sys.stderr)
            text = ""
        else:
            try:
                text = txt_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = txt_path.read_text(encoding="latin-1")
                print(f"Warning: '{txt_path.name}' was not UTF-8; read as latin-1",
                      file=sys.stderr)

        try:
            img = load_image_as_rgb(image_path)
        except Exception as e:
            print(f"Warning: could not open '{image_path.name}' ({e}); skipping",
                  file=sys.stderr)
            continue

        page_width, page_height = compute_page_size(img)
        c.setPageSize((page_width, page_height))

        c.drawImage(
            ImageReader(img), 0, 0,
            width=page_width, height=page_height,
            preserveAspectRatio=False,
        )

        add_invisible_text_layer(c, text, page_width, page_height)

        c.showPage()
        pages_written += 1

    c.save()
    return pages_written


def main():
    parser = argparse.ArgumentParser(
        description="Build a searchable PDF from images and matching transcription .txt files."
    )
    parser.add_argument("directory", type=str, help="Directory containing image and .txt files")
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Name (or path) of the output PDF file. "
             "If omitted, saves as 'searchable_output.pdf' inside the source directory. "
             "If just a filename is given, it is also saved inside the source directory."
    )
    args = parser.parse_args()

    directory = Path(args.directory).expanduser().resolve()

    if not directory.is_dir():
        print(f"Error: '{directory}' is not a valid directory.", file=sys.stderr)
        sys.exit(1)

    if args.output is None:
        output_path = directory / "searchable_output.pdf"
    else:
        output_arg = Path(args.output).expanduser()
        if output_arg.parent == Path("."):
            output_path = directory / output_arg.name
        else:
            output_path = output_arg.resolve()

    pages_written = build_pdf(directory, output_path)

    if pages_written:
        print(f"Wrote {pages_written} page(s) to {output_path}")
    else:
        print("No pages were written.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
