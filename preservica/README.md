# Preservica

| File | What it does | Runs on |
|---|---|---|
| [`preservica_search_export.py`](preservica_search_export.py) | Exports select metadata from Preservica's via search API | Any computer with Python 3 and Preservica API access |
| [`downloadCount.py`](downloadCount.py) | Counts downloads of each asset from Preservica's audit logs and writes the count into the asset's metadata | Any computer with Python 3 and Preservica API access |

## preservica_search_export.py
Setup (one time)

1. Install the library: pip install pyPreservica
2. Put the same credentials.properties file next to the script: username, password, server and tenant under a [credentials] header.
3. Run python preservica_search_export.py --list-fields. This writes indexed_fields.csv, listing every field your Preservica instance indexes. It shows the standard xip.* fields plus whatever your metadata adds, such as oai_dc.creator.
4. Edit fields.txt: put one field per line, in the column order you want.

Running it

-Everything under one folder: python preservica_search_export.py --folder <folder-UUID>
Whole repository, folders included: add --type all

-Only records with a particular value: write a line like xip.security_descriptor = open in fields.txt. To keep only records where a field has a value, use = *.

-Fields with several values, such as subjects, go into one cell separated by |. The CSV opens correctly in Excel, accented characters included.

-The script writes a .partial file first and renames it only when the export finishes. A scheduled run that fails never leaves a half-finished CSV with the real name.


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
