#!/usr/bin/env python3
"""
iq_to_bigquery.py
=================
Convert Intranet Quorum (IQ) exit-conversion exports -- SCDIF and the 32-field
Archive Format -- into BigQuery tables and Looker Studio-ready views.

Pipeline
--------
  1. convert   raw export  ->  one NDJSON file + BigQuery schema per table
  2. (Presidio) redact the NDJSON files in place or to a new folder.
               Keep every key and one JSON object per line. Use
               presidio_columns.json to decide which columns to scan.
  3. load      NDJSON  ->  BigQuery tables (WRITE_TRUNCATE by default)
  4. views     create joined views to connect Looker Studio to

Usage
-----
  pip install google-cloud-bigquery        # only needed for load / views
  gcloud auth application-default login     # or GOOGLE_APPLICATION_CREDENTIALS

  # 1. Convert
  python iq_to_bigquery.py convert scdif  export/*.txt  --out staged/
  python iq_to_bigquery.py convert archive export/*.tab --out staged/

  # 2. Run your Presidio process over staged/*.ndjson  ->  redacted/

  # 3. Load (schemas are read from staged/, data from redacted/)
  python iq_to_bigquery.py load --data-dir redacted/ --schema-dir staged/ \
      --project my-project --dataset constituent_mail --location US

  # 4. Views
  python iq_to_bigquery.py views --project my-project --dataset constituent_mail
  python iq_to_bigquery.py views --project my-project --dataset constituent_mail --print-sql

Custom layouts
--------------
Vendors must document custom fields they add to SCDIF records. If your
conversion documentation shows different or extra fields, dump the built-in
layouts, edit them, and pass the file back in:

  python iq_to_bigquery.py layout-template > layout.json
  python iq_to_bigquery.py convert scdif export/*.txt --out staged/ --layout layout.json

Each record type in layout.json lists its fields in file order (excluding the
leading Record Type column). A field is "name" (type inferred from the name)
or {"name": ..., "type": "STRING|DATE|TIME|BOOL|INT64"}. Names starting with
"_unused" are skipped. Values past the end of a layout are kept as a JSON
array in the extra_fields column, so nothing is silently dropped.
"""

import argparse
import codecs
import datetime as dt
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

# --------------------------------------------------------------------------
# Field typing
# --------------------------------------------------------------------------

INT_FIELDS = {"sequence_number"}
DATE_FIELDS = {"date_of_birth"}


def infer_type(name):
    if name in INT_FIELDS:
        return "INT64"
    if name == "date" or name.endswith("_date") or name in DATE_FIELDS:
        return "DATE"
    if name == "time" or name.endswith("_time"):
        return "TIME"
    if name.endswith("_flag"):
        return "BOOL"
    return "STRING"


def normalize_fields(fields):
    """Accept ["name", {"name":..,"type":..}] and return [(name, type)]."""
    out = []
    for f in fields:
        if isinstance(f, str):
            out.append((f, infer_type(f)))
        else:
            out.append((f["name"], f.get("type") or infer_type(f["name"])))
    return out


# --------------------------------------------------------------------------
# SCDIF layouts (Senate CSS Data Interchange Format, SAA 2017 Appendix C)
# --------------------------------------------------------------------------

ADDRESS = ["address_line_1", "address_line_2", "address_line_3",
           "address_line_4", "city", "state_code", "zip_code"]


def notes(*keys):
    return [*keys, "sequence_number", "text", "date", "time", "user_id"]


def attachments(*keys):
    return [*keys, "document_name", "document_id", "user_id", "attached_date", "text"]


