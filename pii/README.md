# Personally identifiable information (PII)

Find, and optionally remove, personal information (names, emails, phone numbers, Social Security numbers, addresses…) in files before they're made public.

There are three tools here, from quickest to most thorough:

| Tool | Best for | Finds PII in | Removes PII? | Runs on |
|---|---|---|---|---|
| [`pii_scanner.py`](#pii_scannerpy) | A quick check of a folder of PDFs | PDFs with a text layer | No, report only | Local |
| [`presidio_review/`](presidio_review/) | Small to medium collections where a person reviews each finding before redaction | PDFs (incl. scans), images, Word, Excel, CSV, text | Yes, after human review & anonymize step | Local or HPC (SLURM, see [HPC.md](presidio_review/HPC.md)) |
| [`pii_pipeline/`](pii_pipeline/) | Large collections and Intranet Quorum (IQ) / Senate CSS database exports | PDFs, images, email, CSV, text, IQ exports | Yes, automatically | HPC (SLURM, see [README.txt](pii_pipeline/README.txt)) or local |

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

Full instructions: **[presidio_review/README.md](presidio_review/README.md)**; for running it on an HPC cluster with SLURM, **[presidio_review/HPC.md](presidio_review/HPC.md)**. The `sample/` folder holds made-up test files, and `review.csv`, `review_settings.json` and `review_warnings.txt` are the results of analyzing them, so you can see what the output looks like.

---

## pii_pipeline/

Presidio-based analysis and anonymization designed for the HPC, including field-by-field handling of Intranet Quorum (IQ) / SCDIF exports through an editable policy file (`iq_field_policy.csv`).

Full instructions: **[pii_pipeline/README.txt](pii_pipeline/README.txt)**.

**Input directory: `/data/<catID>/pii`**, for both IQ and Non-IQ data. Every job reads from there (subfolders included) and writes its results into subfolders of it. To process only some files, use `file=` (see [README.txt](pii_pipeline/README.txt)) rather than another folder.

| Folder | Holds |
|---|---|
| `/data/<catID>/pve/` | The Python environment from step 2 |
| `/data/<catID>/pii/` | **Input:** the files to check |
| `/data/<catID>/pii/results/` | Job logs, plus Non-IQ results |
| `/data/<catID>/pii/iq_results/` | IQ results |
| `/data/<catID>/pii/anonymize*/`, `iq_anonymize*/` | Anonymized copies |

The scripts find these folders from your username (`$USER`, your catID), so there's nothing to edit.

### 1. Upload the scripts and create the folders (once)

1. Upload **all** the files in `pii_pipeline/` to one folder in your HPC directory (e.g. with WinSCP), and always run `sbatch` from that folder. The jobs depend on `analyze_pipeline.py`, `anonymize_pipeline.py` and `iq_policy.py` being next to them.
2. Create the input and results folders. The results folder must exist **before** you submit a job, because that's where SLURM writes the job's log:

   ```bash
   mkdir -p /data/$USER/pii/results
   chmod 700 /data/$USER/pii
   ```

### 2. Install Presidio in a virtual environment (once)

On a login node, from the folder you uploaded the scripts to:

```bash
export TMPDIR="/home/$USER/data/tmp"
mkdir -p $TMPDIR
module load python3.11
python -m venv /data/$USER/pve
/data/$USER/pve/bin/pip install -U pip
/data/$USER/pve/bin/pip install -r requirements.txt
/data/$USER/pve/bin/python -m spacy download en_core_web_lg
```

Images are read with Tesseract OCR. Use `module load tesseract` if your cluster has it; otherwise install it without root as in [README.txt](pii_pipeline/README.txt), SETUP step 4. If a job fails with `error while loading shared libraries: libpython3...`, run `module load python3.11` before `sbatch`.

Each job checks the environment before it starts, and stops with the exact command to run if a package is missing.

### 3. Run

Upload the files to check into `/data/<catID>/pii/`. Then follow **IQ data** if that folder holds any IQ / Senate CSS exports, or **Non-IQ data** if it doesn't. Each job prints the command for the next step when it finishes.

#### IQ data

For Intranet Quorum / Senate CSS exports (SCDIF `.dat`, or pipe-delimited / header-row `.txt` exports), plus any PDFs, emails or other files alongside them, which are handled the same way as Non-IQ data. Edit `iq_field_policy.csv` first to set what happens to each export field (`redact`, `scan`, `keep` or `keep_all`).

| Step | Command |
|---|---|
| Convert, only if the export isn't standard tab-delimited SCDIF (keep the original **outside** `/data/<catID>/pii`) | `python convert_to_scdif.py original.txt --output_dir /data/$USER/pii` |
| Analyze | `sbatch iq_analyze.script` |
| Anonymize | `sbatch iq_anonymize.script /data/$USER/pii/iq_results/pii_results_<job>.csv` |

Add a second argument to keep some types unchanged in `scan` fields, e.g. `... pii_results_123.csv names,dates`. After changing a field to `redact` or `keep_all`, re-run only the anonymize step. After changing one from `redact`/`keep_all` to `scan`/`keep`, re-run both (until then it shows as `<UNSCANNED>`).

#### Non-IQ data

For PDFs, images, emails (`.eml`), CSVs and plain-text files, with no IQ exports in `/data/<catID>/pii`.

| Step | Command |
|---|---|
| Analyze | `sbatch analyze.script` |
| Review | Check `results/pii_results_<job>.csv`, and `results/pii_errors_<job>.csv` for files that weren't analyzed |
| Anonymize | `sbatch anonymize.script /data/$USER/pii/results/pii_results_<job>.csv` |

Add a second argument to keep some types unchanged, e.g. `... pii_results_123.csv names,dates`. Scanned PDFs with no text layer aren't analyzed (they're listed in the errors file as "No text extracted"). OCR them first (see [`../ocr`](../ocr/)) or use `presidio_review`.

To get an email when a job finishes, add `--mail-type=END,FAIL --mail-user=you@uni.edu` after `sbatch`.

The data contains PII. Keep it off OneDrive and other synced folders.
