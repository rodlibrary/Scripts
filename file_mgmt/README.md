# File management

Small utilities for organizing batches of files and auditing links. The PowerShell scripts were written for the Student Newspapers OCR project, so each one has that folder path built in. **Change the path at the top of the script before you run it.**

| File | What it does | Runs on |
|---|---|---|
| [`organize.ps1`](organize.ps1) | Moves each file into a subfolder named after the first 18 characters of its file name | Windows (PowerShell) |
| [`combinePDFs.ps1`](combinePDFs.ps1) | Merges all PDFs in each folder into one PDF named after that folder | Windows (PowerShell + PDFtk) |
| [`removeemptyfolders.ps1`](removeemptyfolders.ps1) | Deletes empty subfolders | Windows (PowerShell) |
| [`find_scholarworks_links.py`](find_scholarworks_links.py) | Crawls uni.edu websites and saves every link to ScholarWorks in a CSV file | Any OS (Python 3) |

A typical sequence for the PowerShell scripts is `organize.ps1` → `combinePDFs.ps1` → `removeemptyfolders.ps1`.

> The PowerShell scripts move and delete files without asking. Test them on a copy of your folder first.

## organize.ps1

Groups files that share a name prefix. For example, `1965-03-12_studentnews_p01.pdf` and `1965-03-12_studentnews_p02.pdf` both go into a folder called `1965-03-12_student`.

1. Edit `$sourceDir` on line 1.
2. If your prefix isn't 18 characters long, change `Substring(0, 18)` on line 6.
3. Run `.\organize.ps1`.

Files with names shorter than 18 characters cause an error and stay where they are.

## combinePDFs.ps1

For every folder under the starting path, merges that folder's PDFs (in name order) into a single PDF named after the folder, saved inside that folder.

**Requirements:** [PDFtk Server](https://www.pdflabs.com/tools/pdftk-server/), installed at `C:\Program Files (x86)\PDFtk Server\bin\pdftk.exe`. If it's somewhere else, edit `$PDFtk` on line 2.

1. Edit the folder path on line 5.
2. Run `.\combinePDFs.ps1`.

Run it only once per batch. The merged PDF is saved next to the pages, so a second run would merge it in again.

## removeemptyfolders.ps1

Deletes every empty subfolder under the starting path.

1. Edit the folder path in the script.
2. Run `.\removeemptyfolders.ps1`.

It checks one level at a time, so a folder that only contains other empty folders may need a second run.

## find_scholarworks_links.py

Crawls uni.edu websites and records every link that points to [ScholarWorks](https://scholarworks.uni.edu/), so you can see which pages link to it. It starts at www.uni.edu, adds the pages listed in the site's sitemap, and follows links to every `*.uni.edu` subdomain.

**Requirements:** Python 3 and two packages:

```
pip install requests beautifulsoup4
```

**Run it:**

```
python find_scholarworks_links.py
```

Results are saved to `scholarworks_links.csv`, one row per link:

| Column | Contents |
|---|---|
| `source_page` | The uni.edu page the link is on |
| `page_title` | That page's title |
| `link_url` | The ScholarWorks URL |
| `link_text` | The link's visible text (or image alt text) |
| `html_tag` | The tag the link came from (`a`, `area`, `iframe` or `embed`) |
| `found_at` | When the link was found (UTC) |

Rows are saved as they're found, so you can stop the crawl with **Ctrl+C** and keep what it has collected.

**What it skips:**

- **ArchivesSpace:** `aspace.library.uni.edu` is never crawled, because its finding aids would slow the crawl down. Any ScholarWorks links that appear only in ArchivesSpace records won't be in the results. To skip other sites on every run, add them to `ALWAYS_EXCLUDED_HOSTS` near the top of the script.
- **Pages blocked by robots.txt.**
- **Files that aren't web pages,** such as PDFs, images and Office documents.
- **Calendar and paging URLs** that can go on forever. Use `--allow-traps` to crawl them anyway.
- **Sites that keep failing:** each page is tried up to 3 times. If 5 pages in a row on the same subdomain fail, that subdomain is skipped for the rest of the run. If 25 pages in a row fail across all sites (usually a dropped network connection), the crawl stops.

**Common options:**

| Option | What it does | Default |
|---|---|---|
| `--start URL [URL ...]` | Where to start crawling | `https://www.uni.edu/` |
| `--no-subdomains` | Stay on the start URLs' sites instead of all `*.uni.edu` subdomains | off |
| `--exclude-host HOST [HOST ...]` | Skip these sites for this run, e.g. `calendar.uni.edu` | none |
| `--output FILE` | CSV file to write | `scholarworks_links.csv` |
| `--max-pages N` | Stop after fetching this many pages | 5000 |
| `--max-depth N` | Only follow links this many steps from the start pages | no limit |
| `--delay SECONDS` | Pause between requests | 0.5 |
| `--ignore-query` | Treat URLs that differ only in `?query` as the same page | off |
| `--retries N` | Extra attempts per page after a timeout or server error | 2 |
| `--max-host-failures N` | Skip a site after this many failed pages in a row | 5 |
| `--max-consecutive-failures N` | Stop after this many failures in a row across all sites (0 = never) | 25 |
| `--no-sitemaps` | Don't add pages from sitemaps | off |
| `--verbose` | Print every page as it's crawled | off |

Run `python find_scholarworks_links.py --help` for the full list.

**Examples:**

```
# Crawl only the library website
python find_scholarworks_links.py --start https://library.uni.edu/ --no-subdomains

# A bigger crawl that skips the events calendar
python find_scholarworks_links.py --max-pages 20000 --exclude-host calendar.uni.edu
```

The whole uni.edu site is probably larger than the 5,000-page default. If the summary at the end says URLs are still queued, raise `--max-pages`. At the default delay, 5,000 pages takes roughly an hour.