SCDIF_LAYOUT = {
    # 1 - Person
    "1A": ("person", ["person_id", "person_type", "prefix", "first_name", "middle_name",
                      "last_name", "suffix", "appellation", "organization_name",
                      "salutation", "date_of_birth", "no_mail_flag", "deceased_flag",
                      "spouse_name", "email_flag"]),
    "1B": ("address", ["person_id", "address_id", "address_type", "primary_flag",
                       "default_address_flag", "title", "organization_name",
                       "address_line_1", "address_line_2", "address_line_3",
                       "address_line_4", "city", "state_code", "zip_code",
                       "carrier_route", "county", "country", "district", "precinct",
                       "no_mail_flag", "deliverability"]),
    "1C": ("person_codes", ["person_id", "person_code_type", "code"]),
    "1D": ("person_notes", notes("person_id")),
    "1E": ("person_contacts", ["person_id", "contact_type", "value", "primary_flag",
                               "invalid_flag"]),
    "1F": ("person_attachments", attachments("person_id")),
    # 2 - Communication
    "2A": ("communication", ["person_id", "communication_id", "workflow_id",
                             "workflow_person_id", "communication_type", "user_id",
                             "approved_by", "status", "date_in", "date_out",
                             "reminder_date", "update_date", "response_type",
                             "address_id", "email_address", "household_flag",
                             "household_id", "group_name", "salutation"]),
    "2B": ("communication_codes", ["person_id", "communication_id",
                                   "communication_code", "position"]),
    "2C": ("communication_documents", ["person_id", "communication_id", "document_type",
                                       "document_name", "document_id", "file_location"]),
    "2D": ("communication_notes", notes("person_id", "communication_id")),
    "2E": ("communication_fill_ins", ["person_id", "communication_id",
                                      "fill_in_field_name", "fill_in_data"]),
    # 3 - Workflow (casework, flags, tours, ...)
    "3A": ("workflow", ["person_id", "workflow_id", "workflow_type", "user_id",
                        "start_date", "end_date", "reminder_date", "update_date",
                        "workflow_description", "status"]),
    "3B": ("workflow_codes", ["person_id", "workflow_id", "workflow_code"]),
    "3D": ("workflow_notes", notes("person_id", "workflow_id")),
    "3E": ("workflow_parties", ["person_id", "workflow_id", "party_id"]),
    "3F": ("workflow_attachments", attachments("person_id", "workflow_id")),
    "3L": ("workflow_fields", ["person_id", "workflow_id", "workflow_type",
                               "field_name", "field_value"]),
    # 5 - Household
    "5A": ("household", ["household_id", "household_name", "household_salutation"]),
    "5B": ("household_members", ["household_id", "person_id", "primary_contact_flag"]),
    "5C": ("household_attachments", attachments("household_id")),
    "5D": ("household_notes", notes("household_id")),
    # 6 - Form letter / document library
    "6A": ("documents", ["document_id", "version", "document_grouping_id",
                         "document_type", "display_name", "description",
                         "document_name", "created_by", "revised_by", "approved_by",
                         "creation_date", "revision_date", "last_used_date", "status",
                         "inactive_flag", "virtual_directory"]),
    "6B": ("document_fill_ins", ["document_id", "fill_in_field_name"]),
    "6C": ("document_codes", ["document_id", "code_type", "code"]),
    "6D": ("document_attachments", ["document_id", "document_name", "user_id",
                                    "attached_date", "text",
                                    "form_letter_attachment_flag"]),
    "6E": ("document_notes", notes("document_id")),
    "6F": ("document_owners", ["document_id", "owned_by"]),
    # 7 - Schedule
    "7A": ("events", ["event_id", "recurrence_id", "event_description", "contact",
                      "county", "organization", "start_date", "start_time",
                      "end_date", "end_time", "time_zone", "location", "status",
                      "private_flag", "scheduled_date", "scheduled_by",
                      "revision_date", "revised_by", *ADDRESS, "country"]),
    "7B": ("event_codes", ["event_id", "event_code"]),
    "7C": ("event_participants", ["event_id", "participant_type", "participant_id"]),
    "7D": ("event_attachments", attachments("event_id")),
    "7E": ("event_notes", notes("event_id")),
    "7F": ("event_workflows", ["event_id", "workflow_id"]),
    "7H": ("recurrences", ["recurrence_id", "start_series_date", "end_series_date",
                           "recurrence_type", "recurrence_interval"]),
    "7I": ("recurrence_details", ["recurrence_id", "_unused_3", "event_description",
                                  "contact", "county", "organization", "_unused_8",
                                  "start_time", "_unused_10", "end_time", "time_zone",
                                  "location", "status", "private_flag",
                                  "scheduled_date", "scheduled_by", "revision_date",
                                  "revised_by", *ADDRESS, "country"]),
    "7J": ("recurrence_codes", ["recurrence_id", "event_code"]),
    "7K": ("recurrence_participants", ["recurrence_id", "participant_type",
                                       "participant_id"]),
    "7L": ("recurrence_attachments", attachments("recurrence_id")),
    "7M": ("recurrence_notes", notes("recurrence_id")),
    "7N": ("recurrence_workflows", ["recurrence_id", "workflow_id"]),
    # 8 - Code tables (topic codes, workflow codes, staff IDs, ...)
    "8A": ("codes", ["code_type", "code", "code_description", "inactive_flag",
                     "user_id"]),
}

