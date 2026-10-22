#!/usr/bin/env python3
"""
embed_webvtt_metadata.py

Embed a FADGI-style metadata data block into a directory of WebVTT (.vtt)
files, using a CSV file as the source of the metadata. Follows the
Federal Agencies Digital Guidelines Initiative (FADGI) "Guidelines for
Embedding Metadata in WebVTT Files" (v1.0, June 2024):
https://www.digitizationguidelines.gov/guidelines/accessibilty_WebVTT.html

Per that guideline, the metadata block is recorded as "Element: Value"
lines immediately after the WEBVTT line, followed by a blank line and
then the cues, e.g.:

    WEBVTT
    Type: caption
    Language: eng
    Responsible Party: US, Library of Congress
    Media Identifier: 2021690767, Library of Congress Control Number
    Originating File: 180419plc1900_1280x720_2000.mp4
    Title: Staying Human: Poetry in the Age of Technology
    File Creator: LOC Multimedia Group
    File Creation Date: 2018-04-21
    Origin History: Created as part of LOC's routine web accessibility processes.
    Local Usage Element: [version history] v1.0; [quality control] true

    00:00.680 --> 00:22.090
    >> From the Library of
    Congress in Washington, DC.

CSV format
----------
Required header row with these columns (case-insensitive):

    Filename              - matches a .vtt file in the target directory
                             (with or without the .vtt extension)
    Application            - FADGI's "Type" element: one of subtitle,
                             caption, audio description, chapters, metadata
    Language                - ISO 639-3 code(s), semicolon-separated if
                             more than one, e.g. "eng" or "eng; fra"
    Responsible Party        - "US, Library of Congress" (repeatable,
                             semicolon-separated)
    Media Identifier          - repeatable, semicolon-separated
    Originating File           - filename of the source AV file
    File Creator                - person ("Last, First") or organization
    File Creation Date           - ISO 8601, e.g. 2018-04-21 or 2020-04
    Title                         - free text
    Origin History                 - free text
    Local Usage Element              - "[element name] value" pairs,
                             semicolon-separated, e.g.
                             "[version history] v1.0; [quality control] true"

Note: "Application" is the column name requested for this script; FADGI's
actual field name for that element is "Type", so that's what gets written
into the .vtt file (Type: <value>). All other column names are written
verbatim as their FADGI field names.

Blank cells are simply omitted from the output data block, per the
guideline ("Any elements left blank will not be included").

Re-running this script on a file it has already tagged replaces the
previously-embedded FADGI fields rather than duplicating them; any other
header content (e.g. X-TIMESTAMP-MAP) is left in place.

Usage
-----
    python embed_webvtt_metadata.py /path/to/vtt_dir metadata.csv
    python embed_webvtt_metadata.py /path/to/vtt_dir metadata.csv --backup
    python embed_webvtt_metadata.py /path/to/vtt_dir metadata.csv --dry-run
"""

import argparse
import csv
import io
import os
import re
import shutil
import sys

# Canonical FADGI field order, per Appendix A of the guideline.
# Each tuple is (CSV column name, FADGI field name written into the .vtt file).
FADGI_FIELDS = [
    ("Application", "Type"),
    ("Language", "Language"),
    ("Responsible Party", "Responsible Party"),
    ("Media Identifier", "Media Identifier"),
    ("Originating File", "Originating File"),
    ("Title", "Title"),
    ("File Creator", "File Creator"),
    ("File Creation Date", "File Creation Date"),
    ("Origin History", "Origin History"),
    ("Local Usage Element", "Local Usage Element"),
]

# The set of FADGI field names, used to strip stale metadata from files
# that already have a data block from a previous run of this script.
FADGI_FIELD_NAMES = {vtt_name for _, vtt_name in FADGI_FIELDS}

VALID_TYPES = {"subtitle", "caption", "audio description", "chapters", "metadata"}

ISO_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _open_csv_text(csv_path):
    """
    Read the CSV's raw bytes and decode them, trying UTF-8 first (the
    expected encoding) and falling back to Windows-1252 (cp1252), since
    CSVs exported from Excel are very often saved in that encoding and
    can contain bytes (e.g. curly quotes) that aren't valid UTF-8.
    """
    with open(csv_path, "rb") as f:
        raw = f.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        print(
            "  ! note: CSV is not valid UTF-8; re-reading as Windows-1252 "
            "(cp1252), which is common for CSVs exported from Excel"
        )
        return raw.decode("cp1252")


def load_metadata_rows(csv_path):
    """Load the CSV into a dict keyed by lowercase filename (with .vtt extension)."""
    rows = {}
    text = _open_csv_text(csv_path)
    with io.StringIO(text) as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            sys.exit("CSV file has no header row.")
        normalized = {name: name.strip().lower() for name in reader.fieldnames}
        for raw_row in reader:
            row = {normalized[k]: v for k, v in raw_row.items() if k in normalized}
            filename = (row.get("filename") or "").strip()
            if not filename:
                print("  ! skipping CSV row with no filename")
                continue
            if not filename.lower().endswith(".vtt"):
                filename += ".vtt"
            rows[filename.lower()] = row
    return rows


