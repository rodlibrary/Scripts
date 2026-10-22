#!/usr/bin/env python3
"""
Generate a CSV file mapping image filenames to descriptions.

For a given directory, this script finds image files (.tiff, .tif, .jpg, .jpeg)
and pairs each one with the contents of a same-named .txt file (e.g.
"photo1.jpg" pairs with "photo1.txt") as its description.

Usage:
    python generate_image_descriptions_csv.py /path/to/directory [-o output.csv]
"""

import argparse
import csv
import sys
from pathlib import Path

IMAGE_EXTENSIONS = {".tiff", ".tif", ".jpg", ".jpeg"}


def build_records(directory: Path):
    """Return a list of (filename, description) tuples and a list of warnings."""
    records = []
    warnings = []

    image_files = sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )

    if not image_files:
        warnings.append(f"No image files found in {directory}")

    for image_path in image_files:
        txt_path = image_path.with_suffix(".txt")

        if not txt_path.exists():
            warnings.append(f"Missing description file for '{image_path.name}' "
                             f"(expected '{txt_path.name}')")
            description = ""
        else:
            try:
                description = txt_path.read_text(encoding="utf-8").strip()
            except UnicodeDecodeError:
                description = txt_path.read_text(encoding="latin-1").strip()
                warnings.append(f"'{txt_path.name}' was not UTF-8; read as latin-1")

        records.append((image_path.name, description))

    return records, warnings


def write_csv(records, output_path: Path):
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["filename", "description"])
        writer.writerows(records)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a CSV of image filenames and their text-file descriptions."
    )
    parser.add_argument("directory", type=str, help="Directory containing image and .txt files")
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Name (or path) of the output CSV file. "
             "If omitted, saves as 'image_descriptions.csv' inside the source directory. "
             "If just a filename is given, it is also saved inside the source directory."
    )
    args = parser.parse_args()

    directory = Path(args.directory).expanduser().resolve()

    if not directory.is_dir():
        print(f"Error: '{directory}' is not a valid directory.", file=sys.stderr)
        sys.exit(1)

    if args.output is None:
        output_path = directory / "image_descriptions.csv"
    else:
        output_arg = Path(args.output).expanduser()
        # If the user only gave a bare filename (no directory component),
        # place it inside the source directory. Otherwise respect their path.
        if output_arg.parent == Path("."):
            output_path = directory / output_arg.name
        else:
            output_path = output_arg.resolve()

    records, warnings = build_records(directory)

    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    write_csv(records, output_path)
    print(f"Wrote {len(records)} record(s) to {output_path}")


if __name__ == "__main__":
    main()
