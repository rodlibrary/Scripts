#!/usr/bin/env python3
"""
Convert an Intranet Quorum / Senate CSS export into standard SCDIF, so the
PII pipeline (analyze_pipeline.py / iq_analyze_pipeline.py) can read it.

Some exports differ from the SCDIF specification. This script handles:
  * "|" (pipe) instead of TAB between fields;
  * a header row naming the columns (e.g. "Record Type|Constituent ID|...");
    columns are matched to the standard SCDIF fields BY NAME, so an export
    that leaves out or reorders fields (e.g. a 13-column 1A record with no
    Organization Name, Spouse Name or Email Flag) still lands in the right
    places, and missing standard fields are left empty;
  * line breaks inside a field (a line that doesn't start with a record
    type continues the record above it); they become "|" as SCDIF requires.

Columns with no standard SCDIF equivalent are kept, appended after the
standard fields; the conversion report gives their new field numbers so you
can add rows for them to the IQ field policy.

Records that can't be mapped with certainty -- more fields than the header
has columns, or text before the first record -- are NOT guessed at. They go
to a separate *_rejected.txt file (which still contains PII) and are counted
in the report. They are not in the converted file, so they are not
anonymized: review them before anything is shared.

Usage
-----
  # 1. Look at a file's structure first. Every letter and digit is masked,
  #    so the output is safe to share:
  python convert_to_scdif.py --inspect /path/to/export.txt

  # 2. Convert (one or more files):
  python convert_to_scdif.py /path/to/export.txt --output_dir /data/hartwigd/pii

  Output:  <output_dir>/<name>_scdif.dat          tab-delimited SCDIF, CRLF
           <report_dir>/<name>_conversion_report.txt
           <report_dir>/<name>_rejected.txt        (only if records were rejected)
  <report_dir> defaults to a folder next to <output_dir> named
  <output_dir>_conversion_reports, so the reports and rejected records are
  not picked up by the PII analysis.

Keep the ORIGINAL export outside the folder the PII analysis scans (e.g.
not in /data/hartwigd/pii), or it will also be analyzed as plain text.

Only the Python standard library is needed.
"""

import argparse
import os
import re
import sys
from collections import Counter, defaultdict

