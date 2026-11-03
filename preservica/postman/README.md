# Postman collections for Preservica

Six collections for [Postman](https://www.postman.com/downloads/) that call Rod Library's Preservica (`us.preservica.com`, tenant `UNIOWA`). Each has step-by-step instructions in its **Overview** panel and a note on every request.

| Collection | What it does | Changes records? |
|---|---|---|
| [`Preservica_read_metadata`](Preservica_read_metadata.postman_collection.json) | Browse folders, open one asset, and download its metadata records (MODS, Dublin Core…) | No |
| [`Preservica_download_all_metadata`](Preservica_download_all_metadata.postman_collection.json) | Exports every top-level folder as a ZIP (OPEX package) holding the full metadata of every asset as XML. One automatic run starts and waits for all the exports; then one **Send and Download** per folder saves the ZIPs. | No |
| [`Preservica_selected_metadata_CSV`](Preservica_selected_metadata_CSV.postman_collection.json) | Chosen indexed fields for every asset in every top-level folder, as a CSV with a `top_level_folder` column | No |
| [`Preservica_storage_report`](Preservica_storage_report.postman_collection.json) | Storage used, by tier (Hot, Cold), by storage location and by representation (Preservation, Access), with a CSV. Also lists Preservica's Analytics datasets. | No |
| [`Preservica_monthly_stats`](Preservica_monthly_stats.postman_collection.json) | The month's figures for the DCDS_stats Google Sheet: the DCDS tab's Preservica rows, the Storage tab's Tier 1 rows (Hot and Cold TB) and detailed rows for the Preservica tab, with boxes to paste and a CSV | No |
| [`Preservica_ARK_assets_CSV`](Preservica_ARK_assets_CSV.postman_collection.json) | Every asset's MODS Archival Resource Key (ARK blade) and Location (URL), with its Preservica reference, the full ARK, whether the two agree, and chosen fields, as a CSV. Lists ARKs found in more than one asset. | No |

## Import

1. Download the `.json` file: open it above, then use **Download raw file**.
2. In Postman, sign in (the version you get without an account can't import collections), then do one of these:
   - drag the file onto the Postman window;
   - press **Ctrl+O** (**Cmd+O** on a Mac) and choose the file;
   - click **Import** at the top of the left sidebar.
3. Open the collection's **Variables** tab, enter `username` and `password` in the **Current value** column, and click **Save**. Current values stay on your computer; initial values sync to Postman's servers, so leave the password out of them.

When you import a newer version, choose **Replace**, then enter the username and password again.

## Notes

- All six log in by themselves: run *1. Log in* first, and later requests log in again when the 15-minute token is about to expire or is rejected.
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

## Monthly stats

Run it once a month to get the figures for the **DCDS_stats** Google Sheet, then copy them in by hand.

1. Run it on the last day of the month or in the first five days of the next: in the first five days it reports the month that just ended. To pick a month, set `month` (such as `2026-10`).
2. **Run collection** with every request ticked. Like the storage report, it makes several requests per asset, so a few hundred assets take a few minutes. Sending requests 1 to 6 one at a time also works: **5. Measure the assets** measures one asset per send (`assetsPerSend`) and carries on where it left off, so for hundreds of assets use **Run collection**, which repeats request 5 by itself. **Wait for each send to finish** before sending anything else, and send it again until its **Visualize** tab says all assets are done: Postman keeps a request's results only when its script finishes, so a send that's interrupted loses that send's progress. Request 7 shows how the last send of request 5 ended, including any script error, and the collection's version. If it shows no version, Postman is running an old copy: delete the older copies of the collection.
3. Open **6. Show the monthly figures** and its **Visualize** tab:
   - **DCDS tab: Preservica rows.** Copy the box of seven values, click the month's cell in the `#Collections` row, and paste: they fill `#Collections` down to `#Downloads`.
   - **Storage tab: Tier 1.** `#TBPreservicaHot` and `#TBPreservicaCold`, in TB with 2 decimal places, in a box to paste the same way.
   - **Preservica tab.** About 45 rows of category, metric and value. The first time, type `Category` and `Metric` in A2 and B2 and the month names January to December in C1 to N1 (as on the other tabs), then paste the first box at cell A3: it fills in the labels and puts the values in the month's column. In later months, paste the values-only box into the month's cell of the Content `#Collections` row. A new collection, storage location or security tag adds a row, so check the labels still line up, and insert a row first where one was added.
   - **Download CSV** saves everything as `preservica_stats_<month>.csv` (columns Tab, Category, Metric and the month).

Things to know:

- **The DCDS rows show what was added that month** (records created in it), like the tab's other monthly rows. Set `dcdsCounts` to `total` and send request 6 again for all-time totals.
- **`#Downloads`** is the month's change in Preservica's all-time download counter. Postman saves each month's all-time count in `downloadsHistory` on your computer, so the first month is blank. To fill it, put the previous month's all-time count in `previousDownloadsAllTime`, send request 6 again, then clear the variable. Run it from the same Postman each month, and choose **Replace** but keep the current value of `downloadsHistory` when importing a newer version (or note `#DownloadsAllTime` each month and use `previousDownloadsAllTime`).
- **Everything else is measured as of the run**, so run it near the end of the month.
- **Hot and Cold** follow `coldPattern`, as in the storage report. **Images, sound, moving images and text** come from the file extension of each asset's current Preservation file: Images `.jpg .jpeg .tif .tiff .png .gif .jp2 .dng .heic…`, Text `.pdf .doc .docx .txt .rtf .xml .html .epub .xls .csv…`, SoundRecordings `.mp3 .wav .aif .flac .m4a…`, MovingImages `.mp4 .mov .avi .mkv .mxf .mpg…`, anything else Other. A file without an extension falls back to Preservica's format group. The AssetsByFileType, FilesByFileType and TBByFileType rows list every extension found. **Duplicates** are files whose SHA1 checksum also appears in another asset.
- `integrityChecks` set to `false` skips the integrity-check rows and one request per file.
- The report's yellow boxes say what's missing and why. HTTP 401 or 403 means the service account needs the Access functional role with Read Metadata and Read Content.
- **If the report shows little data,** look at its **What the run found** table first. It shows how many collections, folders and assets were found and measured, how many assets came with a created date, top-level folder, security tag, format group and download count, and how many had no files. Then send **7. Debug: show raw responses** on its own. Its **Visualize** tab shows what the last run collected and Preservica's raw answers: the top-level folders, both searches (with the fields Preservica returned) and one asset followed down to a file, its storage locations and integrity checks. Put a reference in `debugAsset` to follow a particular asset. The page shows no password or token, so it's safe to send on.

## Assets with an ARK

Reads two fields of each asset's MODS record and lists them, with the asset's Preservica reference, as a CSV:

- **Archival Resource Key:** `<identifier displayLabel="Archival Resource Key" type="ark">`, which holds the ARK blade;
- **Location (URL):** `<physicalLocation displayLabel="Location (URL)" type="url"><url>`, which holds the full URL.

Preservica's search index keeps identifier values but not their `type`, and doesn't index the URL, so the collection reads each asset's MODS record: about 2 requests per asset.

1. On the **Variables** tab, set `fields` to any other fields you want, separated by commas: names from [`indexed_fields.csv`](../indexed_fields.csv). The default is `mods.dateCreated,xip.security_descriptor`; the title is always included.
2. **Run collection** with requests 1 to 5 ticked. Request 4 repeats until every asset is read, 5 per send. If you send the requests one at a time instead, send 4 again until its **Visualize** tab says all are done.
3. Open **5. Show the CSV** > **Visualize** and click **Download CSV**.

Columns:

| Column | What it holds |
|---|---|
| `preservica_reference` | The asset's reference (UUID) |
| `preservica_ref` | `IO_<UUID>`, as in the Redirects repository's ARK manifest |
| `title` | The asset's title |
| `ark_blade (Archival Resource Key)` | The Archival Resource Key identifier, as entered |
| `location_url (Location (URL))` | The Location (URL), as entered |
| `ark` | The full ARK: from the URL if it holds one, otherwise `ark:/32498/` plus the blade |
| `ark_status` | `both, match`; `both, DIFFERENT` (the blade and URL name different ARKs); `URL has no ARK`; `blade only`; `URL only`; `neither`; or a problem reading the record |
| `source_id` | The MODS Source ID (local identifier) |
| `top_level_folder` | The asset's top-level folder |
| then | Your `fields` |

Things to know:

- **`onlyWithArk`** (default `true`) lists only assets with a blade or a URL. Set it to `false` and send request 5 again to list every asset, including those whose ARK fields are still empty.
- **ARKs found in more than one asset** are listed on the report (ignoring hyphens, which carry no meaning in ARKs), since a redirect needs exactly one target.
- `naan` (default `32498`) goes in front of a bare blade. `folderRefs` limits the run to some folders.
- If a value is missing or wrong, send **6. Debug: one asset's MODS**. It shows an asset's MODS record (the first with an ARK, or the one in `debugAsset`), what the collection read from it, and how far the last run got.
