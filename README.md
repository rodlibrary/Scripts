# Scripts

PowerShell, Python and SLURM scripts for batch processing files as part of archival processing and accessibility work at Rod Library, University of Northern Iowa.

Each folder has its own README with setup steps, commands and options. Open a folder to see it.

| Folder | What it's for | Runs on |
|---|---|---|
| [**virus_scan**](virus_scan/) | Scan a folder with Microsoft Defender and export detections to CSV | Windows |
| [**checksums**](checksums/) | Generate SHA-256 checksums for every file in a folder | Windows |
| [**file_mgmt**](file_mgmt/) | Sort files into folders by name, merge PDFs per folder, remove empty folders | Windows |
| [**pii**](pii/) | Find and redact personal information: quick PDF scan, Presidio with human review, and an HPC pipeline | Local / HPC |
| [**ocr**](ocr/) | Make images and PDFs searchable with Tesseract, OCRmyPDF or Gemini | Local / HPC |
| [**htt**](htt/) | Transcribe handwriting with Gemini and build searchable PDFs | Local |
| [**transcription**](transcription/) | Speech to text with Whisper / WhisperX (with speaker labels) | HPC |
| [**metadata**](metadata/) | Read and write metadata for WAV, video, WebVTT and images; count PDF pages; extract entities; summarize documents | Local |
| [**images**](images/) | Find duplicate and near-duplicate images | Local |
| [**arks**](arks/) | Generate Archival Resource Keys (ARKs) and ANVL records from a CSV | Local |
| [**preservica**](preservica/) | Write download counts from Preservica audit logs into asset metadata | Local |
| [**iq**](iq/) | Convert Intranet Quorum / SCDIF exports; load them into BigQuery | Local / HPC |
| [**video**](video/) | Multi-stage AV collection pipeline on the HPC (in progress) | HPC |

## Getting started

**Local scripts** run in Windows Terminal with [Python 3](https://www.python.org/downloads/) (PowerShell scripts need nothing extra). The usual pattern is:

1. Copy the script to your Documents folder.
2. Install what it needs, e.g. `python -m pip install pdfminer.six` (listed in the folder's README).
3. Open Terminal in Documents and run the command shown in the README.

Run any Python script with `--help` to see all of its options.

**HPC scripts** (`.script`, `.slurm`) run on UNI's High Performance Computing system. Most use a Python environment in `/data/<your catID>/pve` and find your folders automatically from your username. Each README shows how to create the environment, any lines you still need to edit, and the `sbatch` command to submit the job.