ARCHIVE_FIELDS = [
    "prefix", "first_name", "middle_name", "last_name", "suffix", "appellation",
    "title", "organization", "address_line_1", "address_line_2", "address_line_3",
    "address_line_4", "city", "state_code", "zip_code", "country",
    "in_corr_id", "in_corr_type", "in_corr_method", "in_corr_date",
    "in_corr_topics", "in_corr_text", "in_corr_doc_names", "in_corr_filing_number",
    "out_corr_id", "out_corr_type", "out_corr_method", "out_corr_date",
    "out_corr_topics", "out_corr_text", "out_corr_doc_names",
    "out_corr_fill_in_fields",
]

RECORD_TYPE_RE = re.compile(r"^\d[A-Za-z]$")

# --------------------------------------------------------------------------
# Presidio column hints
# --------------------------------------------------------------------------

REDACT_COLUMNS = {
    "first_name", "middle_name", "last_name", "salutation", "spouse_name",
    "date_of_birth", "address_line_1", "address_line_2", "address_line_3",
    "address_line_4", "email_address", "value", "text", "contact", "fill_in_data",
    "household_name", "household_salutation", "field_value", "workflow_description",
    "event_description", "document_name", "in_corr_text", "out_corr_text",
    "in_corr_doc_names", "out_corr_doc_names", "out_corr_fill_in_fields",
    "_parse_errors", "extra_fields",
}
PRESERVE_EXACT = {
    "code", "position", "status", "sequence_number", "zip5", "state_code", "county",
    "district", "city", "country", "version", "time_zone", "deliverability",
    "in_corr_topics", "out_corr_topics", "topic", "direction", "corr_id",
    "_source_file", "_source_line", "_record_key", "code_description",
    "recurrence_interval",
}


def column_hint(name, ftype):
    if name in REDACT_COLUMNS:
        return "redact"
    if (name in PRESERVE_EXACT or name.endswith("_id") or name.endswith("_type")
            or ftype in ("DATE", "TIME", "BOOL", "INT64")
            or (name.endswith("_code") and name != "zip_code")
            or name.endswith("_by")):
        return "preserve"
    return "review"


# --------------------------------------------------------------------------
# Value parsing
# --------------------------------------------------------------------------

TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
TRUE_VALUES = {"Y", "YES", "T", "TRUE", "1"}
FALSE_VALUES = {"N", "NO", "F", "FALSE", "0"}


def coerce(raw, ftype):
    """Return (value, ok). Empty / NULL -> (None, True)."""
    v = raw.strip()
    if v == "" or v.upper() == "NULL":
        return None, True
    if ftype == "STRING":
        return v, True
    if ftype == "DATE":
        d = re.sub(r"[^0-9]", "", v)
        if len(d) == 8 and d != "00000000":
            try:
                return dt.date(int(d[:4]), int(d[4:6]), int(d[6:])).isoformat(), True
            except ValueError:
                pass
        return None, False
    if ftype == "TIME":
        m = TIME_RE.match(v)
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            if h < 24 and mi < 60 and s < 60:
                return f"{h:02d}:{mi:02d}:{s:02d}", True
        return None, False
    if ftype == "BOOL":
        u = v.upper()
        if u in TRUE_VALUES:
            return True, True
        if u in FALSE_VALUES:
            return False, True
        return None, False
    if ftype == "INT64":
        if re.fullmatch(r"-?\d+", v):
            return int(v), True
        return None, False
    return v, True


def zip5(value):
    if not value:
        return None
    m = re.match(r"^\s*(\d{5})", value)
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# I/O helpers
# --------------------------------------------------------------------------

def _cp1252_fallback(err):
    bad = err.object[err.start:err.end]
    return bad.decode("cp1252", errors="replace"), err.end


codecs.register_error("cp1252_fallback", _cp1252_fallback)