def build_metadata_lines(row):
    """Build the ordered 'Field: Value' lines for one CSV row, skipping blanks."""
    lines = []
    for csv_col, vtt_field in FADGI_FIELDS:
        value = (row.get(csv_col.lower()) or "").strip()
        if not value:
            continue
        if vtt_field == "Type" and value not in VALID_TYPES:
            print(
                f"  ! warning: Application/Type {value!r} is not one of "
                f"{sorted(VALID_TYPES)}"
            )
        if vtt_field == "File Creation Date" and not ISO_DATE_RE.match(value):
            print(
                f"  ! warning: File Creation Date {value!r} doesn't look like "
                f"ISO 8601 (yyyy-mm-dd or yyyy-mm)"
            )
        lines.append(f"{vtt_field}: {value}")
    return lines


def split_header_and_body(lines):
    """
    Given the file's lines (line 0 is the WEBVTT line), split the rest into
    (existing_header_lines, body_lines), where body_lines starts with the
    blank line that separates the header from the first cue (if one exists
    in the original file).
    """
    blank_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "":
            blank_index = i
            break
    if blank_index is None:
        return [], lines[1:]
    return lines[1:blank_index], lines[blank_index:]


def strip_existing_fadgi_lines(header_lines):
    """Remove lines whose key matches a known FADGI field name, keep the rest."""
    kept = []
    for line in header_lines:
        key = line.split(":", 1)[0].strip()
        if key in FADGI_FIELD_NAMES:
            continue
        kept.append(line)
    return kept


def embed_metadata(vtt_path, row, dry_run=False):
    with open(vtt_path, encoding="utf-8-sig") as f:
        text = f.read()

    lines = text.splitlines()
    if not lines or not lines[0].strip().upper().startswith("WEBVTT"):
        raise ValueError("file does not start with a WEBVTT line")

    webvtt_line = lines[0]
    existing_header, body_lines = split_header_and_body(lines)
    kept_header = strip_existing_fadgi_lines(existing_header)
    new_metadata_lines = build_metadata_lines(row)

    if not new_metadata_lines:
        print("  ! warning: no non-blank metadata values for this row; nothing to add")

    final_lines = [webvtt_line] + new_metadata_lines + kept_header
    if body_lines and body_lines[0].strip() == "":
        final_lines += body_lines
    else:
        final_lines += [""] + body_lines

    output_text = "\n".join(final_lines)
    if not output_text.endswith("\n"):
        output_text += "\n"

    if dry_run:
        return

    with open(vtt_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(output_text)


def find_vtt_files(directory):
    return sorted(
        f for f in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, f)) and f.lower().endswith(".vtt")
    )


def main():
    parser = argparse.ArgumentParser(
        description="Embed FADGI-style metadata into a directory of WebVTT files from a CSV."
    )
    parser.add_argument("vtt_dir", help="Directory containing .vtt files")
    parser.add_argument("csv_file", help="CSV file with metadata (see script docstring)")
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Keep a .bak copy of each original file before overwriting it",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the CSV against the directory without writing any files",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.vtt_dir):
        sys.exit(f"Not a directory: {args.vtt_dir}")
    if not os.path.isfile(args.csv_file):
        sys.exit(f"CSV file not found: {args.csv_file}")

    print(f"Reading metadata from {args.csv_file} ...")
    rows_by_filename = load_metadata_rows(args.csv_file)
    print(f"  {len(rows_by_filename)} metadata row(s) loaded")

    vtt_files = find_vtt_files(args.vtt_dir)
    print(f"Found {len(vtt_files)} .vtt file(s) in {args.vtt_dir}")

    processed = 0
    skipped_no_row = []
    matched_filenames = set()

    for filename in vtt_files:
        row = rows_by_filename.get(filename.lower())
        if row is None:
            skipped_no_row.append(filename)
            continue
        matched_filenames.add(filename.lower())

        vtt_path = os.path.join(args.vtt_dir, filename)
        print(f"Processing {filename} ...")

        if args.backup and not args.dry_run:
            shutil.copy2(vtt_path, vtt_path + ".bak")

        try:
            embed_metadata(vtt_path, row, dry_run=args.dry_run)
            processed += 1
        except Exception as e:
            print(f"  ! error: failed to process {filename}: {e}")

    unmatched_rows = [fn for fn in rows_by_filename if fn not in matched_filenames]

    print()
    print("Summary")
    print("-------")
    print(f"  Processed: {processed}{' (dry run, nothing written)' if args.dry_run else ''}")
    if skipped_no_row:
        print(f"  .vtt files with no matching CSV row ({len(skipped_no_row)}):")
        for fn in skipped_no_row:
            print(f"    - {fn}")
    if unmatched_rows:
        print(f"  CSV rows with no matching .vtt file ({len(unmatched_rows)}):")
        for fn in unmatched_rows:
            print(f"    - {fn}")


if __name__ == "__main__":
    main()