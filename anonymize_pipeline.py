"""
PII anonymization pipeline.

Consumes the pii_results_<job_id>.csv produced by analyze_pipeline.py and
runs *only* presidio_anonymizer against it -- it does not re-run the
analyzer. Each row of the results CSV already carries an entity_type,
start_char/end_char offset, and score; those are exactly what
presidio_analyzer.RecognizerResult needs, so we rebuild RecognizerResult
objects directly from the CSV instead of re-scanning files with
AnalyzerEngine. This means no spaCy model has to be loaded here at all.

Every recorded finding is checked before it is applied: the text at
[start_char, end_char) must equal the recorded entity_value. If it
doesn't (e.g. a file changed since analysis, or an undecodable byte
shifted an offset), we look for the value nearby and use that position;
if it can't be found, the finding is counted as "unmatched" in the
summary rather than redacting the wrong characters.

Source types produced by analyze_pipeline.py, and how each is handled:

  - unstructured_text (pdf / image / txt / eml): the recorded offsets are
    character positions in the *extracted* text (pdfminer output for
    PDFs, pytesseract OCR output for images, raw file contents for .txt,
    headers + bodies for .eml). To anonymize, we re-run only the (cheap)
    extraction step to get that same text back, then hand the recorded
    RecognizerResults to AnonymizerEngine. Output is written as a .txt
    transcript -- for images and PDFs this is an anonymized *transcript*,
    not a visually redacted image/PDF, since we only have text offsets,
    not pixel bounding boxes. (True pixel-level image redaction would
    require re-running presidio_image_redactor's own detection from
    scratch, which is a different pipeline than "reuse the analyze
    results".)

    Large .txt files that the analyzer streamed in byte-range chunks
    (location "byte_offset=..,byte_end=..") are anonymized the same way
    without ever loading the whole file: each chunk's findings are turned
    into absolute byte spans, overlapping spans are merged, and the file is
    copied through once with each span replaced by the anonymizer's
    output. The result is the original file with only the PII changed.

  - structured_csv: the recorded offsets are character positions within
    a single cell's text, plus a location string "column=X,row=Y". We
    reload the original CSV, group matches by (column, row), anonymize
    each affected cell in place, and write out a full CSV with the same
    shape as the original but redacted cell values.

  - scdif / archive_format (Intranet Quorum / Senate CSS exports): the
    recorded offsets are character positions within one TAB-separated
    field, and `location` gives the line's byte_offset and the field's
    position (field_no). The export is copied through line by line; lines
    with findings get just those fields anonymized, every other byte is
    left exactly as it was, so the output is still a valid export with the
    same records, IDs and codes. Replacement text never contains TABs or
    line breaks (line breaks become "|", as SCDIF requires). Large exports
    are split into byte-range chunks processed in parallel and then
    concatenated in order.

Only files that appear in the results CSV are touched. There is no
reliable way to tell "scanned successfully, zero PII found" apart from
"never got scanned / errored out" using just that file, so nothing is
auto-copied through -- files not present in the results CSV are left
alone rather than guessed about.

Output files mirror the input's subfolders and are named
anonymized_<original name> (anonymized_<stem>.txt for extracted text).

Keeping some entity types:
  --keep_names leaves every PERSON finding (constituent, spouse, staff and
  other names) in place and anonymizes everything else. --keep_entities
  does the same for any comma-separated list of entity types, e.g.
  --keep_entities LOCATION,DATE_TIME. Kept findings are counted in the
  summary's Entities_Kept column. Files whose only findings are kept still
  get an output file (an unchanged copy, or transcript for PDFs/images),
  so the output folder always has one file per file in the results CSV.
"""

import os
import re
import argparse
import html
import multiprocessing as mp
from collections import defaultdict
from email import policy
from email.parser import BytesParser

import pandas as pd
import pdfminer.high_level
import pytesseract
from PIL import Image

from presidio_analyzer import RecognizerResult
from presidio_anonymizer import AnonymizerEngine


