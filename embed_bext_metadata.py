#!/usr/bin/env python3
"""
embed_bext_metadata.py

Embed Broadcast Wave Format (BWF) 'bext' chunk metadata into a directory of
.wav files, using a CSV file as the source of the metadata. Uses the
`wave-bwf-rf64` package (a BWF/RF64-aware fork of Python's stdlib `wave`
module) to read and rewrite the files.

Install the dependency first:
    pip install wave-bwf-rf64

CSV format
----------
The CSV must have a header row. One column identifies which wav file the
row applies to; the rest supply bext fields. Recognized columns
(case-insensitive, all optional except `filename`):

    filename              - required. Name of the .wav file in the target
                             directory (with or without the .wav extension).
    description           - free text, max 256 bytes (bext "Description").
    originator             - free text, max 32 bytes.
    originator_reference    - free text, max 32 bytes.
    origination_date        - "YYYY-MM-DD". Defaults to today if omitted.
    origination_time        - "HH:MM:SS". Defaults to now if omitted.
    time_reference_samples  - integer sample count since midnight (the
                              64-bit SMPTE time reference). Defaults to 0.
    coding_history          - free text. If omitted, an EBU R98-style
                              coding history line is auto-generated from the
                              file's own audio format.

Any row whose `filename` doesn't match a file found in the directory is
reported and skipped. Any wav file in the directory with no matching CSV
row is left untouched.

Usage
-----
    python embed_bext_metadata.py /path/to/wav_dir metadata.csv
    python embed_bext_metadata.py /path/to/wav_dir metadata.csv --backup
    python embed_bext_metadata.py /path/to/wav_dir metadata.csv --dry-run
"""

import argparse
import csv
import datetime
import os
import shutil
import sys
import tempfile

try:
    import wave_bwf_rf64 as wb
except ImportError:
    sys.exit(
        "The 'wave-bwf-rf64' package is required but not installed.\n"
        "Install it with:  pip install wave-bwf-rf64"
    )

# Max byte lengths defined by the BWF spec (struct.pack truncates/pads to
# these automatically, but we validate up front so problems are reported
# clearly instead of silently truncating).
MAX_DESCRIPTION_BYTES = 256
MAX_ORIGINATOR_BYTES = 32
MAX_ORIGINATOR_REF_BYTES = 32

FRAMES_PER_CHUNK = 1_000_000  # read/write audio in chunks to bound memory


def to_bytes(value, max_len=None, field_name=""):
    """Encode a CSV string field to bytes, warning (not failing) on overlong values."""
    if value is None:
        return b""
    value = value.strip()
    if value == "":
        return b""
    data = value.encode("utf-8", errors="replace")
    if max_len is not None and len(data) > max_len:
        print(
            f"  ! warning: {field_name!r} is {len(data)} bytes, "
            f"will be truncated to {max_len} bytes"
        )
    return data


def parse_date(value):
    """Return [year, month, day] from 'YYYY-MM-DD', or today's date if blank."""
    if value and value.strip():
        d = datetime.date.fromisoformat(value.strip())
        return [d.year, d.month, d.day]
    now = datetime.date.today()
    return [now.year, now.month, now.day]


def parse_time(value):
    """Return [hour, minute, second] from 'HH:MM:SS', or current time if blank."""
    if value and value.strip():
        t = datetime.time.fromisoformat(value.strip())
        return [t.hour, t.minute, t.second]
    now = datetime.datetime.now().time()
    return [now.hour, now.minute, now.second]


def parse_time_reference(value):
    """Return [low32, high32] signed-32-bit words from an integer sample count."""
    if not value or not value.strip():
        return [0, 0]
    samples = int(value.strip())
    samples &= 0xFFFFFFFFFFFFFFFF  # wrap to unsigned 64-bit
    low = samples & 0xFFFFFFFF
    high = (samples >> 32) & 0xFFFFFFFF
    # the library packs these as signed 32-bit ints, so re-sign if needed
    if low >= 0x80000000:
        low -= 0x100000000
    if high >= 0x80000000:
        high -= 0x100000000
    return [low, high]


def load_metadata_rows(csv_path):
    """Load the CSV into a dict keyed by lowercase filename (with .wav extension)."""
    rows = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            sys.exit("CSV file has no header row.")
        # normalize header names to lowercase for lookups
        normalized = {name: name.lower().strip() for name in reader.fieldnames}
        for raw_row in reader:
            row = {normalized[k]: v for k, v in raw_row.items() if k in normalized}
            filename = (row.get("filename") or "").strip()
            if not filename:
                print("  ! skipping CSV row with no filename")
                continue
            if not filename.lower().endswith(".wav"):
                filename += ".wav"
            rows[filename.lower()] = row
    return rows


