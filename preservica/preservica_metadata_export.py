#!/usr/bin/env python3
"""
Export Preservica metadata to CSV.

Walks a Preservica folder (or the whole repository), fetches each entity's
core fields, external identifiers, and every attached descriptive metadata
document (Dublin Core, MODS, custom schemas, ...), flattens the XML into
columns, and writes one row per entity to a CSV file.

Requires:  pip install pyPreservica

Credentials: put a credentials.properties file next to this script:

    [credentials]
    username=you@example.edu
    password=********
    server=us.preservica.com
    tenant=YOURTENANT

(or set PRESERVICA_USERNAME / PRESERVICA_PASSWORD / PRESERVICA_SERVER /
PRESERVICA_TENANT environment variables, which pyPreservica also reads).

Examples:
    # Everything under one folder, assets only
    python preservica_metadata_export.py --folder 9bad5acf-e7a1-458a-927d-2d1e7f15974d

    # Whole repository, assets and folders, dated output file
    python preservica_metadata_export.py --include-folders --output "export_{date}.csv"

    # Only Dublin Core
    python preservica_metadata_export.py --folder <ref> --schema http://www.openarchives.org/OAI/2.0/oai_dc/
"""

import argparse
import csv
import datetime as dt
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

from pyPreservica import EntityAPI, only_assets

# Common namespace URIs -> short prefixes used in column names.
# Anything not listed gets a prefix derived from the URI.
KNOWN_PREFIXES = {
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://purl.org/dc/terms/": "dcterms",
    "http://www.openarchives.org/OAI/2.0/oai_dc/": "oai_dc",
    "http://www.loc.gov/mods/v3": "mods",
    "http://www.loc.gov/MARC21/slim": "marc",
    "http://www.w3.org/2001/XMLSchema-instance": "xsi",
}

CORE_COLUMNS = [
    "reference", "entity_type", "title", "description",
    "security_tag", "parent", "identifiers", "metadata_schemas",
]

MULTI_VALUE_SEP = " | "
log = logging.getLogger("preservica_export")


def split_tag(tag):
    """'{uri}local' -> (uri, local)."""
    if tag.startswith("{"):
        uri, local = tag[1:].split("}", 1)
        return uri, local
    return "", tag


def prefix_for(uri):
    if not uri:
        return ""
    if uri in KNOWN_PREFIXES:
        return KNOWN_PREFIXES[uri]
    # Derive something readable from the last path segment of the URI
    seg = uri.rstrip("/#").replace("#", "/").split("/")[-1] or "ns"
    KNOWN_PREFIXES[uri] = seg
    return seg


def col_name(tag):
    uri, local = split_tag(tag)
    p = prefix_for(uri)
    return f"{p}:{local}" if p else local


def flatten_xml(xml_text):
    """
    Turn an XML metadata document into {column: [values]}.

    Leaf elements become columns named by their path from the document root
    (root element omitted), e.g. 'dc:title' or 'mods:titleInfo/mods:title'.
    Attributes on leaves are added as 'path@attr'. Repeated elements are
    collected as multiple values.
    """
    out = defaultdict(list)
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as exc:
        log.warning("Could not parse metadata XML: %s", exc)
        return out

    def walk(elem, path):
        children = list(elem)
        if not children:
            text = (elem.text or "").strip()
            if text:
                out[path].append(text)
            for attr, val in elem.attrib.items():
                if split_tag(attr)[0] == "http://www.w3.org/2001/XMLSchema-instance":
                    continue  # skip xsi:schemaLocation etc.
                out[f"{path}@{col_name(attr)}"].append(val)
            return
        for child in children:
            name = col_name(child.tag)
            walk(child, f"{path}/{name}" if path else name)

    walk(root, "")
    return out


def with_retry(fn, *args, attempts=4, **kwargs):
    """Retry transient API failures with backoff."""
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # pyPreservica raises various exception types
            if i == attempts - 1:
                raise
            wait = 2 ** i * 5
            log.warning("API call failed (%s); retrying in %ss", exc, wait)
            time.sleep(wait)


def build_row(client, entity, schema_filter):
    full = with_retry(client.entity, entity.entity_type, entity.reference)

    row = {
        "reference": full.reference,
        "entity_type": {"IO": "Asset", "SO": "Folder"}.get(
            getattr(full.entity_type, "value", ""), str(full.entity_type)),
        "title": full.title or "",
        "description": full.description or "",
        "security_tag": full.security_tag or "",
        "parent": full.parent or "",
    }

    try:
        ids = with_retry(client.identifiers_for_entity, full)
        row["identifiers"] = MULTI_VALUE_SEP.join(f"{t}={v}" for t, v in sorted(ids))
    except Exception as exc:
        log.warning("Identifiers failed for %s: %s", full.reference, exc)
        row["identifiers"] = ""

    schemas = []
    for url, schema in (full.metadata or {}).items():
        if schema_filter and schema not in schema_filter:
            continue
        schemas.append(schema)
        try:
            xml_text = with_retry(client.metadata, url)
        except Exception as exc:
            log.warning("Metadata fetch failed for %s (%s): %s", full.reference, schema, exc)
            continue
        for col, values in flatten_xml(xml_text).items():
            existing = row.get(col)
            joined = MULTI_VALUE_SEP.join(values)
            row[col] = f"{existing}{MULTI_VALUE_SEP}{joined}" if existing else joined
    row["metadata_schemas"] = MULTI_VALUE_SEP.join(schemas)
    return row


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

def main():
    ap = argparse.ArgumentParser(description="Export Preservica metadata to CSV.")
    ap.add_argument("--folder", help="Reference (UUID) of the folder to export. "
                                     "Omit to export the whole repository.")
    ap.add_argument("--output", default="preservica_metadata_{date}.csv",
                    help="Output CSV path. {date} is replaced with YYYY-MM-DD.")
    ap.add_argument("--include-folders", action="store_true",
                    help="Include folders as rows (default: assets only).")
    ap.add_argument("--schema", action="append", default=[],
                    help="Only include metadata with this schema URI. Repeatable.")
    ap.add_argument("--credentials", default="credentials.properties",
                    help="Path to credentials.properties.")
    ap.add_argument("--limit", type=int, help="Stop after N entities (for testing).")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    out_path = args.output.replace("{date}", dt.date.today().isoformat())
    c = load_credentials(args.credentials)
    client = EntityAPI(username=c["username"], password=c["password"], tenant=c["tenant"],
                       server=c["server"], two_fa_secret_key=c["two_fa"])
    log.info("Connected to %s", client.server)

    entities = client.all_descendants(args.folder) if args.folder else client.all_descendants()
    if not args.include_folders:
        entities = filter(only_assets, entities)

    rows, extra_cols, failures = [], [], 0
    seen_cols = set(CORE_COLUMNS)
    for n, ent in enumerate(entities, 1):
        if args.limit and n > args.limit:
            break
        try:
            row = build_row(client, ent, set(args.schema))
        except Exception as exc:
            failures += 1
            log.error("Skipping %s: %s", ent.reference, exc)
            continue
        for col in row:
            if col not in seen_cols:
                seen_cols.add(col)
                extra_cols.append(col)
        rows.append(row)
        if n % 100 == 0:
            log.info("Processed %d entities", n)

    columns = CORE_COLUMNS + sorted(extra_cols)
    # utf-8-sig so Excel opens accented characters correctly
    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    log.info("Wrote %d rows, %d columns to %s (%d failures)",
             len(rows), len(columns), out_path, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
