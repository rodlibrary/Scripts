# Running presidio_review on an HPC cluster (Slurm)

The same three steps as in the [README](README.md) (analyze → human review → anonymize),
with the two compute-heavy steps run as Slurm jobs and Python in a virtual environment.

```
login node                      compute node                 your computer            compute node
bash slurm/setup_env.sh  ──►  sbatch slurm/analyze.script ──► review CSV in Excel ──► sbatch slurm/anonymize.script
(once)                        (review/<job>/review.csv)       (copy back, same name)   (redacted/<job>/)
```

| File | Job |
|---|---|
| `slurm/env.sh` | **Edit this:** data folder, venv path, Python and Tesseract modules |
| `slurm/setup_env.sh` | One-time setup on a login node: venv, packages, spaCy model, Tesseract, folders |
| `slurm/analyze.script` | Slurm job for `analyze.py` (step 1) |
| `slurm/anonymize.script` | Slurm job for `anonymize.py` (step 3) |

Both job scripts check the environment before starting (Python version, every package, the
spaCy model, Tesseract and its language data), so a broken setup fails in seconds with a
message saying what to fix, not hours into a job.

> **The documents, the review CSV and the OCR cache all contain PII.** Use cluster storage
> that is approved for it. The scripts make the data folders readable only by you
> (`chmod 700`); don't loosen that, and don't put the data in a shared project folder
> unless everyone with access is allowed to see it.

## 1. One-time setup

### 1a. Get the scripts onto the cluster

On a login node:

```bash
cd /data/$USER
git clone https://github.com/rodlibrary/Scripts.git
cd Scripts/pii/presidio_review
```

(Or copy the `presidio_review` folder up with `scp`, `rsync`, Globus or your cluster's web
portal. Everything below is run from inside that folder.)

### 1b. Edit `slurm/env.sh`

| Setting | Default | Change it if |
|---|---|---|
| `DATA` | `/data/$USER/presidio_review` | your cluster's storage is somewhere else (e.g. `/scratch/$USER/...` or a project space). Also change the `#SBATCH --output` lines at the top of both `.script` files to match. |
| `VENV` | `/data/$USER/presidio_review_venv` | you want the environment elsewhere. Keep it off node-local `/tmp`: compute nodes must see it. |
| `PYTHON_MODULE` | empty (system `python3`) | the system Python is older than 3.11, which is common. Run `module avail python` (or `module spider python`) and set e.g. `PYTHON_MODULE=python/3.12`. Presidio needs Python 3.11–3.14. |
| `TESSERACT_MODULE` | empty | your cluster has a Tesseract module (`module avail tesseract`). If not, leave it empty and setup installs Tesseract for you. |

If your cluster needs a partition or account for jobs, uncomment and fill in the
`##SBATCH --partition` / `##SBATCH --account` lines in both `.script` files (remove one `#`).

### 1c. Run setup

On a **login node** (compute nodes usually have no internet):

```bash
bash slurm/setup_env.sh
```

It:

1. creates the virtual environment with the Python from `PYTHON_MODULE` (or `python3`);
2. installs `requirements.txt` and downloads the spaCy model `en_core_web_lg` (~400 MB) into it;
3. finds Tesseract, or, if there's no module and none on `PATH`, installs it into
   `TESSERACT_PREFIX` from conda-forge using a standalone `micromamba` (no root or sudo
   needed; this is only for the Tesseract program, Python stays in the venv). To skip it,
   run `SKIP_TESSERACT=1 bash slurm/setup_env.sh` and analyze with `--ocr off`;
4. creates `$DATA/docs`, `review`, `redacted` and `logs`;
5. checks the result.

It's safe to re-run, e.g. after changing `requirements.txt` or `PYTHON_MODULE`. If you
change `PYTHON_MODULE` to a different Python version, delete the venv first
(`rm -r /data/$USER/presidio_review_venv`) so it's rebuilt with the new one.

Optional: a quick test on the sample files that ship with the scripts:

```bash
sbatch slurm/analyze.script input=$PWD/sample
```

### Doing it by hand instead

`setup_env.sh` just automates this; run these on a login node if you prefer:

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

The job scripts put `/data/$USER/tesseract/bin` on `PATH` automatically (`TESSERACT_PREFIX`
in `env.sh`).

### Tesseract languages

English (`eng`) and orientation detection (`osd`) are needed by default. For other
languages, check what's installed with `tesseract --list-langs`, then either load a module
that has them or add the data files to a self-installed Tesseract:

```bash
cd /data/$USER/tesseract/share/tessdata
curl -LO https://github.com/tesseract-ocr/tessdata_fast/raw/main/spa.traineddata
```

and pass `--ocr-lang eng+spa` to `analyze.script`. The job checks that every language
you ask for is installed before it starts.

## 2. Analyze (step 1)

Copy the documents into `/data/$USER/presidio_review/docs` (subfolders are fine), then
from the `presidio_review` folder:

```bash
sbatch slurm/analyze.script
```