def embed_bext(wav_path, row, dry_run=False):
    """Rewrite wav_path in place with bext metadata from `row`."""
    reader = wb.open(wav_path, "rb")
    try:
        params = reader.getparams()
        nframes_total = reader.getnframes()
        framerate = reader.getframerate()
        sampwidth = reader.getsampwidth()
        nchannels = reader.getnchannels()

        if dry_run:
            reader.close()
            return

        fd, tmp_path = tempfile.mkstemp(
            suffix=".wav", dir=os.path.dirname(os.path.abspath(wav_path))
        )
        os.close(fd)
        try:
            writer = wb.open(tmp_path, "wb")
            try:
                writer.setparams(params)

                writer.set_bext_description(
                    to_bytes(row.get("description"), MAX_DESCRIPTION_BYTES, "description")
                )
                writer.set_bext_originator(
                    to_bytes(row.get("originator"), MAX_ORIGINATOR_BYTES, "originator")
                )
                writer.set_bext_originator_reference(
                    to_bytes(
                        row.get("originator_reference"),
                        MAX_ORIGINATOR_REF_BYTES,
                        "originator_reference",
                    )
                )
                writer.set_bext_origination_date(parse_date(row.get("origination_date")))
                writer.set_bext_origination_time(parse_time(row.get("origination_time")))
                writer.set_bext_time_reference(
                    parse_time_reference(row.get("time_reference_samples"))
                )

                coding_history = row.get("coding_history")
                if coding_history and coding_history.strip():
                    ch_bytes = coding_history.strip().encode("utf-8")
                    if not ch_bytes.endswith(b"\r\n"):
                        ch_bytes += b"\r\n"
                    if len(ch_bytes) % 2 == 1:
                        ch_bytes += b"\0"
                    writer.set_bext_coding_history(ch_bytes)
                # else: leave blank so set_bext() auto-generates it below

                writer.set_bext()

                # copy audio data in bounded chunks to avoid loading huge
                # files entirely into memory
                remaining = nframes_total
                while remaining > 0:
                    n = min(FRAMES_PER_CHUNK, remaining)
                    data = reader.readframes(n)
                    if not data:
                        break
                    writer.writeframes(data)
                    remaining -= n
            finally:
                writer.close()
        except Exception:
            os.remove(tmp_path)
            raise
        finally:
            reader.close()

        shutil.move(tmp_path, wav_path)
    except Exception:
        try:
            reader.close()
        except Exception:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Embed BWF bext metadata into a directory of wav files from a CSV."
    )
    parser.add_argument("wav_dir", help="Directory containing .wav files")
    parser.add_argument("csv_file", help="CSV file with bext metadata (see script docstring)")
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

    if not os.path.isdir(args.wav_dir):
        sys.exit(f"Not a directory: {args.wav_dir}")
    if not os.path.isfile(args.csv_file):
        sys.exit(f"CSV file not found: {args.csv_file}")

    print(f"Reading metadata from {args.csv_file} ...")
    rows_by_filename = load_metadata_rows(args.csv_file)
    print(f"  {len(rows_by_filename)} metadata row(s) loaded")

    wav_files = sorted(
        f for f in os.listdir(args.wav_dir) if f.lower().endswith(".wav")
    )
    print(f"Found {len(wav_files)} .wav file(s) in {args.wav_dir}")

    processed = 0
    skipped_no_row = []
    matched_filenames = set()

    for filename in wav_files:
        row = rows_by_filename.get(filename.lower())
        if row is None:
            skipped_no_row.append(filename)
            continue
        matched_filenames.add(filename.lower())

        wav_path = os.path.join(args.wav_dir, filename)
        print(f"Processing {filename} ...")

        if args.backup and not args.dry_run:
            shutil.copy2(wav_path, wav_path + ".bak")

        try:
            embed_bext(wav_path, row, dry_run=args.dry_run)
            processed += 1
        except Exception as e:
            print(f"  ! error: failed to process {filename}: {e}")

    unmatched_rows = [
        fn for fn in rows_by_filename if fn not in matched_filenames
    ]

    print()
    print("Summary")
    print("-------")
    print(f"  Processed: {processed}{' (dry run, nothing written)' if args.dry_run else ''}")
    if skipped_no_row:
        print(f"  Wav files with no matching CSV row ({len(skipped_no_row)}):")
        for fn in skipped_no_row:
            print(f"    - {fn}")
    if unmatched_rows:
        print(f"  CSV rows with no matching wav file ({len(unmatched_rows)}):")
        for fn in unmatched_rows:
            print(f"    - {fn}")


if __name__ == "__main__":
    main()
