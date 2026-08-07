# Set your paths
$targetDir = "F:\Born Digital Accessions"
$exportPath = "F:\Born Digital Accessions\checksums.csv"

# Fetch files, calculate hashes, and export
Get-ChildItem -Path $targetDir -Recurse -File | Get-FileHash | Export-Csv -Path $exportPath -NoTypeInformation