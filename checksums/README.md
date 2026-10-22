# Checksums

Generate SHA-256 checksums for every file in a folder (and its subfolders) to verify file integrity later.

| File | What it does | Runs on |
|---|---|---|
| [`sha256.ps1`](sha256.ps1) | Hashes every file under a folder and exports the results to CSV | Windows (PowerShell) |

## sha256.ps1

**Run**

1. Copy `sha256.ps1` to `C:\Users\catID\Documents`.
2. Open it in a text editor and set the two paths at the top, then save:

   ```powershell
   $targetDir  = "\path\to\files"                  # folder to hash
   $exportPath = "path\to\export\checksums.csv"    # where to save the results
   ```

3. Open Terminal in Documents.
4. Enter:

   ```powershell
   .\sha256.ps1
   ```

**Output:** a CSV with one row per file and the columns `Algorithm`, `Hash` and `Path`.
