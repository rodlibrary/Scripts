# Preservica

| File | What it does | Runs on |
|---|---|---|
| [`downloadCount.py`](downloadCount.py) | Counts downloads of each asset from Preservica's audit logs and writes the count into the asset's metadata | Any computer with Python 3 and Preservica API access |

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
