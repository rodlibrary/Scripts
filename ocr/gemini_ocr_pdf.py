#!/usr/bin/env python3
"""
gemini_ocr_pdf.py

Batch OCR a directory of PDF files using the Google Gemini API and produce
"searchable" PDFs: the original page graphics are left untouched, and an
invisible text layer is added on top so the PDF can be searched, copied
from, and indexed.

How it works
------------
For each page of each input PDF:
  1. The page is rendered to an image (via PyMuPDF).
  2. The image is sent to Gemini, which is asked to return every text
     block it can read, each with a bounding box normalized to a
     0-1000 scale (Gemini's standard spatial convention).
  3. The bounding boxes are converted back to PDF coordinates and the
     recognized text is written onto the page as invisible text
     (render mode 3), positioned and sized to roughly match the
     original glyphs.
  4. The page's original appearance is untouched, so the output PDF
     looks identical to the input but is now searchable.

Setup
-----
    pip install google-genai pymupdf pillow

    export GEMINI_API_KEY="your-api-key"

Usage
-----
    python gemini_ocr_pdf.py --input ./pdfs --output ./searchable_pdfs

    # Options
    python gemini_ocr_pdf.py \
        --input ./pdfs \
        --output ./searchable_pdfs \
        --model gemini-2.5-flash \
        --dpi 200 \
        --overwrite \
        --skip-existing

Notes
-----
- This performs OCR page-by-page (one Gemini request per page), which
  keeps prompts small and reliable. For very large PDFs this means one
  API call per page -- budget your API quota accordingly.
- The invisible text is positioned well enough for search/copy/select,
  but it is a best-effort reconstruction, not pixel-perfect glyph
  placement (Gemini gives bounding boxes, not exact font metrics).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("Missing dependency: pip install pymupdf")

try:
    from google import genai
    from google.genai import types
except ImportError:
    sys.exit("Missing dependency: pip install google-genai")


OCR_PROMPT = """You are an OCR engine. Look at this scanned document page image
and extract every piece of text you can find (paragraphs, headings, labels,
table cells, captions, footnotes, page numbers -- everything).

Return a JSON array. Each element must be an object with:
  - "text": the exact text of that block, preserving reading order
  - "box_2d": [ymin, xmin, ymax, xmax], the bounding box of that text,
    with each value an integer normalized to a 0-1000 scale relative to
    the image (0,0 = top-left corner, 1000,1000 = bottom-right corner)

Break text into reasonably small blocks (lines or short paragraphs) rather
than one giant block for the whole page, so bounding boxes stay accurate.
If the page has no legible text, return an empty JSON array: []
Return ONLY the JSON array, no markdown fences, no commentary.
"""

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "text": {"type": "STRING"},
            "box_2d": {
                "type": "ARRAY",
                "items": {"type": "INTEGER"},
            },
        },
        "required": ["text", "box_2d"],
    },
}


def get_client(api_key: str | None) -> "genai.Client":
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit(
            "No API key found. Pass --api-key or set the GEMINI_API_KEY "
            "environment variable."
        )
    return genai.Client(api_key=key)


def render_page_png(page: "fitz.Page", dpi: int) -> bytes:
    """Render a PDF page to PNG bytes at the given DPI."""
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    return pix.tobytes("png")


def ocr_page_with_gemini(
    client: "genai.Client",
    model: str,
    image_bytes: bytes,
    max_retries: int = 4,
) -> list[dict]:
    """Send a page image to Gemini and return a list of {text, box_2d} dicts."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
        temperature=0,
    )

    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                    OCR_PROMPT,
                ],
                config=config,
            )
            raw = response.text.strip()
            # Defensive cleanup in case the model wraps the JSON in fences.
            if raw.startswith("```"):
                raw = raw.strip("`")
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw
                if raw.lower().startswith("json"):
                    raw = raw[4:]
            blocks = json.loads(raw)
            if not isinstance(blocks, list):
                return []
            return blocks
        except Exception as exc:  # noqa: BLE001 - retry on any transient issue
            last_error = exc
            wait = 2**attempt
            print(f"    ! Gemini request failed ({exc}); retrying in {wait}s...")
            time.sleep(wait)

    print(f"    ! Giving up on this page after {max_retries} attempts: {last_error}")
    return []


