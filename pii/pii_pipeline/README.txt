PII review and redaction pipeline (Microsoft Presidio)
=======================================================

Runs on an HPC cluster with SLURM, using a Python virtual environment.
Put all of these files in one folder on the cluster and run sbatch from it.

Input directory: /data/$USER/pii, for both IQ and Non-IQ data. Every job
reads its input from there (subfolders included) and writes its results
and output into subfolders of it. To process only some files, use file=
(see each section) rather than another folder.

Which instructions to follow:
  IQ DATA       /data/$USER/pii holds Intranet Quorum / Senate CSS database
                exports (SCDIF .dat, or pipe-delimited / header-row .txt
                exports), with or without other files. Each export field is
                handled by a policy you edit (iq_field_policy.csv); PDFs,
                images, emails and CSVs alongside them are handled exactly as
                in NON-IQ DATA.
  NON-IQ DATA   /data/$USER/pii holds no IQ exports: only PDFs, images,
                emails (.eml), CSVs and plain-text files.
Do SETUP once first, whichever you use.

The data contains PII. Keep /data/$USER/pii readable only by you
(chmod 700) and out of shared project folders.


FILES
-----
Shared
  requirements.txt          Python packages for the virtual environment
  analyze_pipeline.py       finds PII and writes pii_results_<job>.csv
  anonymize_pipeline.py     replaces the PII listed in a results CSV

IQ data (Intranet Quorum / SCDIF exports, controlled field by field)
  iq_field_policy.csv       EDIT THIS: redact / scan / keep / keep_all per field
  iq_analyze.script         SLURM job: analysis using the policy
  iq_anonymize.script       SLURM job: anonymization using the policy
  iq_analyze_pipeline.py    } used by the two jobs above; they also need
  iq_anonymize_pipeline.py  } analyze_pipeline.py and anonymize_pipeline.py
  iq_policy.py              } in the same folder
  convert_to_scdif.py       turns a pipe-delimited / header-row IQ export into
                            standard SCDIF (Python only, no packages needed)

Non-IQ data
  analyze.script            SLURM job for analyze_pipeline.py
  anonymize.script          SLURM job for anonymize_pipeline.py


SETUP (ONE TIME, BOTH IQ AND NON-IQ)
------------------------------------
Run these on a LOGIN node: compute nodes usually have no internet.
All the job scripts expect the virtual environment at /data/$USER/pve and
the data in /data/$USER/pii.

1. Get the scripts onto the cluster:
     cd /data/$USER
     git clone https://github.com/rodlibrary/Scripts.git
     cd Scripts/pii/pii_pipeline
   (Or copy this folder up with WinSCP)

2. Load Python 3.11 or newer. Presidio needs 3.11-3.14, and the system
   python3 on many clusters is older:
     python3 --version
     module avail python              # if it's too old; or: module spider python
     module load python/3.12          # use the name your cluster shows
   If you load a module here, load the same one before every sbatch (step 6).

3. Create the virtual environment and install the packages:
     python3 -m venv /data/$USER/pve
     source /data/$USER/pve/bin/activate
     python -m pip install --upgrade pip
     python -m pip install -r requirements.txt
     python -m spacy download en_core_web_lg        # ~400 MB
   Keep the venv on storage the compute nodes can see (not /tmp). To keep
   pip's cache out of a small home quota, first run:
     export PIP_CACHE_DIR=/data/$USER/.pip_cache

4. Tesseract (OCR for images; needed by analysis and anonymization):
     module avail tesseract
   If there's a module, load it before every sbatch, like Python. If not,
   install it without root into /data/$USER/tesseract (the job scripts find
   it there automatically):
     mkdir -p /data/$USER/.micromamba
     curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
         | tar -xj -C /data/$USER/.micromamba bin/micromamba
     MAMBA_ROOT_PREFIX=/data/$USER/.micromamba \
         /data/$USER/.micromamba/bin/micromamba create -y \
         -p /data/$USER/tesseract -c conda-forge tesseract
   Check with: /data/$USER/tesseract/bin/tesseract --list-langs

5. Create the data folder. The results folder must exist BEFORE any sbatch,
   because SLURM opens the log files before the job starts (a missing
   folder makes the job fail with no log at all):
     mkdir -p /data/$USER/pii/results
     chmod 700 /data/$USER/pii

6. Before each sbatch, in the same login shell:
     cd /data/$USER/Scripts/pii/pii_pipeline       # always submit from here
     module load python/3.12                       # only if you used a module in step 2
   sbatch passes your current environment on to the job.

If your cluster needs a partition or account, add #SBATCH --partition=...
and #SBATCH --account=... to the top of the .script files you use.

Each job prints the Python it's using and checks every package it needs
before starting, and stops with the exact pip command to run if anything's
missing.
Re-run step 3 from a login node to fix it.


IQ DATA
-------
Input directory: /data/$USER/pii. Put the IQ exports there, with the
attachment and email folders that come with them and any other files to
review in the same run.

1. Edit iq_field_policy.csv. Policy actions:
     redact    whole value replaced with the label, e.g. <FIRST_NAME>
     scan      only the PII Presidio detects is replaced
     keep      shown, but ID numbers (SSNs, phones, emails, ...) still removed
     keep_all  shown unchanged

