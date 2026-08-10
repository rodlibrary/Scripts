Various PowerShell, Python, and SLURM scripts for batch processing files as part of archival processing and accessibility work.
----------------------------------------------------------------------------------------------
**Virus check**

Windows Defender is a cybersecurity and threat protection solution. It provides antivirus software, identity theft monitoring, and real-time security alerts across Windows, macOS, iOS, and Android devices.

Terminal
1. Copy defenderSearch.ps1 to Documents
2. Open Terminal in Documents
3. Enter: 
.\defenderSearch.ps1 -Path "\path\to\files" -OutputCsv "\path\to\files\defender.csv"
----------------------------------------------------------------------------------------------
**PII**

PDF Miner is an open-source PDF PII detection tool.

Terminal
  1. Install PDF Miner
    python -m pip install pdfminer.six
  2. Download a copy of pii_scanner.py to Documents
  3. Open Terminal in Documents
  4. Enter:
      python pii_scanner.py "\path\to\files" -o "\path\to\files\results.csv”

Presidio

Presidio is an open-source PII detection and remediation application.

HPC
We have set up Presidio to run on UNI’s High Performance Computing System. 

1. Install Presidio
	* export TMPDIR="/home/catID/data/tmp" 
	* mkdir $TMPDIR || exit 1 
	* module load python3.11
	* pve/bin/pip install -U presidio_analyzer
	* pve/bin/pip install -U presidio_anonymizer
	* pve/bin/pip install -U presidio_image_redactor
	* python -m spacy download en_core_web_lg
2. Create a /pii folder and /pii/results subfolder in your hpc directory

Analyze

1. Upload analyze.script and analyze_pipeline to your hpc directory
2. Upload files to pii folder
3. Open Terminal
4. Enter: sbatch analyze.script
5. Review results

Anonymize

1. Upload anonymize.script and anonymize_pipeline to your hpc directory
2. Upload files to pii folder
3. Open Terminal
4. Enter: sbatch anonymize.script /data/ID/pii/results/pii_results_job#.csv

----------------------------------------------------------------------------------------------
**Optical character recognition (OCR)**

JP2s

Pytesseract is an open-source optical character recognition (OCR) engine. This process allows you to recursively convert jp2s into .txt, .xml, and .pdf formats (one per image) for a directory of folders. Tesseract is required.

Terminal
1. Enter the following commands to install the dependencies:
 * python -m pip install pytesseract pillow
 * python -m pip install pylibjpeg-openjpeg 
2. Verify PATH has been added:
    * Start > Environmental Variables > Path > Edit
    * New 
    * Enter: C:\Program Files\Tesseract-OCR\
3. Copy ocr_jp2.py to your Documents
4. Open Terminal in Documents
5. Enter:  
    python ocr_jp2.py “\path\to\files" [note: you can set this to run an entire directory, creating subfolders for \ocr_ouput if you so wish]

PDFs

OCRmyPDF is an open-source optical character recognition (OCR) engine for pdfs.

We have set OCRmyPDF up to run on UNI’s High Performance Computing System.

HPC
1. Create a folder \pdfs in your hpc directory
2. Create a subfolder in pdfs for pdfs\ocr_completed in your hpc directory
3. Install ocrmypdf using the following commands (you can skip to step e. if you have already installed WhisperX)
	xport TMPDIR="/home/$USER/data/tmp" 
	mkdir $TMPDIR || exit 1 
	module load python3.11
	python -m venv pve
	pve/bin/pip install -U ocrmypdf

7. Download the ocr_pdfs.script
8. Modify line 46 to add your ID
	/data/ID/pve/bin/activate

9. Upload pdfs you want to OCR into the \pdfs folder. [Note: the script does not currently support subfolders, so all pdfs need to be in the same folder.] 

10. Click on the Terminal icon
11. Count the number of pdfs to OCR and enter that number X highlighted in the next step
12. Enter: sbatch --array=0-X ocr_pdfs.script
----------------------------------------------------------------------------------------------
**Handwriting to text (HTT)**

Terminal
1. Create a Google AI Studio account
2. Create an API key
3. Sign up for credit billing
4. Open Terminal
    python -m pip install google-genai pillow 
		$env:GEMINI_API_KEY="your_api_key_here"  
