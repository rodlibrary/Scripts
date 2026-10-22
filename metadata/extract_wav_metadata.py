#!/usr/bin/env python3
"""
extract_wav_metadata.py

Walk a directory of .wav files, read their metadata with `wavinfo`
(basic format info, BWF 'bext', RIFF LIST/INFO tags, cue points, and
presence of iXML/ADM/Sound-Devices/sampler chunks), and export it all to
a single CSV file -- one row per wav file.

Install the dependency first:
    pip install wavinfo

Usage
-----
    python extract_wav_metadata.py /path/to/wav_dir metadata_out.csv
    python extract_wav_metadata.py /path/to/wav_dir metadata_out.csv --recursive
    python extract_wav_metadata.py /path/to/wav_dir metadata_out.csv --include-ixml-xml
"""

import argparse
import csv
import os
import sys

try:
    from wavinfo import WavInfoReader, WavInfoEOFError
except ImportError:
    sys.exit(
        "The 'wavinfo' package is required but not installed.\n"
        "Install it with:  pip install wavinfo"
    )

# Column order for the output CSV. Grouped by chunk so it's easy to read.
FIELDNAMES = [
    "filename",
    "filepath",
    "file_size_bytes",
    # fmt chunk
    "audio_format",
    "channel_count",
    "sample_rate",
    "byte_rate",
    "block_align",
    "bits_per_sample",
    # data chunk
    "frame_count",
    "duration_seconds",
    "data_byte_count",
    # bext chunk
    "bext_description",
    "bext_originator",
    "bext_originator_ref",
    "bext_originator_date",
    "bext_originator_time",
    "bext_time_reference",
    "bext_version",
    "bext_coding_history",
    "bext_umid_hex",
    "bext_loudness_value",
    "bext_loudness_range",
    "bext_max_true_peak",
    "bext_max_momentary_loudness",
    "bext_max_shortterm_loudness",
    # RIFF LIST/INFO chunk
    "info_title",
    "info_artist",
    "info_album",
    "info_genre",
    "info_comment",
    "info_created_date",
    "info_software",
    "info_engineer",
    "info_technician",
    "info_copyright",
    "info_keywords",
    "info_subject",
    "info_source",
    "info_tape",
    "info_archival_location",
    "info_commissioned",
    # cue points / labels / ranges / notes
    "cue_count",
    "cue_positions",
    "label_count",
    "note_count",
    "range_count",
    # other chunk presence / basic content
    "has_ixml",
    "has_adm",
    "has_dolby",
    "has_smpl",
    "ixml_xml",  # only populated with --include-ixml-xml
    # errors
    "read_error",
]


def fmt_umid(umid_bytes):
    if not umid_bytes:
        return ""
    if not any(umid_bytes):
        return ""  # all-zero UMID means "not set"
    return umid_bytes.hex()