SCDIF_FIELDS = {
    '1A': ['Record Type', 'Person ID', 'Person Type', 'Prefix', 'First Name', 'Middle Name', 'Last Name', 'Suffix', 'Appellation', 'Organization Name', 'Salutation', 'Date of Birth', 'No Mail Flag', 'Deceased Flag', 'Spouse Name', 'Email Flag'],
    '1B': ['Record Type', 'Person ID', 'Address ID', 'Address Type', 'Primary Flag', 'Default Address Flag', 'Title', 'Organization Name', 'Address Line 1', 'Address Line 2', 'Address Line 3', 'Address Line 4', 'City', 'State Code', 'Zip Code', 'Carrier Route', 'County', 'Country', 'District', 'Precinct', 'No Mail Flag', 'Deliverability'],
    '1C': ['Record Type', 'Person ID', 'Person Code Type', 'Code'],
    '1D': ['Record Type', 'Person ID', 'Sequence', 'Text', 'Date', 'Time', 'User ID'],
    '1E': ['Record Type', 'Person ID', 'Phone or Email Type', 'Value', 'Primary Flag', 'Invalid Flag'],
    '1F': ['Record Type', 'Person ID', 'Document Name', 'Document ID', 'User ID', 'Attached Date', 'Text'],
    '2A': ['Record Type', 'Person ID', 'Communication ID', 'Workflow ID', 'Workflow Person ID', 'Communication Type', 'User ID', 'Approved By', 'Status', 'Date In', 'Date Out', 'Reminder Date', 'Update Date', 'Response Type', 'Address ID', 'Email Address', 'Household Flag', 'Household ID', 'Group Name', 'Salutation'],
    '2B': ['Record Type', 'Person ID', 'Communication ID', 'Communication Code', 'Position'],
    '2C': ['Record Type', 'Person ID', 'Communication ID', 'Document Type', 'Communication Document Name', 'Communication Document ID', 'File Location'],
    '2D': ['Record Type', 'Person ID', 'Communication ID', 'Sequence', 'Text', 'Date', 'Time', 'User ID'],
    '2E': ['Record Type', 'Person ID', 'Communication ID', 'Fill-in Field Name', 'Fill-in Data'],
    '3A': ['Record Type', 'Person ID', 'Workflow ID', 'Workflow Type', 'User ID', 'Start Date', 'End Date', 'Reminder Date', 'Update Date', 'Workflow Description', 'Status'],
    '3B': ['Record Type', 'Person ID', 'Workflow ID', 'Workflow Code'],
    '3D': ['Record Type', 'Person ID', 'Workflow ID', 'Sequence', 'Text', 'Date', 'Time', 'User ID'],
    '3E': ['Record Type', 'Person ID', 'Workflow ID', 'Party ID'],
    '3F': ['Record Type', 'Person ID', 'Workflow ID', 'Document Name', 'Document ID', 'User ID', 'Attached Date', 'Text'],
    '3L': ['Record Type', 'Person ID', 'Workflow ID', 'Workflow Type', 'Field Name', 'Field Value'],
    '5A': ['Record Type', 'Household ID', 'Household Name', 'Household Salutation'],
    '5B': ['Record Type', 'Household ID', 'Person ID', 'Primary Contact Flag'],
    '5C': ['Record Type', 'Household ID', 'Document Name', 'Document ID', 'User ID', 'Attached Date', 'Text'],
    '5D': ['Record Type', 'Household ID', 'Sequence', 'Text', 'Date', 'Time', 'User ID'],
    '6A': ['Record Type', 'Document ID', 'Version', 'Document Grouping ID', 'Document Type', 'Document Display Name', 'Document Description', 'Document Name', 'Created By', 'Revised By', 'Approved By', 'Creation Date', 'Revision Date', 'Last Used Date', 'Status', 'Inactive Flag', 'Virtual Directory'],
    '6B': ['Record Type', 'Document ID', 'Fill-in Field Name'],
    '6C': ['Record Type', 'Document ID', 'Code Type', 'Code'],
    '6D': ['Record Type', 'Document ID', 'Document Name', 'User ID', 'Attached Date', 'Text', 'Form Letter Attachment Flag'],
    '6E': ['Record Type', 'Document ID', 'Sequence', 'Text', 'Date', 'Time', 'User ID'],
    '6F': ['Record Type', 'Document ID', 'Owned By'],
    '7A': ['Record Type', 'Event ID', 'Recurrence ID', 'Event Description', 'Contact', 'County', 'Organization', 'Start Date', 'Start Time', 'End Date', 'End Time', 'Time Zone', 'Location', 'Status', 'Private Flag', 'Scheduled Date', 'Scheduled By', 'Revision Date', 'Revised By', 'Address Line 1', 'Address Line 2', 'Address Line 3', 'Address Line 4', 'City', 'State Code', 'Zip Code', 'Country'],
    '7B': ['Record Type', 'Event ID', 'Event Code'],
    '7C': ['Record Type', 'Event ID', 'Participant Type', 'Participant ID'],
    '7D': ['Record Type', 'Event ID', 'Document Name', 'Document ID', 'User ID', 'Attached Date', 'Text'],
    '7E': ['Record Type', 'Event ID', 'Sequence', 'Text', 'Date', 'Time', 'User ID'],
    '7F': ['Record Type', 'Event ID', 'Workflow ID'],
    '7H': ['Record Type', 'Recurrence ID', 'Start Series Date', 'End Series Date', 'Recurrence Type', 'Recurrence Interval'],
    '7I': ['Record Type', 'Recurrence ID', 'Not Used', 'Event Description', 'Contact', 'County', 'Organization', 'Not Used', 'Start Time', 'Not Used', 'End Time', 'Time Zone', 'Location', 'Status', 'Private Flag', 'Scheduled Date', 'Scheduled By', 'Revision Date', 'Revised By', 'Address Line 1', 'Address Line 2', 'Address Line 3', 'Address Line 4', 'City', 'State Code', 'Zip Code', 'Country'],
    '7J': ['Record Type', 'Recurrence ID', 'Event Code'],
    '7K': ['Record Type', 'Recurrence ID', 'Participant Type', 'Participant ID'],
    '7L': ['Record Type', 'Recurrence ID', 'Document Name', 'Document ID', 'User ID', 'Attached Date', 'Text'],
    '7M': ['Record Type', 'Recurrence ID', 'Sequence', 'Text', 'Date', 'Time', 'User ID'],
    '7N': ['Record Type', 'Recurrence ID', 'Workflow ID'],
    '8A': ['Record Type', 'Code Type', 'Code', 'Code Description', 'Inactive Flag', 'User ID'],
}

