"""
Collect download URLs for every asset in a Preservica collection (folder),
including all nested sub-folders, and write them to a CSV file.

Requires: pip install pyPreservica

Auth: pyPreservica will read credentials from a credentials.properties file
in the working directory, or you can pass them explicitly to EntityAPI()
(see commented-out example below).
"""

import csv
from pyPreservica import EntityAPI, Folder, Asset, EntityType

# ---- Configuration -------------------------------------------------------

# The UUID of the top-level Collection/Folder you want to crawl.
COLLECTION_REF = "your-collection-uuid-here"

OUTPUT_CSV = "asset_download_links.csv"

# ---- Client setup ----------------------------------------------------

client = EntityAPI()
# Or, without a credentials.properties file:
# client = EntityAPI(username="you@example.com", password="secret",
#                     tenant="YOUR-TENANT", server="yourserver.preservica.com")


def download_url_for_asset(asset: Asset) -> str:
    """
    Build the Content API download link for an asset.
    Note: this URL requires a valid Preservica-Access-Token header to
    actually fetch content -- it is not a public, unauthenticated link.
    """
    return f"https://{client.server}/api/content/download?id=sdb:IO|{asset.reference}"


def collect_assets(folder_ref: str):
    """
    Recursively walk a folder and yield every Asset found beneath it,
    at any depth.
    """
    for entity in client.descendants(folder_ref):
        if entity.entity_type == EntityType.ASSET:
            # descendants() gives lightweight entries; fetch the full
            # Asset object so we have title/reference etc.
            yield client.asset(entity.reference)


def main():
    rows = []
    top = client.folder(COLLECTION_REF)
    print(f"Crawling collection: {top.title} ({top.reference})")

    for asset in collect_assets(COLLECTION_REF):
        url = download_url_for_asset(asset)
        rows.append({
            "title": asset.title,
            "reference": asset.reference,
            "download_url": url,
        })
        print(f"  {asset.title}: {url}")

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["title", "reference", "download_url"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} asset links to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()