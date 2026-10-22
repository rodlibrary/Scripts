"""
PII discovery pipeline.

Scans a directory containing PDFs, images, plain text, email (.eml), CSV
files, and Intranet Quorum (IQ) / Senate CSS database exports, runs
OCR/text extraction where needed, and searches everything for PII using
Microsoft Presidio. Results are written to a single CSV report.

Design notes (why different Presidio strategies are used):
  - PDFs / images / .txt / .eml files produce *unstructured* free text.
    That text is searched directly with
    `presidio_analyzer.AnalyzerEngine.analyze()`, which returns
    character-offset matches (RecognizerResult) - exactly what we want for
    a text blob.
  - .csv files are *structured*: PII usually already lives in named
    columns. `presidio_structured.PandasAnalysisBuilder` is built for that
    case - it inspects a DataFrame and figures out which columns are likely
    to contain which entity type. We then re-scan just the flagged columns
    with the analyzer so the output CSV keeps consistent per-value
    offsets/scores across every file type.
  - IQ / Senate CSS database exports (SCDIF and the older 32-field Senate
    "Archive Format") are structured too, but their schema is *published*,
    so we don't need presidio_structured to guess it. See "Database
    exports" below.

  `presidio_structured` is NOT used on raw OCR/PDF text: it isn't designed
  to find PII embedded inside a blob of narrative text, only inside
  already-separated fields/columns.

Database exports (Intranet Quorum / Senate CSS):
  SCDIF (Senate CSS Data Interchange Format) is the exit-conversion format
  defined by the Senate Sergeant at Arms; IQ and the other CSS vendors
  produce it. Each record is one line of TAB-separated ASCII, CRLF
  terminated, whose first field is a 2-character record type ("1A" person
  name, "1B" address, "2D" communication notes, "3L" custom workflow /
  casework fields, ...). Newlines inside a field are encoded as "|".
  Files typically arrive as .txt, .dat or .tab, so those extensions are
  content-sniffed rather than trusted.

  For every record we look up the field layout for its record type and
  treat each field according to its role:
    * known PII fields (names, street address, ZIP, DOB, phone, email):
      reported directly from the schema with score 1.0 and
      detector='schema' - no NLP needed, and running spaCy over millions
      of name cells would be slow and less accurate than the schema.
      Disable with --db_known_fields skip.
    * free-text fields (notes, action histories, fill-in data, document
      paths, descriptions): scanned with the Presidio analyzer. This is
      where the surprises are - SSNs in casework notes, etc.
    * key/value fields (3L custom workflow fields, 2E fill-ins): the value
      is scanned with the field *name* passed as Presidio context, and
      field names like "SSN" / "Date of Birth" / "A-Number" also produce a
      schema finding.
    * IDs, codes, flags, dates: ignored (but IDs are written into the
      `location` column so findings can be traced back to the record).
  Offices may add documented custom fields; any field beyond the known
  layout is scanned as free text, and --db_schema lets you supply the
  exact layout from the conversion documentation shipped with the export.

  The 32-field Archive Format (no record-type column) is detected and
  handled the same way, with a single fixed layout.

Long texts:
  spaCy refuses texts over 1,000,000 characters and needs roughly 1 GB of
  memory per 100,000 characters, so free text is never analyzed in one
  call. Extracted text (PDF, OCR, email, small .txt) is analyzed in
  overlapping windows of --max_text_chars, and each finding is kept only
  by the window that "owns" its start position, so nothing is reported
  twice. Plain-text files larger than --db_chunk_mb are additionally
  streamed in byte-range chunks across workers instead of being read into
  memory whole; their findings carry `byte_offset=<chunk start>,
  byte_end=<chunk end>` in `location`, and start_char/end_char are
  character offsets relative to byte_offset.

  Note: the House IQ exit format (a Microsoft Access database) is not
  handled here.

Parallelism:
  Each input file is independent, so we fan the work out across a
  multiprocessing.Pool with one worker per requested CPU. Database exports
  can be many GB in a single file, so those are additionally split into
  byte-range chunks (--db_chunk_mb) that are processed in parallel; each
  chunk starts at the first full line after its start offset. Each worker
  process builds its own AnalyzerEngine once (via an initializer) and
  reuses it for every task that lands in that worker, rather than paying
  the spaCy-model-load cost per task. Workers return plain dict rows
  instead of writing to the CSV directly, since file handles/csv.writers
  aren't safely shared across processes; the main process is the only
  one that touches the output files.
"""

import os
import re
import argparse
import csv
import fnmatch
import glob
import html
import json
import multiprocessing as mp
import sys
from collections import Counter
from email import policy
from email.parser import BytesParser

import pandas as pd
from PIL import Image
import pdfminer.high_level
import pytesseract

from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_structured import PandasAnalysisBuilder


IMAGE_EXTS = {"png", "jpg", "jpeg", "tiff", "tif", "bmp"}
UNSTRUCTURED_TEXT_EXTS = {"pdf", "txt", "eml"} | IMAGE_EXTS
STRUCTURED_EXTS = {"csv"}
# Extensions that may hold an SCDIF / Archive Format export. Content is
# sniffed; a .txt that isn't a DB export falls back to plain-text handling.
DB_CANDIDATE_EXTS = {"txt", "dat", "tab", "scdif"}

FIELDNAMES = [
    "filename",      # path relative to --input_dir
    "source_type",   # 'unstructured_text', 'structured_csv', 'scdif', 'archive_format'
    "location",      # column/row for CSVs; record/ids/field_no/field for DB exports;
                     # byte range for chunked large .txt; blank for other text
    "entity_type",
    "entity_value",
    "score",
    "start_char",    # offsets are within the field/cell for structured sources
    "end_char",
    "detector",      # 'presidio' or 'schema' (known PII field in a DB export)
]
ERROR_FIELDNAMES = ["filename", "stage", "severity", "message"]

MAX_ERRORS_PER_TASK = 100

# Characters of overlap between analysis windows. Entities shorter than this
# that straddle a window seam are still found intact.
WINDOW_OVERLAP_CHARS = 1000
_WHITESPACE_RE = re.compile(r"\s")


