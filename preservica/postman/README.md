# Postman collections for Preservica

Three collections for [Postman](https://www.postman.com/downloads/) that call Rod Library's Preservica (`us.preservica.com`, tenant `UNIOWA`). Each has step-by-step instructions in its **Overview** panel and a note on every request.

| Collection | What it does | Changes records? |
|---|---|---|
| [`Preservica_read_metadata`](Preservica_read_metadata.postman_collection.json) | Browse folders, open one asset, and download its metadata records (MODS, Dublin Core…) | No |
| [`Preservica_download_all_metadata`](Preservica_download_all_metadata.postman_collection.json) | Exports every top-level folder as a ZIP (OPEX package) holding the full metadata of every asset as XML. One automatic run starts and waits for all the exports; then one **Send and Download** per folder saves the ZIPs. | No |
| [`Preservica_selected_metadata_CSV`](Preservica_selected_metadata_CSV.postman_collection.json) | Chosen indexed fields for every asset in every top-level folder, as a CSV with a `top_level_folder` column | No |

## Import

1. Download the `.json` file: open it above, then use **Download raw file**.
2. In Postman, sign in (the version you get without an account can't import collections), then do one of these:
   - drag the file onto the Postman window;
   - press **Ctrl+O** (**Cmd+O** on a Mac) and choose the file;
   - click **Import** at the top of the left sidebar.
3. Open the collection's **Variables** tab, enter `username` and `password` in the **Current value** column, and click **Save**. Current values stay on your computer; initial values sync to Postman's servers, so leave the password out of them.

When you import a newer version, choose **Replace**, then enter the username and password again.

## Notes

- All three log in by themselves: run *1. Log in* first, and later requests log in again when the 15-minute token is about to expire or is rejected.
- The account sees only records it has **Read Metadata** permission for. An empty folder list or 0 results means the account needs that permission on the records' security tags.
- The *download all metadata* collection also needs permission to export. A `403` when an export starts means the account lacks it.
- The *selected metadata* collection keeps its results inside Postman. For tens of thousands of assets, [`preservica_search_export.py`](../preservica_search_export.py) or the scheduled GitHub export copes better.
- Field names for the *selected metadata* collection are listed in [`indexed_fields.csv`](../indexed_fields.csv).