def read_lines(path, encoding, stats):
    """Yield (line_number, text). Handles CRLF/LF, BOM, and cp1252 fallback."""
    with open(path, "rb") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.rstrip(b"\r\n")
            if encoding == "auto":
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    # Only the invalid bytes fall back to cp1252, so a line that
                    # mixes UTF-8 and Windows-1252 text keeps both intact.
                    text = raw.decode("utf-8", errors="cp1252_fallback")
                    stats["lines_decoded_as_cp1252"] += 1
            else:
                text = raw.decode(encoding, errors="replace")
            if lineno == 1:
                text = text.lstrip("\ufeff")
            # Stray CR or NUL inside a record: the spec forbids them in fields.
            text = text.replace("\x00", "").replace("\r", "|")
            yield lineno, text


class Output:
    """Streams rows to <out>/<table>.ndjson and tracks schemas and stats."""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.handles = {}
        self.schemas = {}
        self.rows = Counter()
        self.parse_errors = defaultdict(Counter)
        self.rows_with_extra_fields = Counter()

    def register(self, table, fields, derived=()):
        if table in self.schemas:
            return
        cols = [(n, t) for n, t in fields if not n.startswith("_unused")]
        cols += list(derived)
        cols += [("extra_fields", "STRING"), ("_parse_errors", "STRING"),
                 ("_source_file", "STRING"), ("_source_line", "INT64")]
        self.schemas[table] = cols

    def write(self, table, row):
        fh = self.handles.get(table)
        if fh is None:
            fh = open(os.path.join(self.out_dir, f"{table}.ndjson"), "w", encoding="utf-8")
            self.handles[table] = fh
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.rows[table] += 1

    def close(self, extra_manifest):
        for fh in self.handles.values():
            fh.close()
        presidio = {}
        for table, cols in self.schemas.items():
            if self.rows[table] == 0:
                continue
            schema = [{"name": n, "type": t, "mode": "NULLABLE"} for n, t in cols]
            with open(os.path.join(self.out_dir, f"{table}.schema.json"), "w") as f:
                json.dump(schema, f, indent=2)
            hints = defaultdict(list)
            for n, t in cols:
                if n in ("_source_file", "_source_line"):
                    continue
                hints[column_hint(n, t)].append(n)
            presidio[table] = {k: hints.get(k, []) for k in ("redact", "review", "preserve")}
        path = os.path.join(self.out_dir, "presidio_columns.json")
        existing = {}
        if os.path.exists(path):
            with open(path) as f:
                existing = json.load(f)
        existing.update(presidio)
        with open(path, "w") as f:
            json.dump(existing, f, indent=2)
        manifest = {
            "generated": dt.datetime.now().isoformat(timespec="seconds"),
            "rows": dict(self.rows),
            "parse_errors": {t: dict(c) for t, c in self.parse_errors.items()},
            "rows_with_extra_fields": dict(self.rows_with_extra_fields),
            **extra_manifest,
        }
        mpath = os.path.join(self.out_dir, f"manifest_{extra_manifest['format']}.json")
        with open(mpath, "w") as f:
            json.dump(manifest, f, indent=2)
        return manifest


def build_row(out, table, fields, values, source, lineno):
    row, errors = {}, []
    for i, (name, ftype) in enumerate(fields):
        raw = values[i] if i < len(values) else ""
        if name.startswith("_unused"):
            continue
        val, ok = coerce(raw, ftype)
        if not ok:
            errors.append(f"{name}={raw.strip()}")
            out.parse_errors[table][name] += 1
        row[name] = val
    extra = list(values[len(fields):])
    while extra and extra[-1].strip() == "":
        extra.pop()
    if extra:
        row["extra_fields"] = json.dumps(extra, ensure_ascii=False)
        out.rows_with_extra_fields[table] += 1
    else:
        row["extra_fields"] = None
    row["_parse_errors"] = "; ".join(errors) or None
    row["_source_file"] = source
    row["_source_line"] = lineno
    return row


def expand_inputs(patterns):
    files = []
    for p in patterns:
        matches = sorted(glob.glob(p))
        files.extend(matches if matches else [p])
    missing = [f for f in files if not os.path.isfile(f)]
    if missing:
        sys.exit(f"Input file(s) not found: {', '.join(missing)}")
    return files


# --------------------------------------------------------------------------
# SCDIF conversion
# --------------------------------------------------------------------------

def load_layout(path):
    layout = {rt: (name, normalize_fields(fields)) for rt, (name, fields) in SCDIF_LAYOUT.items()}
    archive = normalize_fields(ARCHIVE_FIELDS)
    if path:
        with open(path) as f:
            custom = json.load(f)
        for rt, spec in custom.get("scdif", {}).items():
            rt = rt.upper()
            default_name = layout[rt][0] if rt in layout else f"custom_{rt.lower()}"
            layout[rt] = (spec.get("table", default_name), normalize_fields(spec["fields"]))
        if "archive" in custom:
            archive = normalize_fields(custom["archive"]["fields"])
    return layout, archive