def row_for_file(path, include_ixml_xml=False):
    row = {name: "" for name in FIELDNAMES}
    row["filename"] = os.path.basename(path)
    row["filepath"] = path
    try:
        row["file_size_bytes"] = os.path.getsize(path)
    except OSError:
        pass

    try:
        info = WavInfoReader(path)
    except (WavInfoEOFError, Exception) as e:
        row["read_error"] = f"{type(e).__name__}: {e}"
        return row

    # fmt
    fmt = info.fmt
    if fmt is not None:
        row["audio_format"] = fmt.audio_format
        row["channel_count"] = fmt.channel_count
        row["sample_rate"] = fmt.sample_rate
        row["byte_rate"] = fmt.byte_rate
        row["block_align"] = fmt.block_align
        row["bits_per_sample"] = fmt.bits_per_sample

    # data
    data = info.data
    if data is not None:
        row["frame_count"] = data.frame_count
        row["data_byte_count"] = data.byte_count
        if fmt is not None and fmt.sample_rate:
            row["duration_seconds"] = round(data.frame_count / fmt.sample_rate, 6)

    # bext
    bext = info.bext
    if bext is not None:
        row["bext_description"] = bext.description or ""
        row["bext_originator"] = bext.originator or ""
        row["bext_originator_ref"] = bext.originator_ref or ""
        row["bext_originator_date"] = bext.originator_date or ""
        row["bext_originator_time"] = bext.originator_time or ""
        row["bext_time_reference"] = bext.time_reference
        row["bext_version"] = bext.version
        row["bext_coding_history"] = (bext.coding_history or "").replace("\r\n", " | ").strip(" |")
        row["bext_umid_hex"] = fmt_umid(bext.umid)
        row["bext_loudness_value"] = bext.loudness_value if bext.loudness_value is not None else ""
        row["bext_loudness_range"] = bext.loudness_range if bext.loudness_range is not None else ""
        row["bext_max_true_peak"] = bext.max_true_peak if bext.max_true_peak is not None else ""
        row["bext_max_momentary_loudness"] = (
            bext.max_momentary_loudness if bext.max_momentary_loudness is not None else ""
        )
        row["bext_max_shortterm_loudness"] = (
            bext.max_shortterm_loudness if bext.max_shortterm_loudness is not None else ""
        )

    # LIST/INFO
    list_info = info.info
    if list_info is not None:
        row["info_title"] = list_info.title or ""
        row["info_artist"] = list_info.artist or ""
        row["info_album"] = list_info.album or ""
        row["info_genre"] = list_info.genre or ""
        row["info_comment"] = list_info.comment or ""
        row["info_created_date"] = list_info.created_date or ""
        row["info_software"] = list_info.software or ""
        row["info_engineer"] = list_info.engineer or ""
        row["info_technician"] = list_info.technician or ""
        row["info_copyright"] = list_info.copyright or ""
        row["info_keywords"] = list_info.keywords or ""
        row["info_subject"] = list_info.subject or ""
        row["info_source"] = list_info.source or ""
        row["info_tape"] = list_info.tape or ""
        row["info_archival_location"] = list_info.archival_location or ""
        row["info_commissioned"] = list_info.commissioned or ""

    # cues
    cues = info.cues
    if cues is not None:
        row["cue_count"] = len(cues.cues)
        row["cue_positions"] = ";".join(str(c.position) for c in cues.cues) if cues.cues else ""
        row["label_count"] = len(cues.labels)
        row["note_count"] = len(cues.notes)
        row["range_count"] = len(cues.ranges)

    # presence flags for the larger XML-based chunks
    row["has_ixml"] = bool(info.ixml)
    row["has_adm"] = bool(info.adm)
    row["has_dolby"] = bool(info.dolby)
    row["has_smpl"] = bool(info.smpl)

    if include_ixml_xml and info.ixml is not None:
        try:
            row["ixml_xml"] = info.ixml.xml_str()
        except Exception:
            pass

    return row


def find_wav_files(directory, recursive):
    if recursive:
        for root, _dirs, files in os.walk(directory):
            for f in sorted(files):
                if f.lower().endswith(".wav"):
                    yield os.path.join(root, f)
    else:
        for f in sorted(os.listdir(directory)):
            full = os.path.join(directory, f)
            if os.path.isfile(full) and f.lower().endswith(".wav"):
                yield full


def main():
    parser = argparse.ArgumentParser(
        description="Extract wav metadata (fmt/bext/INFO/cues/etc via wavinfo) to a CSV."
    )
    parser.add_argument("wav_dir", help="Directory containing .wav files")
    parser.add_argument("csv_out", help="Path to the CSV file to write")
    parser.add_argument(
        "--recursive", action="store_true", help="Also search subdirectories"
    )
    parser.add_argument(
        "--include-ixml-xml",
        action="store_true",
        help="Include the full raw iXML document as a column (can be large)",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.wav_dir):
        sys.exit(f"Not a directory: {args.wav_dir}")

    wav_paths = list(find_wav_files(args.wav_dir, args.recursive))
    print(f"Found {len(wav_paths)} .wav file(s) in {args.wav_dir}"
          f"{' (recursive)' if args.recursive else ''}")

    fieldnames = FIELDNAMES if args.include_ixml_xml else [
        f for f in FIELDNAMES if f != "ixml_xml"
    ]

    rows_written = 0
    errors = 0
    with open(args.csv_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for path in wav_paths:
            print(f"Reading {os.path.relpath(path, args.wav_dir)} ...")
            row = row_for_file(path, include_ixml_xml=args.include_ixml_xml)
            if not args.include_ixml_xml:
                row.pop("ixml_xml", None)
            writer.writerow(row)
            rows_written += 1
            if row["read_error"]:
                errors += 1
                print(f"  ! error: {row['read_error']}")

    print()
    print(f"Wrote {rows_written} row(s) to {args.csv_out}"
          f"{f' ({errors} with read errors)' if errors else ''}")


if __name__ == "__main__":
    main()
