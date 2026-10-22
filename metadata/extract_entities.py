#!/usr/bin/env python3
"""
extract_entities.py

Extracts named entities from all text files in a directory using spaCy
and writes the results to a CSV file.

Usage:
    python extract_entities.py --input_dir ./documents --output_file entities.csv
    python extract_entities.py --input_dir ./documents --output_file entities.csv --model en_core_web_sm --extensions .txt .md

Requirements:
    pip install spacy
    python -m spacy download en_core_web_sm
"""

import argparse
import csv
import sys
from pathlib import Path

import spacy


def load_model(model_name: str):
    """Load a spaCy language model, exiting with a helpful message if missing."""
    try:
        return spacy.load(model_name)
    except OSError:
        print(
            f"Error: spaCy model '{model_name}' is not installed.\n"
            f"Install it with:\n    python -m spacy download {model_name}",
            file=sys.stderr,
        )
        sys.exit(1)


def find_files(input_dir: Path, extensions, recursive: bool):
    """Yield files in input_dir matching the given extensions."""
    pattern = "**/*" if recursive else "*"
    for path in sorted(input_dir.glob(pattern)):
        if path.is_file() and (not extensions or path.suffix.lower() in extensions):
            yield path


def read_file(path: Path):
    """
    Read a file as text, tolerating encoding issues.

    Returns the decoded text, or None if the file appears to be binary
    (contains a NUL byte) or can't be read at all.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None

    if b"\x00" in raw:
        # Almost certainly a binary file (image, pdf, docx, etc.) -- skip it.
        # Use the pdf/docx/pptx/xlsx skills separately to extract text from those first.
        return None

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def extract_entities(nlp, text: str):
    """Run the spaCy pipeline and yield (text, label, start_char, end_char) tuples."""
    doc = nlp(text)
    for ent in doc.ents:
        yield ent.text, ent.label_, ent.start_char, ent.end_char


def main():
    parser = argparse.ArgumentParser(
        description="Extract named entities from files in a directory using spaCy."
    )
    parser.add_argument(
        "--input_dir", required=True, type=Path, help="Directory containing input files."
    )
    parser.add_argument(
        "--output_file", required=True, type=Path, help="Path to the output CSV file."
    )
    parser.add_argument(
        "--model",
        default="en_core_web_sm",
        help="spaCy model to use (default: en_core_web_sm).",
    )
    parser.add_argument(
        "--extensions",
        nargs="*",
        default=[],
        help="File extensions to include, e.g. --extensions .txt .md (default: all files). "
        "Pass no arguments to include every file in the directory.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recurse into subdirectories.",
    )
    parser.add_argument(
        "--max_length",
        type=int,
        default=1_000_000,
        help="spaCy nlp.max_length setting, for very large files (default: 1,000,000 chars).",
    )
    args = parser.parse_args()

    if not args.input_dir.is_dir():
        print(f"Error: '{args.input_dir}' is not a valid directory.", file=sys.stderr)
        sys.exit(1)

    extensions = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in args.extensions}

    files = list(find_files(args.input_dir, extensions, args.recursive))
    if not files:
        print(f"No matching files found in '{args.input_dir}'.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading spaCy model '{args.model}'...")
    nlp = load_model(args.model)
    nlp.max_length = args.max_length

    args.output_file.parent.mkdir(parents=True, exist_ok=True)

    total_entities = 0
    skipped = 0
    with open(args.output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["file_name", "entity_text", "entity_label", "start_char", "end_char"])

        for i, path in enumerate(files, start=1):
            print(f"[{i}/{len(files)}] Processing '{path.name}'...")
            text = read_file(path)
            if text is None:
                print(f"    Skipping '{path.name}' (binary or unreadable file).")
                skipped += 1
                continue
            if not text.strip():
                continue
            try:
                for ent_text, label, start, end in extract_entities(nlp, text):
                    writer.writerow([path.name, ent_text, label, start, end])
                    total_entities += 1
            except ValueError as e:
                print(f"    Skipping '{path.name}' (spaCy error: {e}).")
                skipped += 1

    print(f"\nDone. Extracted {total_entities} entities from {len(files)} file(s).")
    if skipped:
        print(f"Skipped {skipped} file(s) that were binary or unreadable as text.")
    print(f"Results written to '{args.output_file}'.")


if __name__ == "__main__":
    main()
