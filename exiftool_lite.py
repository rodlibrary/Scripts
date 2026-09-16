#!/usr/bin/env python3
"""
exiftool_lite.py - A Python tool for reading, writing, and editing
image metadata (EXIF, GPS, IPTC, XMP), inspired by Phil Harvey's ExifTool.

Supported formats:
    - JPEG  : EXIF (full read/write/delete), IPTC (read), XMP (read/write raw)
    - TIFF  : EXIF (full read/write/delete)
    - PNG   : EXIF via the 'eXIf' chunk (read/write/delete), XMP (read/write raw)

This is NOT a full reimplementation of ExifTool (which supports ~150 formats
and thousands of tags) -- it covers the metadata operations people use most:
inspecting, editing, stripping, and copying EXIF/GPS/IPTC/XMP data.

Usage examples mirror ExifTool's own CLI syntax:

    python3 exiftool_lite.py photo.jpg
    python3 exiftool_lite.py -Artist -Model photo.jpg
    python3 exiftool_lite.py -Artist="Jane Doe" photo.jpg
    python3 exiftool_lite.py -GPSLatitude=40.7128 -GPSLongitude=-74.0060 photo.jpg
    python3 exiftool_lite.py -Artist= photo.jpg              # delete one tag
    python3 exiftool_lite.py -all= photo.jpg                 # strip all metadata
    python3 exiftool_lite.py -TagsFromFile src.jpg dst.jpg   # copy EXIF between files
    python3 exiftool_lite.py -json photo.jpg
    python3 exiftool_lite.py -r -ext jpg ./photos/           # recurse a directory
    python3 exiftool_lite.py -list                           # list known tag names

By default, like ExifTool, a backup of the original file is kept
(file.jpg_original) whenever a file is modified. Pass -overwrite_original
to skip that.
"""

import argparse
import csv
import io
import json
import os
import re
import signal
import sys
from fractions import Fraction

# Avoid a noisy traceback when output is piped into something like `head`
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

try:
    import piexif
    from piexif import helper as piexif_helper
except ImportError:
    print("Missing dependency 'piexif'. Install with: pip install piexif", file=sys.stderr)
    sys.exit(1)

try:
    from PIL import Image
    from PIL import IptcImagePlugin
