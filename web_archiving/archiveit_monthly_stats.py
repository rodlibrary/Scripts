#!/usr/bin/env python3
"""
archiveit_monthly_stats.py
--------------------------
Compile monthly crawl statistics for an Archive-It account into a CSV.

Data source: the Archive-It Partner API (https://partner.archive-it.org/api/),
table `crawl_job`. One row in the output = one calendar month, summing every
crawl that *started* in that month (or finished, with --date-field end).

Optionally (--wasapi) it also totals the size of the WARC files actually
stored for each month, via WASAPI (https://warcs.archive-it.org/wasapi/v1/).

Requirements:  Python 3.8+,  pip install requests

Credentials (an Archive-It login for your account) are read from
archiveit_credentials.properties in the folder you run it from (copy
archiveit_credentials.properties.example), or from the ARCHIVEIT_USER and
ARCHIVEIT_PASS environment variables, which take priority over the file.

Examples:
    python archiveit_monthly_stats.py                        # whole history
    python archiveit_monthly_stats.py --month 2026-10        # one month
    python archiveit_monthly_stats.py --start 2025-01 --end 2025-12
    python archiveit_monthly_stats.py --account 1234 --wasapi -o stats.csv
    python archiveit_monthly_stats.py --by-collection        # month x collection
"""

import argparse
import configparser
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime

try:
    import requests
except ImportError:
    sys.exit("This script needs the 'requests' package:  pip install requests")

API = "https://partner.archive-it.org/api"
WASAPI = "https://warcs.archive-it.org/wasapi/v1/webdata"

TB = 1000 ** 4   # decimal terabyte (what Archive-It uses for data budgets)
GB = 1000 ** 3

# crawl_job fields that get summed per month. Missing fields are treated as 0,
# so the script keeps working if Archive-It adds/drops a field.
SUM_FIELDS = [
    "novel_count",            # new documents (URLs) archived
    "duplicate_count",        # documents unchanged since a previous crawl
    "warc_revisit_count",     # revisit records written for duplicates
    "discovered_count",       # URLs the crawler discovered
    "queued_count",           # URLs still queued when the crawl stopped
    "download_failures",
    "novel_bytes",            # new data archived (uncompressed)
    "duplicate_bytes",        # duplicate data (not re-stored)
    "warc_compressed_bytes",  # size of the WARC files written (storage)
]


# --------------------------------------------------------------------------- #
# API helpers
# --------------------------------------------------------------------------- #
CREDENTIALS_FILE = "archiveit_credentials.properties"


def load_credentials(path=CREDENTIALS_FILE):
    """Environment variables first, then the properties file."""
    user, pw = os.environ.get("ARCHIVEIT_USER"), os.environ.get("ARCHIVEIT_PASS")
    if not (user and pw) and os.path.exists(path):
        cp = configparser.ConfigParser()
        cp.read(path, encoding="utf-8")
        sec = cp["credentials"] if cp.has_section("credentials") else {}
        user = user or sec.get("username", "").strip()
        pw = pw or sec.get("password", "").strip()
    if not (user and pw):
        if os.path.exists(path + ".txt"):
            sys.exit(f"Found {path}.txt - rename it to {path} (Windows hides the .txt).")
        sys.exit(f"No Archive-It login found. Copy {path}.example to {path} and fill it in,\n"
                 "or set the ARCHIVEIT_USER and ARCHIVEIT_PASS environment variables.")
    return user, pw


def make_session(user, password):
    s = requests.Session()
    s.auth = (user, password)
    s.headers["Accept"] = "application/json"
    s.headers["User-Agent"] = "archiveit-monthly-stats/1.0"
    return s


def api_get(session, table, **params):
    """GET a Partner API table. limit=-1 returns all rows (no paging)."""
    params.setdefault("limit", -1)
    params.setdefault("format", "json")
    r = session.get(f"{API}/{table}", params=params, timeout=300)
    if r.status_code == 401:
        sys.exit("Archive-It rejected the credentials (HTTP 401).")
    if r.status_code == 429:
        sys.exit("Archive-It rate limit hit (HTTP 429). Wait a bit and retry.")
    r.raise_for_status()
    return r.json()