# --------------------------------------------------------------------------
# SCDIF / Archive Format layouts
# --------------------------------------------------------------------------
#
# Field roles:
#   None             ignore (flags, dates, codes)
#   "id"             not scanned, but included in `location` for tracing
#   "text"           free text, scanned with the Presidio analyzer
#   "known:<ENTITY>" known PII field, reported from the schema
#   "contact"        1E phone/email/URL value; entity depends on its type field
#   "kv_name"        name of a key/value pair (3L, 2E)
#   "kv_value"       value of a key/value pair; scanned with the name as context
#   "code_desc"      8A code description; a staff name when Code Type is STAFF
#
# Field order follows the SCDIF specification (Senate Sergeant at Arms;
# reproduced in Appendix C of the SAA Congressional Papers Section report
# "Archiving Constituent Services Data of the U.S. Congress", 2017).

T, ID, N = "text", "id", None
PERSON, ADDR, ZIP = "known:PERSON", "known:STREET_ADDRESS", "known:ZIP_CODE"

def _notes(owner_fields):
    """Layout for the many '<owner> Text (Comments or Notes)' record types."""
    return [("Record Type", N)] + owner_fields + [
        ("Sequence", N), ("Text", T), ("Date", N), ("Time", N), ("User ID", N)]


def _attachments(owner_fields):
    """Layout for the '<owner> Attachments' record types."""
    return [("Record Type", N)] + owner_fields + [
        ("Document Name", T), ("Document ID", ID), ("User ID", N),
        ("Attached Date", N), ("Text", T)]


_EVENT_COMMON = [
    ("Event Description", T), ("Contact", T), ("County", N), ("Organization", T),
    ("Start Date", N), ("Start Time", N), ("End Date", N), ("End Time", N),
    ("Time Zone", N), ("Location", T), ("Status", N), ("Private Flag", N),
    ("Scheduled Date", N), ("Scheduled By", N), ("Revision Date", N),
    ("Revised By", N), ("Address Line 1", T), ("Address Line 2", T),
    ("Address Line 3", T), ("Address Line 4", T), ("City", N), ("State Code", N),
    ("Zip Code", N), ("Country", N),
]

SCDIF_SCHEMA = {
    # ---- 1: Person data ----
    "1A": [("Record Type", N), ("Person ID", ID), ("Person Type", ID),
           ("Prefix", N), ("First Name", PERSON), ("Middle Name", N),
           ("Last Name", PERSON), ("Suffix", N), ("Appellation", N),
           ("Organization Name", N), ("Salutation", T),
           ("Date of Birth", "known:DATE_OF_BIRTH"), ("No Mail Flag", N),
           ("Deceased Flag", N), ("Spouse Name", PERSON), ("Email Flag", N)],
    "1B": [("Record Type", N), ("Person ID", ID), ("Address ID", ID),
           ("Address Type", N), ("Primary Flag", N), ("Default Address Flag", N),
           ("Title", N), ("Organization Name", N), ("Address Line 1", ADDR),
           ("Address Line 2", ADDR), ("Address Line 3", ADDR),
           ("Address Line 4", ADDR), ("City", N), ("State Code", N),
           ("Zip Code", ZIP), ("Carrier Route", N), ("County", N),
           ("Country", N), ("District", N), ("Precinct", N),
           ("No Mail Flag", N), ("Deliverability", N)],
    "1C": [("Record Type", N), ("Person ID", ID), ("Person Code Type", N),
           ("Code", N)],
    "1D": _notes([("Person ID", ID)]),
    "1E": [("Record Type", N), ("Person ID", ID), ("Phone or Email Type", ID),
           ("Value", "contact"), ("Primary Flag", N), ("Invalid Flag", N)],
    "1F": _attachments([("Person ID", ID)]),
    # ---- 2: Communication data ----
    "2A": [("Record Type", N), ("Person ID", ID), ("Communication ID", ID),
           ("Workflow ID", ID), ("Workflow Person ID", N),
           ("Communication Type", N), ("User ID", N), ("Approved By", N),
           ("Status", N), ("Date In", N), ("Date Out", N), ("Reminder Date", N),
           ("Update Date", N), ("Response Type", N), ("Address ID", N),
           ("Email Address", "known:EMAIL_ADDRESS"), ("Household Flag", N),
           ("Household ID", N), ("Group Name", N), ("Salutation", T)],
    "2B": [("Record Type", N), ("Person ID", ID), ("Communication ID", ID),
           ("Communication Code", N), ("Position", N)],
    "2C": [("Record Type", N), ("Person ID", ID), ("Communication ID", ID),
           ("Document Type", ID), ("Communication Document Name", T),
           ("Communication Document ID", N), ("File Location", T)],
    "2D": _notes([("Person ID", ID), ("Communication ID", ID)]),
    "2E": [("Record Type", N), ("Person ID", ID), ("Communication ID", ID),
           ("Fill-in Field Name", "kv_name"), ("Fill-in Data", "kv_value")],
    # ---- 3: Workflow data (casework, flags, tours, ...) ----
    "3A": [("Record Type", N), ("Person ID", ID), ("Workflow ID", ID),
           ("Workflow Type", ID), ("User ID", N), ("Start Date", N),
           ("End Date", N), ("Reminder Date", N), ("Update Date", N),
           ("Workflow Description", T), ("Status", N)],
    "3B": [("Record Type", N), ("Person ID", ID), ("Workflow ID", ID),
           ("Workflow Code", N)],
    "3D": _notes([("Person ID", ID), ("Workflow ID", ID)]),
    "3E": [("Record Type", N), ("Person ID", ID), ("Workflow ID", ID),
           ("Party ID", N)],
    "3F": _attachments([("Person ID", ID), ("Workflow ID", ID)]),
    "3L": [("Record Type", N), ("Person ID", ID), ("Workflow ID", ID),
           ("Workflow Type", ID), ("Field Name", "kv_name"),
           ("Field Value", "kv_value")],
    # ---- 5: Household data ----
    "5A": [("Record Type", N), ("Household ID", ID), ("Household Name", T),
           ("Household Salutation", T)],
    "5B": [("Record Type", N), ("Household ID", ID), ("Person ID", N),
           ("Primary Contact Flag", N)],
    "5C": _attachments([("Household ID", ID)]),
    "5D": _notes([("Household ID", ID)]),
    # ---- 6: Form letter / document library ----
    "6A": [("Record Type", N), ("Document ID", ID), ("Version", N),
           ("Document Grouping ID", N), ("Document Type", N),
           ("Document Display Name", T), ("Document Description", T),
           ("Document Name", T), ("Created By", N), ("Revised By", N),
           ("Approved By", N), ("Creation Date", N), ("Revision Date", N),
           ("Last Used Date", N), ("Status", N), ("Inactive Flag", N),
           ("Virtual Directory", T)],
    "6B": [("Record Type", N), ("Document ID", ID), ("Fill-in Field Name", N)],
    "6C": [("Record Type", N), ("Document ID", ID), ("Code Type", N), ("Code", N)],
    "6D": [("Record Type", N), ("Document ID", ID), ("Document Name", T),
           ("User ID", N), ("Attached Date", N), ("Text", T),
           ("Form Letter Attachment Flag", N)],
    "6E": _notes([("Document ID", ID)]),
    "6F": [("Record Type", N), ("Document ID", ID), ("Owned By", N)],
    # ---- 7: Schedule data ----
    "7A": [("Record Type", N), ("Event ID", ID), ("Recurrence ID", N)] + _EVENT_COMMON,
    "7B": [("Record Type", N), ("Event ID", ID), ("Event Code", N)],
    "7C": [("Record Type", N), ("Event ID", ID), ("Participant Type", N),
           ("Participant ID", N)],
    "7D": _attachments([("Event ID", ID)]),
    "7E": _notes([("Event ID", ID)]),
    "7F": [("Record Type", N), ("Event ID", ID), ("Workflow ID", N)],
    "7H": [("Record Type", N), ("Recurrence ID", ID), ("Start Series Date", N),
           ("End Series Date", N), ("Recurrence Type", N),
           ("Recurrence Interval", N)],
    "7I": [("Record Type", N), ("Recurrence ID", ID), ("Not Used", N)] + _EVENT_COMMON,
    "7J": [("Record Type", N), ("Recurrence ID", ID), ("Event Code", N)],
    "7K": [("Record Type", N), ("Recurrence ID", ID), ("Participant Type", N),
           ("Participant ID", N)],
    "7L": _attachments([("Recurrence ID", ID)]),
    "7M": _notes([("Recurrence ID", ID)]),
    "7N": [("Record Type", N), ("Recurrence ID", ID), ("Workflow ID", N)],
    # ---- 8: Code tables ----
    "8A": [("Record Type", N), ("Code Type", ID), ("Code", ID),
           ("Code Description", "code_desc"), ("Inactive Flag", N),
           ("User ID", N)],
}
# 7I has "Not used" in the slots where 7A has Recurrence/Start Date/End Date.
SCDIF_SCHEMA["7I"][7] = ("Not Used", N)   # spec field 8
SCDIF_SCHEMA["7I"][9] = ("Not Used", N)   # spec field 10