def scdif_records(path, encoding, stats):
    """Yield (lineno, fields). Lines that don't start with a record type are
    treated as continuations of a field broken by a stray line feed."""
    pending, pending_line = None, None
    for lineno, text in read_lines(path, encoding, stats):
        if text.strip() == "":
            continue
        first = text.split("\t", 1)[0].strip()
        if RECORD_TYPE_RE.match(first):
            if pending is not None:
                yield pending_line, pending.split("\t")
            pending, pending_line = text, lineno
        elif pending is not None:
            pending += "|" + text
            stats["continuation_lines_joined"] += 1
        else:
            stats["lines_before_first_record_skipped"] += 1
    if pending is not None:
        yield pending_line, pending.split("\t")


def convert_scdif(files, out_dir, encoding, layout_path):
    layout, _ = load_layout(layout_path)
    out = Output(out_dir)
    stats = Counter()
    unknown = Counter()
    for path in files:
        source = os.path.basename(path)
        print(f"  reading {source} ...", file=sys.stderr)
        for lineno, values in scdif_records(path, encoding, stats):
            rt = values[0].strip().upper()
            body = values[1:]
            if rt in layout:
                name, fields = layout[rt]
                table = f"scdif_{rt.lower()}_{name}"
            else:
                # Vendor-specific record type: keep it as generic STRING columns
                # sized by the first row seen; wider rows spill into extra_fields.
                unknown[rt] += 1
                table = f"scdif_{rt.lower()}_unknown"
                if table in out.schemas:
                    fields = out.schemas[table][:-4]
                else:
                    width = len(body)
                    while width and body[width - 1].strip() == "":
                        width -= 1
                    fields = [(f"field_{i + 2}", "STRING") for i in range(max(width, 1))]
            names = {n for n, _ in fields}
            derived = [("zip5", "STRING")] if "zip_code" in names else []
            out.register(table, fields, derived)
            row = build_row(out, table, fields, body, source, lineno)
            if derived:
                row["zip5"] = zip5(row.get("zip_code"))
            out.write(table, row)
    return out.close({"format": "scdif", "files": files,
                      "unknown_record_types": dict(unknown), **stats})


# --------------------------------------------------------------------------
# Archive Format conversion
# --------------------------------------------------------------------------

def archive_records(path, encoding, delimiter, expected, stats):
    """Yield (lineno, fields). Joins a physical line onto the previous one only
    when the previous row is short AND the join still fits the field count."""
    pending, pending_line = None, None
    for lineno, text in read_lines(path, encoding, stats):
        if text.strip() == "":
            continue
        parts = text.split(delimiter)
        if pending is not None:
            if len(pending) < expected and len(pending) + len(parts) - 1 <= expected:
                pending[-1] += "|" + parts[0]
                pending.extend(parts[1:])
                stats["broken_lines_joined"] += 1
                continue
            yield pending_line, pending
        pending, pending_line = parts, lineno
    if pending is not None:
        yield pending_line, pending


def looks_like_header(values):
    if len(values) < 2:
        return False
    second = re.sub(r"[^a-z]", "", values[1].lower())
    return second in ("firstname", "first", "fname")


def convert_archive(files, out_dir, encoding, delimiter, topic_delims, layout_path,
                    has_header):
    _, fields = load_layout(layout_path)
    out = Output(out_dir)
    stats = Counter()
    main, topics = "archive_correspondence", "archive_topics"
    out.register(main, fields, [("zip5", "STRING"), ("_record_key", "STRING")])
    topic_fields = [("_record_key", "STRING"), ("direction", "STRING"),
                    ("topic", "STRING"), ("corr_id", "STRING"), ("corr_date", "DATE")]
    out.schemas[topics] = topic_fields
    splitter = re.compile("[" + re.escape(topic_delims) + "]") if topic_delims else None
    names = {n for n, _ in fields}
    for path in files:
        source = os.path.basename(path)
        print(f"  reading {source} ...", file=sys.stderr)
        first = True
        for lineno, values in archive_records(path, encoding, delimiter, len(fields), stats):
            if first:
                first = False
                if has_header is True or (has_header is None and looks_like_header(values)):
                    stats["header_rows_skipped"] += 1
                    continue
            if len(values) < len(fields):
                stats["short_rows_padded"] += 1
            row = build_row(out, main, fields, values, source, lineno)
            key = f"{source}:{lineno}"
            row["zip5"] = zip5(row.get("zip_code"))
            row["_record_key"] = key
            out.write(main, row)
            for direction in ("in", "out"):
                col = f"{direction}_corr_topics"
                if col not in names or not row.get(col):
                    continue
                parts = splitter.split(row[col]) if splitter else [row[col]]
                seen = set()
                for t in (p.strip() for p in parts):
                    if t and t not in seen:
                        seen.add(t)
                        out.write(topics, {
                            "_record_key": key, "direction": direction.upper(),
                            "topic": t, "corr_id": row.get(f"{direction}_corr_id"),
                            "corr_date": row.get(f"{direction}_corr_date")})
    return out.close({"format": "archive", "files": files, **stats})


