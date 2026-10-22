#!/usr/bin/env python3
"""
extract_video_metadata.py

Walk a directory of video files, read their technical and tagged metadata
with MediaInfo (via the `pymediainfo` bindings), and export it to a single
CSV file -- one row per video file.

Requirements
------------
    pip install pymediainfo

`pymediainfo` is just a wrapper: it needs the actual MediaInfo library
(libmediainfo) installed on the system to work.
    - Debian/Ubuntu:  sudo apt-get install libmediainfo0v5
    - macOS (Homebrew): brew install mediainfo
    - Windows: pymediainfo ships a bundled DLL, so a separate install is
      usually not required.

Usage
-----
    python extract_video_metadata.py /path/to/video_dir metadata_out.csv
    python extract_video_metadata.py /path/to/video_dir metadata_out.csv --recursive
    python extract_video_metadata.py /path/to/video_dir metadata_out.csv --include-full-json
"""

import argparse
import csv
import json
import os
import sys

try:
    from pymediainfo import MediaInfo
except ImportError:
    sys.exit(
        "The 'pymediainfo' package is required but not installed.\n"
        "Install it with:  pip install pymediainfo"
    )

# Video file extensions to look for.
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".wmv", ".m4v", ".mpg", ".mpeg",
    ".mxf", ".webm", ".flv", ".ts", ".m2ts", ".vob", ".3gp", ".ogv",
}

FIELDNAMES = [
    "filename",
    "filepath",
    "file_size_bytes",
    "file_last_modification_date",
    # container / general
    "container_format",
    "format_profile",
    "duration_seconds",
    "duration_hhmmss",
    "overall_bit_rate",
    "video_track_count",
    "audio_track_count",
    "text_track_count",
    "other_track_count",
    # tagged metadata (from the General track, if present)
    "title",
    "performer",
    "comment",
    "genre",
    "recorded_date",
    "encoded_date",
    "tagged_date",
    "writing_application",
    "writing_library",
    # video track (first video track, if any)
    "video_format",
    "video_format_profile",
    "video_codec_id",
    "width",
    "height",
    "display_aspect_ratio",
    "frame_rate",
    "frame_rate_mode",
    "video_bit_rate",
    "bit_depth",
    "scan_type",
    "color_space",
    "chroma_subsampling",
    "rotation",
    # audio track (first audio track, if any)
    "audio_format",
    "audio_codec_id",
    "audio_channels",
    "audio_sampling_rate",
    "audio_bit_rate",
    "audio_bit_rate_mode",
    "audio_language",
    # notes / errors
    "notes",
    "read_error",
]