# normalized alternative column name -> normalized standard field name
ALIASES = {
    "constituentid": "personid", "constid": "personid", "individualid": "personid",
    "individualtype": "persontype", "constituenttype": "persontype",
    "fname": "firstname", "givenname": "firstname",
    "lname": "lastname", "surname": "lastname", "familyname": "lastname",
    "middleinitial": "middlename", "mi": "middlename", "mname": "middlename",
    "dob": "dateofbirth", "birthdate": "dateofbirth", "birthday": "dateofbirth",
    "spouse": "spousename", "spousesname": "spousename",
    "zip": "zipcode", "postalcode": "zipcode", "zip5": "zipcode", "zip9": "zipcode",
    "address1": "addressline1", "address2": "addressline2",
    "address3": "addressline3", "address4": "addressline4",
    "addr1": "addressline1", "addr2": "addressline2",
    "addr3": "addressline3", "addr4": "addressline4",
    "orgname": "organizationname", "organisation": "organizationname",
    "organization": "organizationname",
    "email": "emailaddress", "state": "statecode",
}

RT_RE = re.compile(r"^[1-9][A-Z]$")
DELIMITERS = {"\t": "tab", "|": "pipe"}


def norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def canon(name):
    n = norm(name)
    return ALIASES.get(n, n)


def clean_rt(field):
    return field.lstrip("﻿").strip().strip('"\'').strip().upper()


def is_header(fields):
    return norm(clean_rt(fields[0])) in ("recordtype", "rectype", "record")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def detect(path, encoding, sample=500):
    lines = []
    with open(path, "rb") as f:
        for _ in range(sample):
            raw = f.readline(1 << 20)
            if not raw:
                break
            s = raw.decode(encoding, errors="replace").rstrip("\r\n")
            if s.strip():
                lines.append(s)
    if not lines:
        return None, None, "the file is empty"
    delim = max(DELIMITERS, key=lambda d: sum(d in ln for ln in lines))
    if sum(delim in ln for ln in lines) < 0.5 * len(lines):
        return None, None, "the file is not tab- or pipe-delimited"
    first = lines[0].split(delim)
    header = [h.strip() for h in first] if is_header(first) else None
    data = lines[1:] if header else lines
    starts = sum(1 for ln in data if RT_RE.match(clean_rt(ln.split(delim, 1)[0])))
    if data and starts < 0.5 * len(data):
        return None, None, (f"only {starts} of {len(data)} sampled lines start with an "
                            f"SCDIF record type such as 1A")
    return delim, header, ""


def iter_records(path, delim, encoding, has_header):
    """
    Yields (line_no, [line strings without line endings], raw_bytes) per
    record: a line starting with a record type plus any continuation lines.
    The header row (if any) is skipped. Text before the first record is
    yielded with line_no negative so the caller can reject it.
    """
    with open(path, "rb") as f:
        line_no = 0
        if has_header:
            f.readline()
            line_no = 1
        cur, cur_raw, cur_no = None, b"", 0
        for raw in f:
            line_no += 1
            s = raw.decode(encoding, errors="surrogateescape").rstrip("\r\n")
            starts = delim in s and RT_RE.match(clean_rt(s.split(delim, 1)[0])) is not None
            if starts or cur is None:
                if cur is not None:
                    yield cur_no, cur, cur_raw
                cur, cur_raw = [s], raw
                cur_no = line_no if starts else -line_no
            else:
                cur.append(s)
                cur_raw += raw
        if cur is not None:
            yield cur_no, cur, cur_raw