# Senate 32-field "Archive Format" (one record per incoming/outgoing pair).
ARCHIVE_FORMAT_FIELDS = [
    ("Prefix", N), ("First Name", PERSON), ("Middle Name", N),
    ("Last Name", PERSON), ("Suffix", N), ("Appellation", N), ("Title", N),
    ("Organization", N), ("Address Line 1", ADDR), ("Address Line 2", ADDR),
    ("Address Line 3", ADDR), ("Address Line 4", ADDR), ("City", N),
    ("State Code", N), ("Zip Code", ZIP), ("Country", N),
    ("In Correspondence ID", ID), ("In Correspondence Type", N),
    ("In Correspondence Method", N), ("In Correspondence Date", N),
    ("In Correspondence Topics", N), ("In Correspondence Text", T),
    ("In Correspondence Doc Names", T), ("In Correspondence Filing No", N),
    ("Out Correspondence ID", ID), ("Out Correspondence Type", N),
    ("Out Correspondence Method", N), ("Out Correspondence Date", N),
    ("Out Correspondence Topics", N), ("Out Correspondence Text", T),
    ("Out Correspondence Doc Names", T), ("Out Correspondence Fill-in Fields", T),
]

SCDIF_RECORD_TYPE_RE = re.compile(r"^[1-8][A-Z]$")

# Key/value field names (3L custom casework fields, 2E fill-ins) that mark
# the value itself as a known identifier.
KV_KNOWN_PATTERNS = [
    (re.compile(r"(?<![a-z])ssn(?![a-z])|social\s*sec", re.I), "US_SSN"),
    (re.compile(r"(?<![a-z])dob(?![a-z])|date\s*of\s*birth|birth\s*date", re.I),
     "DATE_OF_BIRTH"),
    (re.compile(r"(?<![a-z])a[-\s]?(number|no|#)|alien\s*(reg|no|num)", re.I),
     "ALIEN_NUMBER"),
    (re.compile(r"passport", re.I), "US_PASSPORT"),
    (re.compile(r"medicare|medicaid", re.I), "MEDICARE_MEDICAID_ID"),
]