IMAGE_EXTS = {"png", "jpg", "jpeg", "tiff", "tif", "bmp"}
UNSTRUCTURED_TEXT_EXTS = {"pdf", "txt", "eml"} | IMAGE_EXTS
DB_SOURCE_TYPES = {"scdif", "archive_format"}

# How far (in characters) to look for a recorded entity_value when the text
# at its recorded offsets doesn't match.
REALIGN_WINDOW = 64


# --------------------------------------------------------------------------
# Text extraction (must match analyze_pipeline.py exactly, since recorded
# offsets refer to this text)
# --------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: str) -> str:
    return pdfminer.high_level.extract_text(pdf_path)


def extract_text_from_image(image_path: str) -> str:
    img = Image.open(image_path)
    return pytesseract.image_to_string(img)


def extract_text_from_txt(txt_path: str) -> str:
    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def extract_text_from_eml(eml_path: str) -> str:
    with open(eml_path, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)
    parts = []
    for header in ("From", "To", "Cc", "Bcc", "Reply-To", "Subject", "Date"):
        if msg[header]:
            parts.append(f"{header}: {msg[header]}")
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            body = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            body = payload.decode("utf-8", errors="replace")
        if ctype == "text/html":
            body = html.unescape(re.sub(r"<[^>]+>", " ", body))
        parts.append(body)
    return "\n".join(parts)


def extract_text(file_path: str, ext: str) -> str:
    if ext == "pdf":
        return extract_text_from_pdf(file_path)
    if ext in IMAGE_EXTS:
        return extract_text_from_image(file_path)
    if ext == "eml":
        return extract_text_from_eml(file_path)
    if ext == "txt":
        return extract_text_from_txt(file_path)
    raise ValueError(f"Unsupported extension for text extraction: .{ext}")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def output_path(output_dir, rel_path, new_ext=None):
    """output_dir/<same subfolders>/anonymized_<name>, creating folders."""
    folder, name = os.path.split(rel_path)
    if new_ext is not None:
        name = os.path.splitext(name)[0] + new_ext
    out_dir = os.path.join(output_dir, folder)
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, f"anonymized_{name}")


def parse_location(location_str: str):
    """'column=NAME,row=3' -> ('NAME', 3)"""
    parts = dict(kv.split("=", 1) for kv in location_str.split(","))
    return parts["column"], int(parts["row"])


def parse_db_location(location_str: str):
    """
    'record=2D,byte_offset=123,person_id=9,...,field_no=5,field=Text'
      -> ('2D', 123, 5, 'Text')
    `field=` is always last and may itself contain commas, so it is split
    off first. field_no is None in results from before it was recorded.
    """
    head, sep, field = location_str.rpartition(",field=")
    if not sep:
        head, field = location_str, ""
    parts = dict(kv.split("=", 1) for kv in head.split(",") if "=" in kv)
    field_no = int(parts["field_no"]) if parts.get("field_no", "").isdigit() else None
    return parts.get("record", ""), int(parts["byte_offset"]), field_no, field


def parse_chunk_location(location_str: str):
    """'byte_offset=100,byte_end=200' -> (100, 200 or None)"""
    parts = dict(kv.split("=", 1) for kv in location_str.split(",") if "=" in kv)
    end = parts.get("byte_end")
    return int(parts["byte_offset"]), (int(end) if end else None)


def realign(text, start, end, value):
    """
    Returns (start, end) where `value` actually occurs in `text`: the
    recorded offsets if they match, else the nearest occurrence within
    REALIGN_WINDOW characters, else None.
    """
    if value is None or value == "":
        return (start, end) if 0 <= start < end <= len(text) else None
    if 0 <= start < end <= len(text) and text[start:end] == value:
        return start, end
    lo = max(0, start - REALIGN_WINDOW)
    hi = min(len(text), max(end, start + len(value)) + REALIGN_WINDOW)
    candidates = []
    i = text.find(value, lo, hi)
    while i != -1:
        candidates.append(i)
        i = text.find(value, i + 1, hi)
    if not candidates:
        return None
    best = min(candidates, key=lambda c: abs(c - start))
    return best, best + len(value)


