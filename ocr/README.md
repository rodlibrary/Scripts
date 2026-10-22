# Optical character recognition (OCR)

Make scanned images and PDFs searchable.

| File | What it does | Runs on |
|---|---|---|
| [`ocr_jp2.py`](ocr_jp2.py) | OCRs JPEG 2000 (`.jp2`) images into `.txt`, ALTO `.xml` and searchable `.pdf` | Local (Tesseract) |
| [`batch_ocr.py`](batch_ocr.py) | OCRs a folder of images (or PDFs/TIFFs) into `.txt`, `.pdf`, hOCR and ALTO XML | Local (Tesseract) |
| [`ocr_pdfs.py`](ocr_pdfs.py) | Adds a text layer to every PDF in a folder with OCRmyPDF | Local |
| [`gemini_ocr_pdf.py`](gemini_ocr_pdf.py) | Adds a text layer to PDFs using Google Gemini instead of Tesseract | Local (needs a Gemini API key) |
| [`generate_searchable_pdf.py`](generate_searchable_pdf.py) | Combines images and matching `.txt` files into one searchable PDF | Local |
| [`ocr_pdfs.script`](ocr_pdfs.script) | SLURM job: OCRmyPDF on many PDFs in parallel | HPC |
| [`tesseract.script`](tesseract.script) | SLURM job: Tesseract on a folder of images in parallel | HPC |

**Which one?** For digitized PDFs, use `ocr_pdfs.py` (or `ocr_pdfs.script` for large batches). For `.jp2` images, use `ocr_jp2.py`. For other image formats, use `batch_ocr.py` (or `tesseract.script`). Try `gemini_ocr_pdf.py` when Tesseract does poorly, e.g. on difficult layouts or poor-quality scans; it costs API credit per page.

## Install Tesseract (local scripts)

Most of these scripts need the Tesseract program, not just the Python package.

