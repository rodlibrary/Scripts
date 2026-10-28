# ARKs

Generate Archival Resource Keys (ARKs) and matching metadata records from a CSV of source records.

| File | What it does | Runs on |
|---|---|---|
| [`generate_arks.py`](generate_arks.py) | Builds ARK strings, resolvable N2T links and ANVL records from a CSV | Any computer with Python 3 (no extra packages) |
| [`arks.csv`](arks.csv) | Blank input template (header row only) | — |

## What an ARK looks like

```
ark:/NAAN/NAME
```

`NAME` is an optional **shoulder** (a prefix marking a sub-namespace, such as `s3`) followed by a unique **blade**. This script generates each blade as a random, unique alphanumeric string. It does **not** register ARKs with a resolver such as N2T or EZID; it produces well-formed ARKs and metadata that you then load into your resolver.

## Input CSV

Start from the [`arks.csv`](arks.csv) template. Column headers are case-insensitive, and columns can be in any order. To use an optional column the template doesn't include, add it as a new header.

| Column | Required? | In template? | Meaning |
|---|---|---|---|
| `identifier` | **Required** | Yes | Your local identifier. Kept in the output for traceability; it is **not** used to build the ARK. |
| `title` | **Required** | Yes | Title or short description |
| `naan` | Optional | No | Name Assigning Authority Number. Blank = the `--naan` value (or the script's default). |
| `shoulder` | Optional | Yes | ARK shoulder / prefix, e.g. `s3` |
| `url` | Optional | Yes | URL the ARK should resolve to |
| `type` | Optional | No | Resource type, e.g. Text, Image, Dataset |
| `commitment` | Optional | No | Persistence commitment statement |
| `format` | Optional | No | File format, e.g. `application/pdf` |
| `relation` | Optional | No | Related identifier or resource |
| `source` | Optional | No | Source / provenance citation |

The template also has `dc_path`, `dc_article`, `preservica_ref` and `status` columns, for Digital Commons paths, Digital Commons article numbers, Preservica references and redirect status. The current version of `generate_arks.py` doesn't read them: you can leave them filled in, but they won't appear in the output.

## Run

1. Copy `generate_arks.py` and [`arks.csv`](arks.csv) to Documents, then fill in `arks.csv` (at least `identifier` and `title`).
2. Set the NAAN, either by editing `DEFAULT_NAAN` near the top of the script (it ships as `"add NAAN here"`), by filling in the `naan` column, or with `--naan` on the command line.
3. Open Terminal in Documents and enter:

```powershell
python generate_arks.py arks.csv output --add-checksum --master-list "path\to\ark_master_list.csv"
```

| Option | Meaning | Default |
|---|---|---|
| `input_csv` | The input CSV | — |
| `output_basename` | Base name for the outputs (`<name>.csv` and `<name>.anvl`) | — |
| `--naan` | NAAN for rows that don't supply one | `DEFAULT_NAAN` in the script |
| `--add-checksum` | Add a NOID-style check character to each ARK | Off |
| `--blade-length` | Length of the random blade | `10` |
| `--master-list` | Running list of **every** ARK ever generated, used to guarantee uniqueness across runs | `ark_master_list.csv` |
| `--no-master-list` | Skip the master list (uniqueness is then only guaranteed within this run) | Off |
| `--resolver` | Resolver base URL for the links | `https://n2t.net` |

**Always point `--master-list` at the same shared file.** It's what stops a later run from minting an ARK that already exists.

## Output

| File | Contents |
|---|---|
| `<output>.csv` | One row per input record: the ARK, a resolvable `https://n2t.net/...` link, all metadata, and a status/notes column reporting any problems |
| `<output>.anvl` | ANVL metadata records, the format N2T/EZID import |
