import xml.etree.ElementTree as ET
import requests
from pyPreservica import EntityAPI, AdminAPI

# Custom Metadata Schema Properties
SCHEMA_URI = "http://digital.library.uni.edu"
ELEMENT_NAME = f"{{{SCHEMA_URI}}}downloadCount"

def get_session_token():
    """Initializes pyPreservica and extracts the active bearer token for custom requests."""
    # Instantiating EntityAPI automatically reads credentials.properties
    entity_client = EntityAPI()
    admin_client = AdminAPI()
    
    # Extract the token and server attributes populated by the SDK's internal authentication
    token = entity_client.token
    server = entity_client.server
    
    return entity_client, token, server

def fetch_download_counts_from_audit_logs(server, token, start_date="2026-01-01T00:00:00Z"):
    """
    Queries the Preservica System Audit Logs for public delivery/download events.
    Returns a dict mapping asset/entity UUIDs to download counts.
    """
    url = f"https://{server}/api/admin/audit-logs"
    headers = {
        "Preservica-Access-Token": token,
        "Accept": "application/json"
    }
    params = {
        "fromDate": start_date,
        "pageSize": 100
    }
    
    download_totals = {}
    
    while url:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 401:
            raise PermissionError("Token expired or unauthorized.")
        response.raise_for_status()
        
        data = response.json()
        
        # Enterprise Audit records vary; filter for Asset Delivery or public access actions
        for record in data.get("auditRecords", []):
            action = record.get("actionType")
            entity_ref = record.get("entityReference")
            
            # Match actions representing download executions (e.g., Deliver Bitstream, Get Content)
            if action in ["DELIVER_BITSTREAM", "DOWNLOAD_ASSET"] and entity_ref:
                download_totals[entity_ref] = download_totals.get(entity_ref, 0) + 1
        
        # Handle pagination links provided by Preservica pagination bodies
        url = data.get("nextPageLink")
        params = None  # Params are embedded in the next link url strings
        
    return download_totals

def update_asset_metadata(entity_client, asset_uuid, count):
    """
    Fetches, updates, or creates the analytics XML block for a specific asset.
    """
    try:
        asset = entity_client.asset(asset_uuid)
    except Exception as e:
        print(f"Skipping: Asset {asset_uuid} not found or inaccessible. Error: {e}")
        return

    existing_metadata_url = None
    
    # Search the asset's existing metadata fragments for our target namespace
    for url, schema in asset.metadata.items():
        if schema == SCHEMA_URI:
            existing_metadata_url = url
            break

    if existing_metadata_url:
        # Schema already exists on object -> Retrieve and update the element
        xml_string = entity_client.metadata(existing_metadata_url)
        xml_root = ET.fromstring(xml_string)
        
        field = xml_root.find(f".//{ELEMENT_NAME}")
        if field is not None:
            field.text = str(count)
        else:
            # Sub-element doesn't exist yet, append it safely
            new_element = ET.SubElement(xml_root, ELEMENT_NAME)
            new_element.text = str(count)
            
        updated_xml = ET.tostring(xml_root, encoding='UTF-8', xml_declaration=True).decode("utf-8")
        entity_client.update_metadata(asset, SCHEMA_URI, updated_xml)
        print(f"Updated existing count to {count} for Asset: {asset_uuid}")
        
    else:
        # Schema fragment does not exist -> Construct fresh XML block and attach
        root = ET.Element(f"{{{SCHEMA_URI}}}analytics")
        count_elem = ET.SubElement(root, ELEMENT_NAME)
        count_elem.text = str(count)
        
        fresh_xml = ET.tostring(root, encoding='UTF-8', xml_declaration=True).decode("utf-8")
        # Attaches a new fragment to the asset using the schema namespace definition
        entity_client.add_metadata(asset, SCHEMA_URI, fresh_xml)
        print(f"Created fresh analytics fragment with count {count} for Asset: {asset_uuid}")

def main():
    print("Authenticating session via pyPreservica...")
    entity_client, token, server = get_session_token()
    
    print("Parsing audit logs for public download metrics...")
    # Adjust start date to limit the scope of your batch analysis
    counts = fetch_download_counts_from_audit_logs(server, token, start_date="2026-01-01T00:00:00Z")
    
    print(f"Found download actions across {len(counts)} unique objects. Syncing to metadata blocks...")
    for asset_uuid, total_downloads in counts.items():
        update_asset_metadata(entity_client, asset_uuid, total_downloads)
        
    print("Sync complete.")

if __name__ == "__main__":
    main()