# --------------------------------------------------------------------------
# BigQuery load
# --------------------------------------------------------------------------

def bq_client(project):
    try:
        from google.cloud import bigquery
    except ImportError:
        sys.exit("google-cloud-bigquery is not installed: pip install google-cloud-bigquery")
    return bigquery, bigquery.Client(project=project)


def cmd_load(args):
    bigquery, client = bq_client(args.project)
    ds_id = f"{args.project}.{args.dataset}"
    ds = bigquery.Dataset(ds_id)
    ds.location = args.location
    client.create_dataset(ds, exists_ok=True)
    schema_dir = args.schema_dir or args.data_dir
    schema_files = sorted(glob.glob(os.path.join(schema_dir, "*.schema.json")))
    if not schema_files:
        sys.exit(f"No *.schema.json files in {schema_dir}. Run convert first.")
    only = set(args.tables or [])
    disposition = (bigquery.WriteDisposition.WRITE_APPEND if args.append
                   else bigquery.WriteDisposition.WRITE_TRUNCATE)
    for sp in schema_files:
        table = os.path.basename(sp)[: -len(".schema.json")]
        if only and table not in only:
            continue
        data = os.path.join(args.data_dir, f"{table}.ndjson")
        if not os.path.exists(data):
            print(f"  skip {table}: {data} not found", file=sys.stderr)
            continue
        with open(sp) as f:
            schema = [bigquery.SchemaField(c["name"], c["type"], mode=c.get("mode", "NULLABLE"))
                      for c in json.load(f)]
        config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            schema=schema,
            write_disposition=disposition,
            max_bad_records=args.max_bad_records,
        )
        print(f"  loading {table} ...", file=sys.stderr)
        with open(data, "rb") as f:
            job = client.load_table_from_file(f, f"{ds_id}.{table}", job_config=config)
        try:
            job.result()
        except Exception as e:  # surface row-level errors from the job
            for err in (job.errors or [])[:10]:
                print(f"    {err}", file=sys.stderr)
            sys.exit(f"Load failed for {table}: {e}")
        loaded = client.get_table(f"{ds_id}.{table}")
        print(f"  {table}: {loaded.num_rows:,} rows")


# --------------------------------------------------------------------------
# Looker Studio views
# --------------------------------------------------------------------------