def detect_account(session):
    """Find the account ID belonging to the logged-in user."""
    try:
        data = api_get(session, "auth")
    except requests.HTTPError:
        data = None
    rows = data if isinstance(data, list) else [data] if data else []
    for row in rows:
        acct = row.get("account")
        if isinstance(acct, dict):
            acct = acct.get("id")
        if acct:
            return int(acct)
    sys.exit("Couldn't work out your account ID automatically; pass --account <id>.\n"
             "(It's the number in the Archive-It web app URL, or ask Archive-It support.)")


def collection_names(session, account):
    rows = api_get(session, "collection", account=account)
    return {c["id"]: c.get("name", "") for c in rows}


def fetch_crawls(session, account, date_field, start, end):
    params = {"account": account}
    # Server-side date filtering (Django-style lookups); we filter again locally.
    if start:
        params[f"{date_field}__gte"] = start.strftime("%Y-%m-%dT00:00:00Z")
    if end:
        params[f"{date_field}__lt"] = end.strftime("%Y-%m-%dT00:00:00Z")
    try:
        return api_get(session, "crawl_job", **params)
    except requests.HTTPError:
        # Fall back to unfiltered if the date lookups aren't accepted.
        return api_get(session, "crawl_job", account=account)


def fetch_wasapi_bytes(session, account, start, end):
    """Sum stored WARC sizes per month (by crawl-start) via WASAPI."""
    totals = defaultdict(int)
    params = {"page_size": 2000}
    if start:
        params["crawl-start-after"] = start.strftime("%Y-%m-%d")
    if end:
        params["crawl-start-before"] = end.strftime("%Y-%m-%d")
    url = WASAPI
    while url:
        r = session.get(url, params=params, timeout=300)
        r.raise_for_status()
        page = r.json()
        for f in page.get("files", []):
            if f.get("account") not in (None, account):
                continue
            ts = f.get("crawl-start") or f.get("crawl-time")
            if ts:
                totals[ts[:7]] += f.get("size") or 0
        url, params = page.get("next"), None   # 'next' already has the params
    return totals


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def parse_month(s):
    return datetime.strptime(s, "%Y-%m")


def next_month(d):
    return d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)


def is_test_crawl(job):
    # Test crawls aren't kept unless saved; saved ones count toward the archive.
    if not (job.get("test") or job.get("type") == "TEST"):
        return False
    return str(job.get("test_crawl_state") or "").upper() not in ("SAVED", "SAVE")


def aggregate(jobs, date_field, start, end, include_tests, by_collection):
    out = defaultdict(lambda: defaultdict(int))
    for job in jobs:
        ts = job.get(date_field)
        if not ts:
            continue                       # e.g. crawl still running (no end date)
        month = ts[:7]
        m = parse_month(month)
        if (start and m < start) or (end and m >= end):
            continue
        if not include_tests and is_test_crawl(job):
            continue
        coll = job.get("collection")
        if isinstance(coll, dict):
            coll = coll.get("id")
        key = (month, coll) if by_collection else (month,)
        row = out[key]
        row["crawls"] += 1
        for f in SUM_FIELDS:
            row[f] += job.get(f) or 0
        row["total_data_in_kbs"] += job.get("total_data_in_kbs") or 0
    return out


