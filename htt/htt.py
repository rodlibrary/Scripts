import os
import sys
import argparse
from pathlib import Path
from google import genai
from google.genai import types
from PIL import Image

# Supported file extensions
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
PDF_EXTENSIONS = {".pdf"}

# gemini-3.1-pro-preview is currently Google's most advanced multimodal
# reasoning model available via the public API, and the best choice for
# detailed/messy handwriting. gemini-2.5-pro has been retired for new
# API keys. Pass --model gemini-3.5-flash for a faster, cheaper GA option
# (still quite strong, just less careful on hard handwriting).
DEFAULT_MODEL = "gemini-3.1-pro-preview"

PROMPT = (
    "Transcribe the handwriting in this document into clean, editable text. "
    "Maintain the original formatting, paragraphs, and line breaks where possible. "
    "If the document has multiple pages, transcribe every page in order, and "
    "separate each page's transcription with a line containing exactly: "
    "----- PAGE BREAK -----. "
    "Do not add any introductory or concluding text-only output the transcription."
)


def get_client(api_key: str | None):
    """
    Initialize the Gemini client.
    If api_key is provided, use it directly. Otherwise, fall back to the
    GEMINI_API_KEY environment variable (the default behavior of genai.Client()).
    """
    if api_key:
        return genai.Client(api_key=api_key)
    return genai.Client()  # looks for GEMINI_API_KEY env variable


def transcribe_image(client: genai.Client, model: str, image_path: Path) -> str | None:
    """Send a single image to Gemini and return the transcribed text."""
    try:
        img = Image.open(image_path)
    except Exception as e:
        print(f"  Error loading image '{image_path.name}': {e}")
        return None

    try:
        response = client.models.generate_content(
            model=model,
            contents=[img, PROMPT],
        )
        return response.text
    except Exception as e:
        print(f"  API error on '{image_path.name}': {e}")
        return None


def transcribe_pdf(client: genai.Client, model: str, pdf_path: Path) -> str | None:
    """Send a whole PDF to Gemini (native document understanding) and return
    the transcribed text for all pages combined."""
    try:
        pdf_bytes = pdf_path.read_bytes()
    except Exception as e:
        print(f"  Error reading PDF '{pdf_path.name}': {e}")
        return None

    try:
        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        response = client.models.generate_content(
            model=model,
            contents=[pdf_part, PROMPT],
        )
        return response.text
    except Exception as e:
        print(f"  API error on '{pdf_path.name}': {e}")
        return None


def process_directory(input_dir: str, output_dir: str, api_key: str | None, model: str):
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    if not input_path.is_dir():
        print(f"Error: '{input_dir}' is not a valid directory.")
        return

    output_path.mkdir(parents=True, exist_ok=True)

    # Collect all supported files in the directory (non-recursive)
    all_files = sorted(
        f for f in input_path.iterdir()
        if f.is_file() and f.suffix.lower() in (IMAGE_EXTENSIONS | PDF_EXTENSIONS)
    )

    if not all_files:
        print(f"No supported image or PDF files found in '{input_dir}'.")
        return

    client = get_client(api_key)

    print(f"Found {len(all_files)} file(s). Using model '{model}'. Starting transcription...\n")

    success_count = 0
    for file_path in all_files:
        suffix = file_path.suffix.lower()
        print(f"Processing '{file_path.name}'...")

        if suffix in PDF_EXTENSIONS:
            text = transcribe_pdf(client, model, file_path)
        else:
            text = transcribe_image(client, model, file_path)

        if text is None:
            print(f"  Skipped '{file_path.name}' due to an error.\n")
            continue

        out_file = output_path / f"{file_path.stem}.txt"
        try:
            out_file.write_text(text, encoding="utf-8")
            print(f"  Saved -> {out_file}\n")
            success_count += 1
        except Exception as e:
            print(f"  Error writing output file for '{file_path.name}': {e}\n")

    print(f"Done. {success_count}/{len(all_files)} file(s) transcribed successfully.")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert a directory of handwritten scans (images and/or PDFs) into "
            "text files using Google Gemini. Each image or PDF produces one .txt file."
        )
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="images",
        help="Directory containing the handwritten image/PDF files (default: 'images').",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="transcriptions",
        help="Directory to save the output .txt files (default: 'transcriptions').",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Gemini API key. If omitted, the GEMINI_API_KEY environment variable is used.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(
            f"Gemini model to use (default: '{DEFAULT_MODEL}', the most accurate "
            "current option for handwriting). Use 'gemini-3.5-flash' for a faster, "
            "cheaper GA alternative."
        ),
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(
            "Error: No API key provided. Set the GEMINI_API_KEY environment variable "
            "or pass --api-key YOUR_KEY."
        )
        sys.exit(1)

    process_directory(args.input_dir, args.output_dir, api_key, args.model)


if __name__ == "__main__":
    main()
