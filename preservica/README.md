# Preservica

| File | What it does | Runs on |
|---|---|---|
| [`preservica_search_export.py`](preservica_search_export.py) | Exports select metadata from Preservica's via search API | Any computer with Python 3 and Preservica API access, or weekly on GitHub Actions |
| [`preservica_metadata_api.py`](preservica_metadata_api.py) | Reads, downloads and updates one record's metadata through the Entity API; see [`API_GUIDE.md`](API_GUIDE.md) for using Preservica's Swagger pages | Any computer with Python 3 and Preservica API access |
| [`downloadCount.py`](downloadCount.py) | Counts downloads of each asset from Preservica's audit logs and writes the count into the asset's metadata | Any computer with Python 3 and Preservica API access |

## preservica_search_export.py
Setup (one time)

1. Install the library:

```powershell
python -m pip install pyPreservica requests
```
3. Put the credentials.properties file next to the script:

```
username=you@email
password=your-password
tenant=YOUR_TENANT
server=your-server.preservica.com
```
3. Run python preservica_search_export.py --list-fields. This writes indexed_fields.csv, listing every field your Preservica instance indexes. It shows the standard xip.* fields plus whatever your metadata adds, such as oai_dc.creator.
4. Edit fields.txt: put one field per line, in the column order you want.

Running it

-Everything under one folder: python preservica_search_export.py --folder <folder-UUID>
Whole repository, folders included: add --type all

-Only records with a particular value: write a line like xip.security_descriptor = open in fields.txt. To keep only records where a field has a value, use = *.

-Fields with several values, such as subjects, go into one cell separated by |. The CSV opens correctly in Excel, accented characters included.

-The script writes a .partial file first and renames it only when the export finishes. A scheduled run that fails never leaves a half-finished CSV with the real name.

### Scheduled export on GitHub Actions

The workflow [`.github/workflows/preservica-metadata-export.yml`](../.github/workflows/preservica-metadata-export.yml) runs the export every Monday morning on GitHub's servers, so no computer needs to be left on. It exports the fields in [`fields.txt`](fields.txt) to [`exports/preservica_metadata.csv`](exports/) and commits the file only when the metadata has changed. Each record's history is then visible in the file's commit history.

> **This repository is public.** Anyone can read the exported CSV, so list only fields in `fields.txt` that are fine to publish, and consider adding `xip.security_descriptor = open` to leave out restricted records.

**Setup (one time):** in the repository on GitHub, go to **Settings > Secrets and variables > Actions**.

1. On the **Secrets** tab, add four repository secrets. These are encrypted, and never appear in the code or the run logs:

   | Name | Value |
   |---|---|
   | `PRESERVICA_USERNAME` | Service account username |
   | `PRESERVICA_PASSWORD` | Service account password |
   | `PRESERVICA_TENANT` | `UNIOWA` |
   | `PRESERVICA_SERVER` | `us.preservica.com` |

2. Optional: on the **Variables** tab, add `PRESERVICA_FOLDER` with a folder UUID to export just that folder. Without it, the whole repository is exported.

**Run it now:** go to **Actions > Preservica metadata export > Run workflow**. You can enter a different folder or record type for that one run. Each run shows a summary with the record count, and the log shows any errors.

**If a run finds no records**, it fails and leaves the previous CSV unchanged. The run summary then shows how many records the account can see in total, how many are under the chosen folder, and how many match any `field = value` lines in `fields.txt`, which shows whether the folder, the account's access or a filter is the cause. `PRESERVICA_FOLDER` accepts a bare reference, `SO_<reference>`, or a link copied from Preservica.

**Change the schedule:** edit the `cron` line in the workflow file. GitHub uses UTC: `17 11 * * 1` means Mondays at 11:17 UTC.


## downloadCount.py

1. Signs in to Preservica with [pyPreservica](https://pypreservica.readthedocs.io/).
2. Reads the system audit logs (from 1 January 2026 by default) and counts `DELIVER_BITSTREAM` and `DOWNLOAD_ASSET` events per asset.
3. For each asset, updates the `downloadCount` element in a custom metadata fragment (namespace `http://digital.library.uni.edu`), creating the fragment if the asset doesn't have one yet.

> **This writes to live Preservica metadata.** Try it against a test system or a small date range first.

**Install**

```powershell
python -m pip install pyPreservica requests
```

**Credentials:** pyPreservica reads them from a `credentials.properties` file in the folder you run the script from:

```
username=you@email
password=your-password
tenant=YOUR_TENANT
server=your-server.preservica.com
```

Never commit this file to GitHub.

**Run**

```powershell
python downloadCount.py
```

To count from a different date, change `start_date` in `main()`.
