Get-ChildItem -Path "F:\Student Newspapers Access\OCR\pdf" -Recurse -Directory | Where-Object { (Get-ChildItem -Path $_.FullName -Force).Count -eq 0 } | Remove-Item -Force