def rows_to_results(rows, text):
    """
    Rebuild RecognizerResult objects from pii_results.csv rows, verifying
    each against `text`. Returns (results, unmatched_count).

    When several findings cover exactly the same characters (e.g. a value
    in an SSN field found as US_SSN by the schema and as DATE_TIME by
    spaCy), only one is kept, so the placeholder names the most reliable
    type: schema findings first, then the highest score.
    """
    best = {}
    for r in rows:
        key = (r.get("start_char"), r.get("end_char"))
        try:
            rank = (r.get("detector") == "schema", float(r.get("score") or 0))
        except ValueError:
            rank = (False, 0.0)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, r)
    rows = [r for _, r in best.values()]
    results, unmatched = [], 0
    for r in rows:
        try:
            start, end = int(r["start_char"]), int(r["end_char"])
            score = float(r["score"])
        except (KeyError, ValueError, TypeError):
            unmatched += 1
            continue
        span = realign(text, start, end, r.get("entity_value"))
        if span is None:
            unmatched += 1
            continue
        results.append(RecognizerResult(entity_type=r["entity_type"],
                                        start=span[0], end=span[1], score=score))
    return results, unmatched


def anonymize_text(anonymizer, text, rows):
    results, unmatched = rows_to_results(rows, text)
    if not results:
        return text, 0, unmatched
    return anonymizer.anonymize(text=text, analyzer_results=results).text, len(results), unmatched


def _sanitize_db_value(value: str) -> str:
    """Keep anonymizer output from breaking the TAB/CRLF record structure."""
    return (value.replace("\t", " ").replace("\r\n", "|")
                 .replace("\r", "|").replace("\n", "|"))


_layout_cache = {}


def resolve_field_index(fmt, rec_type, field_name, schema_path):
    """
    0-based field index from a field *name*, for results written before
    analyze_pipeline.py recorded field_no. Uses the analyzer's layouts.
    """
    m = re.match(r"Extra Field (\d+)$", field_name)
    if m:
        return int(m.group(1)) - 1
    if "layouts" not in _layout_cache:
        import analyze_pipeline as ap  # same folder; spaCy is not loaded on import
        scdif = dict(ap.SCDIF_SCHEMA)
        if schema_path:
            scdif.update(ap.load_schema_override(schema_path))
        _layout_cache["layouts"] = (scdif, ap.ARCHIVE_FORMAT_FIELDS)
    scdif, archive = _layout_cache["layouts"]
    layout = archive if fmt == "archive_format" else scdif.get(rec_type, [])
    base = field_name.split("[", 1)[0]  # "Field Value[SSN]" -> "Field Value"
    for i, (name, _) in enumerate(layout):
        if name == base:
            return i
    return None


def iter_lines_in_range(f, start: int, end: int):
    """
    Yields raw lines (with their line endings) that *start* in [start, end);
    same rule as analyze_pipeline.py, so consecutive ranges cover the file
    exactly once.
    """
    if start > 0:
        f.seek(start - 1)
        if f.read(1) != b"\n":
            f.readline()
    else:
        f.seek(0)
    pos = f.tell()
    while pos < end:
        line = f.readline()
        if not line:
            break
        yield pos, line
        pos += len(line)


# --------------------------------------------------------------------------
# Per-source anonymizers
# --------------------------------------------------------------------------

