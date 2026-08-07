"""
PII discovery pipeline.
 
Scans a directory containing PDFs, images, plain text, and CSV files, runs
OCR/text extraction where needed, and searches everything for PII using
Microsoft Presidio. Results are written to a single CSV report.
 
Design notes (why two different Presidio engines are used):
  - PDFs / images / .txt files produce *unstructured* free text. That text
    is searched directly with `presidio_analyzer.AnalyzerEngine.analyze()`,
    which returns character-offset matches (RecognizerResult) - exactly
    what we want for a text blob.
  - .csv files are *structured*: PII usually already lives in named
    columns. `presidio_structured.PandasAnalysisBuilder` is built for that
    case - it inspects a DataFrame and figures out which columns are likely
    to contain which entity type. We then re-scan just the flagged columns
    with the analyzer so the output CSV keeps consistent per-value
    offsets/scores across every file type.
 
  `presidio_structured` is NOT used on raw OCR/PDF text: it isn't designed
  to find PII embedded inside a blob of narrative text, only inside
  already-separated fields/columns.
 
Parallelism:
  Each input file is independent, so we fan the file list out across a
  multiprocessing.Pool with one worker per requested CPU. Each worker
  process builds its own AnalyzerEngine once (via an initializer) and
  reuses it for every file that lands in that worker, rather than paying
  the spaCy-model-load cost per file. Workers return plain dict rows
  instead of writing to the CSV directly, since file handles/csv.writers
  aren't safely shared across processes; the main process is the only
  one that touches the output files.
"""
 
import os
import argparse
import csv
import glob
import multiprocessing as mp
 
import pandas as pd
from PIL import Image
import pdfminer.high_level
import pytesseract
 
from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_structured import StructuredEngine, PandasAnalysisBuilder
 
 
UNSTRUCTURED_TEXT_EXTS = {"pdf", "png", "jpg", "jpeg", "tiff", "tif", "bmp", "txt"}
STRUCTURED_EXTS = {"csv"}
 
FIELDNAMES = [
    "filename",
    "source_type",   # 'unstructured_text' or 'structured_csv'
    "location",      # column/row info for CSVs, blank for text sources
    "entity_type",
    "entity_value",
    "score",
    "start_char",
    "end_char",
]
ERROR_FIELDNAMES = ["filename", "stage", "severity", "message"]
 
 
def extract_text_from_pdf(pdf_path: str) -> str:
    """Extracts raw text from a PDF file using pdfminer.six."""
    return pdfminer.high_level.extract_text(pdf_path)
 
 
def extract_text_from_image(image_path: str) -> str:
    """Extracts text from an image file using Tesseract OCR."""
    img = Image.open(image_path)
    return pytesseract.image_to_string(img)
 
 
def extract_text_from_txt(txt_path: str) -> str:
    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()
 
 
def analyze_unstructured_text(analyzer: AnalyzerEngine, text: str, language: str,
                               entities=None):
    """Run the Presidio analyzer directly on a blob of free text."""
    return analyzer.analyze(text=text, language=language, entities=entities)
 
 
def analyze_structured_csv(analyzer: AnalyzerEngine, csv_path: str, language: str,
                            entities=None):
    """
    Use presidio-structured to identify which columns of a CSV likely hold
    PII, then re-run the analyzer over the actual cell values in those
    columns so we get concrete per-value matches (offsets + scores) rather
    than just a column-level label.
 
    Returns a list of tuples:
        (column_name, row_index, RecognizerResult, cell_text)
    """
    df = pd.read_csv(csv_path, dtype=str).fillna("")
 
    # presidio-structured's default StructuredEngine already uses a Pandas
    # data processor; PandasAnalysisBuilder inspects sample values per
    # column and returns a StructuredAnalysis(entity_mapping={col: entity}).
    tabular_analysis = PandasAnalysisBuilder().generate_analysis(df, language=language)
 
    matches = []
    for column, entity_type in tabular_analysis.entity_mapping.items():
        if entities and entity_type not in entities:
            continue
        if column not in df.columns:
            continue
        for row_idx, cell_value in df[column].items():
            cell_text = str(cell_value)
            if not cell_text.strip():
                continue
            results = analyzer.analyze(
                text=cell_text,
                language=language,
                entities=[entity_type] if entity_type else None,
            )
            for res in results:
                matches.append((column, row_idx, res, cell_text))
 
    return matches
 
 
# --------------------------------------------------------------------------
# Worker-process machinery
# --------------------------------------------------------------------------
 
# One AnalyzerEngine per worker process, built once via the Pool initializer
# and reused across every file that worker handles (loading the spaCy model
# is the expensive part, so we don't want to redo it per file).
_worker_analyzer = None
 
 
def _init_worker():
    global _worker_analyzer
    # Requires: `python -m spacy download en_core_web_lg` (or the model
    # matching your chosen language) to have been run before first use.
    _worker_analyzer = AnalyzerEngine()
 
 
