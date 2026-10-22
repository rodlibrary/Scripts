# Handwriting to text (HTT)

Transcribe handwritten documents with Google Gemini, then turn the images and transcriptions into a searchable PDF.

| File | What it does | Runs on |
|---|---|---|
| [`htt.py`](htt.py) | Sends each image or PDF to Gemini and saves the transcription as a `.txt` file | Any computer with Python 3 (needs a Gemini API key) |
| [`generate_searchable_pdf.py`](generate_searchable_pdf.py) | Combines images and their `.txt` transcriptions into one searchable PDF | Any computer with Python 3 |

Typical workflow: run `htt.py`, check the transcriptions, put each `.txt` next to its image, then run `generate_searchable_pdf.py`.

## Set up a Gemini API key (once)

1. Create a [Google AI Studio](https://aistudio.google.com/) account.
2. Create an API key.
3. Set up billing so the key has credit.

Keep the key out of any file you commit. Set it in Terminal instead:

```powershell
$env:GEMINI_API_KEY="your_api_key_here"
```

This lasts until you close the Terminal window.

## htt.py

Transcribes `.jpg .jpeg .png .webp .bmp .gif .tif .tiff` images and `.pdf` files.

**Install**

```powershell
python -m pip install google-genai pillow
```

**Run**

```powershell
python htt.py "\path\to\files" -o "\path\to\files\results"
```

| Option | Meaning | Default |
|---|---|---|
| `input_dir` | Folder of handwritten images/PDFs | `images` |
| `-o`, `--output-dir` | Where to save the `.txt` files | `transcriptions` |
| `--api-key` | Gemini key, if not set in `GEMINI_API_KEY` | — |
| `--model` | Gemini model | `gemini-3.1-pro-preview` |

**Output:** one `.txt` per input file, with the same base name.

## generate_searchable_pdf.py

Pairs each image (`.tif .tiff .jpg .jpeg`) with the `.txt` file of the same name (`page01.jpg` + `page01.txt`) and builds a PDF: one page per image, with the transcription as an invisible, searchable text layer.

Because the transcription has no word positions, search hits won't highlight exactly over the handwriting, but the text is fully searchable and copyable.

**Install**

```powershell
python -m pip install reportlab pillow
```

**Run**

```powershell
python generate_searchable_pdf.py "\path\to\folder" -o output.pdf
```

Without `-o`, the PDF is saved as `searchable_output.pdf` in the same folder.

> An identical copy of this script is in [`../ocr`](../ocr/).
