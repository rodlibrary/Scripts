# Using Preservica's API (Swagger) to read, download and update metadata

Preservica publishes interactive API documentation ("Swagger" pages) on every server. The pages list each request, and **Try it out** sends that request for real. This guide shows which pages to use, how to log in to them, and the requests for reading, downloading and updating metadata. [`preservica_metadata_api.py`](preservica_metadata_api.py) makes the same requests from the command line.

## 1. Use your own server's pages, not the demo

The `demo.preservica.com` pages describe Preservica's demonstration system. Your account doesn't exist there, so read them to learn the API, and send requests from the same pages on the UNI server:

| Page | What it covers |
|---|---|
| `https://us.preservica.com/api/accesstoken/documentation.html` | Logging in (getting a token) |
| `https://us.preservica.com/api/entity/documentation.html` | Assets, folders and their metadata: read, add, update, delete |
| `https://us.preservica.com/api/content/documentation.html` | Search (what `preservica_search_export.py` uses) |

Every request needs the tenant `UNIOWA` at login, and only shows records the account has **Read Metadata** permission for (see [Next steps](#5-next-steps)).

## 2. Log in and get a token

1. Open the **accesstoken** page and expand **POST /api/accesstoken/login**.
2. Click **Try it out**, enter `username`, `password` and `tenant` (`UNIOWA`), and click **Execute**.
3. The response contains `"token": "…"`. Copy that value. It's valid for **15 minutes**. After that, log in again, or call **POST /api/accesstoken/refresh** with the `refresh-token` from the same response (each refresh token works once).

Every other request sends the token in a header named **`Preservica-Access-Token`**. On the entity and content pages, paste it into the **Authorize** dialog if the page has one, or into the `Preservica-Access-Token` field shown on each request.

A `401` means the login was rejected: `incorrect.tenant` points to the tenant or a username that doesn't exist in it, and `Bad credentials` to the password.

## 3. The requests

Use `information-objects` for assets and `structural-objects` for folders. `{ref}` is the record's reference, the UUID after `IO_` or `SO_` in a Preservica link.

| Task | Request | Notes |
|---|---|---|
| Check the login works | `GET /api/entity/versiondetails/version` | Returns the version, e.g. `7.7.2`. Needed for the XML namespace when updating. |
| **Read** a record and list its metadata | `GET /api/entity/information-objects/{ref}` | The response lists each metadata fragment as `<Fragment schema="…">URL</Fragment>`. |
| **Download** one fragment | `GET` the fragment URL: `/api/entity/information-objects/{ref}/metadata/{metadataRef}` | The record (e.g. `<mods>`) is inside `<Content>`. |
| **Update** a fragment | `PUT` the same fragment URL | Body is a `MetadataContainer` (below). **Replaces the whole fragment.** |
| Add a new fragment | `POST /api/entity/information-objects/{ref}/metadata` | Same body without `<Ref>`. |
| Delete a fragment | `DELETE` the fragment URL | Can't be undone. |
| List a folder's contents | `GET /api/entity/structural-objects/{ref}/children?start=0&max=100` | Page through with `start`. |

**Update body.** Set `Content-Type: application/xml;charset=UTF-8`. The `xip` namespace version matches the server's major and minor version (7.7 → `v7.7`):

```xml
<xip:MetadataContainer xmlns:xip="http://preservica.com/XIP/v7.7"
                       schemaUri="http://www.loc.gov/mods/v3">
  <xip:Ref>{metadataRef}</xip:Ref>
  <xip:Entity>{ref}</xip:Entity>
  <xip:Content>
    <mods xmlns="http://www.loc.gov/mods/v3"> … the complete new record … </mods>
  </xip:Content>
</xip:MetadataContainer>
```

> **Try it out sends the request to the live system.** A `PUT` or `DELETE` there changes the real record immediately. Download the fragment first so you have a copy, and practise on a test record.

### The same calls with curl

```bash
TOKEN=$(curl -s -X POST https://us.preservica.com/api/accesstoken/login \
  -d username=SERVICE_ACCOUNT -d password=PASSWORD -d tenant=UNIOWA | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl -H "Preservica-Access-Token: $TOKEN" https://us.preservica.com/api/entity/information-objects/REF

curl -H "Preservica-Access-Token: $TOKEN" -o mods.xml FRAGMENT_URL

curl -X PUT -H "Preservica-Access-Token: $TOKEN" -H "Content-Type: application/xml;charset=UTF-8" \
  --data-binary @container.xml FRAGMENT_URL
```

## 4. The script

`preservica_metadata_api.py` wraps those requests. It reads the same `credentials.properties` file (or the `PRESERVICA_*` environment variables), logs in again by itself when the token expires, and accepts a bare reference, `IO_…`/`SO_…`, or a link copied from Preservica.

```powershell
python -m pip install requests

python preservica_metadata_api.py version
python preservica_metadata_api.py list     <asset-ref>
python preservica_metadata_api.py download <asset-ref>              # saves each fragment to metadata_downloads\
python preservica_metadata_api.py update   <asset-ref> --schema http://www.loc.gov/mods/v3 --file new_mods.xml
python preservica_metadata_api.py update   <asset-ref> --schema http://www.loc.gov/mods/v3 --file new_mods.xml --apply
```

Add `--folder` when the reference is a folder. `update` without `--apply` only prints the request it would send. With `--apply` it first saves the current fragment as a `_backup_` file in `metadata_downloads\`, then sends the update. A typical edit: `download`, edit the saved XML, `update` to check, then `update --apply`.

For exporting many records at once, `preservica_search_export.py` is much faster. Use this script for individual records, or to download full XML that the search index doesn't hold.

## 5. Next steps

1. **Give the service account Read Metadata.** The last scheduled export ran but the account could see 0 records; Read Content alone doesn't make records visible. An administrator ticks **Read Metadata** for the account's role on each security tag the records use (`sdb/security.html`). Updating metadata needs the edit permission on the tag as well.
2. **Confirm by logging in as the service account** in a browser, or with `python preservica_metadata_api.py version` followed by `list` on a record you know.
3. **Rerun the export** from **Actions > Preservica metadata export > Run workflow**. The run summary shows the record count, or the reason none were found.
4. **Practise an update on one test record** with `update` (dry run), then `--apply`, and check the result in Preservica before using it on real records.
