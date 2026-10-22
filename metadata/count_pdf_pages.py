#!/usr/bin/env python3
"""
Count the pages in every PDF in a directory and export the results to a CSV.

Output columns:
    File Name        - the PDF's file name (or relative path when --recursive is used)
    Number of Pages  - formatted as text, e.g. "12 pages"

Requires: pypdf  (install with: pip install pypdf)

Usage:
    python count_pdf_pages.py "C:/path/to/pdfs"
    python count_pdf_pages.py ./pdfs -o page_counts.csv
    python count_pdf_pages.py ./pdfs --recursive
"""

import argparse
import csv
import sys
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    sys.exit("pypdf is not installed. Run: pip install pypdf")


def count_pages(pdf_path: Path) -> str:
    """Return the page count formatted as 'X pages', or an error note."""
    try:
        reader = PdfReader(pdf_path)
        if reader.is_encrypted:
            # Many "encrypted" PDFs only have an owner password; try an empty user password.
            try:
                reader.decrypt("")
            except Exception:
                return "Error: password protected"
        return f"{len(reader.pages)} pages"
    except Exception as exc:
        return f"Error: {exc.__class__.__name__}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Count pages in each PDF in a directory.")
    parser.add_argument("directory", help="Folder containing the PDF files")
    parser.add_argument("-o", "--output", default="pdf_page_counts.csv",
                        help="Output CSV path (default: pdf_page_counts.csv)")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="Include PDFs in subfolders")
    args = parser.parse_args()

    folder = Path(args.directory).expanduser()
    if not folder.is_dir():
        sys.exit(f"Not a directory: {folder}")

    pattern = "**/*" if args.recursive else "*"
    pdfs = sorted(
        (p for p in folder.glob(pattern) if p.is_file() and p.suffix.lower() == ".pdf"),
        key=lambda p: str(p).lower(),
    )

    if not pdfs:
        print(f"No PDF files found in {folder}")
        return

    # utf-8-sig so Excel opens non-English file names correctly
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["File Name", "Number of Pages"])
        for pdf in pdfs:
            name = str(pdf.relative_to(folder)) if args.recursive else pdf.name
            result = count_pages(pdf)
            writer.writerow([name, result])
            print(f"{name}: {result}")

    print(f"\nProcessed {len(pdfs)} PDF(s). Results saved to {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
