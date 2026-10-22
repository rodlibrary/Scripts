# Images

| File | What it does | Runs on |
|---|---|---|
| [`find_similar_images.py`](find_similar_images.py) | Finds duplicate and near-duplicate images and lists them in a CSV for review | Any computer with Python 3 |

## find_similar_images.py

Computes a perceptual hash for every image in a folder, groups images that look alike, and writes the groups to a CSV so a person can decide what to delete. It never deletes anything itself.

Handles `.jpg .jpeg .png .bmp .gif .tif .tiff .webp .heic`.

**Install**

```powershell
python -m pip install pillow imagehash
```

**Run**

```powershell
python find_similar_images.py "\path\to\images"
python find_similar_images.py "\path\to\images" -o results.csv -t 3
```

| Option | Meaning | Default |
|---|---|---|
| `directory` | Folder of images | — |
| `-o`, `--output` | Output CSV | `similar_images.csv` |
| `-t`, `--threshold` | How different two images can be and still count as similar. Lower = stricter (0 = near-identical only). | `5` |
| `--hash-type` | `phash`, `ahash`, `dhash` or `whash` | `phash` |
| `--no-recursive` | Don't scan subfolders | Subfolders are scanned |

Start with the default threshold. If unrelated images are grouped together, lower it; if obvious duplicates are missed, raise it.
