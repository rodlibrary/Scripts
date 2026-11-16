# ARKs

Mint Archival Resource Keys (ARKs), publish them to the resolver repository on GitHub ([rodlibrary/Resolver](https://github.com/rodlibrary/Resolver)), where Apache resolves them, and fill in which Preservica asset holds each ARK automatically.

> The resolver repository moved from GitLab (`git.uni.edu/rod-library/resolver`) to GitHub in November 2026. The `--push-to-gitlab` and `--from-gitlab` options are gone: use `--push-to-github` and `--from-github`, with a GitHub token in `GITHUB_TOKEN`. See [Moving from GitLab](#moving-from-gitlab).

| File | What it does | Runs on |
|---|---|---|
| [`generate_arks.py`](generate_arks.py) | Mints ARKs from a CSV, then adds them to `resolve.csv` and the master list, pulling and pushing the GitHub copies | Any computer with Python 3 (no extra packages) |
| [`match_preservica.py`](match_preservica.py) | Reads the ARK fields of every Preservica asset's MODS record and fills in each ARK's `preservica_ref` (and a `target_url` where none is set) in `resolve.csv` | Weekly on GitHub Actions, or any computer with Python 3 |
| [`check_github.py`](check_github.py) | Tests the GitHub connection step by step, including a test push to a temporary branch, without touching `main` | Any computer with Python 3 |
| [`arks.csv`](arks.csv) | Blank input template (header row only) | – |
| [`tests/`](tests/) | Offline tests with a fake GitHub and a fake Preservica | `python3 -m unittest discover -s arks/tests` |

## How it fits together

1. **Mint:** fill in `arks.csv` and run `generate_arks.py ... --push-to-github`. It pulls the current `resolve.csv` and `ark_master_list.csv` from GitHub, mints the new ARKs, adds them to both files and pushes them back. Apache then resolves the ARKs that have a `target_url`.
2. **Catalogue:** put each ARK in the asset's MODS in Preservica, in the template's two ARK fields:
   - **Archival Resource Key:** `<identifier displayLabel="Archival Resource Key" type="ark">`, holding the blade (such as `s3x7k9q2mbd`);
   - **Location (URL):** `<physicalLocation displayLabel="Location (URL)" type="url"><url>`, holding the full address (such as `https://n2t.net/ark:/32498/s3x7k9q2mbd`).
3. **Match:** every Monday, a [scheduled GitHub Actions workflow in this repository](#weekly-on-github-actions) runs `match_preservica.py`. It finds those ARKs in Preservica, records which asset holds each one in `resolve.csv`, points ARKs that have no target yet at the asset's page in Preservica's portal, and pushes `resolve.csv` back. You can run it yourself at any time, or add `--match-preservica` to step 1 to do everything in one command.

The resolver repository holds the copies of record. The scripts always start from its copies and merge your local rows in, so they never overwrite newer rows.

**[rodlibrary/Resolver](https://github.com/rodlibrary/Resolver) is public**, so everything pushed there (`resolve.csv`, `ark_master_list.csv` with each ARK's identifier and title, and `match_report.csv`) can be read by anyone. Don't put anything in those columns that shouldn't be public.

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
export GITHUB_TOKEN=github_pat_your_token_here
python3 generate_arks.py output --push-to-github
```

In PowerShell, set the token with `$env:GITHUB_TOKEN = "github_pat_your_token_here"` instead.

- `arks.csv` is the input. It's the default, so you don't need to name it. To use a different file, put its path first: `python3 generate_arks.py other.csv output --push-to-github`.
- `output` is the base name for this run's files: the script writes `output.csv` and `output.anvl`.
- `--push-to-github` first pulls `resolve.csv` and `ark_master_list.csv` from [rodlibrary/Resolver](https://github.com/rodlibrary/Resolver) and merges them into your copies. It then mints the ARKs and pushes both files back. You don't need to download them before each run. If GitHub can't be reached, or `GITHUB_TOKEN` isn't set, nothing is minted.
- `--match-preservica` also runs the Preservica matching before the push, so one command does all three steps. It needs the Preservica login: see [Match Preservica references](#match-preservica-references-match_preservicapy).
- Leave `--push-to-github` off to run locally without GitHub.

| Option | Meaning | Default |
|---|---|---|
| `input_csv` | The input CSV | `arks.csv` |
| `output_basename` | Base name for this run's outputs (`<name>.csv` and `<name>.anvl`) | – |
| `--push-to-github` | Pull and merge the GitHub copies first, then push them after the run | Off |
| `--match-preservica` | Fill in Preservica references before pushing (runs `match_preservica.py`) | Off |
| `--no-pull` | With `--push-to-github`, don't pull first (the push then overwrites GitHub's copies) | Off |
| `--naan` | NAAN for rows that don't supply one | `32498` |
| `--add-checksum` | Add a NOID-style check character to each ARK | Off |
| `--blade-length` | Length of the random blade | `10` |
| `--master-list` | Running list of **every** ARK ever minted, which keeps blades unique across runs | `ark_master_list.csv` |
| `--no-master-list` | Skip the master list (uniqueness is then only guaranteed within this run) | Off |
| `--resolve-manifest` | The resolve manifest | `resolve.csv` |
| `--no-resolve-manifest` | Don't write or push the resolve manifest | Off |
| `--slim-manifest` | Where to write the slim `ark` / `p_path` export (below) | `resolve_paths.csv` |
| `--no-slim-manifest` | Don't write the slim export | Off |
| `--resolver` | Resolver base URL for the links in `output.csv` | `https://n2t.net` |
| `--github-repo` | Repository to pull from and push to (or set `RESOLVER_REPO`) | `rodlibrary/Resolver` |
| `--github-branch` | Its branch (or set `RESOLVER_BRANCH`) | `main` |
| `--github-path`, `--github-manifest-path` | Where the master list and `resolve.csv` live in the repository | Same file names, at the top level |
| `--github-token-env` | Environment variable holding the token | `GITHUB_TOKEN` |

### Output

| File | Contents |
|---|---|
| `output.csv` | One row per input record: the ARK, a resolvable `https://n2t.net/...` link, all metadata, and status and notes columns reporting any problems |
| `output.anvl` | ANVL metadata records, the format N2T and EZID import |
| `resolve.csv` | The resolve manifest Apache uses (below). New ARKs are added; existing rows are never changed. |
| `resolve_paths.csv` | The slim export with just `ark` and `p_path` (below). Rebuilt from `resolve.csv` every run. |
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

### resolve_paths.csv (slim export)

If you only need the ARK and where it goes, `resolve_paths.csv` has two columns and nothing else:

| Column | Meaning |
|---|---|
| `ark` | The ARK without `ark:/`, e.g. `32498/s3x7k9q2mbd` (same as in `resolve.csv`) |
| `p_path` | Where the ARK redirects to (the `target_url` in `resolve.csv`) |

- It is built from `resolve.csv` at the end of every run, after any Preservica matching, and **overwritten** each time. It is a copy, not a record, so edit `resolve.csv`, never this file.
- It only lists rows whose `status` is `active` and that have a `target_url`. `withdrawn` and `unmatched` ARKs, and ARKs with no target yet, are left out.
- `resolve.csv` is unchanged and is still what the Preservica matching uses. `--push-to-github` does **not** push `resolve_paths.csv`.
- The weekly Preservica matching changes `target_url` in `resolve.csv` but doesn't rebuild this file. Run `generate_arks.py` again to refresh it.

## Match Preservica references: match_preservica.py

Lists every asset in Preservica and reads its MODS record (about two requests per asset), taking the **Archival Resource Key** identifier and the **Location (URL)**. Preservica's search index keeps neither the identifier's type nor the URL, so the MODS has to be read. For each ARK found:

- if `resolve.csv` has no `preservica_ref` for it, the asset's reference is filled in (`IO_<reference>`);
- if it has no `target_url`, the asset's page in Preservica's portal is filled in (`https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C<reference>`, where `sdb%3AIO%7C` is `sdb:IO|` written for a web address). The status changes from `unmatched` to `active`, so Apache starts resolving it;
- an existing different `preservica_ref` or `target_url` is left alone and reported, unless you use `--overwrite`;
- `withdrawn` ARKs stay withdrawn;
- a note records the date of the match.

These are never filled in, only reported:

- an ARK found in **more than one asset**, since a resolver needs exactly one target;
- an asset whose blade and URL name **different ARKs**;
- an **ARK in Preservica that isn't in `resolve.csv`**.

The blade may be entered with or without the shoulder: the master list connects a bare blade to its ARK. Hyphens are ignored, as they carry no meaning in ARKs.

Every run writes `match_report.csv`: one row per asset with ARK fields, with the outcome. The weekly run pushes it to the resolver repository whenever it changes.

### Run it on your computer

Put the Preservica login in a `credentials.properties` file in the same folder (the same file the [preservica](../preservica/) scripts use), or set `PRESERVICA_USERNAME` and `PRESERVICA_PASSWORD`. Then:

```bash
export GITHUB_TOKEN=github_pat_your_token_here
python3 match_preservica.py --from-github --dry-run                   # report only
python3 match_preservica.py --from-github --push-to-github            # update the resolver
```

Without `--from-github` and `--push-to-github` it works on the `resolve.csv` in the folder. It no longer needs the campus network or VPN: GitHub and Preservica are both on the internet. Other options: `--overwrite`, `--portal-url` (the target address for matched ARKs, with `{ref}` for the reference; `''` leaves `target_url` alone), `--naan`, `--push-report` (also push `match_report.csv` to the resolver repository), `--github-repo`, `--github-branch`, and `--workers` (assets read at once, default 4).

### Weekly on GitHub Actions

The workflow [`.github/workflows/arks-match-preservica.yml`](../.github/workflows/arks-match-preservica.yml) runs every Monday at 13:23 UTC (7:23 am Central in winter, 8:23 am in summer). It runs the offline tests, then `match_preservica.py --from-github --push-to-github --push-report`, so it updates `resolve.csv` in the resolver repository and pushes the match report there. This repository is public, so the run's log and summary show only counts.

To run it by hand: **Actions > ARKs - match Preservica references > Run workflow**. Tick **dry_run** for a report-only run, or **overwrite** to replace references that differ from Preservica.

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

## Setup (one time)

1. **GitHub token.** Signed in to GitHub, go to **Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token**:
   - **Resource owner:** `rodlibrary`;
   - **Expiration:** as long as the organization allows (put a reminder in your calendar to renew it);
   - **Repository access:** *Only select repositories* > `rodlibrary/Resolver`;
   - **Permissions > Repository permissions > Contents:** *Read and write* (Metadata: Read-only is added automatically).

   Copy the token. If the organization requires approval for fine-grained tokens, an owner approves it under the organization's **Settings > Personal access tokens > Pending requests**. If `main` in the resolver gets a branch protection rule or ruleset that requires pull requests, the token's account must be allowed to bypass it, since the scripts push directly.
2. **On your computer:** set the token before running the scripts: `export GITHUB_TOKEN=...` on a Mac, or `$env:GITHUB_TOKEN = "..."` in PowerShell. Never put the token in a file in this repository. Then run `python3 check_github.py` (see [Test the GitHub connection](#test-the-github-connection)).
3. **In this repository:** add the token as the Actions secret `RESOLVER_TOKEN`, along with the Preservica secrets (see [Weekly on GitHub Actions](#weekly-on-github-actions)). Run the workflow once by hand with **dry_run** ticked to check it.
4. **Apache:** the server that resolves ARKs must now get `resolve.csv` from [rodlibrary/Resolver](https://github.com/rodlibrary/Resolver) instead of GitLab. If it clones the repository, change its remote: `git remote set-url origin https://github.com/rodlibrary/Resolver.git`. Because the repository is public, it needs no token to read.

## Moving from GitLab

Done once, in November 2026. Kept here for the record and in case it needs repeating.

1. **Copy the repository with its history**, on a computer that can reach git.uni.edu (on campus or on the VPN). rodlibrary/Resolver was created with a `LICENSE` and a short `README.md`, so its history is merged in rather than overwritten:

   ```bash
   git clone https://git.uni.edu/rod-library/resolver.git resolver
   cd resolver
   git remote add github https://github.com/rodlibrary/Resolver.git
   git fetch github
   git merge github/main --allow-unrelated-histories -m "Merge GitHub's initial commit (LICENSE)"
   # If README.md conflicts, keep GitLab's version:  git checkout --ours README.md && git add README.md && git commit --no-edit
   git rm .gitlab-ci.yml          # the weekly job now runs on GitHub Actions
   git commit -m "Remove GitLab CI: the weekly matching runs in rodlibrary/Scripts"
   git push github main
   git push github --tags
   ```

   If GitLab's default branch is `master`, use `git push github master:main` instead.
2. **Stop the GitLab job:** in the GitLab project, **Build > Pipeline schedules**, deactivate or delete *Match ARKs in Preservica*. Otherwise it keeps running, and since it downloads these scripts from `main` it will now fail with *The ARK resolver moved from GitLab to GitHub*.
3. **Archive the GitLab project** (**Settings > General > Advanced > Archive project**) so no one pushes to it by mistake. Revoke its project access token, and delete the `GITLAB_TOKEN` secret from this repository.
4. **Point Apache at GitHub** (step 4 of [Setup](#setup-one-time)).
5. **Check:** run `python3 check_github.py`, then a dry run of the workflow.

Earlier still, the ARKs lived in rod-library/redirects: its `redirects.csv` became `resolve.csv`, and `generate_arks.py` still copies a local `redirects.csv` into `resolve.csv` on its first run.

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
5. `resolve.csv` and `ark_master_list.csv` can be read (and their names are right);
6. a real push works. It creates a temporary branch, pushes a small test file to it exactly as the scripts push `resolve.csv`, and deletes the branch again. `main` is never written to.

`--no-write` skips step 6. `--github-repo` and `--github-branch` work as in the other scripts. `check_github.py` needs `generate_arks.py` in the same folder.

## If something goes wrong

- **`The ARK resolver moved from GitLab to GitHub`:** an old command or the old GitLab pipeline used `--push-to-gitlab` or `--from-gitlab`. Use `--push-to-github` or `--from-github`, with `GITHUB_TOKEN` set.
- **`Couldn't pull ... from GitHub, so nothing was minted`:** the token is missing, wrong or expired, or can't see the repository. Run `check_github.py`.
- **`push failed (403)` or `(404)`:** the token can read the public repository but not write to it. Give it **Contents: Read and write** on rodlibrary/Resolver (see [Setup](#setup-one-time)). If the message mentions a rule or protected branch, a branch rule is blocking direct pushes to `main`.
- **`push failed (409)`:** someone changed the file while the script was running. Run it again; it pulls the newer copy first.
- **`ARK not in resolve.csv`** in the report: the ARK in the MODS has a typo, or was minted somewhere else. Compare it with `ark_master_list.csv`.
- **`resolve.csv already has IO_...`:** the ARK was matched to a different asset before. Check both assets; run with `--overwrite` if the new one is right.
- **`ARK found in 2 assets`:** the same ARK was entered in two assets' MODS. Remove it from the wrong one.
- **Nothing is matched:** the account can't read the assets' MODS (it needs Read Metadata), or the ARK fields are still empty. The [*assets with an ARK* Postman collection](../preservica/postman/README.md#assets-with-an-ark) lists every asset's ARK fields.
