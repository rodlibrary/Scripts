# Personally identifiable information (PII)

Find, and optionally remove, personal information (names, emails, phone numbers, Social Security numbers, addresses…) in files before they're made public.

There are three tools here, from quickest to most thorough:

| Tool | Best for | Finds PII in | Removes PII? | Runs on |
|---|---|---|---|---|
| [`pii_scanner.py`](#pii_scannerpy) | A quick check of a folder of PDFs | PDFs with a text layer | No, report only | Local |
| [`presidio_review/`](presidio_review/) | Small to medium collections where a person reviews each finding before redaction | PDFs (incl. scans), images, Word, Excel, CSV, text | Yes, after human review | Local |
| [`pii_pipeline/`](pii_pipeline/) | Large collections and Intranet Quorum (IQ) / Senate CSS database exports | PDFs, images, email, CSV, text, IQ exports | Yes, automatically | HPC (SLURM) or local |

All three produce a CSV of findings. **Automated detection misses things and flags things that aren't PII, so always have a person review the results before anything is released.**

---

## pii_scanner.py

Searches the text of every PDF in a folder for common patterns and lists each match with surrounding context:

- email addresses
- US phone numbers
- Social Security numbers
- credit card numbers (checked with the Luhn algorithm to cut false positives)
- IPv4 addresses

It doesn't find names, and it can't read scanned PDFs that have no text layer; OCR them first (see [`../ocr`](../ocr/)) or use `presidio_review`.

**Install**

```powershell
python -m pip install pdfminer.six
```

**Run**

1. Copy `pii_scanner.py` to Documents and open Terminal there.
2. Enter:

   ```powershell
   python pii_scanner.py "\path\to\files" -o "\path\to\files\results.csv"
   ```

| Option | Meaning | Default |
|---|---|---|
| `directory` | Folder of PDFs (subfolders included) | — |
| `-o`, `--output` | Output CSV | `pii_scan_results.csv` |
| `--mask` | Hide matches in the CSV except the last 4 characters | Off |
| `--context-chars` | Characters of surrounding text to include | `50` |
| `--no-recursive` | Don't scan subfolders | Subfolders are scanned |

---

## presidio_review/

Uses [Microsoft Presidio](https://microsoft.github.io/presidio/) to find PII, then pauses for a person to mark each finding **REDACT** or **KEEP** in a spreadsheet before anything is changed. Redacted copies keep their original format, and scans get black boxes plus a new searchable text layer.

```
analyze.py  →  review.csv  →  reviewer marks REDACT / KEEP  →  anonymize.py  →  redacted copies + audit log
```

Full instructions: **[presidio_review/README.md](presidio_review/README.md)**. The `sample/` folder holds made-up test files, and `review.csv`, `review_settings.json` and `review_warnings.txt` are the results of analyzing them, so you can see what the output looks like.

---

## pii_pipeline/

Presidio-based analysis and anonymization designed for the HPC, including field-by-field handling of Intranet Quorum (IQ) / SCDIF exports through an editable policy file (`iq_field_policy.csv`).

Full instructions: **[pii_pipeline/README.txt](pii_pipeline/README.txt)**. Before your first run:

### 1. Install Presidio on the HPC (once)

```bash
export TMPDIR="/home/$USER/data/tmp"
mkdir -p $TMPDIR
module load python3.11
python -m venv pve
pve/bin/pip install -U presidio-analyzer presidio-anonymizer presidio-structured spacy pandas pdfminer.six pytesseract pillow
pve/bin/python -m spacy download en_core_web_lg
```

### 2. Set your paths

The SLURM scripts (`anonymize.script`, `iq_analyze.script`, `iq_anonymize.script`) contain the path `/data/hartwigd/`. Replace **every** `hartwigd` with your catID in your copies. The `#SBATCH --output`/`--error` lines at the top count too.

### 3. Set up folders and upload

1. Create `/data/catID/pii/` and `/data/catID/pii/results/`.
2. Upload **all** the files in `pii_pipeline/` to one folder in your HPC directory, and run `sbatch` from that folder. The IQ scripts depend on `analyze_pipeline.py`, `anonymize_pipeline.py` and `iq_policy.py` being next to them.
3. Upload the files to check into `/data/catID/pii/`.

### 4. Run

| Step | Command |
|---|---|
| Analyze (IQ policy; works for all file types) | `sbatch iq_analyze.script` |
| Anonymize IQ results | `sbatch iq_anonymize.script /data/catID/pii/iq_results/pii_results_<job>.csv` |
| Analyze without the IQ policy | `python analyze_pipeline.py --input_dir /data/catID/pii` |
| Anonymize those results | `sbatch anonymize.script /data/catID/pii/results/pii_results_<job>.csv` |

Add a second argument to keep some types unchanged, e.g. `... pii_results_123.csv names,dates`. To get an email when a job finishes, add `--mail-type=END,FAIL --mail-user=you@uni.edu` after `sbatch`.

The data contains PII. Keep it off OneDrive and other synced folders.
