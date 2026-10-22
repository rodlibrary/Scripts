# Presidio with human review — multi-format, OCR and structured data

Adds a human review step between Presidio's analyzer and anonymizer. It reads and
redacts PDFs (including scans), images, Word, Excel, CSV and plain-text files:

```
                 ┌ pdfminer / python-docx / openpyxl / csv ┐
analyze.py ──────┤ Tesseract OCR (scans, photos)            ├──► review.csv ──► humans: REDACT / KEEP ──► anonymize.py ──► redacted files
  (Presidio      └ Presidio Structured (whole table columns)┘                                                (Presidio Anonymizer +
   Analyzer)                                                                                                   StructuredEngine)
```

| Module | Job |
|---|---|
| `analyze.py` | Step 1: find PII, write `review.csv` |
| `anonymize.py` | Step 3: apply reviewers' decisions, write redacted copies plus `audit_log.csv` |
| `formats.py` | Reads text from each file type; writes redacted copies in the same format |
| `ocr.py` | Tesseract OCR with orientation detection and an on-disk cache |
| `structured.py` | Presidio Structured: column-level detection and anonymization for CSV/XLSX |
| `pii_utils.py` | Cleans up overlapping and duplicate Presidio results |

## Setup

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_lg
```

Tesseract is a separate program:
- **Windows:** the UB Mannheim installer (github.com/UB-Mannheim/tesseract/wiki). Add it to PATH,
  or set `pytesseract.pytesseract.tesseract_cmd` in `ocr.py`.
- **macOS:** `brew install tesseract`
- **Linux:** `sudo apt install tesseract-ocr`

The `osd` data file is needed for auto-rotating sideways scans. The Windows and Homebrew
installs include it; on Linux it comes with `tesseract-ocr`. For other languages, install
their packs (e.g. `tesseract-ocr-spa`) and pass `--ocr-lang eng+spa`.

## Supported files

| Type | How text is found | Redacted output |
|---|---|---|
| `.pdf` | pdfminer.six text layer; pages with no text layer are OCR'd | **Flattened, searchable PDF**: pages become images with black boxes, plus a new invisible OCR text layer (see below). None of the original text layer, metadata, annotations, form fields or attachments survive. Any page rotation is handled. |
| `.png .jpg .jpeg .tif .tiff .bmp` | Tesseract OCR, auto-rotated if sideways or upside down; every page of a multi-page TIFF | Same format with black boxes; EXIF/metadata dropped |
| `.csv .tsv .xlsx` | Every cell, plus **column-level detection with Presidio Structured** | Same file; cells replaced with `--operator`; Excel formulas untouched |
| `.docx` | Body, tables, headers, footers | Same file; text replaced, formatting kept; author fields cleared |
| `.txt .md .log .json .xml .html .htm` | Read as text | Same file with text replaced |

Anything else (e.g. `.zip`, `.doc`, `.pptx`, email) is listed as skipped and **not** copied.

### What the review CSV contains

There are two kinds of rows, shown in the `scope` column:

- **`text`**: one occurrence, e.g. "Jane Doe" on page 2. REDACT replaces just that text.
  For OCR'd text, `ocr_confidence` (0–100) shows how sure Tesseract was about the word.
  Low values mean the text may be misread, so check the original.
- **`column`**: a whole table column that Presidio Structured judged to hold one kind of
  PII, such as "column B 'Email Address' → EMAIL_ADDRESS". `detected_text` shows sample
  values, and `score` is the share of sampled cells that matched. REDACT anonymizes
  every value in the column; KEEP leaves the column alone.

How the two kinds work together in tables:
- Columns of free text (median over 5 words, e.g. Comments or Notes) never become column rows,
  since replacing whole sentences would wipe out the data. Their cells are scanned
  individually instead, so a parent's name inside a comment still gets its own `text` row.
- Inside a column that has a column row, cell rows are shown only for *different*
  PII found inside a value (e.g. an email typed into a Name cell). Header cells are always
  checked individually.
- Weak column matches (under 30% of cells by default) don't get a column row. Their
  cells are still checked one by one.

## 1. Analyze

```bash
python analyze.py --input docs/ --output review.csv
```

Common options:

| Option | Meaning |
|---|---|
| `--threshold 0.4` | Minimum Presidio score to report |
| `--entities PERSON EMAIL_ADDRESS` | Look only for these types |
| `--exclude-entities UK_NHS AU_TFN` | Ignore these types. Useful for US data, where Presidio tends to label phone numbers `UK_NHS`. |
| `--ocr auto` (default) | OCR images and PDF pages with no text layer |
| `--ocr always` | OCR every PDF page; also catches text inside images embedded in digital PDFs |
| `--ocr off` | Never OCR; scans are reported and left out of the output |
| `--ocr-lang eng+spa` | Tesseract language(s) |
| `--ocr-dpi 300` | Resolution PDF pages are OCR'd at; try 400 for small print |
| `--no-auto-rotate` | Don't auto-correct sideways or upside-down scans |
| `--no-structured` | Turn off column-level rows; report every cell individually |
| `--structured-min-ratio 0.3` | Share of cells that must match before a column row is created |
| `--structured-strategy most_common` | Presidio Structured's column vote: `most_common`, `highest_confidence` or `mixed` |
| `--structured-sample 500` | Rows sampled per table for column detection |

Files written next to the CSV:

| File | Contents |
|---|---|
| `review_warnings.txt` | Anything not fully analyzed: scanned pages with OCR off, low OCR confidence, images inside digital PDFs, unreadable files. **Read it.** |
| `review_settings.json` | The OCR settings, so `anonymize.py` reads files the same way |
| `review_ocr_cache/` | OCR results, reused by `anonymize.py` so scans aren't OCR'd twice. **Contains document text; delete it when the job is done.** |

## 2. Review

Open `review.csv` in Excel, Google Sheets or LibreOffice:

| column | who edits | meaning |
|---|---|---|
| `scope`, `file`, `location` | — | text or column finding, and where it is (`page 3`, `Roster!B4`, `column B 'Email'`) |
| `detected_text`, `context` | — | what was found and the text around it (`[[ ]]` marks the hit) |
| `entity_type`, `also_detected_as`, `score`, `ocr_confidence` | — | Presidio's label(s) and confidence; Tesseract's confidence |
| **`action`** | reviewer | `REDACT` or `KEEP` (also accepted: Y/N, X, 1/0, TRUE/FALSE, ✓) |
| `reviewer_notes` | reviewer | optional, copied into the audit log |
| `finding_id`, `start`, `end`, `file_sha256` | — | **do not edit** |

Every row starts as `REDACT`, so a row someone skips is redacted rather than leaked. Sorting and
filtering rows is fine; deleting rows is not.

To use checkboxes in Excel, insert them on the `action` column (Insert ▸ Checkbox):
checked = TRUE = REDACT, unchecked = FALSE = KEEP. Or add a Data Validation drop-down with
the list `REDACT,KEEP`. Save as CSV when done.

**OCR can miss things.** Handwriting, stamps, faint or very small text may not be
recognized at all, and then they won't be in the CSV. Have someone glance at
redacted scans before release, prioritizing pages listed in the warnings.

## 3. Anonymize

```bash
python anonymize.py --input docs/ --review review.csv --output-dir redacted/
```

| Option | Meaning |
|---|---|
| `--operator replace` (default) | How text, Word, Excel and CSV redactions look: `replace` gives `<PERSON>`, `redact` removes the text, `mask` gives `****`, `hash` gives a hash. PDFs and images always get black boxes. |
| `--pdf-dpi 200` | Resolution of flattened PDF pages (also what the searchable layer is OCR'd from; 300 helps small print) |
| `--no-text-layer` | Image-only PDFs, without the searchable layer |
| `--keep-metadata` | Keep author fields in Word and Excel files |

Whole-column decisions are applied with Presidio Structured's `StructuredEngine`;
individual findings with Presidio Anonymizer.

### Searchable layer on redacted PDFs

After the black boxes are drawn, each page gets a new invisible text layer, so the
redacted PDF can be searched, selected and copied like the original. It is built so that
redacted text can't get into it:

1. **OCR source:** the layer is OCR'd from the *redacted* page images, never from the
   original text layer. The OCR runs on a copy where the redacted areas are blank white,
   so Tesseract doesn't turn the black boxes into junk characters like "EE" or "|".
2. **Assembly:** Tesseract's PDF renderer writes the invisible text, which is placed
   with the black-box page image, so search hits highlight in the right place.
3. **Rotation:** sideways and upside-down pages are OCR'd upright, then flagged so they
   display exactly as before.
4. **Leak check:** before saving, the finished layer is searched for every redacted string.
   If one appears (it shouldn't), that file is saved image-only instead, with a warning.

The layer is OCR, so it can contain small recognition errors. It reflects what's
visible on the redacted page, not the original text. Pages with only a line or two of
text may be too sparse for Tesseract to detect their orientation.

The script stops instead of guessing if:
- any `action` is blank or unrecognized,
- a source file changed since analysis (SHA-256 check),
- `start`/`end`/`detected_text` no longer line up with the file.

Files are **not written** to the output folder, and are listed at the end, if they are:
- unsupported,
- scans or images where no text could be read.

`audit_log.csv` records every decision, including who-wrote-what notes.

## Limits

- **Word files:** text boxes, comments, footnotes and tracked changes aren't covered,
  and images inside Word files aren't OCR'd.
- **Excel files:** dates and decimal numbers aren't analyzed. A sheet counts as a table
  when its first non-empty row is all text with rows below it.
- **JSON:** analyzed as plain text. Presidio Structured also has a JSON processor
  that could be added the same way as CSV.
- **Adding a file type:** write a class in `formats.py` with `load()` (builds `text`)
  and `write_redacted(edits, dest, ...)`. Then register its extension.