def hhmmss(seconds):
    if seconds is None:
        return ""
    seconds = float(seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def first_or_none(track_list):
    return track_list[0] if track_list else None


def row_for_file(path, include_full_json=False):
    row = {name: "" for name in FIELDNAMES}
    row["filename"] = os.path.basename(path)
    row["filepath"] = path
    try:
        row["file_size_bytes"] = os.path.getsize(path)
    except OSError:
        pass

    try:
        mi = MediaInfo.parse(path)
    except Exception as e:
        row["read_error"] = f"{type(e).__name__}: {e}"
        return row

    general = first_or_none(mi.general_tracks)
    video = first_or_none(mi.video_tracks)
    audio = first_or_none(mi.audio_tracks)

    if video is None:
        row["notes"] = "no video track found (may not be a valid video file)"

    row["video_track_count"] = len(mi.video_tracks)
    row["audio_track_count"] = len(mi.audio_tracks)
    row["text_track_count"] = len(mi.text_tracks)
    row["other_track_count"] = len(mi.other_tracks)

    if general is not None:
        g = general.to_data()
        row["file_last_modification_date"] = g.get("file_last_modification_date", "")
        row["container_format"] = g.get("format", "")
        row["format_profile"] = g.get("format_profile", "")
        duration_ms = g.get("duration")
        if duration_ms is not None:
            duration_s = float(duration_ms) / 1000.0
            row["duration_seconds"] = round(duration_s, 3)
            row["duration_hhmmss"] = hhmmss(duration_s)
        row["overall_bit_rate"] = g.get("overall_bit_rate", "")
        row["title"] = g.get("title", g.get("movie_name", ""))
        row["performer"] = g.get("performer", "")
        row["comment"] = g.get("comment", "")
        row["genre"] = g.get("genre", "")
        row["recorded_date"] = g.get("recorded_date", "")
        row["encoded_date"] = g.get("encoded_date", "")
        row["tagged_date"] = g.get("tagged_date", "")
        row["writing_application"] = g.get("writing_application", "")
        row["writing_library"] = g.get("writing_library", "")

    if video is not None:
        v = video.to_data()
        row["video_format"] = v.get("format", "")
        row["video_format_profile"] = v.get("format_profile", "")
        row["video_codec_id"] = v.get("codec_id", "")
        row["width"] = v.get("width", "")
        row["height"] = v.get("height", "")
        row["display_aspect_ratio"] = v.get("display_aspect_ratio", "")
        row["frame_rate"] = v.get("frame_rate", "")
        row["frame_rate_mode"] = v.get("frame_rate_mode", "")
        row["video_bit_rate"] = v.get("bit_rate", "")
        row["bit_depth"] = v.get("bit_depth", "")
        row["scan_type"] = v.get("scan_type", "")
        row["color_space"] = v.get("color_space", "")
        row["chroma_subsampling"] = v.get("chroma_subsampling", "")
        row["rotation"] = v.get("rotation", "")
        if not row["writing_library"]:
            row["writing_library"] = v.get("writing_library", "")

    if audio is not None:
        a = audio.to_data()
        row["audio_format"] = a.get("format", "")
        row["audio_codec_id"] = a.get("codec_id", "")
        row["audio_channels"] = a.get("channel_s", "")
        row["audio_sampling_rate"] = a.get("sampling_rate", "")
        row["audio_bit_rate"] = a.get("bit_rate", "")
        row["audio_bit_rate_mode"] = a.get("bit_rate_mode", "")
        row["audio_language"] = a.get("language", "")

    if include_full_json:
        row["full_mediainfo_json"] = mi.to_json()

    return row


def find_video_files(directory, recursive):
    if recursive:
        for root, _dirs, files in os.walk(directory):
            for f in sorted(files):
                if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                    yield os.path.join(root, f)
    else:
        for f in sorted(os.listdir(directory)):
            full = os.path.join(directory, f)
            if os.path.isfile(full) and os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                yield full


def main():
    parser = argparse.ArgumentParser(
        description="Extract video metadata via MediaInfo (pymediainfo) to a CSV."
    )
    parser.add_argument("video_dir", help="Directory containing video files")
    parser.add_argument("csv_out", help="Path to the CSV file to write")
    parser.add_argument(
        "--recursive", action="store_true", help="Also search subdirectories"
    )
    parser.add_argument(
        "--include-full-json",
        action="store_true",
        help="Add a column with the complete raw MediaInfo output as JSON per file",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.video_dir):
        sys.exit(f"Not a directory: {args.video_dir}")

    video_paths = list(find_video_files(args.video_dir, args.recursive))
    print(f"Found {len(video_paths)} video file(s) in {args.video_dir}"
          f"{' (recursive)' if args.recursive else ''}")

    fieldnames = FIELDNAMES + (["full_mediainfo_json"] if args.include_full_json else [])

    rows_written = 0
    errors = 0
    with open(args.csv_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for path in video_paths:
            print(f"Reading {os.path.relpath(path, args.video_dir)} ...")
            row = row_for_file(path, include_full_json=args.include_full_json)
            writer.writerow(row)
            rows_written += 1
            if row["read_error"]:
                errors += 1
                print(f"  ! error: {row['read_error']}")
            elif row["notes"]:
                print(f"  ! note: {row['notes']}")

    print()
    print(f"Wrote {rows_written} row(s) to {args.csv_out}"
          f"{f' ({errors} with read errors)' if errors else ''}")


if __name__ == "__main__":
    main()
