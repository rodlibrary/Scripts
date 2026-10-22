#!/usr/bin/env python3
"""
summarize_docs_gemini.py

Analyze every document in a directory using Google's Gemini API (Google AI Studio
API key) and write a filename + summary (max 250 words) to a CSV file.

Supported input file types: .txt, .md, .pdf, .docx
(Easy to extend — see `extract_text()`.)

Setup
-----
1. Install dependencies:
   pip install google-genai pypdf python-docx

2. Get a Google AI Studio API key: https://aistudio.google.com/apikey

3. Set it as an environment variable (recommended) or pass it with --api-key:
   export GEMINI_API_KEY="your-api-key-here"

Usage
-----
   python summarize_docs_gemini.py /path/to/documents -o summaries.csv

   python summarize_docs_gemini.py /path/to/documents \
       --api-key YOUR_KEY \
       --model gemini-2.5-flash \
       --output summaries.csv
"""

import argparse
import csv
import os
import sys
from pathlib import Path

# --- Dependency checks -------------------------------------------------

try:
    from google import genai
except ImportError:
    sys.exit(
        "Missing dependency 'google-genai'.\n"
        "Install it with: pip install google-genai"
    )

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}

MAX_WORDS = 250

SUMMARY_PROMPT = (
    f"Summarize the following document in no more than {MAX_WORDS} words. "
    "Be clear and concise, capturing the key points, purpose, and any "
    "important conclusions. Do not exceed the word limit. Respond with "
    "only the summary text, no preamble or headers.\n\n"
    "DOCUMENT:\n"
    "-----\n"
    "{content}\n"
    "-----"
)


# --- Text extraction -----------------------------------------------------

def extract_text(path: Path) -> str:
    """Extract plain text from a supported file type."""
    ext = path.suffix.lower()

    if ext in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")

    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError(
                "Missing dependency 'pypdf' for PDF support. "
                "Install it with: pip install pypdf"
            )
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if ext == ".docx":
        try:
            import docx
        except ImportError:
            raise RuntimeError(
                "Missing dependency 'python-docx' for DOCX support. "
                "Install it with: pip install python-docx"
            )
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)

    raise ValueError(f"Unsupported file type: {ext}")


def enforce_word_limit(text: str, max_words: int = MAX_WORDS) -> str:
    """Hard safety net in case the model slightly exceeds the word limit."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


# --- Gemini summarization -------------------------------------------------

def summarize_with_gemini(client, model: str, content: str) -> str:
    """Send document text to Gemini and return the summary."""
    # Guard against extremely large documents overwhelming the request.
    # Gemini models support large context windows, but trim defensively.
    max_chars = 800_000
    if len(content) > max_chars:
        content = content[:max_chars]

    prompt = SUMMARY_PROMPT.format(content=content)

    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )
    summary = (response.text or "").strip()
    return enforce_word_limit(summary)


# --- Main workflow ---------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Summarize a directory of documents using Gemini."
    )
    parser.add_argument("directory", type=str, help="Directory containing documents")
    parser.add_argument(
        "-o", "--output", type=str, default="summaries.csv",
        help="Output CSV file path (default: summaries.csv)"
    )
    parser.add_argument(
        "--api-key", type=str, default=None,
        help="Google AI Studio API key (falls back to GEMINI_API_KEY env var)"
    )
    parser.add_argument(
        "--model", type=str, default="gemini-2.5-flash",
        help="Gemini model to use (default: gemini-2.5-flash)"
    )
    parser.add_argument(
        "--recursive", action="store_true",
        help="Recurse into subdirectories"
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit(
            "No API key provided. Set GEMINI_API_KEY env var or use --api-key."
        )

    directory = Path(args.directory)
    if not directory.is_dir():
        sys.exit(f"Not a directory: {directory}")

    pattern = "**/*" if args.recursive else "*"
    files = sorted(
        p for p in directory.glob(pattern)
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not files:
        sys.exit(
            f"No supported files found in {directory} "
            f"(supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))})"
        )

    client = genai.Client(api_key=api_key)

    results = []
    for i, path in enumerate(files, start=1):
        print(f"[{i}/{len(files)}] Processing: {path.name} ...", flush=True)
        try:
            text = extract_text(path)
            if not text.strip():
                summary = "(No extractable text found in this file.)"
            else:
                summary = summarize_with_gemini(client, args.model, text)
        except Exception as e:
            summary = f"ERROR: {e}"
            print(f"  -> Failed: {e}", file=sys.stderr)

        results.append({"filename": path.name, "summary": summary})

    output_path = Path(args.output)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "summary"])
        writer.writeheader()
        writer.writerows(results)

    print(f"\nDone. Wrote {len(results)} summaries to {output_path}")


if __name__ == "__main__":
    main()
