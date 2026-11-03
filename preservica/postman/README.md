# Postman collections for Preservica

Four collections for [Postman](https://www.postman.com/downloads/) that call Rod Library's Preservica (`us.preservica.com`, tenant `UNIOWA`). Each has step-by-step instructions in its **Overview** panel and a note on every request.

| Collection | What it does | Changes records? |
|---|---|---|
| [`Preservica_read_metadata`](Preservica_read_metadata.postman_collection.json) | Browse folders, open one asset, and download its metadata records (MODS, Dublin Core…) | No |
| [`Preservica_download_all_metadata`](Preservica_download_all_metadata.postman_collection.json) | Exports every top-level folder as a ZIP (OPEX package) holding the full metadata of every asset as XML. One automatic run starts and waits for all the exports; then one **Send and Download** per folder saves the ZIPs. | No |
| [`Preservica_selected_metadata_CSV`](Preservica_selected_metadata_CSV.postman_collection.json) | Chosen indexed fields for every asset in every top-level folder, as a CSV with a `top_level_folder` column | No |
| [`Preservica_storage_report`](Preservica_storage_report.postman_collection.json) | Storage used, by tier (Hot, Cold), by storage location and by representation (Preservation, Access), with a CSV. Also lists Preservica's Analytics datasets. | No |

## Import

1. Download the `.json` file: open it above, then use **Download raw file**.
2. In Postman, sign in (the version you get without an account can't import collections), then do one of these:
   - drag the file onto the Postman window;
   - press **Ctrl+O** (**Cmd+O** on a Mac) and choose the file;
   - click **Import** at the top of the left sidebar.
3. Open the collection's **Variables** tab, enter `username` and `password` in the **Current value** column, and click **Save**. Current values stay on your computer; initial values sync to Postman's servers, so leave the password out of them.

When you import a newer version, choose **Replace**, then enter the username and password again.

## Notes

- All four log in by themselves: run *1. Log in* first, and later requests log in again when the 15-minute token is about to expire or is rejected.
- The account sees only records it has **Read Metadata** permission for. An empty folder list or 0 results means the account needs that permission on the records' security tags.
- The *download all metadata* collection also needs permission to export. A `403` when an export starts means the account lacks it.
- The *selected metadata* collection keeps its results inside Postman. For tens of thousands of assets, [`preservica_search_export.py`](../preservica_search_export.py) or the scheduled GitHub export copes better.
- Field names for the *selected metadata* collection are listed in [`indexed_fields.csv`](../indexed_fields.csv).

## Storage report

Preservica has no single "storage used" request, so the collection measures it. For every asset it reads the representations, content objects, every generation (older versions of files still take up space), each file's size, and the storage locations that hold each file. Then it adds the sizes up per storage location.

1. **Run collection** with every request ticked: the measuring only happens in a full run. It makes about six requests per asset, so it takes a few minutes for a few hundred assets, and the Console shows progress.
2. Open **5. Show the storage report** and its **Visualize** tab. It has totals by tier, by storage location, by storage location and representation, and by generation, a **Download CSV** button, and a **Requests to Preservica** table showing how Preservica answered each kind of request.

Things to know:

- **Tiers come from the storage location's name.** Names matching `coldPattern` (default `glacier|deep|cold|archive|tape|offline`) count as Cold, so Rod Library's `S3 Deep Glacier Adapter` is Cold; everything else is Hot. If a location is in the wrong tier, change `coldPattern` and send request 5 again; nothing needs measuring again.
- **A file stored in two locations counts in both,** since it uses storage in both. A file shared by the Preservation and Access representations counts once.
- `allGenerations` (default `true`) counts every stored version of every file; set it to `false` to count only the current versions.
- `folderRefs` limits the report to some folders (references separated by commas).
- **The account needs the Access functional role, with Read Metadata and Read Content.** Without it, Preservica refuses some of the requests, and the report's yellow box says which, with the HTTP code.
- **Compare the totals with Preservica's own figures for the account.** They cover only what the account can see, and Preservica's billing can include things this doesn't (content deleted but not yet removed, metadata, thumbnails).
- **If the report is empty or looks wrong,** send **6. Debug: show one asset's raw responses** on its own after a run. It follows one asset (the first measured, or the one in `debugAsset`) through each step and shows Preservica's raw answers on its **Visualize** tab, which shows where the collection loses track. It contains no password or token.