def view_definitions(ds):
    t = lambda name: f"`{ds}.{name}`"
    staff = f"""staff AS (
  SELECT code AS user_id, ANY_VALUE(code_description) AS staff_name
  FROM {t('scdif_8a_codes')}
  WHERE UPPER(code_type) = 'STAFF'
  GROUP BY code
)"""
    default_addr = f"""default_addr AS (
  SELECT * EXCEPT (rn) FROM (
    SELECT person_id, city, county, state_code, zip5, district, country,
      ROW_NUMBER() OVER (
        PARTITION BY person_id
        ORDER BY IFNULL(default_address_flag, FALSE) DESC,
                 IFNULL(primary_flag, FALSE) DESC, address_id) AS rn
    FROM {t('scdif_1b_address')}
  )
  WHERE rn = 1
)"""
    geo = lambda a, b: ",\n  ".join(
        f"COALESCE({a}.{c}, {b}.{c}) AS {c}"
        for c in ("city", "county", "state_code", "zip5", "district", "country"))

    return [
        ("v_scdif_communications",
         ["scdif_2a_communication", "scdif_1a_person", "scdif_1b_address", "scdif_8a_codes"],
         f"""WITH {staff},
{default_addr}
SELECT
  c.communication_id,
  c.person_id,
  p.person_type,
  c.communication_type,
  c.response_type,
  c.status,
  c.group_name,
  c.date_in,
  c.date_out,
  c.update_date,
  DATE_DIFF(c.date_out, c.date_in, DAY) AS days_to_close,
  c.workflow_id,
  c.user_id,
  s.staff_name AS handled_by,
  {geo('sa', 'da')}
FROM {t('scdif_2a_communication')} AS c
LEFT JOIN {t('scdif_1a_person')} AS p
  ON p.person_id = c.person_id
LEFT JOIN {t('scdif_1b_address')} AS sa
  ON sa.person_id = c.person_id AND sa.address_id = c.address_id
LEFT JOIN default_addr AS da
  ON da.person_id = c.person_id
LEFT JOIN staff AS s
  ON s.user_id = c.user_id"""),

        ("v_scdif_communication_topics",
         ["scdif_2b_communication_codes", "scdif_8a_codes", "v_scdif_communications"],
         f"""WITH topics AS (
  SELECT code, ANY_VALUE(code_description) AS topic_description
  FROM {t('scdif_8a_codes')}
  WHERE UPPER(code_type) = 'COM'
  GROUP BY code
)
SELECT
  t.communication_id,
  t.communication_code AS topic_code,
  COALESCE(k.topic_description, t.communication_code) AS topic_description,
  UPPER(t.position) AS position,
  c.* EXCEPT (communication_id)
FROM {t('scdif_2b_communication_codes')} AS t
LEFT JOIN topics AS k
  ON k.code = t.communication_code
LEFT JOIN {t('v_scdif_communications')} AS c
  ON c.communication_id = t.communication_id"""),

        ("v_scdif_workflows",
         ["scdif_3a_workflow", "scdif_1b_address", "scdif_8a_codes"],
         f"""WITH {staff},
{default_addr}
SELECT
  w.workflow_id,
  w.person_id,
  w.workflow_type,
  w.status,
  w.workflow_description,
  w.start_date,
  w.end_date,
  w.update_date,
  DATE_DIFF(w.end_date, w.start_date, DAY) AS days_open,
  w.user_id,
  s.staff_name AS handled_by,
  da.city, da.county, da.state_code, da.zip5, da.district, da.country
FROM {t('scdif_3a_workflow')} AS w
LEFT JOIN default_addr AS da
  ON da.person_id = w.person_id
LEFT JOIN staff AS s
  ON s.user_id = w.user_id"""),

        ("v_scdif_workflow_codes",
         ["scdif_3b_workflow_codes", "scdif_8a_codes", "v_scdif_workflows"],
         f"""WITH codes AS (
  SELECT code, ANY_VALUE(code_description) AS code_description
  FROM {t('scdif_8a_codes')}
  WHERE UPPER(code_type) = 'WORK'
  GROUP BY code
)
SELECT
  b.workflow_id,
  b.workflow_code,
  COALESCE(k.code_description, b.workflow_code) AS workflow_code_description,
  w.* EXCEPT (workflow_id)
FROM {t('scdif_3b_workflow_codes')} AS b
LEFT JOIN codes AS k
  ON k.code = b.workflow_code
LEFT JOIN {t('v_scdif_workflows')} AS w
  ON w.workflow_id = b.workflow_id"""),

        ("v_archive_correspondence",
         ["archive_correspondence"],
         f"""SELECT
  _record_key,
  in_corr_id, in_corr_type, in_corr_method, in_corr_date, in_corr_topics,
  out_corr_id, out_corr_type, out_corr_method, out_corr_date, out_corr_topics,
  DATE_DIFF(out_corr_date, in_corr_date, DAY) AS days_to_respond,
  city, state_code, zip5, country
FROM {t('archive_correspondence')}"""),

        ("v_archive_topics",
         ["archive_topics", "archive_correspondence"],
         f"""SELECT
  t.direction,
  t.topic,
  t.corr_id,
  t.corr_date,
  a.in_corr_type,
  a.in_corr_method,
  a.out_corr_method,
  DATE_DIFF(a.out_corr_date, a.in_corr_date, DAY) AS days_to_respond,
  a.city, a.state_code, a.zip5, a.country
FROM {t('archive_topics')} AS t
JOIN {t('archive_correspondence')} AS a
  ON a._record_key = t._record_key"""),
    ]


