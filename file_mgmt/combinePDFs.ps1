# Define the path to your PDFtk executable
$PDFtk = "C:\Program Files (x86)\PDFtk Server\bin\pdftk.exe"

# Get all PDFs, group them by folder, and merge each group
(Get-ChildItem -Path "F:\Student Newspapers Access\OCR\pdf" -Recurse -Filter *.pdf) | 
    Group-Object DirectoryName | 
    ForEach-Object {
        $outputFile = Join-Path $_.Name "$($_.Group[0].Directory.Name).pdf"
        & $PDFtk $_.Group.FullName CAT OUTPUT $outputFile
        Write-Host "Created: $outputFile"
    }