def load_schema_override(path):
    """
    JSON file mapping record type -> list of [field_name, role] pairs, e.g.
        {"1A": [["Record Type", null], ["Person ID", "id"], ...,
                ["Gender", null]]}
    Each listed record type replaces the built-in layout entirely; use the
    conversion documentation shipped with the export to build it.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {rt.upper(): [tuple(pair) for pair in fields] for rt, fields in raw.items()}


# --------------------------------------------------------------------------
# Text extraction
# --------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: str) -> str:
    """Extracts raw text from a PDF file using pdfminer.six."""
    return pdfminer.high_level.extract_text(pdf_path)


def extract_text_from_image(image_path: str) -> str:
    """Extracts text from an image file using Tesseract OCR."""
    img = Image.open(image_path)
    return pytesseract.image_to_string(img)


def extract_text_from_txt(txt_path: str) -> str:
    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def extract_text_from_eml(eml_path: str) -> str:
    """
    Headers plus text/plain and text/html bodies of an .eml message (how
    SCDIF exports deliver incoming email). Attached files inside the
    message are not opened.
    """
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


# --------------------------------------------------------------------------
# Analysis helpers
# --------------------------------------------------------------------------

def analyze_unstructured_text(analyzer: AnalyzerEngine, text: str, language: str,
                               entities=None, context=None):
    """Run the Presidio analyzer directly on a (short) piece of free text."""
    return analyzer.analyze(text=text, language=language, entities=entities,
                            context=context or None)


def analyze_long_text(analyzer: AnalyzerEngine, text: str, language: str,
                      entities=None, max_chars: int = 100_000, limit: int = None):
    """
    Analyze text of any length in overlapping windows of about `max_chars`.

    Each window owns a "keep" region [pos, nxt) whose boundaries are snapped
    to whitespace; it is analyzed together with WINDOW_OVERLAP_CHARS of
    trailing context, but only results that *start* inside its keep region
    are returned. Keep regions don't overlap, so every entity is reported
    once, with offsets relative to the start of `text`.

    `limit`: only return results starting before this offset (the rest of
    `text` is look-ahead context belonging to the next byte-range chunk).

    Returns a list of RecognizerResult.
    """
    n = len(text)
    limit = n if limit is None else min(limit, n)
    overlap = min(WINDOW_OVERLAP_CHARS, max(1, max_chars // 4))
    step = max(1, max_chars - overlap)

    if limit == n and n <= max_chars:
        return analyze_unstructured_text(analyzer, text, language, entities)

    results = []
    pos = 0
    while pos < limit:
        nxt = pos + step
        if nxt >= limit:
            nxt = limit
        else:
            m = _WHITESPACE_RE.search(text, nxt, min(n, nxt + overlap // 2))
            if m:
                nxt = m.end()
        win_end = min(n, nxt + overlap)
        window = text[pos:win_end]
        if window.strip():
            for res in analyze_unstructured_text(analyzer, window, language, entities):
                start = res.start + pos
                if pos <= start < nxt:
                    res.start, res.end = start, res.end + pos
                    results.append(res)
        pos = nxt
    return results


def _snap_to_whitespace(f, offset: int, size: int, search_bytes: int = 4096) -> int:
    """
    Smallest position >= offset that follows a whitespace byte (searching up
    to `search_bytes`), so chunk boundaries don't split words. Both chunks
    sharing a boundary compute the same value.
    """
    if offset <= 0 or offset >= size:
        return max(0, min(offset, size))
    f.seek(offset - 1)
    buf = f.read(search_bytes)
    for i, b in enumerate(buf):
        if b in b" \t\r\n\f\v":
            return offset + i  # position just after whitespace byte at offset-1+i
    return offset


def analyze_structured_csv(analyzer: AnalyzerEngine, csv_path: str, language: str,
                            entities=None):
    """
    Use presidio-structured to identify which columns of a CSV likely hold
    PII, then re-run the analyzer over the actual cell values in those
    columns so we get concrete per-value matches (offsets + scores) rather
    than just a column-level label.

    Returns a list of tuples:
        (column_name, row_index, RecognizerResult, cell_text)
    """
    df = pd.read_csv(csv_path, dtype=str).fillna("")

    # PandasAnalysisBuilder inspects sample values per column and returns a
    # StructuredAnalysis(entity_mapping={col: entity}).
    tabular_analysis = PandasAnalysisBuilder().generate_analysis(df, language=language)

    matches = []
    for column, entity_type in tabular_analysis.entity_mapping.items():
        if entities and entity_type not in entities:
            continue
        if column not in df.columns:
            continue
        for row_idx, cell_value in df[column].items():
            cell_text = str(cell_value)
            if not cell_text.strip():
                continue
            results = analyzer.analyze(
                text=cell_text,
                language=language,
                entities=[entity_type] if entity_type else None,
            )
            for res in results:
                matches.append((column, row_idx, res, cell_text))

    return matches


def _clean_record_type(field: str) -> str:
    """Record type with any UTF-8 BOM, quotes and padding removed."""
    return field.lstrip("\ufeff").strip().strip('"\'').strip().upper()


def sniff_db_format(path: str, encoding: str, sample_lines: int = 200,
                    max_line_bytes: int = 1 << 20):
    """
    Looks at the first lines of the file and returns (format, diagnostics),
    where format is 'scdif', 'archive_format', or None. SCDIF lines are
    TAB-separated and start with a record type like '1A'; Archive Format
    lines have 32 TAB-separated fields and no record type. `diagnostics`
    is a short PII-free summary of what was seen, for the error log.
    """
    lines = []
    with open(path, "rb") as f:
        for _ in range(sample_lines):
            raw = f.readline(max_line_bytes)  # bounded: a file may have no newlines
            if not raw:
                break
            line = raw.decode(encoding, errors="replace").rstrip("\r\n")
            if line.strip():
                lines.append(line)
    if not lines:
        return None, "no non-empty lines in sample"
    tabbed = [ln for ln in lines if "\t" in ln]
    rt_hits = sum(1 for ln in tabbed
                  if SCDIF_RECORD_TYPE_RE.match(_clean_record_type(ln.split("\t", 1)[0])))
    n_archive = len(ARCHIVE_FORMAT_FIELDS)
    widths = Counter(len(ln.split("\t")) for ln in tabbed)
    width_hits = sum(c for w, c in widths.items() if n_archive <= w <= n_archive + 1)
    longest = max(len(ln) for ln in lines)
    diagnostics = (
        f"sampled {len(lines)} lines: {len(tabbed)} tab-delimited, "
        f"{rt_hits} start with an SCDIF record type, "
        f"most common field counts {dict(widths.most_common(3)) or 'n/a'}, "
        f"longest line {longest:,} chars"
        + (" - the file may have no line breaks" if longest >= max_line_bytes - 1 else "")
    )
    if len(tabbed) < 0.9 * len(lines):
        return None, diagnostics
    if rt_hits >= 0.9 * len(tabbed):
        return "scdif", diagnostics
    if width_hits >= 0.9 * len(tabbed):
        return "archive_format", diagnostics
    return None, diagnostics


def iter_lines_in_range(path: str, start: int, end: int):
    """
    Yields (byte_offset, raw_line) for every line that *starts* in
    [start, end). A line that straddles `start` belongs to the previous
    chunk, so every line is processed exactly once across chunks.
    """
    with open(path, "rb") as f:
        if start > 0:
            f.seek(start - 1)
            if f.read(1) != b"\n":
                f.readline()  # finish the partial line owned by the previous chunk
        pos = f.tell()
        while pos < end:
            line = f.readline()
            if not line:
                break
            yield pos, line
            pos += len(line)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _context_words(name: str):
    return [w for w in re.findall(r"[a-z]+", name.lower()) if len(w) > 1]


# --------------------------------------------------------------------------
# Worker-process machinery
# --------------------------------------------------------------------------

# One AnalyzerEngine + run config per worker process, built once via the
# Pool initializer and reused across every task that worker handles
# (loading the spaCy model is the expensive part).
_worker_analyzer = None
_cfg = None


def _build_analyzer(language: str, spacy_model: str = None) -> AnalyzerEngine:
    if not spacy_model:
        # Requires: `python -m spacy download en_core_web_lg` (or the model
        # matching your chosen language) to have been run before first use.
        return AnalyzerEngine()
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    nlp_engine = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": language, "model_name": spacy_model}],
    }).create_engine()
    return AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=[language])


def _init_worker(cfg):
    global _worker_analyzer, _cfg
    _cfg = cfg
    _worker_analyzer = _build_analyzer(cfg["language"], cfg.get("spacy_model"))


class _Collector:
    """Accumulates result/error rows for one task."""

    def __init__(self, filename):
        self.filename = filename
        self.rows = []
        self.errors = []
        self.suppressed_errors = 0

    def error(self, stage, message, severity="error"):
        if len(self.errors) >= MAX_ERRORS_PER_TASK:
            self.suppressed_errors += 1
            return
        self.errors.append({"filename": self.filename, "stage": stage,
                            "severity": severity, "message": str(message)})

    def row(self, source_type, location, entity_type, value, score, start, end,
            detector):
        self.rows.append({
            "filename": self.filename,
            "source_type": source_type,
            "location": location,
            "entity_type": entity_type,
            "entity_value": value,
            "score": score,
            "start_char": start,
            "end_char": end,
            "detector": detector,
        })

    def result(self):
        if self.suppressed_errors:
            self.errors.append({
                "filename": self.filename, "stage": "summary", "severity": "warning",
                "message": f"{self.suppressed_errors} further errors suppressed"})
        return self.rows, self.errors


def _display_name(path):
    return os.path.relpath(path, _cfg["input_dir"])


def process_task(task):
    """Pool entry point: dispatches on task kind."""
    if task["kind"] == "db_chunk":
        return process_db_chunk(task)
    if task["kind"] == "text_chunk":
        return process_text_chunk(task)
    return process_file(task)


def process_file(task):
    """
    Runs in a worker process. Extracts + analyzes a single file and returns
    (rows, errors) as plain dicts/lists so they can be pickled back to the
    main process, which is the only place that writes to the output CSVs.
    """
    file_path = task["path"]
    language, entities = _cfg["language"], _cfg["entities"]
    out = _Collector(_display_name(file_path))
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    analyzer = _worker_analyzer

    if ext in STRUCTURED_EXTS:
        # --- Structured (CSV) path: presidio_structured + analyzer ---
        try:
            matches = analyze_structured_csv(analyzer, file_path, language, entities)
        except Exception as e:
            out.error("structured_analysis", e)
            return out.result()

        for column, row_idx, res, cell_text in matches:
            out.row("structured_csv", f"column={column},row={row_idx}",
                    res.entity_type, cell_text[res.start:res.end], res.score,
                    res.start, res.end, "presidio")
        return out.result()

    if ext not in UNSTRUCTURED_TEXT_EXTS:
        out.error("extraction", "Unsupported file type", severity="warning")
        return out.result()

    # --- Unstructured path: extract text, then presidio_analyzer ---
    try:
        if ext == "pdf":
            extracted_text = extract_text_from_pdf(file_path)
        elif ext in IMAGE_EXTS:
            extracted_text = extract_text_from_image(file_path)
        elif ext == "eml":
            extracted_text = extract_text_from_eml(file_path)
        else:
            extracted_text = extract_text_from_txt(file_path)
    except Exception as e:
        out.error("extraction", e)
        return out.result()

    if not extracted_text.strip():
        out.error("extraction", "No text extracted", severity="warning")
        return out.result()

    try:
        results = analyze_long_text(analyzer, extracted_text, language, entities,
                                    max_chars=_cfg["max_text_chars"])
    except Exception as e:
        out.error("analysis", e)
        return out.result()

    for res in results:
        if not isinstance(res, RecognizerResult):
            continue
        out.row("unstructured_text", "", res.entity_type,
                extracted_text[res.start:res.end], res.score, res.start, res.end,
                "presidio")

    return out.result()


def _analyze_db_record(out, source_type, rec_type, values, layout, offset):
    """Applies the field roles in `layout` to one parsed record."""
    cfg = _cfg
    entities = cfg["entities"]
    report_known = cfg["db_known_fields"] == "report"
    analyzer = _worker_analyzer

    fields = []
    for i, value in enumerate(values):
        if i < len(layout):
            name, role = layout[i]
        else:
            name, role = f"Extra Field {i + 1}", T  # undocumented custom field
        fields.append((name, role, value, i + 1))
    by_name = {name: value for name, _, value, _ in fields}

    id_parts = [f"{_slug(name)}={value.strip()}"
                for name, role, value, _ in fields if role == ID and value.strip()]
    base_loc = ",".join([f"record={rec_type}", f"byte_offset={offset}"] + id_parts)

    # `field=` is always last in `location`: names of custom fields come from
    # the data and may contain commas. `field_no` is the 1-based position.
    def known(entity, name, value, field_no):
        if not report_known or (entities and entity not in entities):
            return
        v = value.strip()
        if v:
            lead = len(value) - len(value.lstrip())  # offsets into the raw field
            out.row(source_type, f"{base_loc},field_no={field_no},field={name}",
                    entity, v, 1.0, lead, lead + len(v), "schema")

    def scan(name, value, field_no, context=None):
        if len(value.strip()) < 2:
            return
        # SCDIF encodes in-field line breaks as "|"; restoring them helps
        # sentence segmentation and keeps offsets identical (1 char -> 1 char).
        text = value.replace("|", "\n") if source_type == "scdif" else value
        try:
            if len(text) > cfg["max_text_chars"]:
                results = analyze_long_text(analyzer, text, cfg["language"], entities,
                                            max_chars=cfg["max_text_chars"])
            else:
                results = analyze_unstructured_text(analyzer, text, cfg["language"],
                                                    entities, context)
        except Exception as e:
            out.error("analysis", f"{base_loc},field={name}: {e}")
            return
        for res in results:
            out.row(source_type, f"{base_loc},field_no={field_no},field={name}",
                    res.entity_type,
                    value[res.start:res.end], res.score, res.start, res.end,
                    "presidio")

    kv_name = next((v for _, role, v, _ in fields if role == "kv_name"), "")

    for name, role, value, field_no in fields:
        if role is None or role == ID or role == "kv_name" or not value.strip():
            continue
        if role.startswith("known:"):
            known(role.split(":", 1)[1], name, value, field_no)
        elif role == "text":
            scan(name, value, field_no)
        elif role == "contact":
            ctype = by_name.get("Phone or Email Type", "").strip().upper()
            entity = {"EMAIL": "EMAIL_ADDRESS", "URL": "URL"}.get(ctype, "PHONE_NUMBER")
            known(entity, name, value, field_no)
        elif role == "kv_value":
            label = f"{name}[{kv_name.strip()}]" if kv_name.strip() else name
            for pattern, entity in KV_KNOWN_PATTERNS:
                if pattern.search(kv_name):
                    known(entity, label, value, field_no)
                    break
            scan(label, value, field_no, _context_words(kv_name))
        elif role == "code_desc":
            if by_name.get("Code Type", "").strip().upper() == "STAFF":
                known("PERSON", name, value, field_no)


def process_db_chunk(task):
    """
    Runs in a worker process. Parses one byte range of an SCDIF or Archive
    Format export and analyzes each record field by field.
    """
    cfg = _cfg
    fmt = task["format"]
    out = _Collector(_display_name(task["path"]))
    encoding = cfg["db_encoding"]
    schema = cfg["scdif_schema"]
    unknown_types = Counter()

    try:
        for offset, raw in iter_lines_in_range(task["path"], task["start"], task["end"]):
            line = raw.decode(encoding, errors="replace").rstrip("\r\n")
            if not line.strip():
                continue
            values = line.split("\t")

            if fmt == "scdif":
                rec_type = _clean_record_type(values[0])
                layout = schema.get(rec_type)
                if layout is None:
                    # Unknown/vendor-added record type: scan everything after
                    # the record type as free text so nothing is silently skipped.
                    unknown_types[rec_type[:20]] += 1
                    layout = [("Record Type", N)]
            else:
                rec_type = "ARCHIVE"
                layout = ARCHIVE_FORMAT_FIELDS
                if offset == 0 and _clean_record_type(values[0]) == "PREFIX":
                    continue  # header row

            _analyze_db_record(out, fmt, rec_type, values, layout, offset)
    except Exception as e:
        out.error("db_parse", f"bytes {task['start']}-{task['end']}: {e}")

    for rec_type, count in unknown_types.items():
        out.error("db_parse",
                  f"Unknown SCDIF record type '{rec_type}' ({count} records, "
                  f"bytes {task['start']}-{task['end']}); all fields scanned as text. "
                  f"Add its layout with --db_schema.",
                  severity="warning")
    return out.result()


def process_text_chunk(task):
    """
    Runs in a worker process. Analyzes one byte range of a large plain-text
    file without loading the rest of the file. Chunk boundaries are snapped
    to whitespace, and the chunk reads WINDOW_OVERLAP_CHARS of look-ahead so
    entities spanning a boundary are found by the chunk they start in.
    """
    cfg = _cfg
    out = _Collector(_display_name(task["path"]))
    size = task["size"]
    try:
        with open(task["path"], "rb") as f:
            kstart = _snap_to_whitespace(f, task["start"], size)
            kend = _snap_to_whitespace(f, task["end"], size)
            if kend <= kstart:
                return out.result()
            f.seek(kstart)
            raw = f.read(kend - kstart + WINDOW_OVERLAP_CHARS * 4)
    except Exception as e:
        out.error("extraction", f"bytes {task['start']}-{task['end']}: {e}")
        return out.result()

    encoding = cfg["text_encoding"]
    core_chars = len(raw[:kend - kstart].decode(encoding, errors="replace"))
    text = raw.decode(encoding, errors="replace")
    try:
        results = analyze_long_text(_worker_analyzer, text, cfg["language"],
                                    cfg["entities"], max_chars=cfg["max_text_chars"],
                                    limit=core_chars)
    except Exception as e:
        out.error("analysis", f"bytes {kstart}-{kend}: {e}")
        return out.result()

    for res in results:
        out.row("unstructured_text", f"byte_offset={kstart},byte_end={kend}", res.entity_type,
                text[res.start:res.end], res.score, res.start, res.end, "presidio")
    return out.result()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def discover_files(input_dir, recursive, exclude_dir, extra_excludes=()):
    pattern = (os.path.join(input_dir, "**", "*") if recursive
               else os.path.join(input_dir, "*"))
    excls = [os.path.abspath(d) + os.sep for d in [exclude_dir, *extra_excludes]]
    found = []
    for p in glob.glob(pattern, recursive=recursive):
        if not os.path.isfile(p):
            continue
        if any((os.path.abspath(p) + os.sep).startswith(e) for e in excls):
            continue  # never rescan our own output or excluded folders
        if os.path.basename(p).startswith("."):
            continue
        found.append(p)
    return sorted(found)


def filter_only(file_paths, input_dir, patterns):
    """
    Keep only files matching one of `patterns` (from --only). A pattern is a
    path relative to input_dir ("export1.dat", "docs/letter.pdf"), may use
    wildcards ("docs/*.eml", "*.dat"), and may be an absolute path inside
    input_dir. A pattern without "/" also matches a file name in any
    subfolder. Returns (kept_paths, patterns_that_matched_nothing).
    """
    root = os.path.abspath(input_dir)
    norm = []
    for pat in patterns:
        if os.path.isabs(pat):
            pat = os.path.relpath(os.path.abspath(pat), root)
        pat = pat.replace(os.sep, "/")
        if pat.startswith("./"):
            pat = pat[2:]
        norm.append(pat)
    kept, used = [], set()
    for fp in file_paths:
        rel = os.path.relpath(fp, root).replace(os.sep, "/")
        for pat in norm:
            if fnmatch.fnmatchcase(rel, pat) or ("/" not in pat and
                                                 fnmatch.fnmatchcase(os.path.basename(rel), pat)):
                kept.append(fp)
                used.add(pat)
                break
    return kept, [p for p in norm if p not in used]


def looks_like_delimited_table(path, sample_lines=200, min_fields=5):
    """True if most sampled lines split into several fields on '|' or TAB,
    i.e. the file is probably a database export in a layout we don't read."""
    lines = []
    with open(path, "rb") as f:
        for _ in range(sample_lines):
            raw = f.readline(1 << 20)
            if not raw:
                break
            if raw.strip():
                lines.append(raw)
    if len(lines) < 3:
        return False
    for d in (b"|", b"\t"):
        if sum(1 for ln in lines if ln.count(d) >= min_fields - 1) >= 0.6 * len(lines):
            return True
    return False


