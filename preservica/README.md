# Preservica

Scripts, a scheduled GitHub export and Postman collections for reading, exporting and updating metadata in Rod Library's Preservica repository (`us.preservica.com`, tenant `UNIOWA`).

## Which tool to use

| I want to… | Use |
|---|---|
| A CSV of chosen fields for every asset, kept up to date automatically | [Scheduled export on GitHub Actions](#scheduled-export-on-github-actions) |
| A CSV of chosen fields, now, on my computer | [`preservica_search_export.py`](#preservica_search_exportpy) or the [*selected metadata* Postman collection](postman/) |
| Every metadata field of every asset, as XML | The [*download all metadata* Postman collection](postman/) (one ZIP per top-level folder) |
| Every metadata field of every asset under a folder, flattened into a CSV | [`preservica_metadata_export.py`](#preservica_metadata_exportpy) |
| Look at, download or update one record's metadata | [`preservica_metadata_api.py`](#preservica_metadata_apipy), the [*read metadata* Postman collection](postman/), or Preservica's Swagger pages ([`API_GUIDE.md`](API_GUIDE.md)) |
| Find out why a login fails | [`check_preservica_login.py`](#check_preservica_loginpy) |

## Files

| File | What it does | Runs on |
|---|---|---|
| [`preservica_search_export.py`](preservica_search_export.py) | Exports chosen indexed fields for many records to CSV through the search (Content) API. Fast. | Any computer with Python 3, or weekly on GitHub Actions |
| [`fields.txt`](fields.txt) | The fields `preservica_search_export.py` and the scheduled export include | – |
| [`indexed_fields.csv`](indexed_fields.csv) | Every field Rod Library's Preservica indexes, i.e. every name you can put in `fields.txt` | – |
| [`exports/preservica_metadata.csv`](exports/) | The latest scheduled export | Written by GitHub Actions |
| [`preservica_metadata_export.py`](preservica_metadata_export.py) | Walks a folder and flattens every metadata document of every asset (MODS, Dublin Core, custom schemas) into CSV columns. Slower, but not limited to indexed fields. | Any computer with Python 3 |
| [`preservica_metadata_api.py`](preservica_metadata_api.py) | Lists, downloads and updates one record's metadata through the Entity API | Any computer with Python 3 |
| [`API_GUIDE.md`](API_GUIDE.md) | How to use Preservica's Swagger pages, and the requests for reading, downloading and updating metadata | – |
| [`postman/`](postman/) | Three ready-made Postman collections: read one record, download all metadata, selected metadata to CSV | Postman |
| [`check_preservica_login.py`](check_preservica_login.py) | Checks the credentials file and shows exactly why a login fails | Any computer with Python 3 |
| [`preservica_search_test.py`](preservica_search_test.py) | Tries the search several ways and shows what each returns, to find out why an export finds 0 records when the account can open them | Any computer with Python 3 |
| [`credentials.properties.example`](credentials.properties.example) | Template for the login file the Python scripts read | – |
| [`downloadCount.py`](downloadCount.py) | Counts downloads of each asset from the audit logs and writes the count into the asset's metadata | Any computer with Python 3 |
| [`preservica_download_collection_urls.py`](preservica_download_collection_urls.py) | Lists the download link of every asset in a folder (and its subfolders) in a CSV | Any computer with Python 3 |

## Before you start

**Service Account.** The scripts and collections log in using a Preservica service account and see only what that account can see. Use the service account. It needs:

- a Preservica password of its own (a university single sign-on login can't be used by the API);
- **Read Metadata** on the security tags of the records you want. An administrator sets this per tag in Preservica's `sdb/security.html` page. *Read Content* alone isn't enough: without Read Metadata, searches return nothing and records look empty;
- the permission to edit metadata, only if you'll use the update commands, and the export permission for the *download all metadata* collection.

**Python.** Install [Python 3](https://www.python.org/downloads/), then the libraries:

```powershell
python -m pip install pyPreservica requests
```

**Credentials file.** Copy [`credentials.properties.example`](credentials.properties.example) to `credentials.properties` in the same folder as the scripts, and fill in the username and password. `preservica_search_export.py`, `preservica_metadata_export.py`, `preservica_metadata_api.py` and `check_preservica_login.py` look for it in the folder your terminal is in and then next to the script; `downloadCount.py` and `preservica_download_collection_urls.py` only look in the folder your terminal is in, so `cd` there first. The `[credentials]` line at the top is required:

```
[credentials]
username=service-account@uni.edu
password=your-password
server=us.preservica.com
tenant=UNIOWA
```

`credentials.properties` is listed in the repository's `.gitignore`, so it won't be committed, but keep it private: it holds the password in plain text. Instead of the file, you can set the environment variables `PRESERVICA_USERNAME`, `PRESERVICA_PASSWORD`, `PRESERVICA_SERVER` and `PRESERVICA_TENANT`; they take priority over the file.

**Login problems.** Run `python check_preservica_login.py` in the same folder. It also catches a file saved as `credentials.properties.txt` (Windows hides the `.txt` that Notepad adds) and an empty `PRESERVICA_USERNAME` environment variable hiding the file's value. The usual server answers:

| Server says | Meaning |
|---|---|
| `incorrect.tenant` | The tenant is wrong, or the username doesn't exist in tenant `UNIOWA` |
| `Bad credentials` | The password is wrong, or the account signs in through the university login and has no Preservica password |
| HTTP 404 | `server` is wrong, e.g. the public access site instead of `us.preservica.com` |

## preservica_search_export.py

Exports the fields listed in `fields.txt` for many records at once, using Preservica's search index.

**Setup (one time)**

1. Install the libraries and create `credentials.properties` (see [Before you start](#before-you-start)).
2. Optional: `python preservica_search_export.py --list-fields` refreshes `indexed_fields.csv`, the list of fields you can export.
3. Edit `fields.txt`: one field per line, in the column order you want. Lines starting with `#` are ignored.

**Run it**

```powershell
python preservica_search_export.py                              # all assets
python preservica_search_export.py --folder <folder-reference>  # assets under one folder
python preservica_search_export.py --type all                   # folders as well as assets
```

The CSV is written as `preservica_export_<date>.csv` (change it with `--output`). Run `python preservica_search_export.py --help` for every option.

- `--folder` accepts a bare reference, `SO_<reference>`, or a link copied from Preservica.
- Only records with a particular value: write a line like `xip.security_descriptor = open` in `fields.txt`. To keep only records where a field has a value, use `= *`.
- Fields with several values, such as subjects, go into one cell separated by ` | `. The CSV opens correctly in Excel, accented characters included.
- The script writes a `.partial` file first and renames it only when the export finishes, so a failed run never leaves a half-finished CSV.
- If nothing matches, the script stops without writing a file and explains why: how many records the account can see, how many are under the folder, and how many match each filter. `--allow-empty` writes the empty file anyway.
- The search asks for everything with the query `*`. Preservica 9.4 returns nothing for `%`, which pyPreservica uses by default, so keep `*` if you pass your own `--query`.
- Only indexed fields can be exported. `xip.full_text` is in `fields.txt` but turned off: it would put each document's whole extracted text in a cell.

### Scheduled export on GitHub Actions

The workflow [`.github/workflows/preservica-metadata-export.yml`](../.github/workflows/preservica-metadata-export.yml) runs `preservica_search_export.py` every Monday morning on GitHub's servers, so no computer needs to be left on. It exports the fields in [`fields.txt`](fields.txt) to [`exports/preservica_metadata.csv`](exports/) and commits the file only when the metadata has changed. Each record's history is then visible in the file's commit history.

> **This repository is public.** Anyone can read the exported CSV, so list only fields in `fields.txt` that are fine to publish. Giving the service account Read Metadata only on tags that are fine to publish (such as `open`), or adding `xip.security_descriptor = open` to `fields.txt`, keeps restricted records out.

**Setup (one time):** in the repository on GitHub, go to **Settings > Secrets and variables > Actions**.

1. On the **Secrets** tab, add four **repository secrets** (not variables, and not environment secrets). They're encrypted, and never appear in the code or the run logs:

   | Name | Value |
   |---|---|
   | `PRESERVICA_USERNAME` | Service account username |
   | `PRESERVICA_PASSWORD` | Service account password |
   | `PRESERVICA_TENANT` | `UNIOWA` |
   | `PRESERVICA_SERVER` | `us.preservica.com` |

2. Optional: on the **Variables** tab, add `PRESERVICA_FOLDER` with a folder reference to export just that folder. Without it, every asset is exported.

**Run it now:** go to **Actions > Preservica metadata export > Run workflow**. You can enter a different folder or record type for that one run. Each run shows a summary with the record count, and the log shows any errors.

**If it says secrets are missing**, they were added as variables or as environment secrets; add them again as repository secrets.

**If a run finds no records**, it fails and leaves the previous CSV unchanged. The run summary shows how many records the account can see in total, how many are under the chosen folder, and how many match any `field = value` lines in `fields.txt`. `Records this account can see in total: 0` means the account needs Read Metadata (see [Before you start](#before-you-start)).

**Change the schedule:** edit the `cron` line in the workflow file. GitHub uses UTC: `17 11 * * 1` means Mondays at 11:17 UTC. GitHub pauses schedules in public repositories after 60 days without activity and emails you; one click in the Actions tab turns it back on.

## preservica_metadata_export.py

Walks every asset under a folder (or the whole repository), fetches each one's metadata documents and flattens the XML into columns, e.g. `dc:subject` or `mods:titleInfo/mods:title`, with one row per asset. Unlike the search export it isn't limited to indexed fields, but it makes several requests per asset, so large folders take a long time.

```powershell
python preservica_metadata_export.py --folder <folder-reference> --limit 20     # try it on 20 assets
python preservica_metadata_export.py --folder <folder-reference>
python preservica_metadata_export.py --folder <folder-reference> --schema http://www.loc.gov/mods/v3   # MODS only
```

`--include-folders` adds folders as rows. The CSV is written as `preservica_metadata_<date>.csv`.

## preservica_metadata_api.py

Works on one record at a time through the Entity API, making the same requests documented in [`API_GUIDE.md`](API_GUIDE.md). Each command accepts a bare reference, `IO_`/`SO_` followed by the reference, or a link copied from Preservica.

```powershell
python preservica_metadata_api.py version                    # checks the login works
python preservica_metadata_api.py list     <asset-reference> # its metadata records
python preservica_metadata_api.py download <asset-reference> # saves each as XML in metadata_downloads\
python preservica_metadata_api.py update   <asset-reference> --schema http://www.loc.gov/mods/v3 --file new_mods.xml
```

Add `--folder` when the reference is a folder. `update` replaces the whole metadata record. Without `--apply` it only prints what it would send; with `--apply` it first saves a backup of the current record in `metadata_downloads\`, then sends the update. A typical edit: `download`, edit the saved XML, `update` to check, then `update --apply`. Try it on a test record first.

## Postman collections

[`postman/`](postman/) has three collections that make the same requests from Postman, with step-by-step instructions built in:

- **Read metadata:** look at one folder or asset and download its metadata records.
- **Download all metadata (export packages):** exports every top-level folder as a ZIP of XML files, with all the metadata of every asset.
- **Selected metadata for all assets (CSV):** chosen indexed fields for every asset in every top-level folder, with a column naming each asset's top-level folder.

See [`postman/README.md`](postman/README.md) for importing them.

## check_preservica_login.py

```powershell
python check_preservica_login.py
```

Run it in the folder that holds `credentials.properties`. It shows where each value comes from (the file or an environment variable, which takes priority), flags common mistakes (quotes, spaces, placeholder text, a full web address as the server), then tries to log in and prints the server's exact answer. It never shows the password.

## preservica_search_test.py

If `preservica_search_export.py` (or the scheduled export) says the account can see 0 records, but the account can open records in Preservica or with `preservica_metadata_export.py`, run:

```powershell
python preservica_search_test.py <folder-reference>
```

It sends the search nine slightly different ways and prints the result count or error for each, plus a look-up of the folder through the search API and the Entity API. It only reads. The output has no password or token, so it can be shared.

- Some searches return results and others don't: the export's search needs changing to the form that works. (On Rod Library's Preservica 9.4, `%` returned 0 and `*` returned everything, which is why the export now uses `*`.)
- Every search returns 0, but the folder look-ups work: the account can read records but not search them. Ask your Preservica administrator about its search permission, or whether the search index needs rebuilding.
- The search-API look-up fails but the Entity API one works: the account isn't allowed to use the search API.

## downloadCount.py

1. Signs in to Preservica with [pyPreservica](https://pypreservica.readthedocs.io/).
2. Reads the system audit logs (from 1 January 2026 by default) and counts `DELIVER_BITSTREAM` and `DOWNLOAD_ASSET` events per asset.
3. For each asset, updates the `downloadCount` element in a custom metadata fragment (namespace `http://digital.library.uni.edu`), creating the fragment if the asset doesn't have one yet.

> **This writes to live Preservica metadata.** Try it against a test system or a small date range first.

```powershell
python downloadCount.py
```

It reads `credentials.properties` from the folder you run it in. To count from a different date, change `start_date` in `main()`.

## preservica_download_collection_urls.py

Lists every asset in a folder and all its subfolders with its download link, in `asset_download_links.csv`. Set `COLLECTION_REF` at the top of the script to the folder's reference, then:

```powershell
python preservica_download_collection_urls.py
```

The links need a valid Preservica login token to download anything; they aren't public links.
