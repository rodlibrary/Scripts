<#
.SYNOPSIS
    Scans a directory for malware using Microsoft Defender and exports results to a CSV file.

.DESCRIPTION
    Uses the built-in Windows Defender command-line scanner (MpCmdRun.exe) to perform a
    custom scan of a target folder, then reads any threat detections from the Defender
    event log / Get-MpThreatDetection and writes a summary to a CSV file.

.PARAMETER Path
    The directory to scan. Defaults to the current directory.

.PARAMETER OutputCsv
    Path to the CSV file to write results to. Defaults to .\DefenderScanResults.csv

.EXAMPLE
    .\Scan-DirectoryWithDefender.ps1 -Path "C:\Users\me\Downloads" -OutputCsv "C:\Reports\scan.csv"

.NOTES
    Must be run as Administrator. Requires Windows with Microsoft Defender installed and enabled.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Path = (Get-Location).Path,

    [Parameter(Mandatory = $false)]
    [string]$OutputCsv = ".\DefenderScanResults.csv"
)

# --- Verify running as Administrator ---
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator (Defender scans require elevated privileges)."
    exit 1
}

# --- Verify target path exists ---
if (-not (Test-Path -Path $Path)) {
    Write-Error "The specified path '$Path' does not exist."
    exit 1
}
$Path = (Resolve-Path $Path).Path

# --- Locate MpCmdRun.exe (Defender's CLI scanner) ---
$mpCmdRunCandidates = @(
    "$env:ProgramFiles\Windows Defender\MpCmdRun.exe",
    "$env:ProgramData\Microsoft\Windows Defender\Platform\*\MpCmdRun.exe"
)

$mpCmdRun = $null
foreach ($candidate in $mpCmdRunCandidates) {
    $found = Get-ChildItem -Path $candidate -ErrorAction SilentlyContinue | Sort-Object VersionInfo.FileVersion -Descending | Select-Object -First 1
    if ($found) {
        $mpCmdRun = $found.FullName
        break
    }
}

if (-not $mpCmdRun) {
    Write-Error "Could not locate MpCmdRun.exe. Is Microsoft Defender installed on this machine?"
    exit 1
}

Write-Host "Using Defender scanner: $mpCmdRun"
Write-Host "Scanning path: $Path"
Write-Host "This may take a while depending on folder size..."

# --- Record scan start time so we can filter detections that occur during this run ---
$scanStartTime = Get-Date

# --- Run a custom scan against the target directory ---
# -Scan -ScanType 3 = custom scan, -File specifies the target path
$scanArgs = @("-Scan", "-ScanType", "3", "-File", "`"$Path`"", "-DisableRemediation")
$process = Start-Process -FilePath $mpCmdRun -ArgumentList $scanArgs -NoNewWindow -Wait -PassThru

Write-Host "Scan completed with exit code: $($process.ExitCode)"

# --- Collect any threat detections that occurred during/after the scan ---
# Get-MpThreatDetection returns Defender's detection history; filter to this scan window
$detections = @()
try {
    $detections = Get-MpThreatDetection -ErrorAction Stop |
        Where-Object { $_.InitialDetectionTime -ge $scanStartTime.AddMinutes(-1) }
} catch {
    Write-Warning "Could not retrieve threat detections via Get-MpThreatDetection: $_"
}

# --- Build result objects for CSV export ---
$results = @()

if ($detections.Count -gt 0) {
    foreach ($d in $detections) {
        $threatInfo = Get-MpThreat -ThreatID $d.ThreatID -ErrorAction SilentlyContinue
        $row = New-Object -TypeName PSObject -Property @{
            ScanPath           = $Path
            DetectionTime      = $d.InitialDetectionTime
            ThreatName         = $threatInfo.ThreatName
            SeverityID         = $threatInfo.SeverityID
            FilePath           = ($d.Resources -join "; ")
            ActionSuccess      = $d.ActionSuccess
            AMRunningMode      = $d.AMRunningMode
            ExitCode           = $process.ExitCode
        }
        $results += $row
    }
} else {
    # No threats found; still record that the scan ran
    $row = New-Object -TypeName PSObject -Property @{
        ScanPath       = $Path
        DetectionTime  = $scanStartTime
        ThreatName     = "No threats detected"
        SeverityID     = ""
        FilePath       = ""
        ActionSuccess  = ""
        AMRunningMode  = ""
        ExitCode       = $process.ExitCode
    }
    $results += $row
}

# --- Export to CSV ---
try {
    $csvColumns = "ScanPath", "DetectionTime", "ThreatName", "SeverityID", "FilePath", "ActionSuccess", "AMRunningMode", "ExitCode"
    $results | Select-Object -Property $csvColumns | Export-Csv -Path $OutputCsv -NoTypeInformation -Encoding UTF8
    Write-Host "Results written to: $OutputCsv"
} catch {
    Write-Error "Failed to write CSV file: $_"
    exit 1
}

# --- Print summary to console ---
Write-Host "`n--- Scan Summary ---"
Write-Host "Path scanned : $Path"
Write-Host "Threats found: $($detections.Count)"
Write-Host "CSV report   : $OutputCsv"