def add_invisible_text_layer(
    page: "fitz.Page", blocks: list[dict]
) -> None:
    """Overlay invisible, searchable text on a page from normalized boxes."""
    page_w, page_h = page.rect.width, page.rect.height

    for block in blocks:
        text = (block.get("text") or "").strip()
        box = block.get("box_2d")
        if not text or not box or len(box) != 4:
            continue

        try:
            ymin, xmin, ymax, xmax = (float(v) for v in box)
        except (TypeError, ValueError):
            continue

        # Convert from 0-1000 normalized space to PDF point coordinates.
        x0 = (xmin / 1000.0) * page_w
        y0 = (ymin / 1000.0) * page_h
        x1 = (xmax / 1000.0) * page_w
        y1 = (ymax / 1000.0) * page_h

        box_w = max(x1 - x0, 1.0)
        box_h = max(y1 - y0, 1.0)
        if box_h <= 0 or box_w <= 0:
            continue

        # Fit the invisible text into the box: insert_textbox auto-shrinks
        # to fit width, so start from a fontsize based on box height.
        fontsize = max(min(box_h * 0.85, 72), 1)

        rect = fitz.Rect(x0, y0, x1, y1)
        try:
            page.insert_textbox(
                rect,
                text,
                fontsize=fontsize,
                fontname="helv",
                render_mode=3,  # invisible text
                align=fitz.TEXT_ALIGN_LEFT,
            )
        except Exception as exc:  # noqa: BLE001
            # Some boxes are pathological (zero area, huge text); skip them
            # rather than aborting the whole page.
            print(f"    ! Skipped a text block ({exc}): {text[:40]!r}")


def process_pdf(
    client: "genai.Client",
    model: str,
    src_path: Path,
    dst_path: Path,
    dpi: int,
) -> None:
    print(f"-> {src_path.name}")
    doc = fitz.open(src_path)

    for page_index in range(len(doc)):
        page = doc[page_index]
        print(f"   page {page_index + 1}/{len(doc)}: rendering + OCR...")
        image_bytes = render_page_png(page, dpi)
        blocks = ocr_page_with_gemini(client, model, image_bytes)
        add_invisible_text_layer(page, blocks)

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dst_path, garbage=4, deflate=True)
    doc.close()
    print(f"   saved: {dst_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OCR a directory of PDFs with Gemini and produce searchable PDFs."
    )
    parser.add_argument("--input", required=True, help="Directory of input PDFs.")
    parser.add_argument(
        "--output", required=True, help="Directory to write searchable PDFs to."
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Gemini API key. Defaults to the GEMINI_API_KEY environment variable.",
    )
    parser.add_argument(
        "--model",
        default="gemini-2.5-flash",
        help="Gemini model to use (default: gemini-2.5-flash).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Rendering resolution for pages sent to Gemini (default: 200).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip files that already exist in the output directory.",
    )
    args = parser.parse_args()

    input_dir = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()

    if not input_dir.is_dir():
        sys.exit(f"Input directory not found: {input_dir}")

    pdf_paths = sorted(input_dir.glob("*.pdf")) + sorted(input_dir.glob("*.PDF"))
    pdf_paths = sorted(set(pdf_paths))
    if not pdf_paths:
        sys.exit(f"No PDF files found in {input_dir}")

    client = get_client(args.api_key)

    print(f"Found {len(pdf_paths)} PDF(s) in {input_dir}")
    print(f"Model: {args.model}  |  Render DPI: {args.dpi}")
    print(f"Output: {output_dir}\n")

    failures = []
    for src_path in pdf_paths:
        dst_path = output_dir / src_path.name
        if args.skip_existing and dst_path.exists():
            print(f"-> {src_path.name} (skipped, already exists)")
            continue
        try:
            process_pdf(client, args.model, src_path, dst_path, args.dpi)
        except Exception as exc:  # noqa: BLE001 - keep batch going
            print(f"   ! FAILED: {exc}")
            failures.append(src_path.name)
        print()

    print("Done.")
    if failures:
        print(f"{len(failures)} file(s) failed: {', '.join(failures)}")


if __name__ == "__main__":
    main()
