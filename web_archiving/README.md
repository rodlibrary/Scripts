# Web archiving

Find PDFs on uni.edu that aren't yet captured in UNI's [Archive-It](https://archive-it.org/home/uni) collections, review them in Excel, and turn the ones you approve into seed lists ready to paste into Archive-It.

| File | What it does | Runs on |
|---|---|---|
| [`pdf_archive_gap.py`](pdf_archive_gap.py) | Crawls uni.edu for PDF links, checks them against Archive-It, writes a review workbook and exports seed lists | Any computer with Python 3.8+ and `openpyxl` |
| [`example_pdf_comparison.xlsx`](example_pdf_comparison.xlsx) | A small example of the review workbook (made-up rows) showing the columns, dropdowns and highlighting | — |

## How it works

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

## Install

```powershell
python -m pip install openpyxl
```

Everything else is in the Python standard library.

## Run

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

## Review in Excel

Open `pdf_comparison.xlsx` and work on the **Review** sheet. Only edit the three yellow columns. Everything else is rewritten on each run, but these columns are read back and kept.

| Column | Values | Meaning |
|---|---|---|
| `appraisal` | `add` / `ignore` | Whether the PDF should be captured. Blank = not reviewed yet; unreviewed rows sort to the top. |
| `target_collection_id` | An Archive-It collection ID | The collection to add it to. The dropdown lists known collections (the **Collections** sheet); you can type another ID and click **Yes** at the warning. |
| `seed_type` | `One Page` / `One Page+` | **One Page** (or blank): the seed is the PDF itself, captured on its own. **One Page+**: the seed is the page the PDF was found on, captured with everything it links to one step away, so the PDF is archived in context. PDFs on the same page share one seed. |

`seeded_on` (grey) is filled in by the script when a row is exported, so it isn't exported twice. Clear it to export that row again.

The **Instructions** sheet in the workbook repeats this, with an example row.

**Save and close the workbook before running the script.** If it's open in Excel, the script stops straight away rather than after a long crawl.

## Export seed lists and add them to Archive-It

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

## Output

| File | Contents |
|---|---|
| `pdf_comparison.xlsx` | The review workbook: **Review**, **Collections** and **Instructions** sheets |
| `uni_pdfs.csv` | Every PDF link found on uni.edu, with the page it was found on and the date first seen (cumulative across runs) |
| `archiveit_pdfs.csv` | Every PDF captured in the checked Archive-It collections, with capture counts and first/last capture dates |
| `crawled_pages.csv` | Log of every page fetched (used to skip pages on the next run) |
| `crawl_queue.txt` | Pages found but not yet crawled (where the next run starts) |
| `seeds\` | Exported seed lists, one dated folder per export |

## Limitations

- **Only links ending in `.pdf` are found.** PDFs served from addresses without that extension are missed.
- **Only public Archive-It collections are visible.** Private collections need an export from your Archive-It account.
- **Pages already crawled aren't re-read**, so a new PDF link added to an old page is only found when that page is re-crawled. Use `--recrawl-days 90` (for example) for periodic check-ups.
- The crawl identifies itself to the web server as `UNI-PDF-Archive-Audit/1.0`. Increase `--delay` if it puts noticeable load on the site.