except ImportError:
    print("Missing dependency 'Pillow'. Install with: pip install Pillow", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------
# Tag registry: build a friendly-name <-> (ifd, tag_id) lookup from piexif
# --------------------------------------------------------------------------

IFD_NAMES = ("0th", "Exif", "GPS", "1st", "Interop")

class TagRegistry:
    """Maps friendly tag names (e.g. 'Artist', 'DateTimeOriginal') to their
    (ifd, tag_id, type) location, using piexif's built-in tag tables."""

    def __init__(self):
        self.name_to_loc = {}   # name -> list of (ifd, tag_id)
        self.loc_to_name = {}   # (ifd, tag_id) -> name
        for ifd in IFD_NAMES:
            table = piexif.TAGS.get(ifd, {})
            for tag_id, info in table.items():
                name = info["name"]
                self.name_to_loc.setdefault(name, []).append((ifd, tag_id))
                self.loc_to_name[(ifd, tag_id)] = name

    def resolve(self, name, preferred_ifd=None):
        """Return the (ifd, tag_id) for a tag name, or None."""
        locs = self.name_to_loc.get(name)
        if not locs:
            return None
        if preferred_ifd:
            for ifd, tag_id in locs:
                if ifd == preferred_ifd:
                    return (ifd, tag_id)
        return locs[0]

    def all_names(self):
        return sorted(self.name_to_loc.keys())

    def tag_type(self, ifd, tag_id):
        return piexif.TAGS[ifd][tag_id]["type"]


REGISTRY = TagRegistry()

# A handful of exiftool-style composite/convenience aliases
ALIASES = {
    "Make": "Make",
    "Model": "Model",
    "Author": "Artist",
    "Creator": "Artist",
    "Description": "ImageDescription",
    "Comment": "UserComment",
}


# --------------------------------------------------------------------------
# Value conversion helpers
# --------------------------------------------------------------------------

def _to_rational(value):
    """Convert a float/int/str to an (numerator, denominator) tuple."""
    frac = Fraction(str(value)).limit_denominator(1000000)
    return (frac.numerator, frac.denominator)


def _rational_to_float(rational):
    try:
        num, den = rational
        return num / den if den else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def dms_to_decimal(dms, ref):
    """Convert EXIF GPS ((d,1),(m,1),(s,100)) + ref ('N'/'S'/'E'/'W') to decimal degrees."""
    try:
        d = _rational_to_float(dms[0])
        m = _rational_to_float(dms[1])
        s = _rational_to_float(dms[2])
        dec = d + m / 60.0 + s / 3600.0
        if ref in (b"S", "S", b"W", "W"):
            dec = -dec
        return dec
    except Exception:
        return None


def decimal_to_dms(decimal_deg):
    """Convert decimal degrees to EXIF GPS rational DMS tuple + ref sign."""
    negative = decimal_deg < 0
    decimal_deg = abs(decimal_deg)
    degrees = int(decimal_deg)
    minutes_full = (decimal_deg - degrees) * 60
    minutes = int(minutes_full)
    seconds = round((minutes_full - minutes) * 60 * 100)
    dms = ((degrees, 1), (minutes, 1), (seconds, 100))
    return dms, negative


def encode_value_for_tag(ifd, tag_id, raw_value):
    """Convert a user-supplied string into the correct python type for piexif."""
    tag_type = REGISTRY.tag_type(ifd, tag_id)
    if tag_type == "Ascii":
        return raw_value.encode("utf-8") + b"\x00" if not isinstance(raw_value, bytes) else raw_value
    if tag_type in ("Byte", "Short", "Long", "SShort", "SLong"):
        return int(raw_value)
    if tag_type == "Rational":
        return _to_rational(raw_value)
    if tag_type == "SRational":
        return _to_rational(raw_value)
    if tag_type == "Undefined":
        # Undefined fields (e.g. UserComment) - store as UTF-8 with piexif helper if possible
        return raw_value.encode("utf-8") if isinstance(raw_value, str) else raw_value
    # Fallback: try ascii
    return raw_value.encode("utf-8") if isinstance(raw_value, str) else raw_value


def decode_value_for_display(ifd, tag_id, value):
    tag_type = REGISTRY.tag_type(ifd, tag_id)
    if tag_type == "Ascii" and isinstance(value, bytes):
        return value.rstrip(b"\x00").decode("utf-8", errors="replace")
    if tag_type in ("Rational", "SRational"):
        if isinstance(value, tuple) and len(value) == 2:
            f = _rational_to_float(value)
            return f
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace").strip("\x00")
        except Exception:
            return repr(value)
    return value


# --------------------------------------------------------------------------
# XMP helpers (raw packet read/write - JPEG APP1 + PNG iTXt)
# --------------------------------------------------------------------------

XMP_APP1_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"


def read_xmp_from_jpeg_bytes(data):
    """Scan JPEG APP1 segments for an XMP packet, return the XML string or None."""
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i < len(data) - 4:
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xD9 or marker == 0xDA:  # EOI or start of scan
            break
        seg_len = (data[i + 2] << 8) + data[i + 3]
        if marker == 0xE1:  # APP1
            segment = data[i + 4: i + 2 + seg_len]
            if segment.startswith(XMP_APP1_HEADER):
                xml_bytes = segment[len(XMP_APP1_HEADER):]
                return xml_bytes.decode("utf-8", errors="replace")
        i += 2 + seg_len
    return None


def write_xmp_to_jpeg_bytes(data, xmp_xml):
    """Insert (or replace) an XMP APP1 segment in JPEG bytes. Returns new bytes."""
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Not a valid JPEG")
    out = bytearray(data[:2])
    i = 2
    inserted = False
    xmp_payload = XMP_APP1_HEADER + xmp_xml.encode("utf-8")
    new_segment = bytes([0xFF, 0xE1]) + (len(xmp_payload) + 2).to_bytes(2, "big") + xmp_payload

    while i < len(data) - 1:
        if data[i] != 0xFF:
            out += data[i:]
            break
        marker = data[i + 1]
        if marker == 0xDA:  # start of scan -> insert before if not yet inserted
            if not inserted:
                out += new_segment
                inserted = True
            out += data[i:]
            break
        if marker in (0xD8,):
            i += 2
            continue
        seg_len = (data[i + 2] << 8) + data[i + 3]
        segment_full = data[i:i + 2 + seg_len]
        if marker == 0xE1 and segment_full[4:4 + len(XMP_APP1_HEADER)] == XMP_APP1_HEADER:
            # replace existing XMP segment
            out += new_segment
            inserted = True
        else:
            out += segment_full
        i += 2 + seg_len

    if not inserted:
        # no scan found (shouldn't happen) - just append after SOI
        out = bytearray(data[:2]) + new_segment + data[2:]
    return bytes(out)


def strip_xmp_from_jpeg_bytes(data):
    if not data.startswith(b"\xff\xd8"):
        return data
    out = bytearray(data[:2])
    i = 2
    while i < len(data) - 1:
        if data[i] != 0xFF:
            out += data[i:]
            break
        marker = data[i + 1]
        if marker == 0xDA:
            out += data[i:]
            break
        if marker in (0xD8,):
            i += 2
            continue
        seg_len = (data[i + 2] << 8) + data[i + 3]
        segment_full = data[i:i + 2 + seg_len]
        if marker == 0xE1 and segment_full[4:4 + len(XMP_APP1_HEADER)] == XMP_APP1_HEADER:
            pass  # drop it
        else:
            out += segment_full
        i += 2 + seg_len
    return bytes(out)


def extract_xmp_summary(xml_text):
    """Pull a few common human-readable fields out of a raw XMP packet via regex,
    without needing a full XML/RDF parser."""
    if not xml_text:
        return {}
    fields = {}
    patterns = {
        "XMP-dc:Title": r"<dc:title>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
        "XMP-dc:Creator": r"<dc:creator>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
        "XMP-dc:Description": r"<dc:description>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
        "XMP-dc:Rights": r"<dc:rights>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
        "XMP-photoshop:DateCreated": r'photoshop:DateCreated="([^"]*)"',
        "XMP-xmp:CreatorTool": r'xmp:CreatorTool="([^"]*)"',
        "XMP-xmp:Rating": r'xmp:Rating="([^"]*)"',
    }
    for name, pat in patterns.items():
        m = re.search(pat, xml_text, re.DOTALL)
        if m:
            fields[name] = m.group(1).strip()
    return fields


# --------------------------------------------------------------------------
# Core MetadataFile class
# --------------------------------------------------------------------------

class MetadataFile:
    def __init__(self, path):
        self.path = path
        self.ext = os.path.splitext(path)[1].lower()

    # ---- reading ----

    def read_exif(self):
        """Return dict {friendly_name: value} using piexif, works for JPEG/TIFF.
        For PNG, uses Pillow's getexif() as piexif can't parse PNG."""
        result = {}
        if self.ext in (".jpg", ".jpeg", ".tif", ".tiff"):
            try:
                exif_dict = piexif.load(self.path)
            except Exception:
                return result
            for ifd in ("0th", "Exif", "GPS", "1st"):
                for tag_id, value in exif_dict.get(ifd, {}).items():
                    name = REGISTRY.loc_to_name.get((ifd, tag_id), f"Unknown{ifd}{tag_id}")
                    result[name] = decode_value_for_display(ifd, tag_id, value)
            # friendly GPS decimal
            gps = exif_dict.get("GPS", {})
            if piexif.GPSIFD.GPSLatitude in gps and piexif.GPSIFD.GPSLatitudeRef in gps:
                dec = dms_to_decimal(gps[piexif.GPSIFD.GPSLatitude], gps[piexif.GPSIFD.GPSLatitudeRef])
                if dec is not None:
                    result["GPSLatitudeDecimal"] = dec
            if piexif.GPSIFD.GPSLongitude in gps and piexif.GPSIFD.GPSLongitudeRef in gps:
                dec = dms_to_decimal(gps[piexif.GPSIFD.GPSLongitude], gps[piexif.GPSIFD.GPSLongitudeRef])
                if dec is not None:
                    result["GPSLongitudeDecimal"] = dec
        elif self.ext == ".png":
            try:
                img = Image.open(self.path)
                exif = img.getexif()
                for tag_id, value in exif.items():
                    name = REGISTRY.loc_to_name.get(("0th", tag_id)) or REGISTRY.loc_to_name.get(("Exif", tag_id)) or f"Tag{tag_id}"
                    result[name] = value
            except Exception:
                pass
        return result

    def read_iptc(self):
        """Return dict of IPTC fields via Pillow (read-only)."""
        result = {}
        try:
            img = Image.open(self.path)
            iptc = IptcImagePlugin.getiptcinfo(img)
            if not iptc:
                return result
            for key, value in iptc.items():
                name = IPTC_FIELD_NAMES.get(key, f"IPTC-{key[0]}:{key[1]}")
                if isinstance(value, list):
                    value = [v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v for v in value]
                elif isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                result[name] = value
        except Exception:
            pass
        return result

    def read_xmp_raw(self):
        if self.ext in (".jpg", ".jpeg"):
            with open(self.path, "rb") as f:
                data = f.read()
            return read_xmp_from_jpeg_bytes(data)
        elif self.ext == ".png":
            try:
                img = Image.open(self.path)
                return img.info.get("XML:com.adobe.xmp")
            except Exception:
                return None
        return None

    def read_all(self):
        """Return a merged dict of all readable metadata, grouped."""
        out = {}
        exif = self.read_exif()
        if exif:
            out["EXIF"] = exif
        iptc = self.read_iptc()
        if iptc:
            out["IPTC"] = iptc
        xmp_raw = self.read_xmp_raw()
        if xmp_raw:
            xmp_fields = extract_xmp_summary(xmp_raw)
            xmp_fields["_RawPacketLength"] = len(xmp_raw)
            out["XMP"] = xmp_fields
        out["File"] = {
            "FileName": os.path.basename(self.path),
            "FileSize": os.path.getsize(self.path),
            "FileType": self.ext.lstrip(".").upper(),
        }
        return out

    def get_tag(self, name):
        exif = self.read_exif()
        if name in exif:
            return exif[name]
        iptc = self.read_iptc()
        if name in iptc:
            return iptc[name]
        xmp = extract_xmp_summary(self.read_xmp_raw())
        return xmp.get(name)

    # ---- writing ----

    def set_tags(self, tag_values, backup=True):
        """tag_values: dict of {name: value_str_or_None}. None means delete."""
        if self.ext in (".jpg", ".jpeg", ".tif", ".tiff"):
            self._set_exif_tags(tag_values, backup=backup)
        elif self.ext == ".png":
            self._set_png_exif_tags(tag_values, backup=backup)
        else:
            raise ValueError(f"Writing metadata not supported for {self.ext} files")

    def _load_exif_dict(self):
        try:
            return piexif.load(self.path)
        except Exception:
            return {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    def _set_exif_tags(self, tag_values, backup=True):
        exif_dict = self._load_exif_dict()
        for name, raw_value in tag_values.items():
            if name in ("GPSLatitude", "GPSLongitude") and raw_value is not None:
                self._apply_gps(exif_dict, name, raw_value)
                continue
            loc = REGISTRY.resolve(name)
            if not loc:
                print(f"Warning: unknown tag '{name}', skipping", file=sys.stderr)
                continue
            ifd, tag_id = loc
            if raw_value is None:
                exif_dict.get(ifd, {}).pop(tag_id, None)
            else:
                encoded = encode_value_for_tag(ifd, tag_id, raw_value)
                exif_dict.setdefault(ifd, {})[tag_id] = encoded
        exif_bytes = piexif.dump(exif_dict)
        self._write_exif_bytes(exif_bytes, backup=backup)

    def _apply_gps(self, exif_dict, name, raw_value):
        dec = float(raw_value)
        dms, negative = decimal_to_dms(dec)
        gps = exif_dict.setdefault("GPS", {})
        if name == "GPSLatitude":
            gps[piexif.GPSIFD.GPSLatitude] = dms
            gps[piexif.GPSIFD.GPSLatitudeRef] = "S" if negative else "N"
        else:
            gps[piexif.GPSIFD.GPSLongitude] = dms
            gps[piexif.GPSIFD.GPSLongitudeRef] = "W" if negative else "E"

    def _write_exif_bytes(self, exif_bytes, backup=True):
        if backup:
            backup_path = self.path + "_original"
            if not os.path.exists(backup_path):
                with open(self.path, "rb") as src, open(backup_path, "wb") as dst:
                    dst.write(src.read())
        if self.ext in (".tif", ".tiff"):
            # piexif.insert() only supports JPEG/WEBP; use Pillow for TIFF
            img = Image.open(self.path)
            img.load()
            img.save(self.path, exif=exif_bytes)
        else:
            piexif.insert(exif_bytes, self.path)

    def _set_png_exif_tags(self, tag_values, backup=True):
        img = Image.open(self.path)
        exif = img.getexif()
        for name, raw_value in tag_values.items():
            loc = REGISTRY.resolve(name, preferred_ifd="0th") or REGISTRY.resolve(name, preferred_ifd="Exif")
            if not loc:
                print(f"Warning: unknown tag '{name}', skipping", file=sys.stderr)
                continue
            _, tag_id = loc
            if raw_value is None:
                if tag_id in exif:
                    del exif[tag_id]
            else:
                exif[tag_id] = raw_value
        if backup:
            backup_path = self.path + "_original"
            if not os.path.exists(backup_path):
                with open(self.path, "rb") as src, open(backup_path, "wb") as dst:
                    dst.write(src.read())
        img.save(self.path, exif=exif)

    def set_xmp_raw(self, xmp_xml, backup=True):
        if self.ext not in (".jpg", ".jpeg"):
            raise ValueError("Raw XMP write currently only supported for JPEG")
        with open(self.path, "rb") as f:
            data = f.read()
        new_data = write_xmp_to_jpeg_bytes(data, xmp_xml)
        if backup:
            backup_path = self.path + "_original"
            if not os.path.exists(backup_path):
                with open(backup_path, "wb") as dst:
                    dst.write(data)
        with open(self.path, "wb") as f:
            f.write(new_data)

    def strip_all(self, backup=True):
        """Remove EXIF, IPTC, XMP metadata entirely."""
        if backup:
            backup_path = self.path + "_original"
            if not os.path.exists(backup_path):
                with open(self.path, "rb") as src, open(backup_path, "wb") as dst:
                    dst.write(src.read())
        if self.ext in (".jpg", ".jpeg"):
            with open(self.path, "rb") as f:
                data = f.read()
            try:
                piexif.remove(self.path)
                with open(self.path, "rb") as f:
                    data = f.read()
            except Exception:
                pass
            data = strip_xmp_from_jpeg_bytes(data)
            with open(self.path, "wb") as f:
                f.write(data)
        elif self.ext in (".tif", ".tiff", ".png"):
            # Re-save pixel data into a fresh image with no metadata carried over
            img = Image.open(self.path)
            img.load()
            clean = Image.new(img.mode, img.size)
            clean.paste(img)
            clean.save(self.path)
        else:
            raise ValueError(f"Stripping metadata not supported for {self.ext} files")


IPTC_FIELD_NAMES = {
    (2, 5): "IPTC:ObjectName",
    (2, 25): "IPTC:Keywords",
    (2, 40): "IPTC:SpecialInstructions",
    (2, 55): "IPTC:DateCreated",
    (2, 80): "IPTC:By-line",
    (2, 85): "IPTC:By-lineTitle",
    (2, 90): "IPTC:City",
    (2, 92): "IPTC:Sub-location",
    (2, 95): "IPTC:Province-State",
    (2, 101): "IPTC:Country-PrimaryLocationName",
    (2, 105): "IPTC:Headline",
    (2, 110): "IPTC:Credit",
    (2, 115): "IPTC:Source",
    (2, 116): "IPTC:CopyrightNotice",
    (2, 120): "IPTC:Caption-Abstract",
}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

SUPPORTED_EXTS = {".jpg", ".jpeg", ".tif", ".tiff", ".png"}


def collect_files(paths, recursive, ext_filter):
    files = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _, names in os.walk(p):
                    for n in names:
                        ext = os.path.splitext(n)[1].lower().lstrip(".")
                        if ext_filter and ext not in ext_filter:
                            continue
                        if not ext_filter and f".{ext}" not in SUPPORTED_EXTS:
                            continue
                        files.append(os.path.join(root, n))
            else:
                for n in sorted(os.listdir(p)):
                    full = os.path.join(p, n)
                    if os.path.isfile(full):
                        ext = os.path.splitext(n)[1].lower().lstrip(".")
                        if ext_filter and ext not in ext_filter:
                            continue
                        if not ext_filter and f".{ext}" not in SUPPORTED_EXTS:
                            continue
                        files.append(full)
        else:
            files.append(p)
    return files


def parse_args(argv):
    """Custom parser: ExifTool-style '-Tag', '-Tag=Value', '-Tag=' flags mixed with files."""
    read_tags = []
    write_tags = {}   # name -> value or None (delete)
    files = []
    options = {
        "json": False,
        "csv": False,
        "overwrite_original": False,
        "recursive": False,
        "ext": None,
        "group": False,
        "list_tags": False,
        "all_delete": False,
        "tags_from_file": None,
        "version": False,
    }

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        elif arg in ("-ver", "--version"):
            options["version"] = True
        elif arg == "-json":
            options["json"] = True
        elif arg == "-csv":
            options["csv"] = True
        elif arg == "-overwrite_original":
            options["overwrite_original"] = True
        elif arg == "-r":
            options["recursive"] = True
        elif arg == "-ext":
            i += 1
            options["ext"] = argv[i].lower().lstrip(".")
        elif arg in ("-g", "-G"):
            options["group"] = True
        elif arg == "-list":
            options["list_tags"] = True
        elif arg == "-all=":
            options["all_delete"] = True
        elif arg == "-TagsFromFile":
            i += 1
            options["tags_from_file"] = argv[i]
        elif arg.startswith("-") and "=" in arg:
            name, _, value = arg[1:].partition("=")
            write_tags[name] = value if value != "" else None
        elif arg.startswith("-"):
            read_tags.append(arg[1:])
        else:
            files.append(arg)
        i += 1

    return read_tags, write_tags, files, options


def print_metadata(all_meta, name, use_json, use_csv, group):
    if use_json:
        print(json.dumps({name: all_meta}, indent=2, default=str))
        return
    if use_csv:
        writer = csv.writer(sys.stdout)
        flat = {}
        for grp, fields in all_meta.items():
            for k, v in fields.items():
                flat[f"{grp}:{k}" if group else k] = v
        writer.writerow(["SourceFile"] + list(flat.keys()))
        writer.writerow([name] + list(flat.values()))
        return
    print(f"======== {name}")
    for grp, fields in all_meta.items():
        for k, v in fields.items():
            label = f"[{grp}] {k}" if group else k
            print(f"{label:35s}: {v}")


def main():
    argv = sys.argv[1:]
    read_tags, write_tags, files, options = parse_args(argv)

    if options["version"]:
        print("exiftool_lite.py 1.0 (Python, EXIF/IPTC/XMP subset)")
        return

    if options["list_tags"]:
        for n in REGISTRY.all_names():
            print(n)
        return

    if not files:
        print("No files specified. Use -h for help.", file=sys.stderr)
        sys.exit(1)

    ext_filter = {options["ext"]} if options["ext"] else None
    file_list = collect_files(files, options["recursive"], ext_filter)
    backup = not options["overwrite_original"]

    if options["tags_from_file"]:
        src = MetadataFile(options["tags_from_file"])
        src_exif = src.read_exif()
        str_tags = {}
        for k, v in src_exif.items():
            if k in ("GPSLatitude", "GPSLongitude"):
                continue  # raw DMS tuples; handled via the Decimal aliases below
            if k == "GPSLatitudeDecimal":
                str_tags["GPSLatitude"] = str(v)
                continue
            if k == "GPSLongitudeDecimal":
                str_tags["GPSLongitude"] = str(v)
                continue
            str_tags[k] = v if isinstance(v, str) else str(v)
        for target in file_list:
            mf = MetadataFile(target)
            mf.set_tags(str_tags, backup=backup)
            print(f"Copied {len(str_tags)} tags from {options['tags_from_file']} to {target}")
        return

    if options["all_delete"]:
        for target in file_list:
            if not os.path.isfile(target):
                print(f"Error: File not found - {target}", file=sys.stderr)
                continue
            mf = MetadataFile(target)
            mf.strip_all(backup=backup)
            print(f"    1 image files updated ({target}, all metadata removed)")
        return

    if write_tags:
        for target in file_list:
            if not os.path.isfile(target):
                print(f"Error: File not found - {target}", file=sys.stderr)
                continue
            mf = MetadataFile(target)
            mf.set_tags(write_tags, backup=backup)
            print(f"    1 image files updated ({target})")
        return

    # read mode
    for target in file_list:
        if not os.path.isfile(target):
            print(f"Error: File not found - {target}", file=sys.stderr)
            continue
        mf = MetadataFile(target)
        if read_tags:
            flat = {}
            for tag in read_tags:
                flat[tag] = mf.get_tag(tag)
            print_metadata({"Selected": flat}, target, options["json"], options["csv"], options["group"])
        else:
            all_meta = mf.read_all()
            print_metadata(all_meta, target, options["json"], options["csv"], options["group"])


if __name__ == "__main__":
    main()