def cmd_views(args):
    ds = f"{args.project}.{args.dataset}"
    views = view_definitions(ds)
    if args.print_sql:
        for name, _, sql in views:
            print(f"-- {name}\nCREATE OR REPLACE VIEW `{ds}.{name}` AS\n{sql};\n")
        return
    _, client = bq_client(args.project)
    existing = {tbl.table_id for tbl in client.list_tables(ds)}
    for name, needs, sql in views:
        missing = [n for n in needs if n not in existing]
        if missing:
            print(f"  skip {name}: missing {', '.join(missing)}", file=sys.stderr)
            continue
        client.query(f"CREATE OR REPLACE VIEW `{ds}.{name}` AS\n{sql}").result()
        existing.add(name)
        print(f"  created {ds}.{name}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_convert(args):
    files = expand_inputs(args.files)
    if args.format == "scdif":
        m = convert_scdif(files, args.out, args.encoding, args.layout)
    else:
        delim = args.delimiter.encode().decode("unicode_escape")
        header = {"yes": True, "no": False, "auto": None}[args.header]
        m = convert_archive(files, args.out, args.encoding, delim, args.topic_delimiters,
                            args.layout, header)
    print(f"\nWrote {sum(m['rows'].values()):,} rows to {args.out}")
    for table, n in sorted(m["rows"].items()):
        print(f"  {table:<40} {n:>12,}")
    if any(m["parse_errors"].values()):
        print("\nValues that failed type parsing (kept raw in _parse_errors):")
        for table, cols in m["parse_errors"].items():
            for col, n in cols.items():
                print(f"  {table}.{col}: {n:,}")
    for key in ("unknown_record_types", "rows_with_extra_fields"):
        if m.get(key):
            print(f"\n{key}: {m[key]}")
    for key in ("lines_decoded_as_cp1252", "continuation_lines_joined",
                "broken_lines_joined", "short_rows_padded", "header_rows_skipped",
                "lines_before_first_record_skipped"):
        if m.get(key):
            print(f"{key}: {m[key]:,}")
    print(f"\nNext: run Presidio over {args.out}/*.ndjson using presidio_columns.json, "
          f"then `load`.")


def cmd_layout_template(_args):
    print(json.dumps({
        "scdif": {rt: {"table": name, "fields": fields}
                  for rt, (name, fields) in SCDIF_LAYOUT.items()},
        "archive": {"fields": ARCHIVE_FIELDS},
    }, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="Convert raw exports to NDJSON + schemas")
    c.add_argument("format", choices=["scdif", "archive"])
    c.add_argument("files", nargs="+", help="Export files or glob patterns")
    c.add_argument("--out", required=True, help="Output folder")
    c.add_argument("--encoding", default="auto",
                   help="auto (UTF-8, falling back to cp1252 per line) or a codec name")
    c.add_argument("--layout", help="JSON layout overrides (see layout-template)")
    c.add_argument("--delimiter", default="\\t", help="Archive Format delimiter (default tab)")
    c.add_argument("--topic-delimiters", default="|;",
                   help="Characters that separate multiple Archive topics (default '|;')")
    c.add_argument("--header", choices=["auto", "yes", "no"], default="auto",
                   help="Whether Archive files start with a header row")
    c.set_defaults(func=cmd_convert)

    ld = sub.add_parser("load", help="Load NDJSON into BigQuery")
    ld.add_argument("--data-dir", required=True, help="Folder of (redacted) .ndjson files")
    ld.add_argument("--schema-dir", help="Folder of .schema.json files (default: data-dir)")
    ld.add_argument("--project", required=True)
    ld.add_argument("--dataset", required=True)
    ld.add_argument("--location", default="US")
    ld.add_argument("--tables", nargs="*", help="Only load these tables")
    ld.add_argument("--append", action="store_true", help="Append instead of replace")
    ld.add_argument("--max-bad-records", type=int, default=0)
    ld.set_defaults(func=cmd_load)

    v = sub.add_parser("views", help="Create Looker Studio views in BigQuery")
    v.add_argument("--project", required=True)
    v.add_argument("--dataset", required=True)
    v.add_argument("--print-sql", action="store_true", help="Print SQL instead of running it")
    v.set_defaults(func=cmd_views)

    t = sub.add_parser("layout-template", help="Print built-in layouts as editable JSON")
    t.set_defaults(func=cmd_layout_template)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