1. Install Tesseract with the [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki).
2. Add it to your PATH: **Start > Edit environment variables > Path > Edit > New**, enter `C:\Program Files\Tesseract-OCR\`, then OK.
3. Close and reopen Terminal, then check with `tesseract --version`.

---

## ocr_jp2.py

Recursively finds `.jp2` images and writes three files for each: `.txt` (plain text), `.xml` (ALTO, with word positions) and `.pdf` (image + invisible text). The folder structure of the input is mirrored in the output, so same-named images in different folders don't overwrite each other.

**Install**

```powershell
python -m pip install pytesseract pillow pylibjpeg-openjpeg
```

**Run**

```powershell
python ocr_jp2.py "\path\to\files"
python ocr_jp2.py "\path\to\files" --output-dir "\path\to\ocr_output" --lang eng
```

| Option | Meaning | Default |
|---|---|---|
| `input_dir` | Folder of `.jp2` images (subfolders included) | — |
| `-d`, `--output-dir` | Where to write the results | `<input_dir>\ocr_output` |
| `-l`, `--lang` | Tesseract language, e.g. `eng`, `fra` | `eng` |
| `-c`, `--config` | Extra Tesseract settings, e.g. `"--psm 6"` | — |

## batch_ocr.py

Runs Tesseract over a folder of images and writes four formats for each file, each in its own subfolder: `txt/`, `pdf/`, `hocr/`, `alto/`. Multi-page TIFFs and PDFs are supported.

**Install:** Tesseract only (see above); no Python packages needed.

**Run**

```powershell
python batch_ocr.py -i "\path\to\scans" -o "\path\to\ocr_out"
python batch_ocr.py -i "\path\to\scans" -o "\path\to\ocr_out" -l eng+fra --recursive --workers 4
```

| Option | Meaning | Default |
|---|---|---|
| `-i`, `--input-dir` | Folder of files to OCR | — |
| `-o`, `--output-dir` | Output folder | — |
| `-l`, `--lang` | Language(s), e.g. `eng+fra` | `eng` |
| `--dpi` | Resolution hint for images without DPI metadata | `300` |
| `--psm` / `--oem` | Tesseract page-segmentation / engine mode | `3` / `3` |
| `--extensions` | Only these file types, e.g. `.png .tif` | Common image types |
| `--recursive` | Include subfolders | Off |
| `--workers` | Files processed at once | `1` |

## ocr_pdfs.py

Creates a searchable copy of each PDF, named `<original>_ocr.pdf`.

**Install:** OCRmyPDF also needs Tesseract and Ghostscript. See the [OCRmyPDF Windows install guide](https://ocrmypdf.readthedocs.io/en/latest/installation.html#installing-on-windows).

```powershell
python -m pip install ocrmypdf
```

**Run**

```powershell
python ocr_pdfs.py "\path\to\pdfs"
python ocr_pdfs.py "\path\to\pdfs" --output-dir "\path\to\output" --skip-text
```

| Option | Meaning | Default |
|---|---|---|
| `input_dir` | Folder of PDFs | — |
| `--output-dir` | Where to save the OCR'd copies | Same folder as the input |
| `-l`, `--language` | Language(s), e.g. `eng+fra` | `eng` |
| `-r`, `--recursive` | Include subfolders | Off |
| `--skip-text` | Only OCR pages that have no text yet | Off |
| `--force` | Re-OCR everything, replacing any existing text layer | Off |

## gemini_ocr_pdf.py

Renders each PDF page, asks Gemini for the text and where it sits on the page, and adds that as an invisible text layer. The pages look exactly the same as before. One API call per page, so budget accordingly.

**Install and key**

```powershell
python -m pip install google-genai pymupdf pillow
$env:GEMINI_API_KEY="your_api_key_here"
```

**Run**

```powershell
python gemini_ocr_pdf.py --input "\path\to\pdfs" --output "\path\to\searchable_pdfs"
```

| Option | Meaning | Default |
|---|---|---|
| `--input` | Folder of PDFs | — |
| `--output` | Where to save the searchable PDFs | — |
| `--model` | Gemini model | `gemini-2.5-flash` |
| `--dpi` | Resolution of the page images sent to Gemini | `200` |
| `--skip-existing` | Skip PDFs already in the output folder | Off |
| `--api-key` | Key, if not set in `GEMINI_API_KEY` | — |

## generate_searchable_pdf.py

Same script as in [`../htt`](../htt/README.md#generate_searchable_pdfpy): pairs images with same-named `.txt` files and builds one searchable PDF.

---

## HPC: ocr_pdfs.script (OCRmyPDF)

Runs OCRmyPDF as a SLURM array job, one PDF per task, so many PDFs are processed at once.

1. **Install OCRmyPDF** on the HPC (skip if your `pve` environment already has it):

   ```bash
   export TMPDIR="/home/$USER/data/tmp"
   mkdir -p $TMPDIR
   module load python3.11
   python -m venv pve
   pve/bin/pip install -U ocrmypdf
   ```

2. In your HPC directory, create a `pdfs` folder with an `ocr_completed` subfolder.
3. Upload `ocr_pdfs.script` to your HPC directory and the PDFs to `pdfs/`. Subfolders aren't read, so put all PDFs directly in `pdfs/`.
4. Count the PDFs:

   ```bash
   ls pdfs/*.pdf | wc -l
   ```

5. Submit one task per PDF. Array numbering starts at 0, so for 37 PDFs use `0-36`:

   ```bash
   sbatch --array=0-36 ocr_pdfs.script
   ```

It activates your environment at `/data/<catID>/pve` automatically.

**Output:** OCR'd PDFs in `pdfs/ocr_completed/`, one log per file in `ocr_logs/`. By default pages that already have text are skipped and pages are deskewed; change `OCRMYPDF_OPTS` in the script to alter that.

## HPC: tesseract.script

Runs Tesseract on every `.png .jpg .jpeg .tif .tiff` in an `images` folder, several at a time, writing `.txt`, `.pdf`, `.hocr` and ALTO `.xml` for each.

1. Upload `tesseract.script` to your HPC directory.
2. Create an `images` folder next to it and upload your images.
3. Enter:

   ```bash
   sbatch tesseract.script
   ```

**Output:** the `outputs` folder.
