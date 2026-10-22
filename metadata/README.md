# Metadata

Read, write and generate descriptive and technical metadata for audio, video, images, PDFs, captions and text.

| File | What it does | Runs on |
|---|---|---|
| **Audio & video** | | |
| [`extract_wav_metadata.py`](extract_wav_metadata.py) | Exports technical and embedded metadata (BWF `bext`, INFO, iXML…) from `.wav` files to CSV | Local |
| [`embed_bext_metadata.py`](embed_bext_metadata.py) | Writes BWF `bext` metadata into `.wav` files from a CSV | Local |
| [`extract_video_metadata.py`](extract_video_metadata.py) | Exports technical metadata from video files to CSV (MediaInfo) | Local |
| [`embed_webvtt_metadata.py`](embed_webvtt_metadata.py) | Adds a FADGI metadata block to WebVTT caption files from a CSV | Local |
| **Images** | | |
| [`exiftool_lite.py`](exiftool_lite.py) | Reads, writes and strips EXIF/GPS/IPTC/XMP in JPEG, TIFF and PNG ([full guide](README_exiftool_lite.md)) | Local |
| [`generate_image_descriptions_csv.py`](generate_image_descriptions_csv.py) | Builds a CSV of image file names and their descriptions from matching `.txt` files | Local |
| **Documents** | | |
| [`count_pdf_pages.py`](count_pdf_pages.py) | Counts pages in every PDF and exports them to CSV | Local |
| [`extract_entities.py`](extract_entities.py) | Finds names, places, organizations, dates etc. in text files (spaCy) and exports them to CSV | Local |
| [`summarize_docs_gemini.py`](summarize_docs_gemini.py) | Writes a summary (up to 250 words) of every document to CSV using Google Gemini | Local (needs a Gemini API key) |
| **CSV templates** | | |
| [`bext_metadata_template.csv`](bext_metadata_template.csv) / [`example_bext_metadata.csv`](example_bext_metadata.csv) | Blank template and filled-in example for `embed_bext_metadata.py` | |
| [`webvtt_metadata_template.csv`](webvtt_metadata_template.csv) / [`example_webvtt_metadata.csv`](example_webvtt_metadata.csv) | Blank template and filled-in example for `embed_webvtt_metadata.py` | |

All commands below are run in Terminal from the folder containing the script.

---

## Audio & video

### extract_wav_metadata.py

One row per `.wav` file: format details, BWF `bext` fields, RIFF INFO tags, cue points, and whether iXML/ADM and other chunks are present.

```powershell
python -m pip install wavinfo
python extract_wav_metadata.py "\path\to\wavs" wav_metadata.csv
```

Options: `--recursive` (include subfolders), `--include-ixml-xml` (add the full iXML document as a column; can be large).

### embed_bext_metadata.py

Writes Broadcast Wave `bext` metadata into `.wav` files. Start from [`bext_metadata_template.csv`](bext_metadata_template.csv); see [`example_bext_metadata.csv`](example_bext_metadata.csv) for a filled-in version.

| CSV column | Notes |
|---|---|
| `filename` | **Required.** The `.wav` file name (extension optional) |
| `description` | Up to 256 characters |
| `originator`, `originator_reference` | Up to 32 characters each |
| `origination_date`, `origination_time` | `YYYY-MM-DD`, `HH:MM:SS`. Default: now |
| `time_reference_samples` | Samples since midnight. Default: 0 |
| `coding_history` | Default: generated from the file's audio format |

```powershell
python -m pip install wave-bwf-rf64
python embed_bext_metadata.py "\path\to\wavs" metadata.csv --dry-run   # check the CSV first
python embed_bext_metadata.py "\path\to\wavs" metadata.csv --backup
```

`--dry-run` checks the CSV against the folder without changing anything. `--backup` keeps a `.bak` copy of each original. CSV rows that don't match a file are reported and skipped; files with no CSV row are left alone.

### extract_video_metadata.py

