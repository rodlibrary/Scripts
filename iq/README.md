# Intranet Quorum (IQ)

Tools for constituent-correspondence exports from Intranet Quorum and other Senate CSS systems: the SCDIF exit-conversion format and the older 32-field Senate Archive Format.

| File | What it does | Runs on |
|---|---|---|
| [`convert_to_scdif.py`](convert_to_scdif.py) | Converts non-standard IQ exports (pipe-delimited, header row, line breaks inside fields) into standard SCDIF | Local or HPC (Python 3, no packages) |
| [`iq_to_bigquery.py`](iq_to_bigquery.py) | Converts SCDIF / Archive Format exports into BigQuery tables and Looker Studio-ready views | Local (Google Cloud) |
| [`pii/`](pii/) | IQ policy files for the PII pipeline (copies; see below) | HPC |

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

## pii/

`iq_field_policy.csv`, `iq_policy.py`, `iq_analyze_pipeline.py`, `iq_anonymize_pipeline.py` and their `.script` files are the IQ branch of the PII pipeline. They are **identical copies** of the files in [`../pii/pii_pipeline/`](../pii/pii_pipeline/), and they won't run from here on their own because they need `analyze_pipeline.py` and `anonymize_pipeline.py` in the same folder. Use the complete set in `pii/pii_pipeline/`, following [its README](../pii/pii_pipeline/README.txt).