5. Copy htt.py to Documents
6. Open Terminal in Documents 
7. Enter:
    python htt.py “\path\to\files” -o “\path\to\files\results”
----------------------------------------------------------------------------------------------
**Speech to text (STT)**
WhisperX builds on OpenAI’s open-source speech recognition model, Whisper, and adds the functionality of speaker recognition.

HPC
We have set up WhisperX to run on UNI’s High Performance Computing System. 

1. Create an account with Hugging Face 
	* Add the following repository to your account: 
		* https://huggingface.co/pyannote/speaker-diarization-community-1 
		* Accept User Conditions
		* Obtain a Read-Only Token
		* Click on Profile
		* Click on Access Tokens
		* Click on Create New Token
		* Name: Transcription
  		* Repositories: Read access to the contents of all public gated repos you can access
    	* Click Create
		* Copy token
2. Download a copy of whisperx.script
3. Edit whisperx.script
	* Line 9: Enter ID into VE_DIR="/data/ID/pve"
	* Line 18: paste Hugging Face token into “insert token here”
	* Save
4. Install Whisperx on HPC
	* export TMPDIR="/home/$USER/data/tmp" 
	* mkdir $TMPDIR || exit 1 
	* module load python3.11
	* python -m venv pve
	* pve/bin/pip install -U whisperx
5. Create an /audio folder in your hpc directory
6. Upload the edited whisperx.script into your hpc directory
7. Upload AV files you want to transcribe into the /audio folder. [Note: the script does not currently support subfolders, so all AV files need to be in this folder.]
8. Open Terminal
9. Enter: sbatch whisperx.script
----------------------------------------------------------------------------------------------
**Generate SHA-256 checksums**

Export results to a CSV file 
1. Copy sha256.ps1 to C:\Users\catID\Documents 
	* Edit input and output directories as needed.
	* Save
2. Open Terminal in Documents
3. Type .\sha256.ps1

Export to a standard .sha256 file (Linux / sha256sum style) to parse or use later with integrity checkers 

1. Copy sha256output.ps1 to C:\Users\catID\Documents 
	* Edit input and output directories as needed.
	* Save
2. Open Terminal in Documents
3. Type .\sha256output.ps1
----------------------------------------------------------------------------------------------
**Generate archival resource keys (ARKs)**
This process generates Archival Resource Keys (ARKs) from a CSV of source records.

An ARK has the form:

    ark:/NAAN/NAME

where NAME is normally built from an optional "shoulder" (a prefix that
marks a sub-namespace, policy, or minter) followed by a unique identifier
string ("blade"). This script does not register ARKs with a resolver
(e.g. N2T / EZID) -- it constructs well-formed ARK strings and matching
ANVL metadata records from data you already have, which you can then load
into whatever resolver/registry you use.

INPUT CSV COLUMNS
------------------
Column headers are case-insensitive and surrounding whitespace is ignored.

    naan          		optional  	Name Assigning Authority Number (32498)
    shoulder      		optional  	ARK shoulder / prefix (e.g. "s3", "z9")
    identifier    		REQUIRED  	Unique local identifier ("blade") for this object
    title         		REQUIRED  	Title / short description of the resource
    url           		optional  	URL the ARK should resolve to
    type          		optional  	Resource type (e.g. Text, Image, Dataset)
    commitment    		optional  	Persistence commitment statement
    format        		optional  	File / media format (e.g. application/pdf)
    relation      		optional  	Related identifier or resource
    source        		optional  	Source / provenance citation

OUTPUT
------
    <output>.csv   One row per input record with the generated ARK,
                   a resolvable https://n2t.net/... URL, and all metadata,
                   plus a status/notes column reporting any problems.
    <output>.anvl  ANVL-formatted metadata records (the format used by
                   ARK resolvers such as N2T/EZID) suitable for import.

Terminal
1. Copy generate_arks.py to Documents
2. Copy arks.csv to Documents
3. Edit arks.csv as necessary (NAAN: #####)
4. Open Terminal in Documents
5. Enter:
	python generate_arks.py arks.csv output --add-checksum --master-list 	“path/to/ark_master_list.csv”
----------------------------------------------------------------------------------------------
