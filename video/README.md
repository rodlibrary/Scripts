# Video collection pipeline

Runs a collection of AV files through a series of processing stages on the HPC: transcription, keyframes, AI frame descriptions and summaries (Google Gemini), tags, accessibility files, IIIF manifests, catalog export, search index and more. Each stage is its own SLURM job, and each job starts only after the previous one succeeds.

> **Incomplete in this repository.** `run_pipeline.py` calls 13 stage scripts (`transcribe_collection.py`, `extract_keyframes.py`, …) and reads a `config.yaml`. None of those are here yet, so the pipeline won't run until they're added to this folder.

| File | What it does |
|---|---|
| [`run_pipeline.py`](run_pipeline.py) | Runs the configured stages in order and records a provenance log for each run |
| [`slurm/submit_pipeline.sh`](slurm/submit_pipeline.sh) | Submits every stage as a chained SLURM job |
| `slurm/01_transcribe.slurm` … `13_clustering.slurm` | One SLURM job per stage (see table below) |
| [`slurm/common_env.sh`](slurm/common_env.sh) | Shared setup for every stage: modules, Python environment, API key |
| [`slurm/cluster_env.sh`](slurm/cluster_env.sh) | Settings for UNI's cluster (partition `defq`, no GPU or memory requests) |
| [`slurm/cluster_env.example.sh`](slurm/cluster_env.example.sh) | Template for other clusters |

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

1. Upload this whole `video` folder to the HPC, keeping the `slurm/` subfolder, and add the stage scripts and your `config.yaml` next to `run_pipeline.py`.
2. In `slurm/common_env.sh`, change `source /data/hartwigd/pve/bin/activate` to your own environment, then install `pyyaml` (and the stage scripts' requirements) into it.
3. Store your Gemini API key in a file only you can read. The jobs load it from there, so it never appears in a script:

   ```bash
   mkdir -p ~/.secrets && chmod 700 ~/.secrets
   echo "your_api_key_here" > ~/.secrets/google_api_key
   chmod 600 ~/.secrets/google_api_key
   ```

## Run

From the `video` folder:

```bash
source slurm/cluster_env.sh
./slurm/submit_pipeline.sh config.yaml
```

Skip stages you've already run, or run only some:

```bash
SKIP_STEPS="transcribe keyframes" ./slurm/submit_pipeline.sh config.yaml
ONLY_STEPS="describe_frames summarize" ./slurm/submit_pipeline.sh config.yaml
```

Logs go to `video/logs/`. To run stages directly without SLURM (e.g. for testing):

```bash
python run_pipeline.py --config config.yaml --steps transcribe keyframes --no-mpi
```

## Notes on UNI's cluster

`cluster_env.sh` is already set up for UNI: the cluster doesn't track memory or GPUs as SLURM resources, so no `--mem` or `--gres` is requested and transcription runs on the CPU partition (`USE_GPU=0`). Set `transcription.device: "cpu"` in `config.yaml` to match. For another cluster, copy `cluster_env.example.sh` and fill in its values from `sinfo -s`.
