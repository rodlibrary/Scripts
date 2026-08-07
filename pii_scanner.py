#!/usr/bin/env python3
"""
pii_scanner.py

Scan a directory of PDF files for common PII patterns using pdfminer.six,
and write findings to a CSV file for manual review.

Detects (via regex, best-effort — always manually review results):
  - Email addresses
  - US-style phone numbers
  - Social Security Numbers (SSN)
  - Credit card numbers (Luhn-checked to cut down false positives)
  - IP addresses (v4)

Usage:
    pip install pdfminer.six --break-system-packages

    python pii_scanner.py /path/to/pdf_dir -o results.csv
    python pii_scanner.py /path/to/pdf_dir -o results.csv --mask
    python pii_scanner.py /path/to/pdf_dir -o results.csv --context-chars 60

Notes:
  - This is a heuristic scanner, not a compliance tool. Regexes will produce
    both false positives (e.g. random 16-digit numbers) and false negatives
    (e.g. non-US phone formats, PII embedded in images/scans with no text
    layer). Always have a human review the CSV output.
  - Scanned/image-only PDFs have no extractable text; this script will not
    find PII in them (OCR is out of scope here).
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer

# ---------------------------------------------------------------------------
# PII patterns
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
)

SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")

# Loose match on digit groups that look like card numbers (13-16 digits,
# optionally split by spaces/dashes in groups of 4). Luhn check applied after.
CREDIT_CARD_RE = re.compile(
    r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)"
)

IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b"
)

PII_PATTERNS = {
    "EMAIL": EMAIL_RE,
    "PHONE": PHONE_RE,
    "SSN": SSN_RE,
    "CREDIT_CARD": CREDIT_CARD_RE,
    "IP_ADDRESS": IPV4_RE,
}


def luhn_valid(number_str: str) -> bool:
    """Validate a digit string against the Luhn checksum (credit card check)."""
    digits = [int(d) for d in number_str if d.isdigit()]
    if not (13 <= len(digits) <= 16):
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def mask_value(value: str) -> str:
    """Mask a matched value, keeping a few edge characters for reference."""
    digits_only = re.sub(r"\D", "", value)
    if len(digits_only) >= 4:
        visible = digits_only[-4:]
        return f"{'*' * (len(digits_only) - 4)}{visible}"
    if len(value) <= 4:
        return "*" * len(value)
    return value[0] + "*" * (len(value) - 2) + value[-1]


def get_page_text(layout_items):
    """Concatenate text from LTTextContainer items on a page."""
    parts = []
    for element in layout_items:
        if isinstance(element, LTTextContainer):
            parts.append(element.get_text())
    return "".join(parts)


def scan_pdf(filepath, context_chars=50):
    """
    Yield dicts of {page, pii_type, match, context} for a single PDF file.
    Raises exceptions on unreadable/corrupt files; caller should catch.
    """
    for page_num, page_layout in enumerate(extract_pages(filepath), start=1):
        text = get_page_text(page_layout)
        if not text.strip():
            continue

        for pii_type, pattern in PII_PATTERNS.items():
            for m in pattern.finditer(text):
                value = m.group().strip()

                if pii_type == "CREDIT_CARD" and not luhn_valid(value):
                    continue

                start = max(0, m.start() - context_chars)
                end = min(len(text), m.end() + context_chars)
                context = text[start:end].replace("\n", " ").strip()

                yield {
                    "page": page_num,
                    "pii_type": pii_type,
                    "match": value,
                    "context": context,
                }


def find_pdfs(directory, recursive=True):
    if recursive:
        for root, _dirs, files in os.walk(directory):
            for name in files:
                if name.lower().endswith(".pdf"):
                    yield os.path.join(root, name)
    else:
        for name in os.listdir(directory):
            if name.lower().endswith(".pdf"):
                full = os.path.join(directory, name)
                if os.path.isfile(full):
                    yield full


def main():
    parser = argparse.ArgumentParser(
        description="Scan a directory of PDFs for PII and write results to CSV."
    )
    parser.add_argument("directory", help="Directory containing PDF files to scan")
    parser.add_argument(
        "-o", "--output", default="pii_scan_results.csv",
        help="Path to output CSV file (default: pii_scan_results.csv)"
    )
    parser.add_argument(
        "--no-recursive", action="store_true",
        help="Only scan the top-level directory (default scans subdirectories too)"
    )
    parser.add_argument(
        "--mask", action="store_true",
        help="Mask matched PII values in the CSV output (keeps last 4 chars/digits)"
    )
    parser.add_argument(
        "--context-chars", type=int, default=50,
        help="Number of characters of surrounding context to include (default: 50)"
    )
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f"Error: '{args.directory}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    pdf_files = sorted(find_pdfs(args.directory, recursive=not args.no_recursive))
    if not pdf_files:
        print(f"No PDF files found in '{args.directory}'.")
        sys.exit(0)

    total_findings = 0
    files_with_errors = 0

    with open(args.output, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(
            ["file", "page", "pii_type", "match", "context", "scanned_at"]
        )

        for filepath in pdf_files:
            rel_path = os.path.relpath(filepath, args.directory)
            print(f"Scanning: {rel_path}")
            scanned_at = datetime.now().isoformat(timespec="seconds")

            try:
                findings = list(scan_pdf(filepath, context_chars=args.context_chars))
            except Exception as exc:  # noqa: BLE001 - report and continue
                files_with_errors += 1
                writer.writerow(
                    [rel_path, "", "ERROR", "", f"Failed to process: {exc}", scanned_at]
                )
                print(f"  -> ERROR: {exc}", file=sys.stderr)
                continue

            for finding in findings:
                match_value = (
                    mask_value(finding["match"]) if args.mask else finding["match"]
                )
                writer.writerow(
                    [
                        rel_path,
                        finding["page"],
                        finding["pii_type"],
                        match_value,
                        finding["context"],
                        scanned_at,
                    ]
                )
            total_findings += len(findings)
            if findings:
                print(f"  -> {len(findings)} potential PII item(s) found")

    print()
    print(f"Done. Scanned {len(pdf_files)} PDF file(s).")
    print(f"Total potential PII findings: {total_findings}")
    if files_with_errors:
        print(f"Files that failed to process: {files_with_errors}")
    print(f"Results written to: {args.output}")


if __name__ == "__main__":
    main()