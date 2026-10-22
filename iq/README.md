# Intranet Quorum (IQ)

Tools for constituent-correspondence exports from Intranet Quorum and other Senate CSS systems: the SCDIF exit-conversion format and the older 32-field Senate Archive Format.

| File | What it does | Runs on |
|---|---|---|
| [`convert_to_scdif.py`](convert_to_scdif.py) | Converts non-standard IQ exports (pipe-delimited, header row, line breaks inside fields) into standard SCDIF | Local or HPC (Python 3, no packages) |
| [`iq_to_bigquery.py`](iq_to_bigquery.py) | Converts SCDIF / Archive Format exports into BigQuery tables and Looker Studio-ready views | Local (Google Cloud) |
| [`generate_scdif.py`](generate_scdif.py) | Generates synthetic SCDIF files (no real data) for testing imports and the pipelines above | Local or HPC (Python 3.8+, no packages) |
| [`validate_scdif.py`](validate_scdif.py) | Checks `generate_scdif.py` output: row and field counts, unique IDs, links between record types | Local or HPC (Python 3, no packages) |
| [`scdif_sample_150k.zip`](scdif_sample_150k.zip) | Ready-made synthetic sample (~150,000 rows, all 35 record types) | — |
| [`pii/`](pii/) | Older copies of the IQ PII files. Use `../pii/pii_pipeline/` instead (see below) | — |

**Exports contain personal information.** Keep originals and working files off OneDrive and other synced folders, and redact before loading anything anywhere shared (see [`../pii/pii_pipeline`](../pii/pii_pipeline/)).

---

## convert_to_scdif.py

Some IQ exports don't follow the SCDIF specification. This script fixes the common differences:

- `|` between fields instead of tabs
- a header row naming the columns. Columns are matched to standard SCDIF fields **by name**, so missing or reordered columns still land in the right place
- line breaks inside a field

Columns with no SCDIF equivalent are kept at the end of the record, and the report tells you their new field numbers so you can add them to the IQ field policy. Records it can't map with certainty are **not** guessed at: they go to a separate `*_rejected.txt` file, which still contains PII and must be reviewed by hand.

**Run**

```bash
# 1. Look at a file's structure. Every letter and digit is masked, so this output is safe to share.
python convert_to_scdif.py --inspect /path/to/export.txt

# 2. Convert (one or more files)
python convert_to_scdif.py /path/to/export.txt --output_dir /data/catID/pii
```

| Option | Meaning | Default |
|---|---|---|
| `files` | Export file(s) | — |
| `--inspect` | Describe the structure only (masked) | Off |
| `--output_dir` | Folder for the converted `*_scdif.dat` files | — |
| `--report_dir` | Folder for reports and rejected records | `<output_dir>_conversion_reports` |
| `--encoding` | Text encoding; try `cp1252` if accented letters look wrong | `utf-8` |
| `--overwrite` | Replace existing output files | Off |

Keep the original export **outside** the folder the PII pipeline scans, so it isn't analyzed twice.

---

## iq_to_bigquery.py

Turns exports into BigQuery tables you can explore in Looker Studio.

```
1. convert   raw export  →  one NDJSON file + BigQuery schema per record type   (staged/)
2. redact    run Presidio over staged/*.ndjson, guided by presidio_columns.json  (redacted/)
3. load      redacted NDJSON  →  BigQuery tables
4. views     create joined views (communications, topics, workflows…) for Looker Studio
```

**Install** (only needed for `load` and `views`)

```bash
pip install google-cloud-bigquery
gcloud auth application-default login
```

**Run**

```bash
# 1. Convert
python iq_to_bigquery.py convert scdif   export/*.txt --out staged/
python iq_to_bigquery.py convert archive export/*.tab --out staged/

# 2. Redact staged/*.ndjson into redacted/ (keep one JSON object per line and every key)

# 3. Load
python iq_to_bigquery.py load --data-dir redacted/ --schema-dir staged/ --project my-project --dataset constituent_mail --location US

# 4. Views (add --print-sql to see the SQL without running it)
python iq_to_bigquery.py views --project my-project --dataset constituent_mail
```

`load` replaces existing tables by default; add `--append` to add to them, or `--tables` to load only some.

**Custom layouts:** if your vendor added or changed SCDIF fields, export the built-in layouts, edit them, and pass them back in:

