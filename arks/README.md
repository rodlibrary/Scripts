# ARKs

Mint Archival Resource Keys (ARKs), publish them to the GitLab resolver repository ([rod-library/resolver](https://git.uni.edu/rod-library/resolver)), where Apache resolves them, and fill in which Preservica asset holds each ARK automatically.

| File | What it does | Runs on |
|---|---|---|
| [`generate_arks.py`](generate_arks.py) | Mints ARKs from a CSV, then adds them to `resolve.csv` and the master list, pulling and pushing the GitLab copies | Any computer with Python 3 (no extra packages) |
| [`match_preservica.py`](match_preservica.py) | Reads the ARK fields of every Preservica asset's MODS record and fills in each ARK's `preservica_ref` (and a `target_url` where none is set) in `resolve.csv` | Weekly on GitHub Actions, or any computer with Python 3 |
| [`arks.csv`](arks.csv) | Blank input template (header row only) | – |
| [`tests/`](tests/) | Offline tests with a fake GitLab and a fake Preservica | `python3 -m unittest discover -s arks/tests` |

## How it fits together

1. **Mint:** fill in `arks.csv` and run `generate_arks.py ... --push-to-gitlab`. It pulls the current `resolve.csv` and `ark_master_list.csv` from GitLab, mints the new ARKs, adds them to both files and pushes them back. Apache then resolves the ARKs that have a `target_url`.
2. **Catalogue:** put each ARK in the asset's MODS in Preservica, in the template's two ARK fields:
   - **Archival Resource Key:** `<identifier displayLabel="Archival Resource Key" type="ark">`, holding the blade (such as `s3x7k9q2mbd`);
   - **Location (URL):** `<physicalLocation displayLabel="Location (URL)" type="url"><url>`, holding the full address (such as `https://n2t.net/ark:/32498/s3x7k9q2mbd`).
3. **Match:** every Monday, the [GitHub workflow](../.github/workflows/arks-match-preservica.yml) runs `match_preservica.py`. It finds those ARKs in Preservica, records which asset holds each one in `resolve.csv`, points ARKs that have no target yet at the asset's page in Preservica's portal, and pushes `resolve.csv` back to GitLab. You can run it yourself at any time, or add `--match-preservica` to step 1 to do everything in one command.

The GitLab repository holds the copies of record. The scripts always start from GitLab's copies and merge your local rows in, so they never overwrite newer rows.

## What an ARK looks like

```
ark:/NAAN/NAME
```

`NAME` is an optional **shoulder** (a prefix marking a sub-namespace, such as `s3`) followed by a unique **blade**. `generate_arks.py` generates each blade as a random, unique alphanumeric string. Rod Library's NAAN is `32498`. The scripts don't register ARKs with N2T or EZID: they produce the ARKs and the `resolve.csv` file Apache uses.

## Mint ARKs: generate_arks.py

### Input CSV

Start from the [`arks.csv`](arks.csv) template. Column headers are case-insensitive, and columns can be in any order. To use an optional column the template doesn't include, add it as a new header.

| Column | Required? | In template? | Meaning |
|---|---|---|---|
| `identifier` | **Required** | Yes | Your local identifier. Kept in the output for traceability; it is **not** used to build the ARK. |
| `title` | **Required** | Yes | Title or short description |
| `shoulder` | Optional | Yes | ARK shoulder / prefix, e.g. `s3` |
| `url` | Optional | Yes | URL the ARK should resolve to (`target_url` in `resolve.csv`). Leave it blank for Preservica items: `match_preservica.py` fills it in once the ARK is in the asset's MODS. |
| `dc_path` | Optional | Yes | Digital Commons path: a landing-page key (`etd/123`) or a collection key (`etd`) |
| `dc_article` | Optional | Yes | Digital Commons file key (`etd/1124`), the article number from `viewcontent.cgi` |
| `preservica_ref` | Optional | Yes | Preservica reference, if already known (`IO_<reference>`). Usually left blank for `match_preservica.py` to fill in. |
| `status` | Optional | Yes | `active` (resolve to `url`), `withdrawn` (served as HTTP 410) or `unmatched` (not resolved yet). If blank: `active` when `url` is filled, otherwise `unmatched`. |
| `naan` | Optional | No | NAAN for this row (default `32498`) |
| `type`, `format`, `relation`, `source`, `commitment` | Optional | No | Extra metadata for the ANVL records |

### Run

1. Copy `generate_arks.py`, `match_preservica.py` and [`arks.csv`](arks.csv) to the same folder (such as Documents), then fill in `arks.csv` (at least `identifier` and `title`).
2. Open Terminal in that folder and enter:

```bash
export GITLAB_TOKEN=glpat-your_token_here
python3 generate_arks.py output --push-to-gitlab
```

- `arks.csv` is the input. It's the default, so you don't need to name it. To use a different file, put its path first: `python3 generate_arks.py other.csv output --push-to-gitlab`.
- `output` is the base name for this run's files: the script writes `output.csv` and `output.anvl`.
- `--push-to-gitlab` first pulls `resolve.csv` and `ark_master_list.csv` from [rod-library/resolver](https://git.uni.edu/rod-library/resolver) and merges them into your copies. It then mints the ARKs and pushes both files back. You no longer need to download them before each run. If GitLab can't be reached, or `GITLAB_TOKEN` isn't set, nothing is minted.
- `--match-preservica` also runs the Preservica matching before the push, so one command does all three steps. It needs the Preservica login: see [Match Preservica references](#match-preservica-references-match_preservicapy).
- Leave `--push-to-gitlab` off to run locally without GitLab.

| Option | Meaning | Default |
|---|---|---|
| `input_csv` | The input CSV | `arks.csv` |
| `output_basename` | Base name for this run's outputs (`<name>.csv` and `<name>.anvl`) | – |
| `--push-to-gitlab` | Pull and merge the GitLab copies first, then push them after the run | Off |
| `--match-preservica` | Fill in Preservica references before pushing (runs `match_preservica.py`) | Off |
| `--no-pull` | With `--push-to-gitlab`, don't pull first (the push then overwrites GitLab's copies) | Off |
| `--naan` | NAAN for rows that don't supply one | `32498` |
| `--add-checksum` | Add a NOID-style check character to each ARK | Off |
| `--blade-length` | Length of the random blade | `10` |
| `--master-list` | Running list of **every** ARK ever minted, which keeps blades unique across runs | `ark_master_list.csv` |
| `--no-master-list` | Skip the master list (uniqueness is then only guaranteed within this run) | Off |
| `--resolve-manifest` | The resolve manifest | `resolve.csv` |
| `--no-resolve-manifest` | Don't write or push the resolve manifest | Off |
| `--resolver` | Resolver base URL for the links in `output.csv` | `https://n2t.net` |
| `--gitlab-project` | GitLab project to pull from and push to | `rod-library/resolver` |
| `--gitlab-branch` | Its branch | `main` |
| `--gitlab-url` | The GitLab server | `https://git.uni.edu` |

### Output

| File | Contents |
|---|---|
| `output.csv` | One row per input record: the ARK, a resolvable `https://n2t.net/...` link, all metadata, and status and notes columns reporting any problems |
| `output.anvl` | ANVL metadata records, the format N2T and EZID import |
| `resolve.csv` | The resolve manifest Apache uses (below). New ARKs are added; existing rows are never changed. |
| `ark_master_list.csv` | Every ARK ever minted, with a timestamp and source file. Only ever added to. |

Running the script again with the same output name overwrites `output.csv` and `output.anvl`, so rename or move them first if you want to keep them.

### resolve.csv

`resolve.csv` replaces the earlier `redirects.csv` and has the same columns. If you still have a `redirects.csv` and no `resolve.csv`, the first run copies its rows into `resolve.csv`. The old `--redirects-manifest` option still works as another name for `--resolve-manifest`.

| Column | Meaning |
|---|---|
| `dc_path`, `dc_article` | Digital Commons path and file key, if any |
| `ark` | The ARK without `ark:/`, e.g. `32498/s3x7k9q2mbd` |
| `preservica_ref` | The Preservica asset holding the ARK (`IO_<reference>`), filled in by `match_preservica.py` |
| `target_url` | Where Apache sends the ARK |
| `status` | `active`, `withdrawn` (HTTP 410) or `unmatched` (not resolved yet) |
| `notes` | Problems reported when minting, and when each ARK was matched in Preservica |

## Match Preservica references: match_preservica.py

Lists every asset in Preservica and reads its MODS record (about two requests per asset), taking the **Archival Resource Key** identifier and the **Location (URL)**. Preservica's search index keeps neither the identifier's type nor the URL, so the MODS has to be read. For each ARK found:

- if `resolve.csv` has no `preservica_ref` for it, the asset's reference is filled in (`IO_<reference>`);
- if it has no `target_url`, the asset's page in Preservica's portal is filled in (`https://uni.access.preservica.com/uncategorized/IO_<reference>/`). The status changes from `unmatched` to `active`, so Apache starts resolving it;
- an existing different `preservica_ref` or `target_url` is left alone and reported, unless you use `--overwrite`;
- `withdrawn` ARKs stay withdrawn;
- a note records the date of the match.

These are never filled in, only reported:

- an ARK found in **more than one asset**, since a resolver needs exactly one target;
- an asset whose blade and URL name **different ARKs**;
- an **ARK in Preservica that isn't in `resolve.csv`**.

The blade may be entered with or without the shoulder: the master list connects a bare blade to its ARK. Hyphens are ignored, as they carry no meaning in ARKs.

Every run writes `match_report.csv`: one row per asset with ARK fields, with the outcome. The weekly GitHub run pushes it to the GitLab resolver repository whenever it changes, instead of attaching it to the public run.

### Run it on your computer

Put the Preservica login in a `credentials.properties` file in the same folder (the same file the [preservica](../preservica/) scripts use), or set `PRESERVICA_USERNAME` and `PRESERVICA_PASSWORD`. Then:

```bash
export GITLAB_TOKEN=glpat-your_token_here
python3 match_preservica.py --from-gitlab --dry-run                   # report only
python3 match_preservica.py --from-gitlab --push-to-gitlab            # update GitLab
```

Without `--from-gitlab` and `--push-to-gitlab` it works on the `resolve.csv` in the folder. Other options: `--overwrite`, `--portal-url` (the target address for matched ARKs, with `{ref}` for the reference; `''` leaves `target_url` alone), `--naan`, `--push-report`, and `--workers` (assets read at once, default 4).

### Weekly on GitHub Actions

The workflow [`.github/workflows/arks-match-preservica.yml`](../.github/workflows/arks-match-preservica.yml) runs every Monday at 12:23 UTC. Run it at any time from **Actions > ARKs - match Preservica references > Run workflow**, optionally as a dry run.

The log shows only counts, since this repository is public. The per-asset report goes to GitLab.

## Setup (one time)

1. **GitLab token.** In [rod-library/resolver](https://git.uni.edu/rod-library/resolver), go to **Settings > Access tokens** and create a project access token with the **Developer** role and the **api** scope. Copy it.
2. **On your computer:** `export GITLAB_TOKEN=...` before running the scripts, as above. Never put the token in a file in this repository.
3. **In GitHub:** in this repository, go to **Settings > Secrets and variables > Actions > Secrets** and add a repository secret `GITLAB_TOKEN` with the token. The workflow also uses the `PRESERVICA_USERNAME`, `PRESERVICA_PASSWORD`, `PRESERVICA_TENANT` and `PRESERVICA_SERVER` secrets that the [scheduled Preservica export](../preservica/README.md#scheduled-export-on-github-actions) already uses. The Preservica account needs Read Metadata on the assets' security tags.
4. **Moving from rod-library/redirects:** copy that repository's `redirects.csv` into the resolver repository as `resolve.csv`, along with `ark_master_list.csv`. Then point Apache at `resolve.csv`.
5. Optional repository **variables** (same page, **Variables** tab):
   - `GITLAB_PROJECT` and `GITLAB_BRANCH`, if the resolver project or branch differ from `rod-library/resolver` and `main`;
   - `PRESERVICA_PORTAL_URL`, to send matched ARKs somewhere other than the portal page.

**If the workflow says GitHub can't reach git.uni.edu,** the GitLab server only accepts connections from campus. Run `match_preservica.py --from-gitlab --push-to-gitlab` on a campus computer instead, or use `generate_arks.py ... --push-to-gitlab --match-preservica`, which does it as part of minting.

## If something goes wrong

- **`Couldn't pull ... from GitLab, so nothing was minted`:** the token is missing or wrong, or GitLab can't be reached. Check `GITLAB_TOKEN` and `--gitlab-project`.
- **`ARK not in resolve.csv`** in the report: the ARK in the MODS has a typo, or was minted somewhere else. Compare it with `ark_master_list.csv`.
- **`resolve.csv already has IO_...`:** the ARK was matched to a different asset before. Check both assets; run with `--overwrite` if the new one is right.
- **`ARK found in 2 assets`:** the same ARK was entered in two assets' MODS. Remove it from the wrong one.
- **Nothing is matched:** the account can't read the assets' MODS (it needs Read Metadata), or the ARK fields are still empty. The [*assets with an ARK* Postman collection](../preservica/postman/README.md#assets-with-an-ark) lists every asset's ARK fields.