def anonymize_unstructured_file(anonymizer, rel, rows, cfg):
    ext = os.path.splitext(rel)[1].lower().lstrip(".")
    text = extract_text(os.path.join(cfg["input_dir"], rel), ext)
    anonymized_text, count, unmatched = anonymize_text(anonymizer, text, rows)
    out = output_path(cfg["output_dir"], rel, ".txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(anonymized_text)
    return out, count, unmatched


def anonymize_structured_file(anonymizer, rel, rows, cfg):
    df = pd.read_csv(os.path.join(cfg["input_dir"], rel), dtype=str).fillna("")

    cell_groups = defaultdict(list)
    for r in rows:
        column, row_idx = parse_location(r["location"])
        cell_groups[(column, row_idx)].append(r)

    entity_count = unmatched = 0
    for (column, row_idx), cell_rows in cell_groups.items():
        if column not in df.columns or row_idx not in df.index:
            unmatched += len(cell_rows)
            continue
        new_text, n, u = anonymize_text(anonymizer, str(df.at[row_idx, column]), cell_rows)
        df.at[row_idx, column] = new_text
        entity_count += n
        unmatched += u

    out = output_path(cfg["output_dir"], rel)
    df.to_csv(out, index=False)
    return out, entity_count, unmatched


def anonymize_db_chunk(anonymizer, task, cfg):
    """
    Copies lines starting in [start, end) of an SCDIF / Archive Format
    export to `part_path`, anonymizing the fields that have findings.
    """
    encoding = cfg["db_encoding"]
    by_line = defaultdict(lambda: defaultdict(list))  # offset -> field idx -> rows
    unmatched = 0
    for r in task["rows"]:
        rec_type, offset, field_no, field_name = parse_db_location(r["location"])
        idx = (field_no - 1) if field_no else resolve_field_index(
            task["source_type"], rec_type, field_name, cfg["db_schema"])
        if idx is None or idx < 1:  # never touch the record type column
            unmatched += 1
            continue
        by_line[offset][idx].append(r)

    count = 0
    with open(task["path"], "rb") as src, open(task["part_path"], "wb") as dst:
        for offset, raw in iter_lines_in_range(src, task["start"], task["end"]):
            fields_rows = by_line.pop(offset, None)
            if not fields_rows:
                dst.write(raw)
                continue
            body = raw.rstrip(b"\r\n")
            ending = raw[len(body):]
            # surrogateescape round-trips any undecodable bytes unchanged
            values = body.decode(encoding, errors="surrogateescape").split("\t")
            for idx, rows in fields_rows.items():
                if idx >= len(values):
                    unmatched += len(rows)
                    continue
                new_value, n, u = anonymize_text(anonymizer, values[idx], rows)
                values[idx] = _sanitize_db_value(new_value)
                count += n
                unmatched += u
            dst.write("\t".join(values).encode(encoding, errors="surrogateescape") + ending)
    # findings whose byte_offset isn't the start of any line in this range
    unmatched += sum(len(v) for fields in by_line.values() for v in fields.values())
    return count, unmatched


def anonymize_streamed_text(anonymizer, rel, rows, cfg):
    """
    For large .txt files analyzed in byte-range chunks: map every finding to
    an absolute byte span, merge overlaps, then copy the file through once,
    replacing each span. Memory use is bounded by one chunk, not the file.
    """
    path = os.path.join(cfg["input_dir"], rel)
    encoding = "utf-8"  # what analyze_pipeline.py uses for plain text
    size = os.path.getsize(path)

    by_chunk = defaultdict(list)
    for r in rows:
        kstart, kend = parse_chunk_location(r["location"])
        by_chunk[(kstart, kend)].append(r)

    spans = []  # (byte_start, byte_end, entity_type, score)
    unmatched = 0
    with open(path, "rb") as f:
        for (kstart, kend), chunk_rows in sorted(by_chunk.items()):
            max_end = max(int(r["end_char"]) for r in chunk_rows) + REALIGN_WINDOW
            # Read enough bytes to cover the furthest finding (+ realign room);
            # grows only if multi-byte characters make the first read too short.
            want = max_end + 16
            while True:
                f.seek(kstart)
                data = f.read(want)
                text = data.decode(encoding, errors="surrogateescape")
                if len(text) >= max_end or kstart + len(data) >= size:
                    break
                want *= 2
            for r in chunk_rows:
                try:
                    start, end = int(r["start_char"]), int(r["end_char"])
                    score = float(r["score"])
                except (ValueError, TypeError):
                    unmatched += 1
                    continue
                span = realign(text, start, end, r.get("entity_value"))
                if span is None:
                    unmatched += 1
                    continue
                s, e = span
                bstart = kstart + len(text[:s].encode(encoding, errors="surrogateescape"))
                bend = bstart + len(text[s:e].encode(encoding, errors="surrogateescape"))
                spans.append((bstart, bend, r["entity_type"], score))

    # Merge overlapping spans; the higher-scoring finding names the merged span.
    spans.sort()
    merged = []
    for bstart, bend, etype, score in spans:
        if merged and bstart < merged[-1][1]:
            m = merged[-1]
            if score > m[3]:
                m[2], m[3] = etype, score
            m[1] = max(m[1], bend)
        else:
            merged.append([bstart, bend, etype, score])

    out = output_path(cfg["output_dir"], rel)
    block = 1 << 20
    with open(path, "rb") as src, open(out, "wb") as dst:
        pos = 0
        for bstart, bend, etype, score in merged:
            while pos < bstart:  # copy untouched bytes
                chunk = src.read(min(block, bstart - pos))
                if not chunk:
                    break
                dst.write(chunk)
                pos += len(chunk)
            original = src.read(bend - bstart).decode(encoding, errors="surrogateescape")
            pos = bend
            replaced = anonymizer.anonymize(
                text=original,
                analyzer_results=[RecognizerResult(etype, 0, len(original), score)],
            ).text
            dst.write(replaced.encode(encoding, errors="surrogateescape"))
        while True:
            chunk = src.read(block)
            if not chunk:
                break
            dst.write(chunk)
    return out, len(merged), unmatched


# --------------------------------------------------------------------------
# Worker-process machinery
# --------------------------------------------------------------------------

# AnonymizerEngine is cheap to build (no spaCy model), but we still build
# one per worker via the Pool initializer to keep the same pattern as
# analyze_pipeline.py and avoid re-instantiating it per file.
_worker_anonymizer = None
_cfg = None


def _init_worker(cfg):
    global _worker_anonymizer, _cfg
    _cfg = cfg
    _worker_anonymizer = AnonymizerEngine()


def process_task(task):
    """
    Returns a dict for the summary. DB chunks return partial results that
    the main process combines into one summary row per file.
    """
    anonymizer, cfg = _worker_anonymizer, _cfg
    rel, source_type = task["rel"], task["source_type"]
    result = {"rel": rel, "kind": task["kind"], "source_type": source_type,
              "count": 0, "unmatched": 0, "output": None, "error": None,
              "chunk_index": task.get("chunk_index")}
    try:
        if task["kind"] == "db_chunk":
            result["count"], result["unmatched"] = anonymize_db_chunk(anonymizer, task, cfg)
        elif task["kind"] == "text_stream":
            result["output"], result["count"], result["unmatched"] = \
                anonymize_streamed_text(anonymizer, rel, task["rows"], cfg)
        elif source_type == "structured_csv":
            result["output"], result["count"], result["unmatched"] = \
                anonymize_structured_file(anonymizer, rel, task["rows"], cfg)
        else:
            result["output"], result["count"], result["unmatched"] = \
                anonymize_unstructured_file(anonymizer, rel, task["rows"], cfg)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


FILE_TYPE_LABELS = {
    "structured_csv": "Structured CSV",
    "unstructured_text": "Unstructured text (extracted/OCR transcript)",
    "text_stream": "Large plain text (streamed; original with PII replaced)",
    "scdif": "SCDIF export (IQ / Senate CSS)",
    "archive_format": "Senate Archive Format export",
}


def summary_record(rel, label, output, count, unmatched, error, kept=0, kept_types=()):
    if error:
        return {"Original_File": rel, "File_Type": label, "Entities_Anonymized": 0,
                "Entities_Kept": kept, "Unmatched_Findings": unmatched,
                "Output_File": "N/A", "Status": "Failed", "Details": error}
    status = "Success" if unmatched == 0 else "Partial"
    details = f"Anonymized {count} entities using offsets from the analyze phase."
    if kept:
        details += (f" Left {kept} findings unchanged as requested "
                    f"({', '.join(sorted(kept_types))}).")
    if unmatched:
        details += (f" {unmatched} recorded findings could not be located in the "
                    f"file and were NOT redacted; review this file manually.")
    return {"Original_File": rel, "File_Type": label, "Entities_Anonymized": count,
            "Entities_Kept": kept, "Unmatched_Findings": unmatched,
            "Output_File": output, "Status": status, "Details": details}


def build_tasks(results_df, cfg, chunk_bytes, keep_types=frozenset()):
    """
    Returns (tasks, db_files, kept_counts). Findings whose entity_type is in
    `keep_types` are left out of the tasks (so they are not anonymized) and
    counted per file in kept_counts.
    """
    tasks, db_files, kept_counts = [], {}, {}
    for rel, group in results_df.groupby("filename", sort=True):
        source_type = group["source_type"].iloc[0]
        is_stream = (source_type == "unstructured_text" and
                     group["location"].str.startswith("byte_offset=").any())
        if keep_types:
            kept_mask = group["entity_type"].isin(keep_types)
            kept_counts[rel] = int(kept_mask.sum())
            group = group[~kept_mask]
        rows = group.to_dict("records")
        path = os.path.join(cfg["input_dir"], rel)

        if source_type in DB_SOURCE_TYPES:
            size = os.path.getsize(path) if os.path.exists(path) else 0
            n_chunks = max(1, -(-size // chunk_bytes))
            out = output_path(cfg["output_dir"], rel)
            per_chunk = defaultdict(list)
            for r in rows:
                try:
                    _, offset, _, _ = parse_db_location(r["location"])
                except (KeyError, ValueError):
                    offset = 0
                per_chunk[min(offset // chunk_bytes, n_chunks - 1)].append(r)
            parts = []
            for i in range(n_chunks):
                part = f"{out}.part{i:05d}"
                parts.append(part)
                tasks.append({"kind": "db_chunk", "rel": rel, "source_type": source_type,
                              "path": path, "start": i * chunk_bytes,
                              "end": size if i == n_chunks - 1 else (i + 1) * chunk_bytes,
                              "rows": per_chunk.get(i, []), "part_path": part,
                              "chunk_index": i})
            db_files[rel] = {"source_type": source_type, "output": out, "parts": parts}
        elif is_stream:
            tasks.append({"kind": "text_stream", "rel": rel,
                          "source_type": source_type, "rows": rows})
        else:
            tasks.append({"kind": "file", "rel": rel,
                          "source_type": source_type, "rows": rows})
    return tasks, db_files, kept_counts


def parse_args():
    parser = argparse.ArgumentParser(
        description="Anonymize files using PII already identified by analyze_pipeline.py"
    )
    parser.add_argument(
        "--input_dir", required=True,
        help="Directory of original raw files (the same one analyze_pipeline.py scanned)",
    )
    parser.add_argument(
        "--pii_results_csv", required=True,
        help="Path to the pii_results_<job_id>.csv produced by analyze_pipeline.py",
    )
    parser.add_argument("--output_dir", required=True, help="Directory to write anonymized files into")
    parser.add_argument(
        "--csv_output", default=None,
        help="Path for the summary CSV (default: <output_dir>/anonymization_summary_<job_id>.csv)",
    )
    parser.add_argument(
        "--keep_names", action="store_true",
        help="Do not anonymize names (PERSON findings); everything else is still "
             "anonymized. Default: names are anonymized.",
    )
    parser.add_argument(
        "--keep_entities", default=None,
        help="Comma-separated entity types to leave unchanged, e.g. "
             "LOCATION,DATE_TIME (combines with --keep_names)",
    )
    parser.add_argument(
        "--db_encoding", default="utf-8",
        help="Encoding of SCDIF/Archive Format files; must match the "
             "--db_encoding used for analyze_pipeline.py (default: utf-8)",
    )
    parser.add_argument(
        "--db_schema", default=None,
        help="Same --db_schema JSON passed to analyze_pipeline.py, if any. Only "
             "needed for results files written before field positions were recorded.",
    )
    parser.add_argument(
        "--db_chunk_mb", type=float, default=64,
        help="Rewrite database exports in chunks of this many MB in parallel (default: 64)",
    )
    parser.add_argument(
        "--num_procs", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", 1)),
        help="Worker processes to run in parallel, one file (or export chunk) per "
             "worker at a time (default: $SLURM_CPUS_PER_TASK if set, else 1)",
    )
    parser.add_argument(
        "--job_id", default=os.environ.get("SLURM_JOB_ID"),
        help="Identifier stamped into the default summary CSV filename "
             "(default: $SLURM_JOB_ID if set under SLURM)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    cfg = {
        "input_dir": args.input_dir,
        "output_dir": args.output_dir,
        "db_encoding": args.db_encoding,
        "db_schema": args.db_schema,
    }

    # keep_default_na=False: an entity_value like "NA" or "None" must stay a string.
    results_df = pd.read_csv(args.pii_results_csv, dtype=str, keep_default_na=False)
    chunk_bytes = max(1, int(args.db_chunk_mb * 1024 * 1024))

    keep_types = {e.strip().upper() for e in (args.keep_entities or "").split(",") if e.strip()}
    if args.keep_names:
        keep_types.add("PERSON")
    if keep_types:
        print(f"Leaving these entity types unchanged: {', '.join(sorted(keep_types))}")
        unknown = keep_types - set(results_df["entity_type"].unique())
        if unknown:
            print(f"Note: no findings of type {', '.join(sorted(unknown))} in the results CSV.")

    tasks, db_files, kept_counts = build_tasks(results_df, cfg, chunk_bytes, keep_types)
    num_procs = max(1, args.num_procs)

    if not tasks:
        print(f"No PII entities found in {args.pii_results_csv}; nothing to anonymize.")
        outcomes = []
    elif num_procs == 1:
        _init_worker(cfg)
        outcomes = [process_task(t) for t in tasks]
    else:
        with mp.Pool(processes=num_procs, initializer=_init_worker, initargs=(cfg,)) as pool:
            outcomes = list(pool.imap_unordered(process_task, tasks))

    records = []
    db_outcomes = defaultdict(list)
    for o in outcomes:
        if o["kind"] == "db_chunk":
            db_outcomes[o["rel"]].append(o)
            continue
        label = FILE_TYPE_LABELS["text_stream" if o["kind"] == "text_stream"
                                 else o["source_type"]]
        records.append(summary_record(o["rel"], label, o["output"], o["count"],
                                      o["unmatched"], o["error"],
                                      kept_counts.get(o["rel"], 0), keep_types))

    # Stitch each export's chunk files together, in order.
    for rel, info in db_files.items():
        chunks = db_outcomes.get(rel, [])
        errors = [o["error"] for o in chunks if o["error"]]
        count = sum(o["count"] for o in chunks)
        unmatched = sum(o["unmatched"] for o in chunks)
        if not errors:
            try:
                with open(info["output"], "wb") as dst:
                    for part in info["parts"]:
                        with open(part, "rb") as src:
                            while True:
                                block = src.read(1 << 20)
                                if not block:
                                    break
                                dst.write(block)
            except Exception as e:
                errors.append(f"assembling output: {e}")
        for part in info["parts"]:
            if os.path.exists(part):
                os.remove(part)
        if errors and os.path.exists(info["output"]):
            os.remove(info["output"])  # never leave a partial export behind
        records.append(summary_record(rel, FILE_TYPE_LABELS[info["source_type"]],
                                      info["output"], count, unmatched,
                                      "; ".join(errors[:5]) if errors else None,
                                      kept_counts.get(rel, 0), keep_types))

    suffix = f"_{args.job_id}" if args.job_id else ""
    csv_output = args.csv_output or os.path.join(args.output_dir, f"anonymization_summary{suffix}.csv")
    summary = pd.DataFrame(records)
    if records:
        summary = summary.sort_values("Original_File")
    summary.to_csv(csv_output, index=False)

    partial = sum(1 for r in records if r["Status"] != "Success")
    print(f"Anonymized {len(records)} file(s) referenced in {args.pii_results_csv}.")
    if keep_types:
        print(f"Left {sum(kept_counts.values())} findings of type "
              f"{', '.join(sorted(keep_types))} unchanged.")
    if partial:
        print(f"WARNING: {partial} file(s) failed or have findings that could not be "
              f"located; see Status/Details in the summary.")
    print(f"Files in {args.input_dir} not present in that results CSV were left untouched.")
    print(f"Summary written to {csv_output}")


if __name__ == "__main__":
    main()