# ---------------------------------------------------------------------------
# Inspect
# ---------------------------------------------------------------------------

def mask(v, width=24):
    if v == "":
        return "''"
    m = re.sub(r"[A-Z]", "A", re.sub(r"[a-z]", "a", re.sub(r"[0-9]", "9", v)))
    m = re.sub(r"[^\x20-\x7e]", "?", m)
    return m[:width] + ("..." if len(m) > width else "")


def inspect(path, encoding):
    print(f"=== {os.path.basename(path)} ({os.path.getsize(path):,} bytes)")
    delim, header, problem = detect(path, encoding)
    if problem:
        print(f"Not convertible: {problem}.")
        return
    print(f"Delimiter: {DELIMITERS[delim]}")
    if header:
        print(f"Header row: {len(header)} columns")
        for i, h in enumerate(header, 1):
            print(f"  {i:3d}  {h}")
    else:
        print("Header row: none (columns are taken to be in standard SCDIF order)")
    rts, widths, lines_per = Counter(), Counter(), Counter()
    samples = defaultdict(list)
    n = 0
    for line_no, lines, _ in iter_records(path, delim, encoding, bool(header)):
        n += 1
        values = "\n".join(lines).split(delim)
        rt = clean_rt(values[0]) if line_no > 0 else "(text before first record)"
        rts[rt] += 1
        widths[(rt, len(values))] += 1
        lines_per[len(lines)] += 1
        if line_no < 0:
            kind = "text before the first record"
        elif header and len(values) > len(header):
            kind = f"MORE fields than the header ({len(header)})"
        elif header and len(values) < len(header):
            kind = f"fewer fields than the header ({len(header)})"
        elif len(lines) > 1:
            kind = "spans several lines, fits the header"
        else:
            kind = "normal"
        if len(samples[kind]) < 3:
            samples[kind].append((abs(line_no), [[mask(v) for v in ln.split(delim)] for ln in lines]))
    print(f"\nRecords: {n:,}")
    print("By record type: " + ", ".join(f"{k}: {v:,}" for k, v in rts.most_common()))
    print("Physical lines per record: " + ", ".join(f"{k}: {v:,}" for k, v in sorted(lines_per.items())))
    print("Fields per record (record type, fields: count):")
    for (rt, w), c in sorted(widths.items()):
        print(f"  {rt} {w:3d} fields: {c:,}")
    print("\nMasked samples (letters -> a/A, digits -> 9; '' = empty field):")
    for kind, items in samples.items():
        print(f"\n  [{kind}]")
        for line_no, lines in items:
            for k, fields in enumerate(lines):
                tag = f"line {line_no + k}"
                print(f"    {tag:>10}: " + " | ".join(f"{i}:{v}" for i, v in enumerate(fields, 1)))
    print()


# ---------------------------------------------------------------------------
# Convert
# ---------------------------------------------------------------------------

class Mapping:
    """How one record type's columns map to standard SCDIF positions."""

    def __init__(self, rt, header):
        self.rt = rt
        self.std = SCDIF_FIELDS.get(rt)
        self.n_std = len(self.std) if self.std else 1
        self.target = {}          # source column index -> output index
        self.notes = []
        std_index = {canon(n): i for i, n in enumerate(self.std or ["Record Type"])}
        used = {0}
        extras = []
        for j, h in enumerate(header):
            if j == 0:
                self.target[0] = 0
                continue
            i = std_index.get(canon(h))
            if i is not None and i not in used:
                self.target[j] = i
                used.add(i)
                self.notes.append(f"  column {j + 1:3d} '{h}' -> {rt} field {i + 1} "
                                  f"'{self.std[i]}'")
            else:
                extras.append(j)
                why = "duplicate of an earlier column" if i is not None else \
                      "no standard SCDIF field of this name"
                self.notes.append(f"  column {j + 1:3d} '{h}' -> {rt} field "
                                  f"{self.n_std + len(extras)} (extra: {why})")
        for k, j in enumerate(extras):
            self.target[j] = self.n_std + k
        self.width = self.n_std + len(extras)
        self.empty = [f"  {rt} field {i + 1} '{n}'" for i, n in enumerate(self.std or [])
                      if i not in used]
        self.checks = [(i, n, validator_for(n)) for i, n in enumerate(self.std or [])
                       if i in used and validator_for(n)]

    def build(self, values):
        """values -> (output row, None) or (None, reason)"""
        row = [""] * self.width
        for j, v in enumerate(values):
            row[self.target[j]] = v
        row[0] = self.rt
        bad = [n for i, n, ok in self.checks if not ok(row[i])]
        if bad:
            return None, ("values don't fit their columns (likely shifted): "
                          + ", ".join(bad[:3]))
        return row, None
        if self.std is None:
            self.notes.insert(0, f"  ({rt} is not a standard SCDIF record type: all its "
                                 f"columns are kept as extra fields)")