def process_file(task):
    """
    Runs in a worker process. Extracts + analyzes a single file and returns
    (rows, errors) as plain dicts/lists so they can be pickled back to the
    main process, which is the only place that writes to the output CSVs.
    """
    file_path, language, entities = task
    filename = os.path.basename(file_path)
    ext = file_path.lower().rsplit(".", 1)[-1]
 
    rows = []
    errors = []
 
    def log_error(stage, message, severity="error"):
        errors.append({
            "filename": filename,
            "stage": stage,
            "severity": severity,
            "message": str(message),
        })
 
    analyzer = _worker_analyzer
 
    if ext in STRUCTURED_EXTS:
        # --- Structured (CSV) path: presidio_structured + analyzer ---
        try:
            matches = analyze_structured_csv(analyzer, file_path, language, entities)
        except Exception as e:
            log_error("structured_analysis", e)
            return rows, errors
 
        for column, row_idx, res, cell_text in matches:
            rows.append({
                "filename": filename,
                "source_type": "structured_csv",
                "location": f"column={column},row={row_idx}",
                "entity_type": res.entity_type,
                "entity_value": cell_text[res.start:res.end],
                "score": res.score,
                "start_char": res.start,
                "end_char": res.end,
            })
        return rows, errors
 
    if ext not in UNSTRUCTURED_TEXT_EXTS:
        log_error("extraction", "Unsupported file type", severity="warning")
        return rows, errors
 
    # --- Unstructured path: extract text, then presidio_analyzer ---
    extracted_text = ""
    try:
        if ext == "pdf":
            extracted_text = extract_text_from_pdf(file_path)
        elif ext in {"png", "jpg", "jpeg", "tiff", "tif", "bmp"}:
            extracted_text = extract_text_from_image(file_path)
        elif ext == "txt":
            extracted_text = extract_text_from_txt(file_path)
    except Exception as e:
        log_error("extraction", e)
        return rows, errors
 
    if not extracted_text.strip():
        log_error("extraction", "No text extracted", severity="warning")
        return rows, errors
 
    try:
        results = analyze_unstructured_text(analyzer, extracted_text, language, entities)
    except Exception as e:
        log_error("analysis", e)
        return rows, errors
 
    for res in results:
        if not isinstance(res, RecognizerResult):
            continue
        rows.append({
            "filename": filename,
            "source_type": "unstructured_text",
            "location": "",
            "entity_type": res.entity_type,
            "entity_value": extracted_text[res.start:res.end],
            "score": res.score,
            "start_char": res.start,
            "end_char": res.end,
        })
 
    return rows, errors
 
 
def main():
    parser = argparse.ArgumentParser(description="OCR + PII analysis pipeline")
    parser.add_argument(
        "--input_dir",
        default="pii",
        help="Directory containing input files (default: ./pii)",
    )
    parser.add_argument(
        "--output_dir",
        default=None,
        help="Directory to write results/errors into "
             "(default: <input_dir>/results)",
    )
    parser.add_argument("--language", default="en", help="Language code for Presidio analysis")
    parser.add_argument(
        "--entities",
        default=None,
        help="Comma-separated list of entity types to restrict detection to "
             "(default: all entities Presidio supports)",
    )
    parser.add_argument(
        "--num_procs",
        type=int,
        default=int(os.environ.get("SLURM_CPUS_PER_TASK", 1)),
        help="Number of worker processes to run in parallel, one file per "
             "worker at a time (default: $SLURM_CPUS_PER_TASK if set, else 1)",
    )
    parser.add_argument(
        "--job_id",
        default=os.environ.get("SLURM_JOB_ID"),
        help="Identifier stamped into the output filenames, e.g. "
             "pii_results_<job_id>.csv (default: $SLURM_JOB_ID if set under "
             "SLURM, otherwise the files are left unstamped)",
    )
    args = parser.parse_args()
 
    entities = args.entities.split(",") if args.entities else None
    num_procs = max(1, args.num_procs)
 
    output_dir = args.output_dir or os.path.join(args.input_dir, "results")
    suffix = f"_{args.job_id}" if args.job_id else ""
    output_csv_path = os.path.join(output_dir, f"pii_results{suffix}.csv")
    error_csv_path = os.path.join(output_dir, f"pii_errors{suffix}.csv")
 
    os.makedirs(output_dir, exist_ok=True)
 
    search_path = os.path.join(args.input_dir, "*.*")
    file_paths = glob.glob(search_path)
    tasks = [(fp, args.language, entities) for fp in file_paths]
 
    with open(output_csv_path, mode="w", newline="", encoding="utf-8") as csv_file, \
         open(error_csv_path, mode="w", newline="", encoding="utf-8") as error_file:
 
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
        writer.writeheader()
 
        error_writer = csv.DictWriter(error_file, fieldnames=ERROR_FIELDNAMES)
        error_writer.writeheader()
 
        if not tasks:
            print(f"No files found in {args.input_dir}")
        elif num_procs == 1:
            # Skip the Pool entirely for num_procs=1: same code path, one
            # less process to reason about, and easier to debug locally.
            _init_worker()
            for task in tasks:
                rows, errors = process_file(task)
                writer.writerows(rows)
                error_writer.writerows(errors)
        else:
            with mp.Pool(processes=num_procs, initializer=_init_worker) as pool:
                # imap_unordered so results/errors stream to disk as each
                # file finishes, rather than waiting on the slowest one.
                for rows, errors in pool.imap_unordered(process_file, tasks):
                    writer.writerows(rows)
                    error_writer.writerows(errors)
 
    print(f"Done. Results written to {output_csv_path}")
    print(f"Errors/warnings written to {error_csv_path}")
 
 
if __name__ == "__main__":
    main()