2. Convert, only if the export isn't standard tab-delimited SCDIF
   (e.g. "Record Type|..."). Keep the original OUTSIDE /data/$USER/pii.
   This is light enough for a login node:
     source /data/$USER/pve/bin/activate
     python convert_to_scdif.py --inspect original.txt     (masked; safe to share)
     python convert_to_scdif.py original.txt --output_dir /data/$USER/pii
   Read the report in /data/$USER/pii_conversion_reports. Rejected records
   (*_rejected.txt) are NOT converted or anonymized and contain PII.
   Standard SCDIF exports go straight into /data/$USER/pii.

3. Analyze:
     sbatch iq_analyze.script                               all files in /data/$USER/pii
     sbatch iq_analyze.script file=NAME_scdif.dat           one file
     sbatch iq_analyze.script policy=my_policy.csv          another policy
   The job checks the policy file before it starts.
   Results: /data/$USER/pii/iq_results/pii_results_<job>.csv
   Log:     /data/$USER/pii/results/iq_analyze_<job>.log (last lines give the
            exact anonymize command)

4. Anonymize:
     sbatch iq_anonymize.script /data/$USER/pii/iq_results/pii_results_<job>.csv
     ... add a second argument to keep types in "scan" fields, e.g. names,dates
     ... add a third argument for another policy, e.g. none my_policy.csv
   Output: /data/$USER/pii/iq_anonymize[...]/anonymized_<name>
   Check anonymization_summary_<job>.csv and iq_policy_report_<job>.csv.

Changing a field to redact or keep_all: re-run step 4 only.
Changing a field from redact/keep_all to scan/keep: re-run steps 3 and 4
(until then that field is shown as <UNSCANNED>).
Note: Presidio does not detect street addresses inside free text.


NON-IQ DATA
-----------
Input directory: /data/$USER/pii. For PDFs, images (.png .jpg .tif ...),
emails (.eml), CSVs and plain-text files. If any IQ exports are in
/data/$USER/pii, use IQ DATA instead: analyze.script would still find
them, but without the field policy.

1. Copy the files into /data/$USER/pii (subfolders are fine; they're all
   scanned).

2. Analyze:
     sbatch analyze.script                                  all files in /data/$USER/pii
     sbatch analyze.script file=docs/letter.pdf             one file
     sbatch analyze.script file='docs/*.eml',report.pdf     wildcards (quoted), several
     sbatch analyze.script --entities PERSON,EMAIL_ADDRESS  any analyze_pipeline.py option
   Results: /data/$USER/pii/results/pii_results_<job>.csv
   Errors:  /data/$USER/pii/results/pii_errors_<job>.csv. READ IT: files that
            couldn't be read are listed here and were NOT analyzed.
   Log:     /data/$USER/pii/results/analyze_<job>.log (last lines give the
            exact anonymize command)
   Scanned PDFs with no text layer are listed in the errors file as "No text
   extracted" and are not analyzed. OCR them first (see ../ocr) or use
   ../presidio_review, which OCRs scans.

3. Review the results CSV. Automated detection misses things and flags
   things that aren't PII, so have a person check it before release.

4. Anonymize:
     sbatch anonymize.script /data/$USER/pii/results/pii_results_<job>.csv
     sbatch anonymize.script <results.csv> names                   keep names
     sbatch anonymize.script <results.csv> names,locations,dates   keep several types
   Shortcuts: names (PERSON), locations (LOCATION; not street addresses),
   dates (DATE_TIME; not dates of birth), organizations (ORGANIZATION), or
   any Presidio entity type. Comma-separated, no spaces.
   Output: /data/$USER/pii/anonymize (nothing kept) or
           /data/$USER/pii/anonymize_keep_<types>, with
           anonymization_summary_<job>.csv. Runs with different settings never
           overwrite each other.


RESOURCES
---------
All four jobs ask for 16 CPUs and 24 hours, and run one worker process per
CPU. Each worker loads its own spaCy model (about 2 GB of memory), so if a
job is killed for memory, request more when submitting:
  sbatch --mem=48G iq_analyze.script
Fewer CPUs need less memory: sbatch --cpus-per-task=8 --mem=24G analyze.script
To see why a job stopped: sacct -j <job> --format=JobID,State,Elapsed,MaxRSS
Watch running jobs:       squeue -u $USER


EMAIL ALERTS
------------
  sbatch --mail-type=END,FAIL --mail-user=you@uni.edu iq_analyze.script


TROUBLESHOOTING
---------------
Job ends at once with no log
    /data/$USER/pii/results doesn't exist. SETUP step 5.
.../pve/bin/activate: No such file or directory  /  no virtual environment
    SETUP step 3.
"... not found in <folder>. Run sbatch from the folder that contains..."
    cd into this folder before sbatch (SETUP step 6).
"these packages are not installed" / "en_core_web_lg is not installed"
    Run the pip / spacy command the error shows, on a login node.
error while loading shared libraries: libpython3...
    The Python module the venv was built with isn't loaded. module load it
    before sbatch (SETUP step 6).
"tesseract not found" / images give no findings
    SETUP step 4.


RUNNING ON WINDOWS
------------------
The Python scripts also run locally (e.g. in PowerShell):
  python -m pip install -r requirements.txt
  python -m spacy download en_core_web_lg
  python analyze_pipeline.py --input_dir C:\path\to\pii --recursive
The IQ scripts then use a single process automatically. The data contains
PII: keep it out of folders synced to OneDrive or other cloud storage.
