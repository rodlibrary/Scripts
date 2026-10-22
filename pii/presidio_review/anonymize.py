#!/usr/bin/env python3
"""
Step 3 of 3: Apply the human-reviewed decisions with Presidio Anonymizer.

Usage:
    python anonymize.py --input docs/ --review review.csv --output-dir redacted/
    python anonymize.py --input docs/ --review review.csv --output-dir redacted/ --operator mask

Only rows whose `action` is REDACT are anonymized; KEEP rows are left as-is.
Each file is written back in its own format (see formats.py):
  text / .docx / .csv / .xlsx -> REDACT spans replaced using --operator;
                                 REDACT column rows anonymized by Presidio Structured
  .pdf                        -> flattened PDF with black boxes and a new searchable
                                 OCR text layer made from the redacted pages
  images                      -> same image format with black boxes
OCR settings and cached OCR results are read from the files analyze.py wrote next
to the review CSV, so scanned pages are not OCR'd twice and offsets match.

The script refuses to run if any row has a blank/unknown action, or if a source
file changed since analysis (offsets would no longer line up).
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig, RecognizerResult

import formats
from ocr import OcrSettings
from structured import anonymize_columns

REDACT_WORDS = {"REDACT", "R", "YES", "Y", "TRUE", "X", "1", "☑", "✓", "✔"}
KEEP_WORDS = {"KEEP", "K", "NO", "N", "FALSE", "0", "LEAVE", "LEAVE ALONE", "☐"}


def parse_action(raw: str):
    v = (raw or "").strip().upper()
    if v in REDACT_WORDS:
        return "REDACT"
    if v in KEEP_WORDS:
        return "KEEP"
    return None


def build_operators(name: str):
    if name == "replace":   # <PERSON>, <EMAIL_ADDRESS>, ...
        return None         # Presidio's default
    if name == "redact":    # remove the text entirely
        return {"DEFAULT": OperatorConfig("redact", {})}
    if name == "mask":
        return {"DEFAULT": OperatorConfig("mask", {"masking_char": "*", "chars_to_mask": 10_000, "from_end": False})}
    if name == "hash":
        return {"DEFAULT": OperatorConfig("hash", {"hash_type": "sha256"})}
    raise ValueError(name)


def merge_overlaps(spans):
    """spans: list of (start, end, entity_type). Overlapping REDACT spans become one;
    the longer original span's entity type wins."""
    merged = []
    for s, e, t in sorted(spans):
        if merged and s < merged[-1][1]:
            ps, pe, pt, plen = merged[-1]
            merged[-1] = (ps, max(pe, e), pt if plen >= e - s else t, max(plen, e - s))
        else:
            merged.append((s, e, t, e - s))
    return [(s, e, t) for s, e, t, _ in merged]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="Same file/folder given to analyze.py")
    ap.add_argument("--review", required=True, help="The reviewed CSV")
    ap.add_argument("--output-dir", default="anonymized", help="Where to write anonymized files")
    ap.add_argument("--operator", default="replace", choices=["replace", "redact", "mask", "hash"],
                    help="How REDACT spans are rendered in text/Word/Excel output (PDFs always get black boxes)")
    ap.add_argument("--pdf-dpi", type=int, default=200, help="Resolution of flattened PDF pages")
    ap.add_argument("--no-text-layer", action="store_true",
                    help="Don't add a searchable OCR text layer to redacted PDFs (image-only output)")
    ap.add_argument("--keep-metadata", action="store_true",
                    help="Keep author/last-modified-by in .docx/.xlsx (stripped by default)")
    ap.add_argument("--audit", default=None, help="Optional audit CSV (default: <output-dir>/audit_log.csv)")
    args = ap.parse_args()

    input_path = Path(args.input)
    base = input_path.parent if input_path.is_file() else input_path
    out_dir = Path(args.output_dir)

    # OCR must run with the same settings as analyze.py, or text offsets won't line up
    review_path = Path(args.review)
    settings_path = review_path.with_name(review_path.stem + "_settings.json")
    saved = json.loads(settings_path.read_text(encoding="utf-8")).get("ocr", {}) \
        if settings_path.exists() else {}
    ocr = OcrSettings(**saved, cache_dir=review_path.with_name(review_path.stem + "_ocr_cache"))

    # ---- Load and validate reviewer decisions ----------------------------
    with open(args.review, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    errors, by_file = [], defaultdict(list)
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        action = parse_action(row.get("action"))
        if action is None:
            errors.append(f"  line {i} ({row.get('detected_text')!r}): action is "
                          f"{row.get('action')!r}; use REDACT or KEEP")
            continue
        row["_action"] = action
        by_file[row["file"]].append(row)

    if errors:
        sys.exit("Review is incomplete. Fix these rows and re-run:\n" + "\n".join(errors))

    # ---- Process every file ---------------------------------------------
    anonymizer = AnonymizerEngine()
    operators = build_operators(args.operator)
    audit, not_output = [], []

    def replacement(entity_type: str, span_text: str) -> str:
        res = anonymizer.anonymize(
            text=span_text,
            analyzer_results=[RecognizerResult(entity_type, 0, len(span_text), 1.0)],
            operators=operators,
        )
        return res.text

    srcs = [input_path] if input_path.is_file() else sorted(
        p for p in input_path.rglob("*") if p.is_file() and not p.name.startswith(("~$", ".")))

    for src in srcs:
        rel = str(src.relative_to(base))
        file_rows = by_file.pop(rel, [])

        if not formats.is_supported(src):
            not_output.append(f"{rel}: unsupported file type")
            continue

        if file_rows:
            expected = file_rows[0].get("file_sha256")
            if expected and hashlib.sha256(src.read_bytes()).hexdigest() != expected:
                sys.exit(f"{rel} changed after analysis; re-run analyze.py and review again.")

        doc = formats.load(src, ocr)
        if not doc.text.strip() and isinstance(doc, (formats.PdfDoc, formats.ImageDoc)):
            not_output.append(f"{rel}: no text could be read (OCR off/unavailable, or nothing "
                              f"recognized); needs manual redaction")
            continue

        spans, columns = [], []
        for row in file_rows:
            if row.get("scope") == "column":
                # finding_id = <file>::column::<table>::<col>::<entity>
                _, _, t, c, et = row["finding_id"].rsplit("::", 4)
                t, c = int(t), int(c)
                if not isinstance(doc, formats.TableDoc) or t >= len(doc.tables) \
                        or c not in doc.tables[t].headers:
                    sys.exit(f"Column finding {row['finding_id']} no longer matches {rel}.")
                if row["_action"] == "REDACT":
                    columns.append((t, c, et))
            else:
                start, end = int(row["start"]), int(row["end"])
                if doc.text[start:end] != row["detected_text"]:
                    sys.exit(f"Offset mismatch in {rel} for finding {row['finding_id']}; "
                             f"was the start/end/detected_text edited?")
                if row["_action"] == "REDACT":
                    spans.append((start, end, row["entity_type"]))
            audit.append({"finding_id": row["finding_id"], "scope": row.get("scope") or "text",
                          "file": rel, "location": row.get("location", ""),
                          "entity_type": row["entity_type"], "detected_text": row["detected_text"],
                          "action": row["_action"], "reviewer_notes": row.get("reviewer_notes", "")})

        edits = [(s, e, replacement(t, doc.text[s:e])) for s, e, t in merge_overlaps(spans)]
        # whole columns approved by reviewers are anonymized by Presidio Structured
        overrides = anonymize_columns(doc, columns, args.operator) if columns else {}

        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_warnings = doc.write_redacted(
            edits, dest, cell_overrides=overrides, dpi=args.pdf_dpi,
            text_layer=not args.no_text_layer, ocr_lang=ocr.lang,
            strip_metadata=not args.keep_metadata) or []
        doc.warnings += write_warnings

        kept = sum(r["_action"] == "KEEP" for r in file_rows)
        redacted = len(file_rows) - kept
        print(f"{rel}: redacted {redacted}, kept {kept} -> {dest}")
        for w in doc.warnings:
            print(f"    WARNING: {w}")

    if by_file:  # review rows whose file is no longer in --input
        sys.exit("These files are in the review CSV but not in --input: " + ", ".join(by_file))

    audit_path = Path(args.audit) if args.audit else out_dir / "audit_log.csv"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["finding_id", "scope", "file", "location", "entity_type",
                                          "detected_text", "action", "reviewer_notes"])
        w.writeheader()
        w.writerows(audit)
    print(f"Audit log: {audit_path}")

    if not_output:
        print(f"\n{len(not_output)} file(s) were NOT written to {out_dir} and need manual handling:")
        for m in not_output:
            print("  - " + m)


if __name__ == "__main__":
    main()
