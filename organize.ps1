$sourceDir = "F:\Student Newspapers Access\OCR\pdf"

# Get all files recursively
Get-ChildItem -Path $sourceDir -File -Recurse | ForEach-Object {
    # Extract folder name (e.g., first 18 characters)
    $folderName = $_.Name.Substring(0, 18)
    $targetPath = Join-Path -Path $sourceDir -ChildPath $folderName
    
    # Create directory if it doesn't exist
    if (-not (Test-Path -Path $targetPath)) {
        New-Item -Path $targetPath -ItemType Directory
    }
    
    # Move the file
    Move-Item -Path $_.FullName -Destination $targetPath
}