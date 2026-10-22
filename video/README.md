# Video collection pipeline

Runs a collection of AV files through a series of processing stages on the HPC: transcription, keyframes, AI frame descriptions and summaries (Google Gemini), tags, accessibility files, IIIF manifests, catalog export, search index and more. Each stage is its own SLURM job, and each job starts only after the previous one succeeds.

> **Incomplete in this repository.** `run_pipeline.py` calls 13 stage scripts (`transcribe_collection.py`, `extract_keyframes.py`, …) and reads a `config.yaml`. None of those are here yet, so the pipeline won't run until they're added to this folder.

| File | What it does |
|---|---|
| [`run_pipeline.py`](run_pipeline.py) | Runs the configured stages in order and records a provenance log for each run |
| [`submit_pipeline.sh`](submit_pipeline.sh) | Submits every stage as a chained SLURM job |
| `01_transcribe.slurm` … `13_clustering.slurm` | One SLURM job per stage (see table below) |
| [`common_env.sh`](common_env.sh) | Shared setup for every stage: modules, your Python environment (`/data/<catID>/pve`), API key |
| [`cluster_env.sh`](cluster_env.sh) | Settings for UNI's cluster (partition `defq`, no GPU or memory requests) |
| [`cluster_env.example.sh`](cluster_env.example.sh) | Template for other clusters |

## Stages

| # | Stage | Stage script it runs (not in repo yet) |
|---|---|---|
| 01 | `transcribe` | `transcribe_collection.py` |
| 02 | `keyframes` | `extract_keyframes.py` |
| 03 | `describe_frames` | `describe_frames.py` |
| 04 | `summarize` | `summarize_collection.py` |
| 05 | `tags` | `generate_tags.py` |
| 06 | `accessibility` | `generate_accessibility.py` |
| 07 | `collection_report` | `collection_report.py` |
| 08 | `preview` | `build_preview.py` |
| 09 | `quality_metrics` | `quality_metrics.py` |
| 10 | `iiif` | `build_iiif_manifest.py` |
| 11 | `catalog_export` | `export_catalog.py` |
| 12 | `search_index` | `build_search_index.py` |
| 13 | `clustering` | `cluster_visuals.py` |

## Setup (once)

1. Upload all the files in this folder to one folder on the HPC, and add the stage scripts and your `config.yaml` to it.
2. Install `pyyaml` (and the stage scripts' requirements) into your environment at `/data/<catID>/pve`. `common_env.sh` activates it automatically from your username.
3. In that folder, create a `logs` folder. SLURM writes each job's log there and won't start a job if it's missing:

   ```bash
   mkdir -p logs
   ```

4. Store your Gemini API key in a file only you can read. The jobs load it from there, so it never appears in a script:

   ```bash
   mkdir -p ~/.secrets && chmod 700 ~/.secrets
   echo "your_api_key_here" > ~/.secrets/google_api_key
   chmod 600 ~/.secrets/google_api_key
   ```

## Run

From the folder containing these files:

```bash
source cluster_env.sh
./submit_pipeline.sh config.yaml
```

Skip stages you've already run, or run only some:

```bash
SKIP_STEPS="transcribe keyframes" ./submit_pipeline.sh config.yaml
ONLY_STEPS="describe_frames summarize" ./submit_pipeline.sh config.yaml
```

Track the jobs with `squeue -u $USER`. Logs go to `logs/`.

To run stages directly without SLURM (e.g. for testing):

```bash
python run_pipeline.py --config config.yaml --steps transcribe keyframes --no-mpi
```

## Notes on UNI's cluster

`cluster_env.sh` is already set up for UNI: the cluster doesn't track memory or GPUs as SLURM resources, so no `--mem` or `--gres` is requested and transcription runs on the CPU partition (`USE_GPU=0`). Set `transcription.device: "cpu"` in `config.yaml` to match. For another cluster, copy `cluster_env.example.sh` and fill in its values from `sinfo -s`.
