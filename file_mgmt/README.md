# File management

Small PowerShell utilities for organizing batches of files. They were written for the Student Newspapers OCR project, so each one has that folder path built in. **Change the path at the top of the script before you run it.**

| File | What it does | Runs on |
|---|---|---|
| [`organize.ps1`](organize.ps1) | Moves each file into a subfolder named after the first 18 characters of its file name | Windows (PowerShell) |
| [`combinePDFs.ps1`](combinePDFs.ps1) | Merges all PDFs in each folder into one PDF named after that folder | Windows (PowerShell + PDFtk) |
| [`removeemptyfolders.ps1`](removeemptyfolders.ps1) | Deletes empty subfolders | Windows (PowerShell) |

A typical sequence is `organize.ps1` → `combinePDFs.ps1` → `removeemptyfolders.ps1`.

> These scripts move and delete files without asking. Test them on a copy of your folder first.

## organize.ps1

Groups files that share a name prefix. For example, `1965-03-12_studentnews_p01.pdf` and `1965-03-12_studentnews_p02.pdf` both go into a folder called `1965-03-12_student`.

1. Edit `$sourceDir` on line 1.
2. If your prefix isn't 18 characters long, change `Substring(0, 18)` on line 6.
3. Run `.\organize.ps1`.

Files with names shorter than 18 characters cause an error and stay where they are.

## combinePDFs.ps1

For every folder under the starting path, merges that folder's PDFs (in name order) into a single PDF named after the folder, saved inside that folder.

**Requirements:** [PDFtk Server](https://www.pdflabs.com/tools/pdftk-server/), installed at `C:\Program Files (x86)\PDFtk Server\bin\pdftk.exe`. If it's somewhere else, edit `$PDFtk` on line 2.

1. Edit the folder path on line 5.
2. Run `.\combinePDFs.ps1`.

Run it only once per batch. The merged PDF is saved next to the pages, so a second run would merge it in again.

## removeemptyfolders.ps1

Deletes every empty subfolder under the starting path.

1. Edit the folder path in the script.
2. Run `.\removeemptyfolders.ps1`.

It checks one level at a time, so a folder that only contains other empty folders may need a second run.