```bash
python iq_to_bigquery.py layout-template > layout.json
python iq_to_bigquery.py convert scdif export/*.txt --out staged/ --layout layout.json
```

Values beyond the end of a layout are kept in an `extra_fields` column, so nothing is silently dropped.

---

## generate_scdif.py

Makes synthetic SCDIF exports for testing imports (e.g. CCDT), `iq_to_bigquery.py` and the PII pipeline without touching real constituent data. **Everything is made up:** names come from generic lists, emails use the reserved `example.com/.org/.net` domains, and phone numbers use the fictional 555-01xx range.

Output looks like a real IQ export: one tab-delimited file per record type (`out_1a.dat` … `out_8a.dat`, 35 types), latin-1, CRLF line endings, no header row, plus a `manifest.json` with the expected row counts. IDs link up across files the way they do in real exports (every child row points at an existing person, communication, workflow, household, document or code), and the same `--seed` and `--total-records` always give the same files.

A small share of rows (`--quirk-rate`, 0.5% by default) get the defects seen in real exports, so importers get tested against them: dropped trailing fields, blank lines, `*****` separator lines, quoted fields, LF-only line endings and very long free-text values. The first line of every file is always clean, so delimiter detection isn't thrown off.

**Run**

```bash
# Estimate rows per record type without writing anything
python3 generate_scdif.py --out-dir ./scdif_data --plan

# Generate (default is 15 million rows in total)
python3 generate_scdif.py --out-dir ./scdif_data --total-records 2000000 --seed 7

# Split large record types into files of at most 500,000 rows (out_2a_001.dat, out_2a_002.dat, …)
python3 generate_scdif.py --out-dir ./scdif_data --max-rows-per-file 500000
```

| Option | Meaning | Default |
|---|---|---|
| `--out-dir` | Folder for the `.dat` files and `manifest.json` | — |
| `--total-records` | Total rows across **all** record types (lands within about 2%; exact figure in `manifest.json`) | `15000000` |
| `--seed` | Random seed; same seed, same files | `1` |
| `--workers` | Processes to use; doesn't change the output | All CPUs |
| `--quirk-rate` | Share of rows given a real-world defect (`0` turns them off) | `0.005` |
| `--max-rows-per-file` | Split each record type into files of at most this many rows | No split |
| `--plan` | Only print the estimated plan | Off |

Because the files have no header row, import them into a collection flagged as CMS so line 1 isn't skipped.

**Sample data:** [`scdif_sample_150k.zip`](scdif_sample_150k.zip) is the output of `--total-records 150000` with the default seed and quirk rate (151,715 rows, 8,244 people). Unzip it to try the tools without generating anything.

---

## validate_scdif.py

Checks a folder written by `generate_scdif.py` (or the unzipped sample) and exits with status 1 if anything is wrong. It streams the files, so it stays at a few hundred MB of memory even at 15 million rows.

It checks:

- row counts per file against `manifest.json`
- the number of fields in every row (and against a field policy CSV, if given)
- that the first line of every file is clean and tab-delimited
- that Person, Address, Communication, Workflow, Household, Document and Event IDs are unique
- that every child row points at a real parent (and at the same person, where the parent belongs to one)
- that codes and staff IDs exist in the 8A code tables

**Run**

```bash
python3 validate_scdif.py --data-dir ./scdif_data
python3 validate_scdif.py --data-dir ./scdif_data --policy ../pii/pii_pipeline/iq_field_policy.csv
```

`manifest.json` must be in the folder, so this only works on generated data, not on real exports.

**Note:** the field policy doesn't have rows for record type **1G** yet, so `--policy` reports one failure (`1G field count 24 vs policy 0`) even on good data. Without `--policy`, the sample passes all 115 checks.

---

## pii/

`iq_field_policy.csv`, `iq_policy.py`, `iq_analyze_pipeline.py`, `iq_anonymize_pipeline.py` and their `.script` files are the IQ branch of the PII pipeline. They are **older copies** of the files in [`../pii/pii_pipeline/`](../pii/pii_pipeline/): the `.script` files here still point at another user's folders. They also won't run from here on their own, because they need `analyze_pipeline.py` and `anonymize_pipeline.py` in the same folder. Use the complete set in `pii/pii_pipeline/`, following [its README](../pii/pii_pipeline/README.txt).
