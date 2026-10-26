# Scripts

PowerShell, Python and SLURM scripts for batch processing files as part of archival processing, web archiving and accessibility work at Rod Library, University of Northern Iowa.

Each folder has its own README with setup steps, commands and options. Open a folder to see it.

| Folder | What it's for | Runs on |
|---|---|---|
| [**arks**](arks/) | Generate Archival Resource Keys (ARKs) and ANVL records from a CSV | Local |
| [**checksums**](checksums/) | Generate SHA-256 checksums for every file in a folder | Windows |
| [**file_mgmt**](file_mgmt/) | Sort files into folders by name, merge PDFs per folder, remove empty folders | Windows |
| [**htt**](htt/) | Transcribe handwriting with Gemini and build searchable PDFs | Local |
| [**images**](images/) | Find duplicate and near-duplicate images | Local |
| [**iq**](iq/) | Convert Intranet Quorum / SCDIF exports and load them into BigQuery; generate and validate synthetic SCDIF test data | Local / HPC |
| [**metadata**](metadata/) | Read and write metadata for WAV, video, WebVTT and images; count PDF pages; extract entities; summarize documents | Local |
| [**ocr**](ocr/) | Make images and PDFs searchable with Tesseract, OCRmyPDF or Gemini | Local / HPC |
| [**pii**](pii/) | Find and redact personal information: quick PDF scan, Presidio with human review, and an HPC pipeline | Local / HPC |
| [**preservica**](preservica/) | Write download counts from Preservica audit logs into asset metadata | Local |
| [**transcription**](transcription/) | Speech to text with Whisper / WhisperX (with speaker labels) | HPC |
| [**video**](video/) | Multi-stage AV collection pipeline on the HPC (in progress) | HPC |
| [**virus_scan**](virus_scan/) | Scan a folder with Microsoft Defender and export detections to CSV | Windows |
| [**web_archiving**](web_archiving/) | Find uni.edu PDFs missing from Archive-It, review them in Excel and export seed lists for Archive-It | Local |

## Getting started

**Local scripts** run in Windows Terminal with [Python 3](https://www.python.org/downloads/) (PowerShell scripts need nothing extra). The usual pattern is:

1. Copy the script to your Documents folder.
2. Install what it needs, e.g. `python -m pip install pdfminer.six` (listed in the folder's README).
3. Open Terminal in Documents and run the command shown in the README.

Run any Python script with `--help` to see all of its options.

**HPC scripts** (`.script`, `.slurm`) run on UNI's High Performance Computing system. Most use a Python environment in `/data/<your catID>/pve` and find your folders automatically from your username. Each README shows how to create the environment, any lines you still need to edit, and the `sbatch` command to submit the job.
