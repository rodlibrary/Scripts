# exiftool_lite.py

A single-file Python tool for reading, writing, and editing image metadata,
inspired by [ExifTool](https://exiftool.org/) and using the same command-line syntax.

## What it covers

| Format | EXIF read | EXIF write/delete | IPTC read | XMP read | XMP write |
|--------|:---:|:---:|:---:|:---:|:---:|
| JPEG   | ✅ | ✅ | ✅ | ✅ | ✅ |
| TIFF   | ✅ | ✅ | — | — | — |
| PNG    | ✅ (eXIf chunk) | ✅ | — | ✅ | ✅ |

**Not covered** (real ExifTool supports ~150 formats and thousands of tags —
this script focuses on the ~95% use case of everyday photo metadata):
- RAW formats (CR2, NEF, ARW, DNG, etc.)
- Video files (MP4, MOV)
- Writing IPTC (reading only)
- Full RDF/XML parsing of XMP (it extracts common fields like title, creator,
  description, rating via pattern matching, and can read/write/strip the
  *whole* raw XMP packet — but won't understand every custom XMP schema)

## Install

```bash
pip install Pillow piexif
```

## Usage

The CLI intentionally mirrors ExifTool's own syntax:

```bash
# Read everything
python3 exiftool_lite.py photo.jpg

# Read specific tags
python3 exiftool_lite.py -Artist -Model -DateTimeOriginal photo.jpg

# Write tags
python3 exiftool_lite.py -Artist="Jane Doe" -Copyright="(c) 2026" photo.jpg

# GPS: pass plain decimal degrees, sign gives hemisphere (- = S/W)
python3 exiftool_lite.py -GPSLatitude=40.7128 -GPSLongitude=-74.0060 photo.jpg

# Delete a single tag
python3 exiftool_lite.py -Artist= photo.jpg

# Strip ALL metadata (EXIF + IPTC + XMP)
python3 exiftool_lite.py -all= photo.jpg

# Copy all EXIF metadata from one file to another
python3 exiftool_lite.py -TagsFromFile src.jpg dst.jpg

# JSON / CSV output
python3 exiftool_lite.py -json photo.jpg
python3 exiftool_lite.py -csv photo.jpg

# Recurse a directory, only touching .jpg files
python3 exiftool_lite.py -r -ext jpg ./photos/

# List every tag name the script knows about
python3 exiftool_lite.py -list

# Skip the automatic file.jpg_original backup
python3 exiftool_lite.py -overwrite_original -Artist="Jane" photo.jpg

# Group output by metadata source (EXIF / IPTC / XMP / File)
python3 exiftool_lite.py -g photo.jpg
```

Like ExifTool, by default any file that gets modified is backed up first as
`filename.ext_original` in the same folder. Pass `-overwrite_original` to
skip that and edit in place.

## As a library

Everything is also usable directly from Python:

```python
from exiftool_lite import MetadataFile

mf = MetadataFile("photo.jpg")
print(mf.read_all())                 # dict of EXIF/IPTC/XMP/File info
mf.set_tags({"Artist": "Jane Doe"})  # writes + makes a backup
mf.strip_all()                       # removes everything
```

## Known limitations / gotchas

- `piexif` (the EXIF read/write engine) only understands JPEG, TIFF, and
  WEBP natively; PNG EXIF support here goes through Pillow's `eXIf` chunk
  handling instead, which is a newer (2017+) part of the PNG spec — very old
  PNG readers may not show it.
- Writing IPTC is not implemented — IPTC's IIM binary format inside a
  Photoshop APP13 segment is fiddly to construct correctly, and it's a
  legacy format that's been superseded by XMP for most modern workflows.
  Reading IPTC works fine.
- XMP handling reads/writes the raw packet as text. Editing individual XMP
  fields means constructing the RDF/XML yourself (see the docstring example
  in `set_xmp_raw` usage above) rather than a `-XMP-dc:Title=...` flag.
