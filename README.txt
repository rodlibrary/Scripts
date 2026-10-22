PII review and redaction pipeline (Microsoft Presidio)
=======================================================

Put all of these files in one folder on the cluster and run sbatch from it.


FILES
-----
General pipeline (any files: PDFs, images, emails, CSVs, text, SCDIF exports)
  analyze_pipeline.py       finds PII and writes pii_results_<job>.csv
  anonymize_pipeline.py     replaces the PII listed in a results CSV
  anonymize.script          SLURM job for anonymize_pipeline.py

IQ branch (Intranet Quorum / SCDIF exports, controlled field by field)
  iq_field_policy.csv       EDIT THIS: redact / scan / keep / keep_all per field
  iq_analyze.script         SLURM job: analysis using the policy
  iq_anonymize.script       SLURM job: anonymization using the policy
  iq_analyze_pipeline.py    } used by the two jobs above; they also need
  iq_anonymize_pipeline.py  } analyze_pipeline.py and anonymize_pipeline.py
  iq_policy.py              } in the same folder

Converting non-standard exports
  convert_to_scdif.py       turns a pipe-delimited / header-row IQ export into
                            standard SCDIF (Python only, no packages needed)


IQ WORKFLOW
-----------
1. If the export isn't standard tab-delimited SCDIF (e.g. "Record Type|..."),
   keep the original OUTSIDE /data/catID/pii and convert it:
     python convert_to_scdif.py --inspect original.txt     (masked; safe to share)
     python convert_to_scdif.py original.txt --output_dir /data/catID/pii
   Read the report in /data/catID/pii_conversion_reports. Rejected records
   (*_rejected.txt) are NOT converted or anonymized and contain PII.

2. Analyze:
     sbatch iq_analyze.script                               all files in /data/catID/pii
     sbatch iq_analyze.script file=NAME_scdif.dat           one file
     sbatch iq_analyze.script policy=my_policy.csv          another policy
   Results: /data/catID/pii/iq_results/pii_results_<job>.csv

3. Anonymize:
     sbatch iq_anonymize.script /data/catID/pii/iq_results/pii_results_<job>.csv
     ... add a second argument to keep types in "scan" fields, e.g. names,dates
     ... add a third argument for another policy, e.g. none my_policy.csv
   Output: /data/catID/pii/iq_anonymize[...]/anonymized_<name>
   Check anonymization_summary_<job>.csv and iq_policy_report_<job>.csv.

Policy actions
  redact    whole value replaced with the label, e.g. <FIRST_NAME>
  scan      only the PII Presidio detects is replaced
  keep      shown, but ID numbers (SSNs, phones, emails, ...) still removed
  keep_all  shown unchanged
Changing a field to redact or keep_all: re-run step 3 only.
Changing a field from redact/keep_all to scan/keep: re-run steps 2 and 3
(until then that field is shown as <UNSCANNED>).
Note: Presidio does not detect street addresses inside free text.


GENERAL WORKFLOW
----------------
  python analyze_pipeline.py --input_dir /data/catID/pii [--only FILE]
  sbatch anonymize.script /data/catID/pii/results/pii_results_<job>.csv [names,dates,...]


EMAIL ALERTS
------------
  sbatch --mail-type=END,FAIL --mail-user=you@uni.edu iq_analyze.script


RUNNING ON WINDOWS
------------------
The Python scripts also run locally (e.g. in PowerShell). The IQ scripts then
use a single process automatically. The data contains PII: keep it out of
folders synced to OneDrive or other cloud storage.