def build_tasks(file_paths, args, input_dir):
    """Returns (tasks, pre_errors). DB exports are split into byte chunks."""
    chunk_bytes = max(1, int(args.db_chunk_mb * 1024 * 1024))
    tasks, pre_errors = [], []
    for fp in file_paths:
        ext = os.path.splitext(fp)[1].lower().lstrip(".")
        rel = os.path.relpath(fp, input_dir)
        if ext in DB_CANDIDATE_EXTS:
            try:
                fmt, diagnostics = sniff_db_format(fp, args.db_encoding)
            except Exception as e:
                pre_errors.append({"filename": os.path.relpath(fp, input_dir),
                                   "stage": "db_sniff", "severity": "error",
                                   "message": str(e)})
                continue
            if fmt:
                size = os.path.getsize(fp)
                for start in range(0, size, chunk_bytes):
                    tasks.append({"kind": "db_chunk", "path": fp, "format": fmt,
                                  "start": start,
                                  "end": min(start + chunk_bytes, size)})
                continue
            if ext != "txt":
                pre_errors.append({
                    "filename": rel, "stage": "db_sniff", "severity": "warning",
                    "message": "Not recognized as SCDIF or Senate Archive Format; "
                               f"skipped ({diagnostics})"})
                continue
            size = os.path.getsize(fp)
            if size <= chunk_bytes and looks_like_delimited_table(fp):
                pre_errors.append({
                    "filename": rel, "stage": "db_sniff", "severity": "warning",
                    "message": "This .txt looks like a delimited database export but is not "
                               "standard SCDIF (e.g. pipe-delimited or with a header row), so "
                               "it was scanned as plain text and NO field policy applies. "
                               "Convert it first with convert_to_scdif.py "
                               f"({diagnostics})"})
            if size > chunk_bytes:
                # Big plain text: stream it in byte ranges instead of loading it.
                pre_errors.append({
                    "filename": rel, "stage": "db_sniff", "severity": "warning",
                    "message": f"{size / 1e6:,.0f} MB .txt not recognized as SCDIF or "
                               f"Archive Format, so scanned as plain text in chunks. "
                               f"If it is an IQ export, convert it first with "
                               f"convert_to_scdif.py ({diagnostics})"})
                for start in range(0, size, chunk_bytes):
                    tasks.append({"kind": "text_chunk", "path": fp, "size": size,
                                  "start": start,
                                  "end": min(start + chunk_bytes, size)})
                continue
        tasks.append({"kind": "file", "path": fp})
    return tasks, pre_errors


