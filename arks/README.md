# ARKs

Mint Archival Resource Keys (ARKs), publish them to the resolver repository on GitHub ([rodlibrary/Resolver](https://github.com/rodlibrary/Resolver)), where Apache resolves them, and add each ARK to the resolver automatically once it's catalogued in Preservica.

The resolver works like the ScholarWorks redirects in [rodlibrary/Redirects](https://github.com/rodlibrary/Redirects): one CSV with two columns of full addresses, checked by the same rules. Redirects' `redirects.csv` maps old ScholarWorks addresses (`dc_path`) to Preservica (`p_path`). The resolver's `resolve.csv` maps ARKs (`ark`) to Preservica (`p_path`).

```
arks.csv ──generate_arks.py──▶ ark_master_list.csv   every ARK ever minted
                          └──▶ resolve.csv           ARKs with a url: ark → p_path
Preservica MODS (Archival Resource Key, Location (URL))
        └──match_preservica.py (weekly)──▶ resolve.csv   ARKs found: ark → the asset's portal page
                                                  │
                               rodlibrary/Resolver ▼ main ──▶ Apache on library.uni.edu
```

| File | What it does | Runs on |
|---|---|---|
| [`generate_arks.py`](generate_arks.py) | Mints ARKs from a CSV, records them in the master list, adds those with a url to `resolve.csv`, and pulls and pushes the GitHub copies | Any computer with Python 3 (no extra packages) |
| [`match_preservica.py`](match_preservica.py) | Reads the ARK fields of every Preservica asset's MODS record and adds each minted ARK it finds to `resolve.csv`, pointing at the asset's page in Preservica's portal | Weekly on GitHub Actions, or any computer with Python 3 |
| [`check_resolve.py`](check_resolve.py) | Checks `resolve.csv` row by row (the same checks the other scripts run before they push) | Any computer with Python 3 |
| [`check_github.py`](check_github.py) | Tests the GitHub connection step by step, including a test push to a temporary branch, without touching `main` | Any computer with Python 3 |
| [`arks.csv`](arks.csv) | Blank input template (header row only) | – |
| [`tests/`](tests/) | Offline tests with a fake GitHub and a fake Preservica | `python3 -m unittest discover -s arks/tests` |

## How it fits together

1. **Mint:** fill in `arks.csv` and run `generate_arks.py ... --push-to-github`. It pulls the current `resolve.csv` and `ark_master_list.csv` from GitHub, mints the new ARKs, records every one in the master list, adds those with a `url` to `resolve.csv`, and pushes both files back.
2. **Catalogue:** put each ARK in the asset's MODS in Preservica, in the template's two ARK fields:
   - **Archival Resource Key:** `<identifier displayLabel="Archival Resource Key" type="ark">`, holding the blade (such as `s3x7k9q2mbd`);
   - **Location (URL):** `<physicalLocation displayLabel="Location (URL)" type="url"><url>`, holding the full address (such as `https://n2t.net/ark:/32498/s3x7k9q2mbd`).
3. **Match:** every Monday, a [scheduled GitHub Actions workflow in this repository](#weekly-on-github-actions) runs `match_preservica.py`. It finds the minted ARKs in Preservica, adds each one to `resolve.csv` pointing at its asset's page in Preservica's portal, and pushes `resolve.csv` back. You can run it yourself at any time, or add `--match-preservica` to step 1 to do everything in one command.

The resolver repository holds the copies of record. The scripts always start from its copies and merge your local rows in, so they never overwrite newer rows.

**[rodlibrary/Resolver](https://github.com/rodlibrary/Resolver) is public**, so everything pushed there (`resolve.csv`, `ark_master_list.csv` with each ARK's identifier and title, and `match_report.csv`) can be read by anyone. Don't put anything in those columns that shouldn't be public.

## What an ARK looks like

```
ark:/NAAN/NAME
```

`NAME` is an optional **shoulder** (a prefix marking a sub-namespace, such as `s3`) followed by a unique **blade**. `generate_arks.py` generates each blade as a random, unique alphanumeric string. Rod Library's NAAN is `32498`, registered with the target `https://library.uni.edu/ark:/${content}`, so N2T sends `ark:/32498/s3x7k9q2mbd` to `https://library.uni.edu/ark:/32498/s3x7k9q2mbd`, which is the address in `resolve.csv`. The scripts don't register ARKs with N2T or EZID: they produce the ARKs and the `resolve.csv` file Apache uses.

## resolve.csv

One row per ARK that has somewhere to go, two columns:

| Column | What goes in it | Example |
|---|---|---|
| `ark` | the ARK as a full address on library.uni.edu | `https://library.uni.edu/ark:/32498/s3x7k9q2mbd` |
| `p_path` | where it resolves to, in full, or `410` if the item was withdrawn | `https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C9dbdd64c-5ab8-4344-ae48-99fc70625610` |

```csv
ark,p_path
https://library.uni.edu/ark:/32498/xo2gohohwd,https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C9dbdd64c-5ab8-4344-ae48-99fc70625610
https://library.uni.edu/ark:/32498/s3gone0001,410
```

The rules are the same as for `redirects.csv` in [rodlibrary/Redirects](https://github.com/rodlibrary/Redirects#redirectscsv). `check_resolve.py` names the line and the problem, and `generate_arks.py` and `match_preservica.py` won't push a `resolve.csv` that breaks them:

* `ark` must be a full `https://library.uni.edu/ark:/32498/...` address, listed once. Hyphens and upper/lower case don't make a different ARK.
* `p_path` must be a full `https://` address on `uni.access.preservica.com` or `digital.library.uni.edu`, or `410`. Copy it from the portal as it is: `sdb%3AIO%7C` must stay encoded (a raw `|` is rejected).
* Each row on its own line, and the file ends with a line break. Editing in Excel: save as **CSV UTF-8**.

What's **not** in `resolve.csv`:

* **ARKs with nowhere to go yet.** Every minted ARK is in `ark_master_list.csv`. One minted without a `url` is added to `resolve.csv` only when `match_preservica.py` finds it in an asset's MODS. `check_resolve.py --master-list ark_master_list.csv` lists the ARKs still waiting.
* **Old ScholarWorks addresses.** Those go in `redirects.csv` in [rodlibrary/Redirects](https://github.com/rodlibrary/Redirects).

To fix a row by hand, edit `resolve.csv` in rodlibrary/Resolver: paste the right `p_path`, or set it to `410` for a withdrawn item. Then run `python3 check_resolve.py --from-github` to check it. The scripts never change a row that's already there, unless you run `match_preservica.py --overwrite`, and even then they never change a `410`.

Until November 2026 `resolve.csv` had seven columns (`dc_path`, `dc_article`, `ark`, `preservica_ref`, `target_url`, `status`, `notes`). If the scripts meet a file in that layout, such as an old local copy, they convert it: `active` rows keep their `target_url` as `p_path`, `withdrawn` rows become `410`, and `unmatched` rows are left out, since their ARKs are in the master list. The `resolve_paths.csv` export is gone too, since `resolve.csv` now has the same two columns.

## Mint ARKs: generate_arks.py

### Input CSV

Start from the [`arks.csv`](arks.csv) template. Column headers are case-insensitive, and columns can be in any order. To use an optional column the template doesn't include, add it as a new header.

| Column | Required? | In template? | Meaning |
|---|---|---|---|
| `identifier` | **Required** | Yes | Your local identifier. Kept in the output for traceability; it is **not** used to build the ARK. |
| `title` | **Required** | Yes | Title or short description |
| `shoulder` | Optional | Yes | ARK shoulder / prefix, e.g. `s3` |
| `url` | Optional | Yes | Where the ARK resolves to (its `p_path`): a full `https://` address on an allowed host, or `410` for a withdrawn item. A row with a bad `url` is rejected before an ARK is minted for it. Leave it blank for Preservica items: `match_preservica.py` adds them once the ARK is in the asset's MODS. |
| `preservica_ref` | Optional | Yes | Preservica reference (`IO_<uuid>`), if already known. With no `url`, the ARK resolves to that asset's portal page. |
| `naan` | Optional | No | NAAN for this row (default `32498`) |
| `type`, `format`, `relation`, `source`, `commitment` | Optional | No | Extra metadata for the ANVL records |

Older input files may still have `dc_path`, `dc_article` and `status` columns. They're ignored, with a note in `output.csv`. Old ScholarWorks addresses go in [rodlibrary/Redirects](https://github.com/rodlibrary/Redirects); `410` in `url` marks a withdrawn item (an old `status` of `withdrawn` still does too).

### Run

1. Copy `generate_arks.py`, `match_preservica.py`, `check_resolve.py` and [`arks.csv`](arks.csv) to the same folder (such as Documents), then fill in `arks.csv` (at least `identifier` and `title`).
2. Open Terminal in that folder and enter:

```bash
export GITHUB_TOKEN=github_pat_your_token_here
python3 generate_arks.py output --push-to-github
```

In PowerShell, set the token with `$env:GITHUB_TOKEN = "github_pat_your_token_here"` instead.

- `arks.csv` is the input. It's the default, so you don't need to name it. To use a different file, put its path first: `python3 generate_arks.py other.csv output --push-to-github`.
- `output` is the base name for this run's files: the script writes `output.csv` and `output.anvl`.
- `--push-to-github` first pulls `resolve.csv` and `ark_master_list.csv` from [rodlibrary/Resolver](https://github.com/rodlibrary/Resolver) and merges them into your copies. It then mints the ARKs and pushes both files back. You don't need to download them before each run. Nothing is minted if GitHub can't be reached, if `GITHUB_TOKEN` isn't set, or if GitHub's `resolve.csv` breaks the [rules](#resolvecsv).
- `--match-preservica` also runs the Preservica matching before the push, so one command does all three steps. It needs the Preservica login: see [Add ARKs found in Preservica](#add-arks-found-in-preservica-match_preservicapy).
- Leave `--push-to-github` off to run locally without GitHub.

| Option | Meaning | Default |
|---|---|---|
| `input_csv` | The input CSV | `arks.csv` |
| `output_basename` | Base name for this run's outputs (`<name>.csv` and `<name>.anvl`) | – |
| `--push-to-github` | Pull and merge the GitHub copies first, then push them after the run | Off |
| `--match-preservica` | Add the ARKs found in Preservica before pushing (runs `match_preservica.py`) | Off |
| `--no-pull` | With `--push-to-github`, don't pull first (the push then overwrites GitHub's copies) | Off |
| `--naan` | NAAN for rows that don't supply one | `32498` |
| `--add-checksum` | Add a NOID-style check character to each ARK | Off |
| `--blade-length` | Length of the random blade | `10` |
| `--master-list` | Running list of **every** ARK ever minted, which keeps blades unique across runs | `ark_master_list.csv` |
| `--no-master-list` | Skip the master list (uniqueness is then only guaranteed within this run) | Off |
| `--resolve-manifest` | The resolve list | `resolve.csv` |
| `--no-resolve-manifest` | Don't write or push `resolve.csv` | Off |
| `--ark-base` | Address ARKs resolve at, for the `ark` column (or set `ARK_BASE_URL`) | `https://library.uni.edu` |
| `--allowed-host` | Host a `url` may point to; repeat for several (or set `ALLOWED_TARGET_HOSTS`, comma-separated) | `uni.access.preservica.com`, `digital.library.uni.edu` |
| `--resolver` | Resolver base URL for the links in `output.csv` | `https://n2t.net` |
| `--github-repo` | Repository to pull from and push to (or set `RESOLVER_REPO`) | `rodlibrary/Resolver` |
| `--github-branch` | Its branch (or set `RESOLVER_BRANCH`) | `main` |
| `--github-path`, `--github-manifest-path` | Where the master list and `resolve.csv` live in the repository | Same file names, at the top level |
| `--github-token-env` | Environment variable holding the token | `GITHUB_TOKEN` |

`--slim-manifest` and `--no-slim-manifest` are no longer needed. They're ignored, with a note, so older commands still work. `--redirects-manifest` is an older name for `--resolve-manifest`.

### Output

| File | Contents |
|---|---|
| `output.csv` | One row per input record: the ARK, a resolvable `https://n2t.net/...` link, its `p_path`, all metadata, and status and notes columns reporting any problems |
| `output.anvl` | ANVL metadata records, the format N2T and EZID import |
| `resolve.csv` | The resolve list (above). ARKs minted with a `url` or `preservica_ref` are added; existing rows are never changed. |
| `ark_master_list.csv` | Every ARK ever minted, with a timestamp and source file. Only ever added to. |

Running the script again with the same output name overwrites `output.csv` and `output.anvl`, so rename or move them first if you want to keep them.

## Add ARKs found in Preservica: match_preservica.py

Lists every asset in Preservica and reads its MODS record (about two requests per asset), taking the **Archival Resource Key** identifier and the **Location (URL)**. Preservica's search index keeps neither the identifier's type nor the URL, so the MODS has to be read. For each ARK found that's in the master list:

- **not in `resolve.csv` yet:** a row is added, with `p_path` the asset's page in Preservica's portal (`https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C<reference>`, where `sdb%3AIO%7C` is `sdb:IO|` written for a web address);
- **already there with that page:** nothing changes;
- **already there with another address:** left alone and reported, unless you use `--overwrite`;
- **`410` (withdrawn):** never changed.

These are never added, only reported:

- an ARK found in **more than one asset**, since a resolver needs exactly one target;
- an asset whose blade and URL name **different ARKs**;
- an **ARK in Preservica that isn't in the master list** (a typo, or minted somewhere else).

The blade may be entered with or without the shoulder: the master list connects a bare blade to its ARK. Hyphens are ignored, as they carry no meaning in ARKs.

Every run writes `match_report.csv`: one row per asset with ARK fields, with the outcome and the line in `resolve.csv`. The weekly run pushes it to the resolver repository whenever it changes. `resolve.csv` is pushed only when it changed, and only if it passes the [checks](#resolvecsv); if it doesn't, the run fails and lists the problems.

### Run it on your computer

Put the Preservica login in a `credentials.properties` file in the same folder (the same file the [preservica](../preservica/) scripts use), or set `PRESERVICA_USERNAME` and `PRESERVICA_PASSWORD`. Then:

```bash
export GITHUB_TOKEN=github_pat_your_token_here
python3 match_preservica.py --from-github --dry-run                   # report only
python3 match_preservica.py --from-github --push-to-github            # update the resolver
```

Without `--from-github` and `--push-to-github` it works on the `resolve.csv` and `ark_master_list.csv` in the folder. It doesn't need the campus network or VPN: GitHub and Preservica are both on the internet. Other options: `--overwrite`, `--portal-url` (the `p_path` for matched ARKs, with `{ref}` for the reference; `''` makes it report only), `--ark-base`, `--allowed-host`, `--naan`, `--push-report` (also push `match_report.csv` to the resolver repository), `--github-repo`, `--github-branch`, and `--workers` (assets read at once, default 4).

### Weekly on GitHub Actions

The workflow [`.github/workflows/arks-match-preservica.yml`](../.github/workflows/arks-match-preservica.yml) runs every Monday at 13:23 UTC (7:23 am Central in winter, 8:23 am in summer). It runs the offline tests, then `match_preservica.py --from-github --push-to-github --push-report`, so it adds newly catalogued ARKs to `resolve.csv` in the resolver repository and pushes the match report there. This repository is public, so the run's log and summary show only counts.

To run it by hand: **Actions > ARKs - match Preservica references > Run workflow**. Tick **dry_run** for a report-only run, or **overwrite** to replace addresses that differ from the asset's portal page.

It needs these, in this repository's **Settings > Secrets and variables > Actions**:

| Name | Kind | What it is |
|---|---|---|
| `PRESERVICA_USERNAME`, `PRESERVICA_PASSWORD` | Secret | The Preservica service account (needs Read Metadata on the assets' security tags) |
| `PRESERVICA_TENANT`, `PRESERVICA_SERVER` | Secret (optional) | Only if not `UNIOWA` and `us.preservica.com` |
| `RESOLVER_TOKEN` | Secret | A token that can write to rodlibrary/Resolver (see [Setup](#setup-one-time)). The workflow's own token can only read this repository, so it can't push to another one. |
| `RESOLVER_REPO`, `RESOLVER_BRANCH` | Variable (optional) | Another repository or branch than `rodlibrary/Resolver` and `main` |
| `PRESERVICA_PORTAL_URL` | Variable (optional) | Send matched ARKs somewhere other than the portal page |
| `DRY_RUN` | Variable (optional) | `true` makes the scheduled runs report-only |

GitHub turns off scheduled workflows in a repository with no activity for 60 days. If that happens, **Actions** shows a banner with an **Enable workflow** button.

## Check resolve.csv: check_resolve.py

```bash
python3 check_resolve.py                                          # resolve.csv in this folder
python3 check_resolve.py --master-list ark_master_list.csv        # also list ARKs still waiting
python3 check_resolve.py --from-github                            # the copy in rodlibrary/Resolver
```

It checks every row by the [rules](#resolvecsv), names the line and the problem, and exits with status 1 if there are any. It's the counterpart of `scripts/build_map.py --check` in [rodlibrary/Redirects](https://github.com/rodlibrary/Redirects). `--from-github` needs `GITHUB_TOKEN` set.

## Setup (one time)

1. **GitHub token.** Signed in to GitHub, go to **Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token**:
   - **Resource owner:** `rodlibrary`;
   - **Expiration:** as long as the organization allows (put a reminder in your calendar to renew it);
   - **Repository access:** *Only select repositories* > `rodlibrary/Resolver`;
   - **Permissions > Repository permissions > Contents:** *Read and write* (Metadata: Read-only is added automatically).

   Copy the token. If the organization requires approval for fine-grained tokens, an owner approves it under the organization's **Settings > Personal access tokens > Pending requests**. If `main` in the resolver gets a branch protection rule or ruleset that requires pull requests, the token's account must be allowed to bypass it, since the scripts push directly.
2. **On your computer:** set the token before running the scripts: `export GITHUB_TOKEN=...` on a Mac, or `$env:GITHUB_TOKEN = "..."` in PowerShell. Never put the token in a file in this repository. Then run `python3 check_github.py` (see [Test the GitHub connection](#test-the-github-connection)).
3. **In this repository:** add the token as the Actions secret `RESOLVER_TOKEN`, along with the Preservica secrets (see [Weekly on GitHub Actions](#weekly-on-github-actions)). Run the workflow once by hand with **dry_run** ticked to check it.
4. **Apache:** the server that resolves ARKs reads `resolve.csv` from [rodlibrary/Resolver](https://github.com/rodlibrary/Resolver). Because the repository is public, it needs no token to read. If it cloned the GitLab repository, change its remote: `git remote set-url origin https://github.com/rodlibrary/Resolver.git`.

## Moving from GitLab

Done once, in November 2026. The resolver moved from `git.uni.edu/rod-library/resolver` to [rodlibrary/Resolver](https://github.com/rodlibrary/Resolver), with its history. The ScholarWorks redirects moved separately, to [rodlibrary/Redirects](https://github.com/rodlibrary/Redirects). What's left to do on the GitLab side:

1. **Stop the GitLab job:** in the GitLab project, **Build > Pipeline schedules**, deactivate or delete *Match ARKs in Preservica*. It now fails with *The ARK resolver moved from GitLab to GitHub*.
2. **Archive the GitLab project** (**Settings > General > Advanced > Archive project**) so no one pushes to it by mistake. Revoke its project access token, and delete the `GITLAB_TOKEN` secret from this repository.
3. **Point Apache at GitHub** (step 4 of [Setup](#setup-one-time)).
4. **Check:** run `python3 check_github.py`, then a dry run of the workflow.

The old `--push-to-gitlab` and `--from-gitlab` options stop with a message naming the GitHub option to use instead.

## Test the GitHub connection

Before pushing anything for real, check that the ARK scripts can pull from and push to GitHub from this computer:

```bash
export GITHUB_TOKEN=github_pat_your_token_here      # PowerShell: $env:GITHUB_TOKEN = "github_pat_your_token_here"
python3 check_github.py
```

It checks each step and stops at the first problem, saying what to change:

1. GitHub can be reached from this computer;
2. the token is accepted;
3. the token can see `rodlibrary/Resolver`;
4. the `main` branch exists (and whether it's protected);
5. `resolve.csv` and `ark_master_list.csv` can be read (their names are right, and `resolve.csv` has the header `ark,p_path`);
6. a real push works. It creates a temporary branch, pushes a small test file to it exactly as the scripts push `resolve.csv`, and deletes the branch again. `main` is never written to.

`--no-write` skips step 6. `--github-repo` and `--github-branch` work as in the other scripts. `check_github.py` needs `generate_arks.py` in the same folder.

## If something goes wrong

- **`The ARK resolver moved from GitLab to GitHub`:** an old command or the old GitLab pipeline used `--push-to-gitlab` or `--from-gitlab`. Use `--push-to-github` or `--from-github`, with `GITHUB_TOKEN` set.
- **`Couldn't pull ... from GitHub, so nothing was minted`:** the token is missing, wrong or expired, or can't see the repository. Run `check_github.py`.
- **`resolve.csv has N problem(s), so nothing was minted`** or **`wasn't pushed`:** a row in rodlibrary/Resolver's `resolve.csv` breaks the [rules](#resolvecsv), usually from a hand edit. The message names the line; `check_resolve.py --from-github` lists them all. Fix the row in GitHub and run again.
- **`url: p_path ...`** in `output.csv`: the `url` for that row isn't a full `https://` address on an allowed host, or has a raw `|`. Nothing was minted for that row; fix it and run that row again.
- **`push failed (403)` or `(404)`:** the token can read the public repository but not write to it. Give it **Contents: Read and write** on rodlibrary/Resolver (see [Setup](#setup-one-time)). If the message mentions a rule or protected branch, a branch rule is blocking direct pushes to `main`.
- **`push failed (409)`:** someone changed the file while the script was running. Run it again; it pulls the newer copy first.
- **`ARK not in the master list`** in the report: the ARK in the MODS has a typo, or was minted somewhere else. Compare it with `ark_master_list.csv`.
- **`resolve.csv already sends it to ...`:** the ARK points somewhere other than this asset's page. Check both; run with `--overwrite` if the asset's page is right.
- **`ARK found in 2 assets`:** the same ARK was entered in two assets' MODS. Remove it from the wrong one.
- **Nothing is added:** the account can't read the assets' MODS (it needs Read Metadata), or the ARK fields are still empty. The [*assets with an ARK* Postman collection](../preservica/postman/README.md#assets-with-an-ark) lists every asset's ARK fields.
