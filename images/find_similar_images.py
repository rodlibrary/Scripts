#!/usr/bin/env python3
"""
find_similar_images.py

Scans a directory (recursively) for images, computes a perceptual hash for
each one, groups images that look visually similar, and writes the results
to a CSV file so a human can review them for possible deletion.

Requirements:
    pip install pillow imagehash

Usage:
    python find_similar_images.py /path/to/photos
    python find_similar_images.py /path/to/photos -o results.csv -t 5
    python find_similar_images.py /path/to/photos --hash-type phash --recursive
"""

import argparse
import csv
import os
import sys
from itertools import combinations

try:
    from PIL import Image
except ImportError:
    sys.exit("Missing dependency 'Pillow'. Install it with: pip install pillow")

try:
    import imagehash
except ImportError:
    sys.exit("Missing dependency 'imagehash'. Install it with: pip install imagehash")


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif", ".webp", ".heic"
}

HASH_FUNCS = {
    "phash": imagehash.phash,       # perceptual hash - good general default
    "ahash": imagehash.average_hash,
    "dhash": imagehash.dhash,
    "whash": imagehash.whash,
}


def find_image_files(root_dir, recursive=True):
    """Yield paths to all image files under root_dir."""
    if recursive:
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for fname in filenames:
                if os.path.splitext(fname)[1].lower() in IMAGE_EXTENSIONS:
                    yield os.path.join(dirpath, fname)
    else:
        for fname in os.listdir(root_dir):
            full_path = os.path.join(root_dir, fname)
            if os.path.isfile(full_path) and os.path.splitext(fname)[1].lower() in IMAGE_EXTENSIONS:
                yield full_path


def compute_hashes(file_paths, hash_func):
    """Return a dict mapping file_path -> perceptual hash. Skips unreadable files."""
    hashes = {}
    for path in file_paths:
        try:
            with Image.open(path) as img:
                hashes[path] = hash_func(img)
        except Exception as e:
            print(f"  Warning: could not process '{path}': {e}", file=sys.stderr)
    return hashes


def group_similar_images(hashes, threshold):
    """
    Group images whose perceptual hashes differ by <= threshold (Hamming distance).
    Uses a simple union-find so that transitively similar images end up in one group.
    Returns a list of groups, each a list of (file_path, hash) tuples.
    """
    paths = list(hashes.keys())
    parent = {p: p for p in paths}

    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in combinations(paths, 2):
        distance = hashes[a] - hashes[b]
        if distance <= threshold:
            union(a, b)

    groups = {}
    for p in paths:
        root = find(p)
        groups.setdefault(root, []).append(p)

    # Only keep groups with more than one image (actual duplicates/similars)
    return [
        [(p, hashes[p]) for p in members]
        for members in groups.values()
        if len(members) > 1
    ]


def write_csv(groups, output_path):
    """Write grouped results to a CSV file for human review."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["group_id", "file_name", "file_path", "file_size_bytes", "perceptual_hash"])
        for group_id, members in enumerate(groups, start=1):
            # Sort within group so largest file (likely original) is listed first
            members_sorted = sorted(
                members, key=lambda item: os.path.getsize(item[0]), reverse=True
            )
            for path, phash in members_sorted:
                writer.writerow([
                    group_id,
                    os.path.basename(path),
                    path,
                    os.path.getsize(path),
                    str(phash),
                ])


def main():
    parser = argparse.ArgumentParser(
        description="Find visually similar images in a directory and export results to CSV."
    )
    parser.add_argument("directory", help="Directory of images to scan")
    parser.add_argument(
        "-o", "--output", default="similar_images.csv",
        help="Path to output CSV file (default: similar_images.csv)"
    )
    parser.add_argument(
        "-t", "--threshold", type=int, default=5,
        help="Max Hamming distance between hashes to consider images similar. "
             "Lower = stricter (near-exact duplicates only), higher = looser. Default: 5"
    )
    parser.add_argument(
        "--hash-type", choices=HASH_FUNCS.keys(), default="phash",
        help="Perceptual hash algorithm to use (default: phash)"
    )
    parser.add_argument(
        "--no-recursive", action="store_true",
        help="Do not scan subdirectories"
    )
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        sys.exit(f"Error: '{args.directory}' is not a valid directory.")

    print(f"Scanning '{args.directory}' for image files...")
    file_paths = list(find_image_files(args.directory, recursive=not args.no_recursive))
    print(f"Found {len(file_paths)} image files.")

    if not file_paths:
        print("No images found. Exiting.")
        return

    print(f"Computing {args.hash_type} hashes...")
    hash_func = HASH_FUNCS[args.hash_type]
    hashes = compute_hashes(file_paths, hash_func)
    print(f"Successfully hashed {len(hashes)} of {len(file_paths)} images.")

    print(f"Comparing images (threshold={args.threshold})...")
    groups = group_similar_images(hashes, args.threshold)
    total_flagged = sum(len(g) for g in groups)
    print(f"Found {len(groups)} group(s) of similar images, {total_flagged} images total.")

    write_csv(groups, args.output)
    print(f"Results written to '{args.output}'.")


if __name__ == "__main__":
    main()