_DATEISH = re.compile(r"^[0-9A-Za-z ,./:-]{4,25}$")
_TIMEISH = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?\s*([AaPp][Mm])?$")
_FLAGISH = re.compile(r"^[A-Za-z]?$")


def validator_for(field_name):
    """A check that a value plausibly belongs in this standard field, used to
    catch records whose columns have shifted. None = no check."""
    n = norm(field_name)
    if n.endswith("flag"):
        return lambda v: bool(_FLAGISH.match(v.strip()))
    if n == "dateofbirth" or n.endswith("date") or n.startswith("date"):
        return lambda v: v.strip() == "" or (bool(_DATEISH.match(v.strip()))
                                             and any(c.isdigit() for c in v))
    if n == "time" or n.endswith("time"):
        return lambda v: v.strip() == "" or bool(_TIMEISH.match(v.strip()))
    return None


def clean_value(v):
    return v.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "|").replace("\t", " ")


def convert(path, out_path, report_dir, encoding):
    base = os.path.splitext(os.path.basename(path))[0]
    report_path = os.path.join(report_dir, f"{base}_conversion_report.txt")
    rejected_path = os.path.join(report_dir, f"{base}_rejected.txt")
    delim, header, problem = detect(path, encoding)
    if problem:
        print(f"ERROR: {path}: not converted, {problem}. Run with --inspect to see why.")
        return False

    mappings = {}
    counts = Counter()
    per_rt = Counter()
    reject_reasons = Counter()
    rejected = None
    with open(out_path, "wb") as out:
        for line_no, lines, raw in iter_records(path, delim, encoding, bool(header)):
            values = "\n".join(lines).split(delim)
            reason, fragment = None, None
            rt = clean_rt(values[0])
            m = None
            if header and line_no > 0:
                if rt not in mappings:
                    mappings[rt] = Mapping(rt, header)
                m = mappings[rt]
            if line_no < 0:
                reason = "text before the first record"
            elif header and len(values) > len(header):
                first = lines[0].split(delim)
                row, why = m.build(first) if len(first) <= len(header) else (None, "x")
                if row is not None and len(lines) > 1:
                    # A complete, valid record followed by line(s) that can't
                    # belong to it: keep the record, reject only the fragment.
                    values, fragment = first, lines[1:]
                    lines = lines[:1]
                else:
                    reason = (f"more fields ({len(values)}) than the header has columns "
                              f"({len(header)})")
            if reason is None and m is not None:
                row, reason = m.build(values)

            def reject(text_lines, why):
                nonlocal rejected
                if rejected is None:
                    rejected = open(rejected_path, "wb")
                rejected.write("\n".join(text_lines).encode(encoding, errors="surrogateescape")
                               + b"\n")
                reject_reasons[why] += 1
                counts["rejected"] += 1

            if fragment:
                reject(fragment, "line(s) not starting with a record type that don't fit "
                                 "the complete record above them")
            if reason:
                reject(lines, reason)
                continue

            per_rt[rt] += 1
            counts["records"] += 1
            if len(lines) > 1:
                counts["multiline"] += 1
            if header:
                if len(values) < len(header):
                    counts["short"] += 1
            else:
                std_len = len(SCDIF_FIELDS.get(rt, [])) or len(values)
                row = values + [""] * max(0, std_len - len(values))
                row[0] = rt
            out.write("\t".join(clean_value(v) for v in row)
                      .encode(encoding, errors="surrogateescape") + b"\r\n")
    if rejected:
        rejected.close()

    with open(report_path, "w", encoding="utf-8") as r:
        r.write(f"Conversion report for {path}\n")
        r.write(f"Output: {out_path}\n\n")
        r.write(f"Source: {DELIMITERS[delim]}-delimited, "
                f"{'header row with ' + str(len(header)) + ' columns' if header else 'no header row'}\n")
        r.write(f"Records converted: {counts['records']:,} "
                f"({', '.join(f'{k}: {v:,}' for k, v in per_rt.most_common())})\n")
        r.write(f"  spanning several lines (line breaks became '|'): {counts['multiline']:,}\n")
        r.write(f"  with fewer fields than the header (missing trailing fields left empty): "
                f"{counts['short']:,}\n")
        r.write(f"Records or fragments REJECTED (not converted, not anonymized): "
                f"{counts['rejected']:,}\n")
        for k, v in reject_reasons.most_common():
            r.write(f"  {v:,}: {k}\n")
        if counts["rejected"]:
            r.write(f"  Written unchanged to {rejected_path} -- it contains PII.\n")
        for rt, m in sorted(mappings.items()):
            r.write(f"\nColumn mapping for {rt}:\n" + "\n".join(m.notes) + "\n")
            if m.empty:
                r.write(f"Standard {rt} fields with no source column (left empty):\n"
                        + "\n".join(m.empty) + "\n")
            if m.width > m.n_std:
                r.write(f"Extra fields are scanned by default (policy row '*'). To control "
                        f"them, add rows for {rt} fields {m.n_std + 1}-{m.width} to the "
                        f"IQ field policy.\n")

    print(f"{os.path.basename(path)}: {counts['records']:,} records converted, "
          f"{counts['multiline']:,} multi-line, {counts['short']:,} short, "
          f"{counts['rejected']:,} rejected")
    print(f"  -> {out_path}")
    print(f"  report: {report_path}")
    if counts["rejected"]:
        print(f"  WARNING: {counts["rejected"]:,} records or fragments were rejected and are NOT in the "
              f"converted file: {rejected_path}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Convert IQ exports to standard SCDIF.",
                                 epilog="See the top of this script for details.")
    ap.add_argument("files", nargs="+", help="export file(s) to inspect or convert")
    ap.add_argument("--inspect", action="store_true",
                    help="only describe each file's structure (masked; safe to share)")
    ap.add_argument("--output_dir", help="folder for the converted *_scdif.dat files")
    ap.add_argument("--report_dir", default=None,
                    help="folder for reports and rejected records "
                         "(default: <output_dir>_conversion_reports)")
    ap.add_argument("--encoding", default="utf-8",
                    help="text encoding of the export (default utf-8; try cp1252 if "
                         "accented letters look wrong)")
    ap.add_argument("--overwrite", action="store_true", help="replace existing output files")
    args = ap.parse_args()

    if args.inspect:
        for p in args.files:
            inspect(p, args.encoding)
        return
    if not args.output_dir:
        sys.exit("ERROR: --output_dir is required (or use --inspect)")
    out_dir = os.path.abspath(args.output_dir)
    report_dir = os.path.abspath(args.report_dir or out_dir.rstrip(os.sep) + "_conversion_reports")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(report_dir, exist_ok=True)

    ok = True
    for p in args.files:
        base = os.path.splitext(os.path.basename(p))[0]
        out_path = os.path.join(out_dir, f"{base}_scdif.dat")
        if os.path.abspath(p) == out_path:
            print(f"ERROR: {p}: output would overwrite the input; skipped")
            ok = False
            continue
        if os.path.exists(out_path) and not args.overwrite:
            print(f"ERROR: {out_path} exists; use --overwrite to replace it")
            ok = False
            continue
        ok = convert(p, out_path, report_dir, args.encoding) and ok
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
