# Set your paths
$targetDir = "\path\to\files"
$exportPath = "path\to\export\checksums.csv"

# Fetch files, calculate hashes, and export
Get-ChildItem -Path $targetDir -Recurse -File | Get-FileHash | Export-Csv -Path $exportPath -NoTypeInformation
