# Running PII review on an HPC cluster (Slurm)

How to install everything in a Python virtual environment on a cluster and run the
compute-heavy steps as Slurm jobs. There are two sets of instructions, depending on the data:

| Your data | Follow | Tools used |
|---|---|---|
| **IQ data:** Intranet Quorum / Senate CSS database exports (SCDIF `.dat`, or pipe-delimited / header-row `.txt` exports) | [IQ data](#iq-data) | [`../pii_pipeline/`](../pii_pipeline/) IQ branch: each export field handled by a policy (`iq_field_policy.csv`) |
| **Non-IQ data:** PDFs (incl. scans), images, Word, Excel, CSV, plain text | [Non-IQ data](#non-iq-data) | this folder (`presidio_review`): a person marks every finding REDACT or KEEP before anything is redacted |

**Keep the two apart.** Don't put IQ exports in the Non-IQ `docs/` folder:
`presidio_review` skips `.dat` files, and a `.txt` export would be scanned as ordinary text,
with no field policy. Collections with both kinds of material go through both sets of
instructions, IQ exports in one folder and everything else in the other.

Both start with the same [one-time setup](#one-time-setup).

> **The documents, exports, results CSVs and OCR cache all contain PII.** Use cluster
> storage that is approved for it. The setup makes the data folders readable only by you
> (`chmod 700`); don't loosen that, and don't put the data in a shared project folder
> unless everyone with access is allowed to see it. Keep downloaded copies out of folders
> synced to OneDrive or other cloud storage, and delete them when you're done.

---

## One-time setup

### Get the scripts onto the cluster

On a login node:

```bash
cd /data/$USER
git clone https://github.com/rodlibrary/Scripts.git
```

This gives you both `Scripts/pii/presidio_review` (Non-IQ) and `Scripts/pii/pii_pipeline`
(IQ). You can also copy the folders up with `scp`, `rsync`, Globus or your cluster's web portal.

### Edit `presidio_review/slurm/env.sh`

| Setting | Default | Change it if |
|---|---|---|
| `DATA` | `/data/$USER/presidio_review` | your cluster's storage is somewhere else (e.g. `/scratch/$USER/...` or a project space). Also change the `#SBATCH --output` lines at the top of both `presidio_review/slurm/*.script` files to match. |
| `VENV` | `/data/$USER/presidio_review_venv` | you want the environment elsewhere. Keep it off node-local `/tmp`: compute nodes must see it. |
| `PYTHON_MODULE` | empty (system `python3`) | the system Python is older than 3.11, which is common. Run `module avail python` (or `module spider python`) and set e.g. `PYTHON_MODULE=python/3.12`. Presidio needs Python 3.11–3.14. |
| `TESSERACT_MODULE` | empty | your cluster has a Tesseract module (`module avail tesseract`). If not, leave it empty and setup installs Tesseract for you. |

If your cluster needs a partition or account for jobs, add `#SBATCH --partition=...` /
`#SBATCH --account=...` to the job scripts you'll use. The `presidio_review/slurm/*.script`
files have them commented out (`##SBATCH`); remove one `#`.

### Run setup

On a **login node** (compute nodes usually have no internet), from `presidio_review`:

```bash
cd /data/$USER/Scripts/pii/presidio_review
bash slurm/setup_env.sh                                  # Non-IQ data
VENV=/data/$USER/pve bash slurm/setup_env.sh             # IQ data (the IQ scripts expect /data/$USER/pve)
```

Run whichever you need, or both. The IQ scripts are hard-wired to the environment at
`/data/$USER/pve`, so IQ needs that second command. It installs everything the IQ
scripts import. If you already have a `pve`, it only adds what's missing.

Setup:

1. creates the virtual environment with the Python from `PYTHON_MODULE` (or `python3`);
2. installs `requirements.txt` and downloads the spaCy model `en_core_web_lg` (~400 MB) into it;
3. finds Tesseract, or, if there's no module and none on `PATH`, installs it into
   `TESSERACT_PREFIX` from conda-forge using a standalone `micromamba` (no root or sudo
   needed; this is only for the Tesseract program, Python stays in the venv). To skip it,
   run with `SKIP_TESSERACT=1` and analyze Non-IQ data with `--ocr off`;
4. creates `$DATA/docs`, `review`, `redacted` and `logs` for Non-IQ data;
5. checks the result.

It's safe to re-run, e.g. after changing `requirements.txt` or `PYTHON_MODULE`. If you
change `PYTHON_MODULE` to a different Python version, delete the venv first
(e.g. `rm -r /data/$USER/presidio_review_venv`) so it's rebuilt with the new one.

#### Doing it by hand instead

`setup_env.sh` just automates this; run these on a login node if you prefer (use
`/data/$USER/pve` as the path for IQ):

```bash
module load python/3.12            # whatever your cluster calls it, if needed
python3 -m venv /data/$USER/presidio_review_venv
source /data/$USER/presidio_review_venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m spacy download en_core_web_lg
mkdir -p /data/$USER/presidio_review/{docs,review,redacted,logs}
chmod 700 /data/$USER/presidio_review
```

Then either `module load tesseract`, or install it without root:

```bash
mkdir -p /data/$USER/.micromamba_$USER
curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
    | tar -xj -C /data/$USER/.micromamba_$USER bin/micromamba
MAMBA_ROOT_PREFIX=/data/$USER/.micromamba_$USER \
    /data/$USER/.micromamba_$USER/bin/micromamba create -y -p /data/$USER/tesseract -c conda-forge tesseract
```

The Non-IQ job scripts put `/data/$USER/tesseract/bin` on `PATH` automatically
(`TESSERACT_PREFIX` in `env.sh`).

#### Tesseract languages

English (`eng`) and orientation detection (`osd`) are needed by default. For other
languages, check what's installed with `tesseract --list-langs`, then either load a module
that has them or add the data files to a self-installed Tesseract:

```bash
cd /data/$USER/tesseract/share/tessdata
curl -LO https://github.com/tesseract-ocr/tessdata_fast/raw/main/spa.traineddata
```

---

## IQ data

Intranet Quorum / Senate CSS exports, run with the IQ branch of
[`pii_pipeline`](../pii_pipeline/). It's automatic: there's no row-by-row REDACT/KEEP review. You
control the result with the field policy, then check the reports before release. Full
details are in [`pii_pipeline/README.txt`](../pii_pipeline/README.txt).

```
login node                                compute node                     compute node
convert_to_scdif.py (if needed)  ──►  sbatch iq_analyze.script  ──►  sbatch iq_anonymize.script
      /data/$USER/pii                  iq_results/pii_results_<job>.csv     iq_anonymize[...]/
```

### Before the first run

- Make the venv at `/data/$USER/pve` ([setup](#run-setup), second command).
- Create the data and log folders. Slurm opens the log files before the job starts,
  so a missing `results` folder makes the job fail with no log:

  ```bash
  mkdir -p /data/$USER/pii/results
  chmod 700 /data/$USER/pii
  ```

- If you set `PYTHON_MODULE`, load the same module in your login shell before each
  `sbatch` (e.g. `module load python/3.12`). The IQ scripts don't load modules, but
  `sbatch` passes your current environment on to the job.
- Edit `iq_field_policy.csv` to set what happens to each export field:

  | Action | Result |
  |---|---|
  | `redact` | whole value replaced with the label, e.g. `<FIRST_NAME>` |
  | `scan` | only the PII Presidio detects is replaced |
  | `keep` | shown, but ID numbers (SSNs, phones, emails, …) still removed |
  | `keep_all` | shown unchanged |

All IQ commands are run **from the `pii_pipeline` folder**:

```bash
cd /data/$USER/Scripts/pii/pii_pipeline
```

### 1. Convert non-standard exports (if needed)

Only needed if the export isn't standard tab-delimited SCDIF (e.g. it starts
`Record Type|...`). Keep the original **outside** `/data/$USER/pii`. This step needs no
extra packages and is light enough for a login node:

```bash
source /data/$USER/pve/bin/activate
python convert_to_scdif.py --inspect original.txt                          # masked; safe to share
python convert_to_scdif.py original.txt --output_dir /data/$USER/pii
```

Read the report in `/data/$USER/pii_conversion_reports`. Rejected records
(`*_rejected.txt`) are **not** converted or anonymized, and they contain PII.

Standard SCDIF exports go straight into `/data/$USER/pii`.

### 2. Analyze

```bash
sbatch iq_analyze.script                               # all files in /data/$USER/pii
sbatch iq_analyze.script file=NAME_scdif.dat           # one file
sbatch iq_analyze.script policy=my_policy.csv          # another policy
```

The job checks the policy file before it starts. Results:
`/data/$USER/pii/iq_results/pii_results_<job_id>.csv`, with `_policy.csv` (the policy used)
and `_exports.csv` (the exports found) next to it. The log is
`/data/$USER/pii/results/iq_analyze_<job_id>.log`. Its last lines give the exact anonymize
command.

### 3. Check, and adjust the policy

Look through the results CSV for fields the policy handles wrongly.

- Changing a field to `redact` or `keep_all`: just re-run step 4.
- Changing a field from `redact`/`keep_all` to `scan`/`keep`: re-run steps 2 and 4.
  Until you do, that field is shown as `<UNSCANNED>`.

### 4. Anonymize

```bash
sbatch iq_anonymize.script /data/$USER/pii/iq_results/pii_results_<job_id>.csv
sbatch iq_anonymize.script <results.csv> names,dates                 # also keep these types in "scan" fields
sbatch iq_anonymize.script <results.csv> none my_policy.csv          # another policy
```

Output: `/data/$USER/pii/iq_anonymize[...]/anonymized_<name>`. The folder name records the
settings, so runs never overwrite each other. Check `anonymization_summary_<job_id>.csv`
and `iq_policy_report_<job_id>.csv` before release. Presidio does not detect street
addresses inside free text, so check notes fields by eye.

### Resources

The IQ jobs ask for 16 CPUs and 24 hours, and use one worker process per CPU. Each worker
loads its own spaCy model, using about 2 GB of memory, so if your cluster's default memory
per job is small, add memory when submitting, e.g.
`sbatch --mem=48G iq_analyze.script`. For email when a job ends, add
`--mail-type=END,FAIL --mail-user=you@uni.edu`.

---

## Non-IQ data

PDFs (including scans), images, Word, Excel, CSV and plain-text files, run with
`presidio_review`. The steps are the same as in the [README](README.md): analyze, human
review, anonymize. Analyze and anonymize run as Slurm jobs.

```
compute node                     your computer                  compute node
sbatch slurm/analyze.script ──► review CSV in Excel ──► sbatch slurm/anonymize.script
(review/<job>/review.csv)        (copy back, same name)         (redacted/<job>/)
```

| File | Job |
|---|---|
| `slurm/env.sh` | settings ([edit once](#edit-presidio_reviewslurmenvsh)) |
| `slurm/setup_env.sh` | [one-time setup](#run-setup) |
| `slurm/analyze.script` | Slurm job for `analyze.py` (step 1) |
| `slurm/anonymize.script` | Slurm job for `anonymize.py` (step 3) |

Both job scripts check the environment before starting: Python version, every package, the
spaCy model, and Tesseract with its language data. A broken setup fails in seconds with a
message saying what to fix, not hours into a job.

All Non-IQ commands are run **from the `presidio_review` folder**:

```bash
cd /data/$USER/Scripts/pii/presidio_review
```

Optional: a quick test on the sample files that ship with the scripts:

```bash
sbatch slurm/analyze.script input=$PWD/sample
```

### 1. Analyze

Copy the documents into `/data/$USER/presidio_review/docs` (subfolders are fine), then:

```bash
sbatch slurm/analyze.script
```

| Command | Does |
|---|---|
| `sbatch slurm/analyze.script` | everything in `$DATA/docs` |
| `sbatch slurm/analyze.script input=letters` | only `$DATA/docs/letters` |
| `sbatch slurm/analyze.script input=/full/path` | any folder or single file |
| `sbatch slurm/analyze.script --ocr always --exclude-entities UK_NHS AU_TFN` | any [`analyze.py` option](README.md#1-analyze) after the script name |
| `sbatch slurm/analyze.script --ocr-lang eng+spa` | OCR in more languages ([install them first](#tesseract-languages)) |
| `sbatch --mail-type=END,FAIL --mail-user=you@uni.edu slurm/analyze.script` | email when it finishes |

Watch it with `squeue -u $USER`. The log is `$DATA/logs/analyze_<job_id>.log`, and its last
lines give the exact command for step 3.

Results go to their own folder, `$DATA/review/<job_id>/`, so no run overwrites another:

| File | Contents |
|---|---|
| `review.csv` | the findings to review |
| `review_warnings.txt` | anything not fully analyzed. **Read it.** |
| `review_settings.json`, `review_ocr_cache/`, `review_input.txt` | used by step 3. **Leave them where they are.** |

#### Large collections: one job per subfolder

`analyze.py` processes one file at a time. A big collection is faster if you split it into
subfolders (e.g. by box or series) and run it as a
[job array](https://slurm.schedmd.com/job_array.html), with one task per subfolder:

```bash
ls -d /data/$USER/presidio_review/docs/*/ | wc -l        # number of subfolders, N
sbatch --array=0-<N-1> slurm/analyze.script              # e.g. --array=0-11 for 12
```

Tasks take the subfolders in alphabetical order. Each task writes `<subfolder>.csv` and its
own sidecar files to `$DATA/review/<array_job_id>/`, so reviewers get one CSV per subfolder.
To run at most 10 tasks at a time, add `%10` (e.g. `--array=0-99%10`). The job refuses to
start if any files sit directly in `docs/` rather than in a subfolder, because the tasks
would miss them.

#### Resources

The defaults (`--cpus-per-task=4 --mem=16G --time=24:00:00`) suit most collections. The
spaCy model needs about 2 GB of memory. The rest goes to rendering PDF pages for OCR and
redaction. Raise `--mem` for very large pages, or for `--ocr-dpi`/`--pdf-dpi` of 400 or more.
Extra CPUs mainly speed up Tesseract; `analyze.py` itself uses one. You can override the
defaults for one run on the command line, e.g. `sbatch --mem=32G --time=48:00:00 slurm/analyze.script`. If a job is killed
for time or memory, `sacct -j <job_id> --format=JobID,State,Elapsed,MaxRSS` shows which.

### 2. Review

Copy the review CSV to your computer, review it as described in the
[README](README.md#2-review), and copy it back:

```bash
# on your computer
scp you@cluster:/data/you/presidio_review/review/123456/review.csv .
#   ... review in Excel; save as "CSV UTF-8 (Comma delimited)" ...
scp review.csv you@cluster:/data/you/presidio_review/review/123456/review.csv
```

It must go back to the **same folder with the same name**. Step 3 uses the CSV's name to
find the OCR settings, the OCR cache and the input folder. If your browser or Excel saved it as
`review (1).csv`, rename it back. Don't move or edit the documents in `docs/` between steps
1 and 3. Step 3 checks each file's fingerprint and stops if anything has changed.

### 3. Anonymize

```bash
sbatch slurm/anonymize.script /data/$USER/presidio_review/review/123456/review.csv
```

For an array analysis, pass the folder instead and use the same `--array` range. Tasks take
the CSVs in alphabetical order, matching the subfolders:

```bash
sbatch --array=0-11 slurm/anonymize.script /data/$USER/presidio_review/review/123456
```

[`anonymize.py` options](README.md#3-anonymize) go after the CSV or folder, e.g.
`sbatch slurm/anonymize.script <review.csv> --operator mask --pdf-dpi 300`.

Redacted copies and `audit_log.csv` go to `$DATA/redacted/<job_id>/` (array jobs:
`$DATA/redacted/<job_id>/<subfolder>/`). The log lists any files that were **not** written
and need handling by hand.

When the job is finished and checked, delete the OCR cache, which holds the full text of
every scanned page:

```bash
rm -r /data/$USER/presidio_review/review/123456/*_ocr_cache
```

---

## Troubleshooting

| Message | Data | Fix |
|---|---|---|
| Job ends at once with no log | both | The log folder doesn't exist. Non-IQ: run setup, or fix the `#SBATCH --output` path if you changed `DATA`. IQ: `mkdir -p /data/$USER/pii/results`. |
| `error while loading shared libraries: libpython3...` | both | The Python module the venv was built with isn't loaded. Non-IQ: set `PYTHON_MODULE` in `env.sh`. IQ: `module load` it before `sbatch`. |
| `No such file or directory: /data/<you>/pve/bin/activate` | IQ | Run `VENV=/data/$USER/pve bash slurm/setup_env.sh` from `presidio_review`. |
| `anonymize_pipeline.py not found` / `iq_policy.py not found` | IQ | `cd` into `pii_pipeline` before `sbatch`. |
| `submit this job from the presidio_review folder` | Non-IQ | `cd` into the folder with `analyze.py` before `sbatch`. |
| `no virtual environment at ...` | Non-IQ | Run `bash slurm/setup_env.sh` on a login node, or check `VENV` in `env.sh`. |
| `Python 3.x found; Presidio needs 3.11-3.14` | Non-IQ | Set `PYTHON_MODULE` in `env.sh`, delete the venv, re-run setup. |
| `not installed in ...` / `en_core_web_lg is not installed` | both | Re-run setup on a login node (with `VENV=/data/$USER/pve` for IQ). |
| `tesseract not found` | Non-IQ | Set `TESSERACT_MODULE`, or re-run setup to install it; or pass `--ocr off` (scans and images are then skipped and listed in the warnings). |
| `Tesseract language data 'xxx' is missing` | Non-IQ | See [Tesseract languages](#tesseract-languages). |
| `..._input.txt not found` | Non-IQ | The review CSV was renamed or moved; put it back where analyze wrote it. |
| `<file> changed after analysis; re-run analyze.py and review again.` | Non-IQ | A document in `docs/` was edited or replaced after step 1. Re-run step 1. |
| IQ export listed as skipped, or analyzed as plain text | Non-IQ | It's IQ data: move it to `/data/$USER/pii` and follow [IQ data](#iq-data). |
