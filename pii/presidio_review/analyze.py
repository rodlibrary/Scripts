#!/usr/bin/env python3
"""
Step 1 of 3: Run Presidio Analyzer and write a review CSV for humans.

Usage:
    python analyze.py --input docs/ --output review.csv
    python analyze.py --input scans/ --output review.csv --ocr always --ocr-lang eng+spa

Supported file types: .pdf .docx .xlsx .csv .tsv, images (.png .jpg .jpeg .tif
.tiff .bmp) and plain text (.txt .md .log .json .xml .html .htm).
Scanned PDF pages and image files are read with Tesseract OCR.
CSV/XLSX tables also get column-level detection from Presidio Structured.

Each finding becomes one row with scope "text" (one occurrence) or "column" (a whole
table column). Reviewers set the `action` column to REDACT or KEEP (default REDACT,
so nothing slips through if a row is skipped).

Also written next to the CSV:
  <name>_settings.json   OCR/structured settings, read by anonymize.py
  <name>_warnings.txt    content that could not be fully analyzed
  <name>_ocr_cache/      OCR results reused by anonymize.py (contains document text;
                         delete it once the job is done)
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from presidio_analyzer import AnalyzerEngine

import formats
from ocr import OcrSettings, tesseract_available
from pii_utils import collapse_same_span, drop_nested
from structured import StructuredSettings, analyze_columns

CONTEXT_CHARS = 40

REVIEW_COLUMNS = [
    "finding_id",       # stable ID, do not edit
    "scope",            # "text" = one occurrence, "column" = every value in a table column
    "file",             # source file (relative to --input)
    "location",         # page / paragraph / cell / column, to help reviewers find it
    "start", "end",     # character offsets in the extracted text (blank for column rows)
    "entity_type",      # e.g. PERSON, EMAIL_ADDRESS
    "also_detected_as", # other types Presidio gave the exact same text
    "detected_text",    # the exact text flagged (sample values for column rows)
    "context",          # surrounding text with the span in [[ ]]; match rate for column rows
    "score",            # Presidio confidence 0-1 (column rows: share of cells matched)
    "ocr_confidence",   # Tesseract confidence 0-100 when the text came from OCR
    "action",           # REVIEWER: REDACT or KEEP
    "reviewer_notes",   # REVIEWER: optional free text
    "file_sha256",      # fingerprint so anonymize.py can detect edited source files
]


def sidecar(review_csv: Path, suffix: str) -> Path:
    return review_csv.with_name(review_csv.stem + suffix)


def iter_input_files(input_path: Path):
    if input_path.is_file():
        yield input_path.parent, input_path
    else:
        for p in sorted(input_path.rglob("*")):
            if p.is_file() and not p.name.startswith(("~$", ".")):  # skip Office lock/hidden files
                yield input_path, p


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_context(text: str, start: int, end: int) -> str:
    left = text[max(0, start - CONTEXT_CHARS):start]
    right = text[end:end + CONTEXT_CHARS]
    snippet = f"{left}[[{text[start:end]}]]{right}"
    return " ".join(snippet.split())  # collapse newlines so the CSV stays one line per row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="A file or a folder (searched recursively)")
    ap.add_argument("--output", default="review.csv", help="Review CSV to write")
    ap.add_argument("--language", default="en")
    ap.add_argument("--threshold", type=float, default=0.35, help="Minimum Presidio score to include")
    ap.add_argument("--entities", nargs="*", help="Limit to these entity types (default: all)")
    ap.add_argument("--exclude-entities", nargs="*", default=[],
                    help="Entity types to ignore, e.g. UK_NHS AU_TFN for US-only data")
    ap.add_argument("--keep-nested", action="store_true",
                    help="Keep findings that sit inside a higher-scoring one "
                         "(e.g. 'example.com' URL inside an email). Off by default.")
    ap.add_argument("--default-action", default="REDACT", choices=["REDACT", "KEEP"],
                    help="Pre-filled value of the action column")
    og = ap.add_argument_group("OCR (Tesseract)")
    og.add_argument("--ocr", default="auto", choices=["off", "auto", "always"],
                    help="auto: OCR images and PDF pages with no text layer; "
                         "always: OCR every PDF page (catches text inside embedded images)")
    og.add_argument("--ocr-lang", default="eng", help="Tesseract language(s), e.g. eng+spa")
    og.add_argument("--ocr-dpi", type=int, default=300, help="Resolution PDF pages are OCR'd at")
    og.add_argument("--no-auto-rotate", action="store_true", help="Don't auto-correct sideways scans")
    sg = ap.add_argument_group("Column-level detection (Presidio Structured, CSV/XLSX)")
    sg.add_argument("--no-structured", action="store_true", help="Only cell-by-cell findings")
    sg.add_argument("--structured-strategy", default="most_common",
                    choices=["most_common", "highest_confidence", "mixed"])
    sg.add_argument("--structured-min-ratio", type=float, default=0.3,
                    help="Share of a column's non-empty cells that must match (default 0.3)")
    sg.add_argument("--structured-sample", type=int, default=500, help="Rows sampled per table")
    args = ap.parse_args()

    out_path = Path(args.output)
    ocr = OcrSettings(mode=args.ocr, lang=args.ocr_lang, dpi=args.ocr_dpi,
                      auto_rotate=not args.no_auto_rotate,
                      cache_dir=sidecar(out_path, "_ocr_cache"))
    struct = StructuredSettings(enabled=not args.no_structured, strategy=args.structured_strategy,
                                sample_rows=args.structured_sample,
                                min_match_ratio=args.structured_min_ratio)
    sidecar(out_path, "_settings.json").write_text(json.dumps(
        {"language": args.language, "ocr": ocr.to_dict(), "structured": struct.to_dict()},
        indent=2), encoding="utf-8")

    analyzer = AnalyzerEngine()
    if args.exclude_entities:
        args.entities = [e for e in (args.entities or analyzer.get_supported_entities(args.language))
                         if e not in set(args.exclude_entities)]
    input_path = Path(args.input)
    rows, skipped, warnings = [], [], []
    if ocr.mode != "off" and not tesseract_available():
        warnings.append("Tesseract is not installed or not on PATH: scanned pages and images "
                        "will NOT be analyzed.")

    for base, path in iter_input_files(input_path):
        rel = str(path.relative_to(base))
        if not formats.is_supported(path):
            skipped.append(rel)
            continue
        try:
            doc = formats.load(path, ocr)
        except Exception as e:  # corrupt / password-protected files
            warnings.append(f"{rel}: could not be read ({e.__class__.__name__}: {e}). NOT analyzed.")
            continue
        warnings += [f"{rel}: {w}" for w in doc.warnings]
        digest = sha256_file(path)
        n_before = len(rows)

        # ---- column-level findings (Presidio Structured) ----------------
        mapped = {}  # (sheet, col) -> entity type, to avoid duplicate cell-level rows
        if struct.enabled and isinstance(doc, formats.TableDoc) and doc.tables:
            for cf in analyze_columns(doc, analyzer, struct, args.language, args.threshold):
                if args.entities and cf.entity_type not in args.entities:
                    continue
                sheet = doc.tables[cf.table].name
                mapped[(sheet, cf.col)] = (cf.entity_type, doc.tables[cf.table].header_row)
                rows.append({
                    "finding_id": f"{rel}::column::{cf.table}::{cf.col}::{cf.entity_type}",
                    "scope": "column",
                    "file": rel,
                    "location": doc.column_location(cf.table, cf.col),
                    "start": "", "end": "",
                    "entity_type": cf.entity_type,
                    "also_detected_as": "",
                    "detected_text": "e.g. " + " | ".join(str(s) for s in cf.samples),
                    "context": f"Presidio Structured: {cf.matched} of {cf.non_empty} sampled "
                               f"non-empty cells matched; REDACT applies to every value in the column",
                    "score": round(cf.ratio, 2),
                    "ocr_confidence": "",
                    "action": args.default_action,
                    "reviewer_notes": "",
                    "file_sha256": digest,
                })

        # ---- text-level findings ----------------------------------------
        text = doc.text
        results = analyzer.analyze(
            text=text,
            language=args.language,
            entities=args.entities,
            score_threshold=args.threshold,
        ) if text.strip() else []
        if not args.keep_nested:
            results = drop_nested(results)

        for r, also in sorted(collapse_same_span(results), key=lambda x: (x[0].start, x[0].end)):
            if mapped:
                cell = doc.cell_at(r.start)
                m = mapped.get((cell[0], cell[2])) if cell else None
                # In a column with its own column row, skip findings already decided by it:
                # the same entity type, or any match that is the whole cell value (e.g. a
                # phone also labelled UK_NHS). Header cells are never covered.
                if m and cell[1] != m[1] and (
                        m[0] == r.entity_type
                        or text[r.start:r.end].strip() == doc.cell_text(cell).strip()):
                    continue
            conf = doc.ocr_confidence(r.start, r.end)
            rows.append({
                "finding_id": f"{rel}:{r.start}-{r.end}:{r.entity_type}",
                "scope": "text",
                "file": rel,
                "location": doc.locate(r.start),
                "start": r.start,
                "end": r.end,
                "entity_type": r.entity_type,
                "also_detected_as": ", ".join(also),
                "detected_text": text[r.start:r.end],
                "context": make_context(text, r.start, r.end),
                "score": round(r.score, 2),
                "ocr_confidence": "" if conf is None else conf,
                "action": args.default_action,
                "reviewer_notes": "",
                "file_sha256": digest,
            })
        n_cols = sum(1 for x in rows[n_before:] if x["scope"] == "column")
        print(f"{rel}: {len(rows) - n_before} findings" + (f" ({n_cols} whole columns)" if n_cols else ""))

    # utf-8-sig so Excel opens accented characters correctly
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    if skipped:
        print(f"\nSkipped {len(skipped)} unsupported file(s): " + ", ".join(skipped))
    warn_path = sidecar(out_path, "_warnings.txt")
    if warnings:
        warn_path.write_text("\n".join(warnings) + "\n", encoding="utf-8")
        print(f"\n{len(warnings)} WARNING(S) - content that was not fully analyzed or needs a "
              f"visual check (also saved to {warn_path}):")
        for wmsg in warnings:
            print("  - " + wmsg)
    elif warn_path.exists():
        warn_path.unlink()

    print(f"\nWrote {len(rows)} findings to {out_path}. "
          f"Set the 'action' column to REDACT or KEEP, then run anonymize.py.")


if __name__ == "__main__":
    main()
