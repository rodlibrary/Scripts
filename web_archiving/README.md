# Web archiving

Scripts for archiving uni.edu: find PDFs that aren't yet captured in UNI's [Archive-It](https://archive-it.org/home/uni) collections and turn the ones you approve into seed lists, count how many web pages uni.edu has, and report Archive-It's monthly crawl totals.

| File | What it does | Runs on |
|---|---|---|
| [`pdf_archive_gap.py`](pdf_archive_gap.py) | Crawls uni.edu for PDF links, checks them against Archive-It, writes a review workbook and exports seed lists | Any computer with Python 3.8+ and `openpyxl` |
| [`uni_page_count.py`](uni_page_count.py) | Counts the web pages on uni.edu and its subdomains from their sitemaps, with an optional small crawl | Any computer with Python 3.8+ (nothing to install) |
| [`archiveit_monthly_stats.py`](archiveit_monthly_stats.py) | Totals UNI's Archive-It crawls by month (web pages, TB of data, crawls) into a CSV, from the Archive-It Partner API | Any computer with Python 3.8+ and `requests` |
| [`archiveit_credentials.properties.example`](archiveit_credentials.properties.example) | Template for the Archive-It login file `archiveit_monthly_stats.py` reads | — |
| [`tests/`](tests/) | Offline tests for `archiveit_monthly_stats.py` (a fake Archive-It) | `python -m unittest discover -s tests` |
| [`example_pdf_comparison.xlsx`](example_pdf_comparison.xlsx) | A small example of the review workbook (made-up rows) showing the columns, dropdowns and highlighting | — |

## Find PDFs missing from Archive-It (`pdf_archive_gap.py`)

Find PDFs on uni.edu that aren't yet captured in UNI's Archive-It collections, review them in Excel, and turn the ones you approve into seed lists ready to paste into Archive-It.

### How it works

1. **Crawl uni.edu.** Starts from the home page, `robots.txt` and the sitemaps, follows links across uni.edu and its subdomains, and records every link ending in `.pdf` with the page it was found on.
2. **Check Archive-It.** Finds UNI's collections on archive-it.org (or uses the IDs you give it) and asks Archive-It's public index for every PDF captured in each one.
3. **Compare.** Matches the two lists, ignoring `http`/`https`, `www.`, capitalization in the host and trailing slashes, and gives each PDF a status.
4. **Review.** Writes everything to `pdf_comparison.xlsx`. Reviewers fill in three yellow columns.
5. **Export seed lists.** Rows marked **add** become seed lists, one file per collection and seed type, ready for Archive-It's **Add Seeds** dialog.

| Status | Meaning |
|---|---|
| `NEEDS_ARCHIVING` | On uni.edu, not found in Archive-It — the list to review |
| `ARCHIVED` | On uni.edu and already captured |
| `ARCHIVED_NOT_LIVE` | Captured in Archive-It, but no longer linked on uni.edu |

### Install

```powershell
python -m pip install openpyxl
```

Everything else is in the Python standard library.

### Run

1. Copy `pdf_archive_gap.py` to a folder of its own (e.g. `Documents\web_archiving`). Every output and the saved crawl progress are written there, so **always run it from the same folder** (or pass the same `--outdir`).
2. Open Terminal in that folder and enter:

```powershell
python pdf_archive_gap.py
```

A full crawl of uni.edu can take hours. You can stop it with **Ctrl+C** at any time. Progress is saved, and the next run picks up where it left off.

| Option | Meaning | Default |
|---|---|---|
| `--collections 1234 5678` | Archive-It collection IDs to check | Discovered from archive-it.org/home/uni |
| `--max-pages` | Pages to fetch **this run**. Run again to do the next batch. | `10000` |
| `--delay` | Seconds between page requests | `0.3` |
| `--recrawl-days` | Also re-crawl pages last crawled this many days ago or more | Never re-crawl |
| `--fresh` | Delete the saved crawl progress and start over | Off |
| `--default-collection` | Pre-fill `target_collection_id` on unreviewed `NEEDS_ARCHIVING` rows | Blank |
| `--seeds-only` | Don't crawl; just export seed lists from the reviewed workbook | Off |
| `--reseed` | Re-export rows already exported before (with `--seeds-only` or a full run) | Off |
| `--outdir` | Folder for all outputs and saved progress | Current folder |

### Review in Excel

Open `pdf_comparison.xlsx` and work on the **Review** sheet. Only edit the three yellow columns. Everything else is rewritten on each run, but these columns are read back and kept.

| Column | Values | Meaning |
|---|---|---|
| `appraisal` | `add` / `ignore` | Whether the PDF should be captured. Blank = not reviewed yet; unreviewed rows sort to the top. |
| `target_collection_id` | An Archive-It collection ID | The collection to add it to. The dropdown lists known collections (the **Collections** sheet); you can type another ID and click **Yes** at the warning. |
| `seed_type` | `One Page` / `One Page+` | **One Page** (or blank): the seed is the PDF itself, captured on its own. **One Page+**: the seed is the page the PDF was found on, captured with everything it links to one step away, so the PDF is archived in context. PDFs on the same page share one seed. |

`seeded_on` (grey) is filled in by the script when a row is exported, so it isn't exported twice. Clear it to export that row again.

The **Instructions** sheet in the workbook repeats this, with an example row.

**Save and close the workbook before running the script.** If it's open in Excel, the script stops straight away rather than after a long crawl.

### Export seed lists and add them to Archive-It

```powershell
python pdf_archive_gap.py --seeds-only
```

This skips the crawl and writes a new folder, `seeds\<date_time>\`, containing:

| File | Contents |
|---|---|
| `collection_<ID>_one-page_seeds.txt` | PDF URLs to add to that collection as **One Page** seeds, one per line |
| `collection_<ID>_one-page-plus_seeds.txt` | Linking-page URLs to add as **One Page+** seeds |
| `..._part1.txt`, `..._part2.txt` | Used when a list has more than 1,000 URLs, the most the Add Seeds dialog accepts at once |
| `seed_summary.csv` | Every exported PDF with the seed that covers it, its collection, seed type and file |
| `README.txt` | Which collection and seed type each file is for, and the Archive-It steps |

A full run also exports any rows that were reviewed but not yet exported, so `--seeds-only` is only needed when you don't want to crawl.

**In Archive-It, for each file:**

1. Open the collection named in the file name, go to the **Seeds** tab and click **Add Seeds**.
2. Paste the file's contents.
3. Set **Seed Type** to the one in the file name (`one-page` → One Page, `one-page-plus` → One Page+). The dialog applies one seed type to the whole batch, which is why the lists are split by type.
4. Choose visibility and crawl frequency, then click **Add Seeds**.
5. Crawl the new seeds. Once the captures are indexed, the next run shows those PDFs as `ARCHIVED`.

Rows marked **add** are skipped, with a warning in Terminal, when they have no collection ID, are no longer on uni.edu, have an unrecognised seed type, or are One Page+ but were found only in a sitemap (so there's no linking page to seed).

### Output

| File | Contents |
|---|---|
| `pdf_comparison.xlsx` | The review workbook: **Review**, **Collections** and **Instructions** sheets |
| `uni_pdfs.csv` | Every PDF link found on uni.edu, with the page it was found on and the date first seen (cumulative across runs) |
| `archiveit_pdfs.csv` | Every PDF captured in the checked Archive-It collections, with capture counts and first/last capture dates |
| `crawled_pages.csv` | Log of every page fetched (used to skip pages on the next run) |
| `crawl_queue.txt` | Pages found but not yet crawled (where the next run starts) |
| `seeds\` | Exported seed lists, one dated folder per export |

### Limitations

- **Only links ending in `.pdf` are found.** PDFs served from addresses without that extension are missed.
- **Only public Archive-It collections are visible.** Private collections need an export from your Archive-It account.
- **Pages already crawled aren't re-read**, so a new PDF link added to an old page is only found when that page is re-crawled. Use `--recrawl-days 90` (for example) for periodic check-ups.
- The crawl identifies itself to the web server as `UNI-PDF-Archive-Audit/1.0`. Increase `--delay` if it puts noticeable load on the site.

## Count pages on uni.edu (`uni_page_count.py`)

Estimates how many web pages uni.edu has, with a breakdown by host (e.g. `www.uni.edu`, `library.uni.edu`) and by top-level section. Useful for scoping an Archive-It crawl or a budget request.

### How it works

1. **Read the sitemaps.** For each host it reads `robots.txt`, then the sitemaps listed there (or `/sitemap.xml` if none are listed), following sitemap indexes and `.gz` sitemaps. This is the same list search engines use, so a few dozen requests can account for thousands of pages.
2. **Find other hosts (optional).** With `--discover-subdomains` it looks up uni.edu hostnames in public certificate logs ([crt.sh](https://crt.sh)), so subdomains such as `library.uni.edu` are counted too. This sends nothing to uni.edu except one `robots.txt` request per host.
3. **Crawl a little (optional).** With `--crawl` it also follows links from each host's home page, up to `--max-pages`, to find pages the sitemaps leave out. It never follows links to the traps described below.
4. **Leave out traps.** Calendars and other pages that generate endless addresses are left out of the count (see [Calendars and other traps](#calendars-and-other-traps)).
5. **Count.** It removes duplicates (`http`/`https`, `www.` or not, `/index.html` and `/` are the same page), leaves out PDFs, images and other files, and prints the totals.

It is designed to be gentle on the web server:

- It obeys `robots.txt`, including any `Crawl-delay`, and never fetches a path it disallows.
- It waits at least 2 seconds between requests to the same host (`--delay`, minimum 1).
- It slows down when the server answers *429 Too Many Requests* or *503*.
- It identifies itself as `PageCountBot/1.0` with the contact address you give, so the web team can reach you.
- When crawling, it stops reading any response that isn't a web page, so large downloads aren't transferred.

### Calendars and other traps

Some pages link to endless new addresses. The calendar is the main one: each day view on calendar.uni.edu (`/day/2026-11-04`) links to the next and previous day, forever. A crawler that follows those links never finishes, and a count that includes them is meaningless. The script recognises these addresses, never requests them while crawling, and leaves them out of the count even when a sitemap lists them:

| Left out as | Examples |
|---|---|
| Calendar/date archive | `/day/2026-11-04`, `/week/2026-11-02`, `/month/2026-11`, `/calendar/2026/11`, `/events/month/2026-11`, `/news/2026/11` |
| Repeating path | `/more/more/`, `/a/b/a/b/`, any folder name repeated three times, more than 15 folders deep, addresses over 300 characters |
| Repeating URL pattern | While crawling, addresses that differ only by numbers (`/gallery/1/photo/1`, `/photo/2`, …) are followed only `--max-per-pattern` times |
| Login/admin | `/user/login`, `/user/password`, `/node/123/edit`, `/Shibboleth.sso/…`, `/wp-admin` |
| Search results | `/search`, `/site-search` |
| Feed/export | `/feed`, `/rss`, `/ical` |
| Print view | `/print/…`, `/printpdf/…` |

The rules are written so that real pages with dates or similar words in them are kept, for example individual events (`/node/6167`), `/commencement/2025`, `/news/2026/11/story-title`, the Northern Iowan index (`/publications/northern-iowan/2006-02-14/week-uni`) and `/its/password`. Single-number addresses such as Drupal's `/node/1234` are never limited by `--max-per-pattern`.

It also removes `;jsessionid=…` from addresses and ignores everything after `?`, which stops `?page=`, `?month=` and `?sort=` loops. With `--keep-query`, parameters that never identify a different page (`utm_…`, `sort`, `view`, `date`, `month`, `start`, session IDs and similar) are still removed.

Terminal lists how many addresses were left out for each reason, with an example of each. Check the examples; with `--out`, every left-out address is in the CSV.

### Install

Nothing to install: it uses only the Python standard library.

### Run

Copy `uni_page_count.py` to a folder, open Terminal in that folder and enter (with your own email address):

```powershell
python uni_page_count.py --contact your.name@uni.edu
```

For the fullest count, include the subdomains and save the list of pages:

```powershell
python uni_page_count.py --contact your.name@uni.edu --discover-subdomains --out uni_pages.csv
```

With `--discover-subdomains` there can be a few hundred hosts, so the run takes a while. Stop it with **Ctrl+C** at any time.

| Option | Meaning | Default |
|---|---|---|
| `--contact` | Your email address, sent with each request (required) | — |
| `--discover-subdomains` | Also count every uni.edu host listed in public certificate logs | Off |
| `--hosts` | Check only these hosts, e.g. `--hosts www.uni.edu library.uni.edu` | `uni.edu` and `www.uni.edu` |
| `--crawl` | Also follow links from each home page to find pages missing from the sitemaps | Off |
| `--max-pages` | Most pages to fetch with `--crawl` | `500` |
| `--max-per-pattern` | With `--crawl`, most addresses to follow that differ only by numbers | `25` |
| `--max-sitemaps` | Most sitemap files to read | `500` |
| `--delay` | Seconds between requests to the same host (at least 1) | `2.0` |
| `--keep-query` | Count addresses that differ only after `?` as separate pages | Off |
| `--include-traps` | Count calendar views and other trap addresses that sitemaps list (the crawl still never follows them) | Off |
| `--out` | Save the pages, and the addresses left out, to this CSV file | Not saved |
| `--top` | Rows to show in the host and section breakdowns | `20` |
| `--domain` | Count a different domain | `uni.edu` |
| `-v` | Show each sitemap as it is read, plus any skipped or failed requests | Off |

### Output

Terminal shows the number of unique pages, the number of files left out, the trap addresses left out (by reason, with an example of each), pages per host, the largest sections, and how many requests were made.

With `--out`, the CSV has one row per address: `url`, `host` and `status`, which is `counted` or `left out: <reason>`. Filter on `status` to review what was left out.

### Limitations

- **It undercounts.** It finds pages that are in a sitemap or, with `--crawl`, linked within `--max-pages` of a home page. Pages that are in neither are missed.
- **Hosts without a sitemap** contribute nothing unless you add `--crawl`.
- **Documents aren't counted.** PDFs, Office files, images and media are left out (use `pdf_archive_gap.py` for PDFs).
- **Trap rules are patterns, not certainty.** A real page can occasionally look like a trap (or the reverse). Check the examples in Terminal, and use `--include-traps` to see the count with sitemap-listed trap addresses included.
- **Query strings are merged.** `page?id=1` and `page?id=2` count as one page unless you add `--keep-query`.
- For an exact count, ask the web team for a report from the content management system.

## Monthly Archive-It statistics (`archiveit_monthly_stats.py`)

Totals UNI's Archive-It crawls by month (how many crawls ran, how many web pages were archived, and how much data in TB) and writes one row per month to a CSV, with a `TOTAL` row at the end. Useful for monthly reports and for tracking the Archive-It data budget.

### How it works

1. **Log in.** Uses an Archive-It login for UNI's account, and finds the account ID from that login.
2. **Read the crawls.** Asks the [Archive-It Partner API](https://support.archive-it.org/hc/en-us/articles/360032747311-Access-your-account-with-the-Archive-It-Partner-API) (`crawl_job` table) for every crawl in the account, with its document and data counts. These are the same figures as the crawl reports in the Archive-It web app.
3. **Total by month.** Each crawl counts in the month it **started** (or finished, with `--date-field end`). Test crawls are left out unless they were saved. Months with no crawls get a row of zeros.
4. **Stored WARCs (optional).** With `--wasapi` it also adds up the size of the WARC files stored for each month, through [WASAPI](https://support.archive-it.org/hc/en-us/articles/360015225051).

### Install

```powershell
python -m pip install requests
```

### Log in

1. Copy `archiveit_credentials.properties.example` to `archiveit_credentials.properties` in the folder you run the script from.
2. Open it in Notepad and fill in an Archive-It username and password for UNI's account.

`archiveit_credentials.properties` is listed in `.gitignore`, so it is never committed. Keep it private. Instead of the file, you can set the `ARCHIVEIT_USER` and `ARCHIVEIT_PASS` environment variables; they take priority over the file:

```powershell
$env:ARCHIVEIT_USER = "your_username"
$env:ARCHIVEIT_PASS = "your_password"
```

### Run

```powershell
python archiveit_monthly_stats.py --month 2026-10                  # one month
python archiveit_monthly_stats.py --start 2026-01 --end 2026-12    # a range of months
python archiveit_monthly_stats.py                                  # the account's whole history
python archiveit_monthly_stats.py --start 2026-01 --by-collection  # one row per collection per month
```

| Option | Meaning | Default |
|---|---|---|
| `--month` | A single month, `YYYY-MM` | — |
| `--start` / `--end` | First and last month to include, `YYYY-MM` (both included) | The whole history |
| `--account` | Archive-It account ID, if it can't be found from the login (it's the number in the Archive-It web app's addresses) | Found from the login |
| `--date-field` | `start`: count each crawl in the month it started. `end`: the month it finished (crawls still running are left out) | `start` |
| `--include-tests` | Also count test crawls that weren't saved | Off |
| `--by-collection` | One row per collection per month, with the collection's name | Off |
| `--wasapi` | Add the size of the WARC files stored each month (slower) | Off |
| `-o`, `--output` | The CSV file to write | `archiveit_monthly_stats.csv` |

### Output

One row per month (per collection with `--by-collection`), then a `TOTAL` row:

| Column | Meaning |
|---|---|
| `month` | `YYYY-MM` |
| `collection_id`, `collection_name` | Only with `--by-collection` |
| `crawls` | Crawls counted in the month |
| `documents_new` | Web pages and other documents archived for the first time, or changed since the last crawl |
| `documents_duplicate` | Documents captured again unchanged (recorded, but not stored again) |
| `documents_total` | New plus duplicate |
| `urls_discovered` | Addresses the crawler found |
| `urls_queued` | Addresses still waiting when the crawl stopped (e.g. at its time or data limit) |
| `download_failures` | Addresses that couldn't be downloaded |
| `new_data_bytes`, `new_data_GB`, `new_data_TB` | Size of the new documents |
| `total_data_TB` | Size of new plus duplicate documents |
| `warc_compressed_bytes`, `warc_compressed_TB` | Size of the compressed WARC files written: the storage used |
| `wasapi_stored_bytes`, `wasapi_stored_TB` | Only with `--wasapi` (not with `--by-collection`): size of the WARC files stored for crawls that started that month |

TB and GB are decimal (1 TB = 1,000,000,000,000 bytes), as Archive-It uses for data budgets.

### Tests

```powershell
python -m unittest discover -s tests
```

They run against a fake Archive-It, so they need no login or network.

### Limitations

- **Only accounts you can log in to.** The Partner API needs an Archive-It login; the public pages on archive-it.org don't give these figures.
- **Figures are the crawl reports' figures.** If a column is all zeros, Archive-It may have renamed the field: compare a month with the crawl reports in the Archive-It web app.
- **A crawl counts in one month.** A crawl that runs across the end of a month is counted in the month it started (or finished, with `--date-field end`), not split.
- **Rate limits.** If Archive-It answers *429 Too Many Requests*, wait a few minutes and run it again.
