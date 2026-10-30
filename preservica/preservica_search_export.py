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

import re

from pyPreservica import ContentAPI, EntityAPI

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
MULTI_VALUE_SEP = " | "
TYPE_FILTERS = {"assets": "IO", "folders": "SO", "all": ""}
log = logging.getLogger("preservica_search_export")


CREDENTIAL_KEYS = {"username": "PRESERVICA_USERNAME", "password": "PRESERVICA_PASSWORD",
                   "tenant": "PRESERVICA_TENANT", "server": "PRESERVICA_SERVER"}


def load_credentials(path="credentials.properties"):
    """
    Read the Preservica login from environment variables or credentials.properties.

    A non-empty PRESERVICA_* environment variable wins. Otherwise the value comes
    from credentials.properties, looked for first in the current folder and then
    next to this script. Exits with a plain explanation if anything is missing.
    """
    import configparser
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [path] if os.path.isabs(path) else [os.path.abspath(path), os.path.join(here, path)]
    found = next((p for p in candidates if os.path.isfile(p)), None)

    file_values = {}
    if found:
        cfg = configparser.RawConfigParser(interpolation=None)
        try:
            cfg.read(found, encoding="utf-8-sig")
        except (configparser.Error, UnicodeError) as exc:
            sys.exit(f"Couldn't read {found}: {exc}\nThe first line must be [credentials], "
                     "and the file should be saved as plain text (UTF-8).")
        section = next((s for s in cfg.sections() if s.strip().lower() == "credentials"), None)
        if section is None:
            sys.exit(f"{found} has no [credentials] line. Add it as the very first line.")
        file_values = {k.lower(): v.strip().strip("\"'") for k, v in cfg[section].items()}

    creds, missing = {}, []
    for key, env in CREDENTIAL_KEYS.items():
        value = (os.environ.get(env) or "").strip() or file_values.get(key, "")
        if not value or "REPLACE_WITH" in value:
            missing.append(key)
        creds[key] = value
    creds["two_fa"] = (os.environ.get("PRESERVICA_2FA_TOKEN") or "").strip() or file_values.get("twofactortoken") or None

    if missing:
        looked = "\n".join(f"  {p}" for p in dict.fromkeys(candidates))
        if found:
            problem = f"{found} has no value for: {', '.join(missing)}."
        else:
            problem = f"No credentials.properties found. Looked in:\n{looked}"
            for p in candidates:
                if os.path.isfile(p + ".txt"):
                    problem += f"\nFound {p}.txt instead: rename it to remove the .txt ending."
        sys.exit(f"{problem}\nThe file needs a [credentials] line followed by username=, password=, "
                 "server= and tenant= lines (see credentials.properties.example).")
    if creds["server"].startswith("http") or "/" in creds["server"]:
        sys.exit(f"server should be just the host name, e.g. us.preservica.com (it is {creds['server']!r}).")
    return creds

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
    ap.add_argument("--allow-empty", action="store_true",
                    help="Write the CSV even if no records match (normally an empty result is an error).")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.folder:
        # Accept a pasted Preservica link or "SO_<uuid>" as well as a bare reference
        found = UUID_RE.search(args.folder)
        if not found:
            sys.exit(f"--folder / PRESERVICA_FOLDER doesn't contain a Preservica reference: {args.folder!r}")
        if found.group(0) != args.folder.strip():
            log.info("Using folder reference %s from %r", found.group(0), args.folder)
        args.folder = found.group(0).lower()

    c = load_credentials(args.credentials)
    client = ContentAPI(username=c["username"], password=c["password"], tenant=c["tenant"],
                        server=c["server"], two_fa_secret_key=c["two_fa"])

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

    if count == 0 and not args.allow_empty:
        os.remove(tmp_path)  # keep the previous export rather than replacing it with an empty one
        explain_empty(client, args.query, selected, args.folder, TYPE_FILTERS[args.type],
                      args.credentials)
        return 2

    os.replace(tmp_path, out_path)  # only replace the file once the export is complete
    log.info("Wrote %d rows to %s", count, out_path)
    return 0


def report(lines):
    """Log diagnostic lines and, on GitHub Actions, add them to the run summary."""
    for line in lines:
        log.error(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### No records were exported\n\n" + "\n".join(f"- {l}" for l in lines) + "\n")


def explain_empty(client, query, selected, folder, doc_type, credentials):
    """Run each part of the search on its own to find which part matches nothing."""
    def hits(f):
        try:
            return client.search_index_filter_hits(query, {"xip.reference": "", **f})
        except Exception as exc:
            log.warning("Check failed: %s", exc)
            return None

    lines = ["The search found no records, so the previous CSV was left unchanged."]
    visible = hits({})
    lines.append(f"Records this account can see in total: {visible}.")
    if visible == 0:
        lines.append("The account logged in but can't see any records. Ask your Preservica "
                     "administrator to give it read access (its roles and security tags) "
                     "to the records you want to export.")
        report(lines)
        return

    if doc_type:
        n = hits({"xip.document_type": doc_type})
        kind = "assets" if doc_type == "IO" else "folders"
        lines.append(f"Of those, {kind}: {n}.")

    if folder:
        n = hits({"xip.parent_hierarchy": folder})
        lines.append(f"Records under folder {folder}: {n}.")
        if n == 0:
            try:
                c = load_credentials(credentials)
                f = EntityAPI(username=c["username"], password=c["password"], tenant=c["tenant"],
                              server=c["server"], two_fa_secret_key=c["two_fa"]).folder(folder)
                lines.append(f'That reference is the folder "{f.title}", but the search index '
                             "has nothing under it that this account can see.")
            except Exception:
                lines.append("No folder with that reference is visible to this account. Check the "
                             "PRESERVICA_FOLDER value (or --folder): it should be a folder's "
                             "reference, not an asset's, and not a link to the public access site.")

    restricting = {k: v for k, v in selected.items() if v}
    for field, value in restricting.items():
        n = hits({field: value})
        lines.append(f'Records matching "{field} = {value}" in fields.txt: {n}.')
    report(lines)


if __name__ == "__main__":
    sys.exit(main())
