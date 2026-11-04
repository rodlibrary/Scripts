"""
Read a CSV containing Archival Resource Keys (ARKs), match each one against
the ARK stored inside each asset's MODS descriptive metadata
(<mods:identifier type="ark">...</mods:identifier>), and write out the
matching Content API download URL for each.

Two ways to build the ark -> asset index are provided:

1. METADATA SCAN (build_ark_index_by_scan)
   Walks every asset in scope and opens its MODS document directly.
   Always works, no configuration required beyond the folder scope,
   but does one metadata fetch per asset so it's slow on large repositories.

2. INDEXED SEARCH (build_ark_index_by_search)
   Much faster -- queries Preservica's search index directly instead of
   opening every asset's metadata. Only works if the MODS ARK field has
   been indexed for search on your system. Run `list_ark_candidate_fields()`
   once first to find the exact indexed field name to use, and set
   MODS_ARK_INDEX_FIELD below to that value.

Requires: pip install pyPreservica
"""

import csv
from xml.etree import ElementTree
from pyPreservica import EntityAPI, ContentAPI, EntityType, only_assets

# ---- Configuration --------------------------------------------------

INPUT_CSV = "arks.csv"          # must contain a column with ARK values
INPUT_ARK_COLUMN = "ark"        # name of that column in the input CSV

OUTPUT_CSV = "ark_download_links.csv"

# Which method to use to build the ark -> asset index.
# "search" is much faster but requires the field to be indexed (see below).
# "scan" always works but is slow on large repositories.
INDEX_METHOD = "scan"   # "scan" or "search"

# --- Used by the metadata-scan method ---
SCOPE_FOLDER_REF = "your-collection-uuid-here"  # None = whole repository
MODS_SCHEMA_HINT = "mods"   # matched as a substring, case-insensitive

# --- Used by the indexed-search method ---
# The indexed field name for the MODS ark identifier, e.g. "mods.identifier".
# Run list_ark_candidate_fields() below to find the exact name on your system
# before using INDEX_METHOD = "search".
MODS_ARK_INDEX_FIELD = "mods.identifier"

# ---- Client setup -----------------------------------------------------

client = EntityAPI()
content = ContentAPI()
# Or, without a credentials.properties file:
# client = EntityAPI(username="you@example.com", password="secret",
#                     tenant="YOUR-TENANT", server="yourserver.preservica.com")
# content = ContentAPI(username="you@example.com", password="secret",
#                       tenant="YOUR-TENANT", server="yourserver.preservica.com")


def download_url_for_asset(reference: str) -> str:
    """
    Build the Content API download link for an asset reference.
    Note: this URL requires a valid Preservica-Access-Token header to
    actually fetch content -- it is not a public, unauthenticated link.
    """
    return f"https://{client.server}/api/content/download?id=sdb:IO|{reference}"


def list_ark_candidate_fields():
    """
    One-off helper: print every indexed field whose name looks related to
    MODS or identifiers, so you can find the exact field name to filter on.
    Run this once interactively, e.g.:
        python -c "from preservica_ark_download_urls import list_ark_candidate_fields; list_ark_candidate_fields()"
    """
    fields = content.indexed_fields()
    print("Indexed fields that might be the MODS ARK field:")
    for name, schema_uri in fields.items():
        if "mods" in name.lower() or "ark" in name.lower() or "identifier" in name.lower():
            print(f"  {name}  ({schema_uri})")


# ---- Method 1: metadata scan -------------------------------------------

def ark_from_mods(asset) -> str | None:
    """
    Look through an asset's descriptive metadata for a MODS document and
    return the value of <identifier type="ark">, or None if not present.
    Namespace-agnostic: matches on local element name so it doesn't
    matter which MODS namespace prefix was used.
    """
    for url, schema in asset.metadata.items():
        if MODS_SCHEMA_HINT not in schema.lower():
            continue
        xml_string = client.metadata(url)
        root = ElementTree.fromstring(xml_string)
        for elem in root.iter():
            local_name = elem.tag.rsplit("}", 1)[-1]  # strip namespace
            if local_name == "identifier" and elem.attrib.get("type", "").lower() == "ark":
                if elem.text:
                    return elem.text.strip()
    return None


def build_ark_index_by_scan():
    index = {}
    scanned = 0

    if SCOPE_FOLDER_REF is None:
        print("WARNING: no SCOPE_FOLDER_REF set -- scanning the entire "
              "repository. This can be slow on a large system.")

    for entity in filter(only_assets, client.all_descendants(SCOPE_FOLDER_REF)):
        asset = client.asset(entity.reference)
        scanned += 1
        ark = ark_from_mods(asset)
        if ark:
            index[ark] = {"title": asset.title, "reference": asset.reference}
        if scanned % 100 == 0:
            print(f"  scanned {scanned} assets, {len(index)} ARKs found so far...")

    print(f"Finished scanning {scanned} assets, found {len(index)} ARKs")
    return index


# ---- Method 2: indexed search (faster) ----------------------------------

def build_ark_index_by_search():
    """
    Query the search index directly for every object that has a value in
    the MODS ark field, instead of opening each asset's metadata document.
    Requires MODS_ARK_INDEX_FIELD to be a real indexed field name -- run
    list_ark_candidate_fields() first if you're not sure of it.
    """
    index = {}
    count = 0

    # Restrict to Information Objects (Assets) and require the ark field
    # to be present; leaving its filter value as "" means "return this
    # field, don't restrict by value" -- every row returned will have it.
    rows = content.search_index_filter_list(
        query="xip.document_type:IO",
        filter_values={
            "xip.reference": "",
            "xip.title": "",
            MODS_ARK_INDEX_FIELD: "",
        },
    )

    for row in rows:
        ark = row.get(MODS_ARK_INDEX_FIELD)
        if not ark:
            continue
        index[ark] = {"title": row.get("xip.title", ""), "reference": row["xip.reference"]}
        count += 1
        if count % 100 == 0:
            print(f"  indexed {count} ARKs so far...")

    print(f"Finished search, found {len(index)} ARKs")
    return index


# ---- Shared driver ------------------------------------------------------

def read_arks(path: str, column: str):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if column not in reader.fieldnames:
            raise SystemExit(
                f"Column '{column}' not found in {path}. "
                f"Available columns: {reader.fieldnames}"
            )
        for row in reader:
            ark = row[column].strip()
            if ark:
                yield ark


def main():
    print(f"Building ARK index using method: {INDEX_METHOD}")
    if INDEX_METHOD == "search":
        ark_index = build_ark_index_by_search()
    elif INDEX_METHOD == "scan":
        ark_index = build_ark_index_by_scan()
    else:
        raise SystemExit(f"Unknown INDEX_METHOD: {INDEX_METHOD!r} (use 'scan' or 'search')")

    rows = []
    for ark in read_arks(INPUT_CSV, INPUT_ARK_COLUMN):
        match = ark_index.get(ark)
        if match is None:
            print(f"NOT FOUND: {ark}")
            rows.append({"ark": ark, "title": "", "reference": "",
                         "download_url": "", "status": "not found"})
            continue

        url = download_url_for_asset(match["reference"])
        print(f"{ark} -> {match['title']}: {url}")
        rows.append({
            "ark": ark,
            "title": match["title"],
            "reference": match["reference"],
            "download_url": url,
            "status": "ok",
        })

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["ark", "title", "reference", "download_url", "status"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
