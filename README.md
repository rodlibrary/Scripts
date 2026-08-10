Various PowerShell, Python, and SLURM scripts for batch processing files as part of archival processing and accessibility work.

**Virus check**
Windows Defender
Windows Defender is a cybersecurity and threat protection solution. It provides antivirus software, identity theft monitoring, and real-time security alerts across Windows, macOS, iOS, and Android devices.
Terminal
1. Copy defenderSearch.ps1 to Documents
2. Open Terminal in Documents
3. Type the following [replace path to files]:

.\defenderSearch.ps1 -Path "\path\to\files" -OutputCsv "\path\to\files\defender.csv"

**PII**
PDF Miner
PDF Miner is an open-source PDF PII detection tool.
Terminal
                        1. Install PDF Miner
                            python -m pip install pdfminer.six
                        2. Download a copy of pii_scanner.py to Documents
                        3. Open Terminal in Documents
                        4. Run
                            python pii_scanner.py "\path\to\files" -o "\path\to\files\results.csv”
**PII**
JP2s
Pytesseract
Pytesseract is an open-source optical character recognition (OCR) engine. This process allows you to recursively convert jp2s into .txt, .xml, and .pdf formats (one per image) for a directory of folders.
Terminal
                                                               1. Enter the following commands to install the dependencies:
                                                               * python -m pip install pytesseract pillow
                                                               * python -m pip install pylibjpeg-openjpeg 
                                                               2. Submit an IT ticket to have Tesseract installed on your computer (already approved software)
                                                               3. Verify PATH has been added:
                                                               1. Start > Environmental Variables > Path > Edit
  
                                                               2. New 
                                                               3. Paste  C:\Program Files\Tesseract-OCR\
                                                                  4. Copy ocr_jp2.py to your Documents
                                                                  5. Open Terminal in Documents
                                                                  6. Type:  
                                                                  1. python ocr_jp2.py “\path\to\files" [note: you can set this to run an entire directory, creating subfolders for \ocr_ouput if you so wish]
                                                              