def main():
    parser = argparse.ArgumentParser(description="OCR + PII analysis pipeline")
    parser.add_argument(
        "--input_dir",
        default="pii",
        help="Directory containing input files (default: ./pii)",
    )
    parser.add_argument(
        "--output_dir",
        default=None,
        help="Directory to write results/errors into "
             "(default: <input_dir>/results)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Also scan subdirectories (useful for SCDIF exports, which keep "
             "attachments and incoming email in a directory tree). The output "
             "directory is always excluded.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FILE_OR_PATTERN",
        help="Analyze only this file (path relative to --input_dir, e.g. "
             "export1.dat or docs/letter.pdf); wildcards like 'docs/*.eml' work. "
             "May be given more than once. Subfolders are searched automatically. "
             "Keep --input_dir the same as for a full run, so the anonymizer "
             "finds the original file.",
    )
    parser.add_argument(
        "--exclude_dir",
        action="append",
        default=[],
        help="Folder to skip (with everything under it); may be given more than "
             "once. Use it to keep earlier results/anonymized output that lives "
             "inside --input_dir from being scanned.",
    )
    parser.add_argument("--language", default="en", help="Language code for Presidio analysis")
    parser.add_argument(
        "--spacy_model",
        default=None,
        help="spaCy model for Presidio's NLP engine, e.g. en_core_web_sm "
             "(default: Presidio's default, en_core_web_lg for English)",
    )
    parser.add_argument(
        "--entities",
        default=None,
        help="Comma-separated list of entity types to restrict detection to "
             "(default: all entities Presidio supports, plus the schema-only "
             "types STREET_ADDRESS, ZIP_CODE, DATE_OF_BIRTH, ALIEN_NUMBER, "
             "MEDICARE_MEDICAID_ID for database exports)",
    )
    parser.add_argument(
        "--db_known_fields",
        choices=["report", "skip"],
        default="report",
        help="For SCDIF/Archive Format exports: 'report' writes every value in "
             "a known PII field (names, addresses, DOB, phone, email) to the "
             "results with detector=schema; 'skip' reports only what Presidio "
             "finds in free-text fields (default: report)",
    )
    parser.add_argument(
        "--db_encoding",
        default="utf-8",
        help="Text encoding of SCDIF/Archive Format files. The spec says ASCII; "
             "use cp1252 if accented characters come out garbled "
             "(default: utf-8, undecodable bytes replaced)",
    )
    parser.add_argument(
        "--db_chunk_mb",
        type=float,
        default=64,
        help="Split database exports and large .txt files into chunks of this "
             "many MB so one large file is processed by several workers "
             "(default: 64)",
    )
    parser.add_argument(
        "--max_text_chars",
        type=int,
        default=100_000,
        help="Longest piece of text handed to spaCy at once; longer text is "
             "analyzed in overlapping windows. spaCy's hard limit is 1,000,000 "
             "and it needs roughly 1 GB of RAM per 100,000 characters "
             "(default: 100000)",
    )
    parser.add_argument(
        "--db_schema",
        default=None,
        help="JSON file with SCDIF record layouts that replace the built-in "
             "ones, for offices whose conversion documentation adds custom "
             "fields. Format: {\"1A\": [[\"Record Type\", null], "
             "[\"Person ID\", \"id\"], ...]}",
    )
    parser.add_argument(
        "--num_procs",
        type=int,
        default=int(os.environ.get("SLURM_CPUS_PER_TASK", 1)),
        help="Number of worker processes to run in parallel, one file (or "
             "database chunk) per worker at a time (default: "
             "$SLURM_CPUS_PER_TASK if set, else 1)",
    )
    parser.add_argument(
        "--job_id",
        default=os.environ.get("SLURM_JOB_ID"),
        help="Identifier stamped into the output filenames, e.g. "
             "pii_results_<job_id>.csv (default: $SLURM_JOB_ID if set under "
             "SLURM, otherwise the files are left unstamped)",
    )
    args = parser.parse_args()

    entities = args.entities.split(",") if args.entities else None
    num_procs = max(1, args.num_procs)

    output_dir = args.output_dir or os.path.join(args.input_dir, "results")
    suffix = f"_{args.job_id}" if args.job_id else ""
    output_csv_path = os.path.join(output_dir, f"pii_results{suffix}.csv")
    error_csv_path = os.path.join(output_dir, f"pii_errors{suffix}.csv")

    os.makedirs(output_dir, exist_ok=True)

    scdif_schema = dict(SCDIF_SCHEMA)
    if args.db_schema:
        scdif_schema.update(load_schema_override(args.db_schema))

    cfg = {
        "input_dir": args.input_dir,
        "language": args.language,
        "entities": entities,
        "spacy_model": args.spacy_model,
        "db_known_fields": args.db_known_fields,
        "db_encoding": args.db_encoding,
        "text_encoding": "utf-8",
        "max_text_chars": max(2_000, min(args.max_text_chars, 900_000)),
        "scdif_schema": scdif_schema,
    }

    file_paths = discover_files(args.input_dir, args.recursive or bool(args.only),
                                output_dir, args.exclude_dir)
    only_errors = []
    if args.only:
        file_paths, unmatched = filter_only(file_paths, args.input_dir, args.only)
        for pat in unmatched:
            print(f"WARNING: --only '{pat}' matched no file in {args.input_dir}")
            only_errors.append({"filename": pat, "stage": "discovery", "severity": "error",
                                "message": f"--only pattern matched no file in {args.input_dir}"})
        if not file_paths:
            sys.exit(f"ERROR: no files in {args.input_dir} match --only "
                     f"{', '.join(args.only)}. Check the name and its folder "
                     f"(paths are relative to --input_dir).")
        print(f"Analyzing {len(file_paths)} file(s) selected with --only:")
        for fp in file_paths[:20]:
            print(f"  {os.path.relpath(fp, args.input_dir)}")
        if len(file_paths) > 20:
            print(f"  ... and {len(file_paths) - 20} more")
    tasks, pre_errors = build_tasks(file_paths, args, args.input_dir)
    pre_errors = only_errors + pre_errors

    with open(output_csv_path, mode="w", newline="", encoding="utf-8") as csv_file, \
         open(error_csv_path, mode="w", newline="", encoding="utf-8") as error_file:

        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
        writer.writeheader()

        error_writer = csv.DictWriter(error_file, fieldnames=ERROR_FIELDNAMES)
        error_writer.writeheader()
        error_writer.writerows(pre_errors)

        if not tasks:
            print(f"No files to analyze in {args.input_dir}")
        elif num_procs == 1:
            # Skip the Pool entirely for num_procs=1: same code path, one
            # less process to reason about, and easier to debug locally.
            _init_worker(cfg)
            for task in tasks:
                rows, errors = process_task(task)
                writer.writerows(rows)
                error_writer.writerows(errors)
        else:
            with mp.Pool(processes=num_procs, initializer=_init_worker,
                         initargs=(cfg,)) as pool:
                # imap_unordered so results/errors stream to disk as each
                # task finishes, rather than waiting on the slowest one.
                for rows, errors in pool.imap_unordered(process_task, tasks):
                    writer.writerows(rows)
                    error_writer.writerows(errors)

    print(f"Done. Results written to {output_csv_path}")
    print(f"Errors/warnings written to {error_csv_path}")


if __name__ == "__main__":
    main()
