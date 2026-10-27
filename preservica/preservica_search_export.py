#!/usr/bin/env python3
"""
Export selected metadata fields from Preservica to CSV using the search API.

The search API returns values straight from Preservica's search index, which
makes it much faster than fetching each record. It can only return
fields that are indexed, so start by listing the fields
your instance offers:

    python preservica_search_export.py --list-fields

That writes indexed_fields.csv. Choose the fields you want and put them in
fields.txt, one per line, in the column order you want:

    xip.title
    xip.description
    oai_dc.creator
    oai_dc.date
    oai_dc.subject
    xip.security_descriptor = open      <- "= value" restricts results
    oai_dc.rights = *                   <- "*" means "has any value"

Then run the export:

    python preservica_search_export.py --folder <folder-UUID>
    python preservica_search_export.py --type all --output "dc_export_{date}.csv"

Requires:  pip install pyPreservica
Credentials: a credentials.properties file next to the script
    [credentials]
    username=you@example.edu
    password=********
    server=us.preservica.com
    tenant=YOURTENANT
"""

import argparse
import csv
import datetime as dt
import logging
import os
import sys
import time

from pyPreservica import ContentAPI

MULTI_VALUE_SEP = " | "
TYPE_FILTERS = {"assets": "IO", "folders": "SO", "all": ""}
log = logging.getLogger("preservica_search_export")


def read_fields(path):
    """Parse fields.txt into an ordered {field: filter_value} dict."""
    fields = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            name, _, value = line.partition("=")
            fields[name.strip()] = value.strip()
    if not fields:
        sys.exit(f"No fields found in {path}")
    return fields


def cell(value):
    """Flatten multi-valued index fields (returned as lists) into one cell."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return MULTI_VALUE_SEP.join(str(v) for v in value if v not in (None, ""))
    return str(value)


def list_fields(client, out_path):
    fields = client.indexed_fields()
    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "schema_uri"])
        for name in sorted(fields):
            w.writerow([name, fields[name]])
    log.info("Wrote %d indexed fields to %s", len(fields), out_path)


def progress(value):
    done, total = value.split(":")
    log.info("Retrieved %s of %s", done, total)


def main():
    ap = argparse.ArgumentParser(description="Export Preservica search-index fields to CSV.")
    ap.add_argument("--list-fields", action="store_true",
                    help="Write the instance's indexed fields to indexed_fields.csv and exit.")
    ap.add_argument("--fields", default="fields.txt", help="Field list file (default fields.txt).")
    ap.add_argument("--folder", help="Only records underneath this folder (UUID).")
    ap.add_argument("--type", choices=TYPE_FILTERS, default="assets",
                    help="Which records to export (default: assets).")
    ap.add_argument("--query", default="%", help='Search query (default "%%" = everything).')
    ap.add_argument("--output", default="preservica_export_{date}.csv",
                    help="Output CSV. {date} becomes YYYY-MM-DD.")
    ap.add_argument("--credentials", default="credentials.properties")
    ap.add_argument("--page-size", type=int, default=100)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = ContentAPI(credentials_path=args.credentials)

    if args.list_fields:
        list_fields(client, "indexed_fields.csv")
        return 0

    selected = read_fields(args.fields)
    columns = ["xip.reference"] + [f for f in selected if f != "xip.reference"]

    # Filters used to scope the search but not shown as columns unless asked for
    filters = {"xip.reference": "", **selected}
    if TYPE_FILTERS[args.type] and not selected.get("xip.document_type"):
        filters["xip.document_type"] = TYPE_FILTERS[args.type]
    if args.folder:
        filters["xip.parent_hierarchy"] = args.folder

    out_path = args.output.replace("{date}", dt.date.today().isoformat())
    tmp_path = out_path + ".partial"
    client.search_callback(progress)

    for attempt in range(1, 4):
        try:
            rows = [[cell(row.get(c)) for c in columns]
                    for row in client.search_index_filter_list(
                        args.query, args.page_size, filters)]
            # Sort by reference so repeated exports list records in the same
            # order and a diff of two exports shows only real changes.
            rows.sort(key=lambda r: r[0])
            with open(tmp_path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow(columns)
                writer.writerows(rows)
            count = len(rows)
            break
        except Exception as exc:
            if attempt == 3:
                log.error("Export failed: %s", exc)
                return 1
            log.warning("Search failed (%s); retrying from the start in %ss", exc, 30 * attempt)
            time.sleep(30 * attempt)

    os.replace(tmp_path, out_path)  # only replace the file once the export is complete
    log.info("Wrote %d rows to %s", count, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