One row per video file with technical and tagged metadata from [MediaInfo](https://mediaarea.net/en/MediaInfo).

```powershell
python -m pip install pymediainfo
python extract_video_metadata.py "\path\to\videos" video_metadata.csv
```

Options: `--recursive`, `--include-full-json` (add the complete MediaInfo output as a column). On Windows, `pymediainfo` includes MediaInfo, so nothing else needs installing.

### embed_webvtt_metadata.py

Adds a metadata block to the top of each `.vtt` caption file, following the FADGI [Guidelines for Embedding Metadata in WebVTT Files](https://www.digitizationguidelines.gov/guidelines/accessibilty_WebVTT.html):

```
WEBVTT
Type: caption
Language: eng
Responsible Party: US, University of Northern Iowa
...
```

Start from [`webvtt_metadata_template.csv`](webvtt_metadata_template.csv). Columns: `Filename`, `Application` (written to the file as FADGI's `Type`: subtitle, caption, audio description, chapters or metadata), `Language` (ISO 639-3, e.g. `eng`), `Responsible Party`, `Media Identifier`, `Originating File`, `File Creator`, `File Creation Date`, `Title`, `Origin History`, `Local Usage Element`. Separate repeated values with `;`.

```powershell
python embed_webvtt_metadata.py "\path\to\vtts" metadata.csv --dry-run
python embed_webvtt_metadata.py "\path\to\vtts" metadata.csv --backup
```

No extra packages needed.

---

## Images

### exiftool_lite.py

A single-file tool for reading and editing image metadata, using the same command syntax as [ExifTool](https://exiftool.org/). See [README_exiftool_lite.md](README_exiftool_lite.md) for everything it can do.

```powershell
python -m pip install pillow piexif
python exiftool_lite.py photo.jpg                          # show all metadata
python exiftool_lite.py -Artist="Rod Library" photo.jpg    # set a tag
python exiftool_lite.py -all= photo.jpg                    # strip all metadata
python exiftool_lite.py -csv -r -ext jpg "\path\to\photos" # report on a folder
```

Like ExifTool, it keeps a backup (`photo.jpg_original`) whenever it changes a file. Add `-overwrite_original` to skip that.

### generate_image_descriptions_csv.py

Pairs each image (`.tif .tiff .jpg .jpeg`) with the `.txt` file of the same name and writes `filename, description` rows to a CSV. Useful for loading alt text or descriptions into a repository.

```powershell
python generate_image_descriptions_csv.py "\path\to\folder" -o descriptions.csv
```

Without `-o`, the CSV is saved as `image_descriptions.csv` in the same folder. No extra packages needed.

---

## Documents

### count_pdf_pages.py

```powershell
python -m pip install pypdf
python count_pdf_pages.py "\path\to\pdfs" -o page_counts.csv --recursive
```

Output columns: `File Name`, `Number of Pages` (e.g. "12 pages"). Default output: `pdf_page_counts.csv`.

### extract_entities.py

Runs spaCy's named-entity recognition over every text file and writes each entity (person, place, organization, date…) to a CSV.

```powershell
python -m pip install spacy
python -m spacy download en_core_web_sm
python extract_entities.py --input_dir "\path\to\texts" --output_file entities.csv --extensions .txt
```

| Option | Meaning | Default |
|---|---|---|
| `--input_dir` | Folder of text files | — |
| `--output_file` | Output CSV | — |
| `--extensions` | Only these file types, e.g. `.txt .md` | All files |
| `--recursive` | Include subfolders | Off |
| `--model` | spaCy model (`en_core_web_lg` is more accurate but larger) | `en_core_web_sm` |
| `--max_length` | Raise for very large files | `1000000` characters |

### summarize_docs_gemini.py

Sends each `.txt`, `.md`, `.pdf` or `.docx` file to Gemini and writes `filename, summary` rows to a CSV.

```powershell
python -m pip install google-genai pypdf python-docx
$env:GEMINI_API_KEY="your_api_key_here"
python summarize_docs_gemini.py "\path\to\documents" -o summaries.csv
```

Options: `--recursive`, `--model` (default `gemini-2.5-flash`), `--api-key`. See [`../htt`](../htt/README.md#set-up-a-gemini-api-key-once) for getting a key.