| Command | Does |
|---|---|
| `sbatch slurm/analyze.script` | everything in `$DATA/docs` |
| `sbatch slurm/analyze.script input=letters` | only `$DATA/docs/letters` |
| `sbatch slurm/analyze.script input=/full/path` | any folder or single file |
| `sbatch slurm/analyze.script --ocr always --exclude-entities UK_NHS AU_TFN` | any [`analyze.py` option](README.md#1-analyze) after the script name |
| `sbatch --mail-type=END,FAIL --mail-user=you@uni.edu slurm/analyze.script` | email when it finishes |

Watch it with `squeue -u $USER`; the log is `$DATA/logs/analyze_<job_id>.log`, and its last
lines give the exact command for step 3.

Results go to their own folder, `$DATA/review/<job_id>/`, so no run overwrites another:

| File | Contents |
|---|---|
| `review.csv` | the findings to review |
| `review_warnings.txt` | anything not fully analyzed. **Read it.** |
| `review_settings.json`, `review_ocr_cache/`, `review_input.txt` | used by step 3. **Leave them where they are.** |

### Large collections: one job per subfolder

`analyze.py` processes one file at a time, so a big collection is fastest split into
subfolders (e.g. by box or series) and run as a
[job array](https://slurm.schedmd.com/job_array.html), one task per subfolder:

```bash
ls -d /data/$USER/presidio_review/docs/*/ | wc -l        # number of subfolders, N
sbatch --array=0-<N-1> slurm/analyze.script              # e.g. --array=0-11 for 12
```

Tasks take subfolders in alphabetical order. Each writes `<subfolder>.csv` (plus its own
sidecar files) to `$DATA/review/<array_job_id>/`, so reviewers get one CSV per subfolder.
Add `%10` (e.g. `--array=0-99%10`) to run at most 10 at a time. Files sitting directly in
`docs/` rather than in a subfolder would be missed, so the job refuses to start if there
are any.

### Resources

The defaults (`--cpus-per-task=4 --mem=16G --time=24:00:00`) suit most collections. The
spaCy model needs about 2 GB of memory; more goes to rendering PDF pages for OCR and
redaction, so raise `--mem` for very large pages or `--ocr-dpi`/`--pdf-dpi` 400+. Extra CPUs
mainly speed up Tesseract; `analyze.py` itself uses one. Override per run on the command
line, e.g. `sbatch --mem=32G --time=48:00:00 slurm/analyze.script`. If a job is killed
for time or memory, `sacct -j <job_id> --format=JobID,State,Elapsed,MaxRSS` shows which.

## 3. Review (step 2)

Copy the review CSV to your computer, review it as described in the
[README](README.md#2-review), and copy it back:

```bash
# on your computer
scp you@cluster:/data/you/presidio_review/review/123456/review.csv .
#   ... review in Excel; save as "CSV UTF-8 (Comma delimited)" ...
scp review.csv you@cluster:/data/you/presidio_review/review/123456/review.csv
```

It must go back to the **same folder with the same name**: step 3 finds the OCR settings,
OCR cache and input folder from the CSV's name. If your browser or Excel saved it as
`review (1).csv`, rename it back. Don't move or edit the documents in `docs/` between steps
1 and 3: step 3 checks each file's fingerprint and stops if anything changed.

Keep downloaded review CSVs out of folders synced to OneDrive or other cloud storage, and
delete local copies when you're done.

## 4. Anonymize (step 3)

```bash
sbatch slurm/anonymize.script /data/$USER/presidio_review/review/123456/review.csv
```

For an array analysis, pass the folder and use the same `--array` range (tasks take the
CSVs in alphabetical order, matching the subfolders):

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

## Troubleshooting

| Message | Fix |
|---|---|
| `submit this job from the presidio_review folder` | `cd` into the folder with `analyze.py` before `sbatch`. |
| `no virtual environment at ...` | Run `bash slurm/setup_env.sh` on a login node, or check `VENV` in `env.sh`. |
| `Python 3.x found; Presidio needs 3.11-3.14` | Set `PYTHON_MODULE` in `env.sh`, delete the venv, re-run setup. |
| `not installed in ...` / `en_core_web_lg is not installed` | Re-run `bash slurm/setup_env.sh` on a login node. |
| `tesseract not found` | Set `TESSERACT_MODULE`, or re-run setup to install it; or pass `--ocr off` (scans and images are then skipped and listed in the warnings). |
| `Tesseract language data 'xxx' is missing` | See [Tesseract languages](#tesseract-languages). |
| `error while loading shared libraries: libpython3...` | The Python module isn't loaded in the job. Set `PYTHON_MODULE` in `env.sh` to the module the venv was built with. |
| Job ends at once with no log | The log folder doesn't exist: run setup, or fix the `#SBATCH --output` path if you changed `DATA`. |
| `..._input.txt not found` | The review CSV was renamed or moved; put it back where analyze wrote it. |
| `<file> changed after analysis; re-run analyze.py and review again.` | A document in `docs/` was edited or replaced after step 1. Re-run step 1. |
