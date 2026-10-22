# Virus scan

Scan a folder with Microsoft Defender and save any detections to a CSV file.

| File | What it does | Runs on |
|---|---|---|
| [`defenderSearch.ps1`](defenderSearch.ps1) | Runs a Defender custom scan of a folder and exports the results to CSV | Windows (PowerShell, as Administrator) |

## defenderSearch.ps1

Uses Windows' built-in Defender scanner (`MpCmdRun.exe`) to scan a folder, then reads any threat detections and writes a summary row for each one.

**Requirements:** Windows with Microsoft Defender turned on. The script must be run **as Administrator**.

**Run**

1. Copy `defenderSearch.ps1` to your Documents folder.
2. Open Terminal **as Administrator** (right-click Terminal > Run as administrator) and go to Documents.
3. Enter:

```powershell
.\defenderSearch.ps1 -Path "\path\to\files" -OutputCsv "\path\to\files\defender.csv"
```

| Option | Meaning | Default |
|---|---|---|
| `-Path` | Folder to scan | The current folder |
| `-OutputCsv` | Where to write the results | `.\DefenderScanResults.csv` |

**Output:** a CSV with `ScanPath`, `DetectionTime`, `ThreatName`, `SeverityID`, `FilePath`, `ActionSuccess`, `AMRunningMode` and `ExitCode`.

If PowerShell refuses to run the script, allow local scripts for this session first:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```