def fill_empty_months(out, start, end, by_collection):
    """Make sure months with no crawls still appear (with zeros)."""
    if by_collection or not out:
        return
    months = sorted(k[0] for k in out)
    m = start or parse_month(months[0])
    stop = end or next_month(parse_month(months[-1]))
    while m < stop:
        out[(m.strftime("%Y-%m"),)]   # touching the defaultdict creates the row
        m = next_month(m)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Monthly Archive-It crawl totals -> CSV")
    ap.add_argument("--account", type=int, help="Archive-It account ID (auto-detected if omitted)")
    ap.add_argument("--month", help="a single month, YYYY-MM (same as --start M --end M)")
    ap.add_argument("--start", help="first month to include, YYYY-MM")
    ap.add_argument("--end", help="last month to include, YYYY-MM (inclusive)")
    ap.add_argument("--date-field", choices=["start", "end"], default="start",
                    help="assign crawls to the month they started (default) or finished")
    ap.add_argument("--include-tests", action="store_true",
                    help="also count unsaved test crawls")
    ap.add_argument("--by-collection", action="store_true",
                    help="one row per month per collection instead of per month")
    ap.add_argument("--wasapi", action="store_true",
                    help="add stored-WARC totals from WASAPI (slower)")
    ap.add_argument("-o", "--output", default="archiveit_monthly_stats.csv")
    args = ap.parse_args()

    if args.month:
        args.start = args.end = args.month
    user, pw = load_credentials()

    start = parse_month(args.start) if args.start else None
    end = next_month(parse_month(args.end)) if args.end else None
    date_field = "original_start_date" if args.date_field == "start" else "processing_end_date"

    s = make_session(user, pw)
    account = args.account or detect_account(s)
    print(f"Account {account}: fetching crawl jobs ...", file=sys.stderr)
    jobs = fetch_crawls(s, account, date_field, start, end)
    print(f"  {len(jobs)} crawl jobs retrieved", file=sys.stderr)

    out = aggregate(jobs, date_field, start, end, args.include_tests, args.by_collection)
    fill_empty_months(out, start, end, args.by_collection)

    names = collection_names(s, account) if args.by_collection else {}
    wasapi = {}
    if args.wasapi:
        print("Fetching WARC sizes from WASAPI ...", file=sys.stderr)
        wasapi = fetch_wasapi_bytes(s, account, start, end)

    header = ["month"]
    if args.by_collection:
        header += ["collection_id", "collection_name"]
    header += [
        "crawls",
        "documents_new", "documents_duplicate", "documents_total",
        "urls_discovered", "urls_queued", "download_failures",
        "new_data_bytes", "new_data_GB", "new_data_TB",
        "total_data_TB",
        "warc_compressed_bytes", "warc_compressed_TB",
    ]
    if args.wasapi and not args.by_collection:
        header += ["wasapi_stored_bytes", "wasapi_stored_TB"]

    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        grand = defaultdict(int)
        for key in sorted(out, key=lambda k: (k[0], k[1] if len(k) > 1 and k[1] is not None else 0)):
            r = out[key]
            total_bytes = r["novel_bytes"] + r["duplicate_bytes"] or r["total_data_in_kbs"] * 1024
            row = [key[0]]
            if args.by_collection:
                row += [key[1], names.get(key[1], "")]
            row += [
                r["crawls"],
                r["novel_count"], r["duplicate_count"], r["novel_count"] + r["duplicate_count"],
                r["discovered_count"], r["queued_count"], r["download_failures"],
                r["novel_bytes"], round(r["novel_bytes"] / GB, 3), round(r["novel_bytes"] / TB, 6),
                round(total_bytes / TB, 6),
                r["warc_compressed_bytes"], round(r["warc_compressed_bytes"] / TB, 6),
            ]
            if args.wasapi and not args.by_collection:
                b = wasapi.get(key[0], 0)
                row += [b, round(b / TB, 6)]
                grand["wasapi"] += b
            w.writerow(row)
            for f in SUM_FIELDS + ["crawls"]:
                grand[f] += r[f]
            grand["total_bytes"] += total_bytes

        # Grand-total row
        if out:
            g = grand
            row = ["TOTAL"] + (["", ""] if args.by_collection else []) + [
                g["crawls"],
                g["novel_count"], g["duplicate_count"], g["novel_count"] + g["duplicate_count"],
                g["discovered_count"], g["queued_count"], g["download_failures"],
                g["novel_bytes"], round(g["novel_bytes"] / GB, 3), round(g["novel_bytes"] / TB, 6),
                round(g["total_bytes"] / TB, 6),
                g["warc_compressed_bytes"], round(g["warc_compressed_bytes"] / TB, 6),
            ]
            if args.wasapi and not args.by_collection:
                row += [g["wasapi"], round(g["wasapi"] / TB, 6)]
            w.writerow(row)

    print(f"Wrote {len(out)} rows to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
