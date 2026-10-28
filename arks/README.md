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
| `naan` | Optional | No | Name Assigning Authority Number. Blank = the `--naan` value, or `32498` if that isn't given. |
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
2. The NAAN defaults to `32498`, so rows with a blank or missing `naan` get it automatically. To use a different NAAN, fill in the `naan` column for those rows, pass `--naan` on the command line, or edit `DEFAULT_NAAN` near the top of the script.
3. Open Terminal in Documents and enter:

```powershell
python generate_arks.py arks.csv --add-checksum --master-list "path\to\ark_master_list.csv"
```

This writes `arks_manifest.csv` and `arks_manifest.anvl` to the current folder. To use a different name, add it after the input file, e.g. `python generate_arks.py arks.csv batch_2026_10`, which writes `batch_2026_10.csv` and `batch_2026_10.anvl`.

| Option | Meaning | Default |
|---|---|---|
| `input_csv` | The input CSV | — |
| `output_basename` | Optional. Base name for the outputs (`<name>.csv` and `<name>.anvl`) | `arks_manifest` |
| `--naan` | NAAN for rows that don't supply one | `32498` |
| `--add-checksum` | Add a NOID-style check character to each ARK | Off |
| `--blade-length` | Length of the random blade | `10` |
| `--master-list` | Running list of **every** ARK ever generated, used to guarantee uniqueness across runs | `ark_master_list.csv` |
| `--no-master-list` | Skip the master list (uniqueness is then only guaranteed within this run) | Off |
| `--resolver` | Resolver base URL for the links | `https://n2t.net` |

**Always point `--master-list` at the same shared file.** It's what stops a later run from minting an ARK that already exists.

## Output

| File | Contents |
|---|---|
| `arks_manifest.csv` | One row per input record: the ARK, a resolvable `https://n2t.net/...` link, all metadata, and a status/notes column reporting any problems |
| `arks_manifest.anvl` | ANVL metadata records, the format N2T/EZID import |

If you give a different output name, `arks_manifest` is replaced by that name in both files. Running the script again with the same output name overwrites the previous `.csv` and `.anvl` files, so rename or move them first if you want to keep them. (The master list is never overwritten; new ARKs are only appended to it.)
