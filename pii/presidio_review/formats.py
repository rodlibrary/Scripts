"""
File-format layer for the review pipeline.

Presidio only works on plain text, so each supported format gets a handler that:
  1. extracts one plain-text string from the file (what Presidio analyzes),
  2. maps any character offset back to a human-readable location (page, paragraph, cell),
  3. writes a redacted copy in the file's own format, given a list of
     (start, end, replacement) edits expressed in offsets of that same text.

Extraction is deterministic, so analyze.py and anonymize.py both call load()
with the same settings and get identical text/offsets while the file is unchanged.

Supported:
  .txt .md .log .json .xml .html .htm   plain text, rewritten in place
  .csv .tsv .xlsx                       tables: cell-by-cell text, plus column-level
                                        detection with Presidio Structured (structured.py)
  .pdf    pdfminer.six text layer; pages without one are OCR'd with Tesseract.
          Output is a flattened (image-only) PDF with black boxes.
  .png .jpg .jpeg .tif .tiff .bmp       Tesseract OCR; output is the image with black boxes
  .docx   python-docx; body, tables, headers and footers, run formatting kept
"""
from __future__ import annotations

import bisect
import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from ocr import OcrSettings, ocr_image, tesseract_available

TEXT_EXTS = {".txt", ".md", ".log", ".json", ".xml", ".html", ".htm"}
TABLE_EXTS = {".csv", ".tsv", ".xlsx"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
SUPPORTED_EXTS = TEXT_EXTS | TABLE_EXTS | IMAGE_EXTS | {".pdf", ".docx"}

Edit = tuple[int, int, str]  # (start, end, replacement) in extracted-text offsets


@dataclass
class LoadedDoc:
    path: Path
    text: str
    # parallel lists: unit_starts[i] is the offset where unit i begins, unit_labels[i] names it
    unit_starts: list[int] = field(default_factory=list)
    unit_labels: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # per character, for visual formats: (page, line_key, box, ocr_conf|None) or None
    _boxes: list = field(default_factory=list, repr=False)

    def locate(self, offset: int) -> str:
        if not self.unit_starts:
            return ""
        i = bisect.bisect_right(self.unit_starts, offset) - 1
        return self.unit_labels[max(i, 0)]

    def ocr_confidence(self, start: int, end: int):
        """Lowest Tesseract confidence among the words in this span, or None if not OCR'd."""
        confs = [b[3] for b in self._boxes[start:end] if b is not None and b[3] is not None]
        return round(min(confs)) if confs else None

    def write_redacted(self, edits: list[Edit], dest: Path, **opts) -> None:  # pragma: no cover
        raise NotImplementedError


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTS


def load(path: Path, ocr: OcrSettings | None = None) -> LoadedDoc:
    ocr = ocr or OcrSettings()
    ext = path.suffix.lower()
    if ext in TEXT_EXTS:
        return TextDoc.load(path)
    if ext in (".csv", ".tsv"):
        return CsvDoc.load(path)
    if ext == ".xlsx":
        return XlsxDoc.load(path)
    if ext == ".pdf":
        return PdfDoc.load(path, ocr)
    if ext in IMAGE_EXTS:
        return ImageDoc.load(path, ocr)
    if ext == ".docx":
        return DocxDoc.load(path)
    raise ValueError(f"Unsupported file type: {path.suffix}")


def apply_edits(s: str, edits: list[Edit]) -> str:
    """Apply non-overlapping edits to a string, last first so offsets stay valid."""
    for start, end, repl in sorted(edits, reverse=True):
        s = s[:start] + repl + s[end:]
    return s


def _rebuild_segments(segments, edits: list[Edit]):
    """
    segments: list of (start, end) ranges that tile the text (Word runs, table cells).
    Returns, per segment, a list of pieces: (a, b) slices of the original text or
    replacement strings. A replacement goes in the segment where its span begins;
    the rest of the span is deleted from later segments.
    """
    edits = sorted(edits)
    out = []
    for seg_start, seg_end in segments:
        pieces, pos = [], seg_start
        for e_start, e_end, repl in edits:
            if e_end <= seg_start or e_start >= seg_end:
                continue
            cut_a, cut_b = max(e_start, seg_start), min(e_end, seg_end)
            pieces.append((pos, cut_a))
            if seg_start <= e_start < seg_end:
                pieces.append(repl)
            pos = cut_b
        pieces.append((pos, seg_end))
        out.append(pieces)
    return out


def _materialize(text: str, pieces) -> str:
    return "".join(text[p[0]:p[1]] if isinstance(p, tuple) else p for p in pieces)


def _rects_for_edits(boxes, edits, pad):
    """Merge the character boxes of each edit into one rectangle per text line.
    Returns {page: [(x0, y0, x1, y1), ...]} in whatever space the boxes use."""
    rects: dict[int, list] = {}
    for start, end, _ in edits:
        lines: dict = {}
        for b in boxes[start:end]:
            if b is None:
                continue
            page, line, (x0, y0, x1, y1), _conf = b
            cur = lines.get((page, line))
            lines[(page, line)] = (x0, y0, x1, y1) if cur is None else (
                min(cur[0], x0), min(cur[1], y0), max(cur[2], x1), max(cur[3], y1))
        for (page, _), (x0, y0, x1, y1) in lines.items():
            rects.setdefault(page, []).append((x0 - pad, y0 - pad, x1 + pad, y1 + pad))
    return rects


class _TextBuilder:
    """Accumulates text plus per-character boxes and unit labels."""

    def __init__(self, doc: LoadedDoc):
        self.doc, self.parts = doc, []

    @property
    def offset(self):
        return len(self.doc._boxes)

    def unit(self, label):
        self.doc.unit_starts.append(self.offset)
        self.doc.unit_labels.append(label)

    def add(self, s: str, box=None):
        self.parts.append(s)
        self.doc._boxes.extend([box] * len(s))

    def finish(self):
        self.doc.text = "".join(self.parts)


def _add_ocr(tb: _TextBuilder, result, page: int, to_space=None):
    """Append an OcrResult to the text, converting its pixel boxes with to_space()."""
    for ch, info in zip(result.text, result.chars):
        if info is None:
            tb.add(ch)
        else:
            line, box, conf = info
            tb.add(ch, (page, ("ocr", line), to_space(box) if to_space else box, conf))


def _ocr_page_warnings(doc, label, result, settings):
    if not result.text.strip():
        doc.warnings.append(f"{label}: OCR found no text. Check it visually.")
    elif result.mean_conf is not None and result.mean_conf < 70:
        doc.warnings.append(f"{label}: low OCR confidence ({result.mean_conf:.0f}/100); "
                            f"PII may have been misread or missed. Check it visually.")
    if result.rotation:
        doc.warnings.append(f"{label}: scan was rotated {result.rotation} degrees; OCR corrected it.")


# --------------------------------------------------------------------------- text
class TextDoc(LoadedDoc):
    @classmethod
    def load(cls, path: Path) -> "TextDoc":
        text, enc = _decode(path.read_bytes())
        doc = cls(path=path, text=text)
        doc._encoding = enc
        offset = 0
        for n, line in enumerate(text.splitlines(keepends=True), start=1):
            doc.unit_starts.append(offset)
            doc.unit_labels.append(f"line {n}")
            offset += len(line)
        return doc

    def write_redacted(self, edits, dest, **opts):
        # newline="" keeps the file's original line endings
        with open(dest, "w", encoding=self._encoding, newline="") as f:
            f.write(apply_edits(self.text, edits))


def _decode(raw: bytes):
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


# --------------------------------------------------------------------------- tables
@dataclass
class Table:
    name: str            # sheet title ("" for CSV)
    header_row: int      # row index of the header
    headers: dict        # col index -> header text
    data_rows: list      # row indexes below the header


class TableDoc(LoadedDoc):
    """
    Shared logic for CSV and Excel. Every analyzable cell becomes one line of text
    (span-level findings). Sheets that look like a table (header row + data rows)
    are also exposed as `tables` so structured.py can run Presidio Structured on
    whole columns.
    """

    def _init_table_state(self):
        self.tables: list[Table] = []
        self._cells: dict = {}       # (table_or_sheet, row, col) -> str value
        self._segments: list = []    # (start, end, cell_key)
        self._seg_starts: list = []

    def _build_text(self, ordered_cells, label_fn):
        tb = _TextBuilder(self)
        for key in ordered_cells:
            value = self._cells[key]
            tb.unit(label_fn(key))
            self._segments.append((tb.offset, tb.offset + len(value), key))
            tb.add(value)
            tb.add("\n")
        tb.finish()
        self._boxes = []  # tables have no visual boxes
        self._seg_starts = [s for s, _, _ in self._segments]

    def cell_at(self, offset: int):
        """Cell key (sheet, row, col) containing this text offset, or None."""
        i = bisect.bisect_right(self._seg_starts, offset) - 1
        if i >= 0:
            s, e, key = self._segments[i]
            if s <= offset < e:
                return key
        return None

    def cell_text(self, key) -> str:
        return self._cells.get(key, "")

    def column_values(self, t: int, col: int):
        """[(row, value)] for the data rows of table t, column col ('' if empty)."""
        table = self.tables[t]
        return [(r, self._cells.get((self._sheet_key(table), r, col), "")) for r in table.data_rows]

    def column_location(self, t: int, col: int) -> str:
        raise NotImplementedError

    def _sheet_key(self, table: Table):
        return table.name

    def _new_cell_values(self, edits, cell_overrides):
        values = {}
        for (s, e, key), pieces in zip(self._segments, _rebuild_segments(
                [(s, e) for s, e, _ in self._segments], edits)):
            new_text = _materialize(self.text, pieces)
            if new_text != self.text[s:e]:
                values[key] = new_text
        values.update(cell_overrides or {})  # column-level decisions win over span edits
        return values


def _col_letter(c: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(c + 1)


class CsvDoc(TableDoc):
    """First row is treated as the header. Written back with the same delimiter."""

    @classmethod
    def load(cls, path: Path) -> "CsvDoc":
        text, enc = _decode(path.read_bytes())
        doc = cls(path=path, text="")
        doc._init_table_state()
        doc._encoding = enc
        try:
            dialect = csv.Sniffer().sniff(text[:65536], delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel
        doc._dialect = dialect
        doc._rows = list(csv.reader(io.StringIO(text, newline=""), dialect))
        order = []
        for r, row in enumerate(doc._rows):
            for c, v in enumerate(row):
                if v.strip():
                    doc._cells[("", r, c)] = v
                    order.append(("", r, c))
        if len(doc._rows) >= 2:
            headers = {c: (h.strip() or f"column {c + 1}") for c, h in enumerate(doc._rows[0])}
            doc.tables.append(Table("", 0, headers, list(range(1, len(doc._rows)))))
        header = doc._rows[0] if doc._rows else []

        def label(key):
            _, r, c = key
            name = header[c].strip() if c < len(header) and header[c].strip() else _col_letter(c)
            return f"row {r + 1}, column '{name}'"
        doc._build_text(order, label)
        return doc

    def column_location(self, t, col):
        return f"column {_col_letter(col)} '{self.tables[t].headers[col]}'"

    def write_redacted(self, edits, dest, cell_overrides=None, **opts):
        rows = [list(r) for r in self._rows]
        for (_, r, c), v in self._new_cell_values(edits, cell_overrides).items():
            rows[r][c] = v
        with open(dest, "w", encoding=self._encoding, newline="") as f:
            csv.writer(f, self._dialect).writerows(rows)


class XlsxDoc(TableDoc):
    """Text and whole-number cells are analyzed; formulas, dates and decimals are
    skipped. A sheet counts as a table when its first non-empty row is all text
    and has rows below it. Redacted numeric cells become text."""

    @classmethod
    def load(cls, path: Path) -> "XlsxDoc":
        import openpyxl
        wb = openpyxl.load_workbook(str(path))
        doc = cls(path=path, text="")
        doc._init_table_state()
        doc._wb = wb
        order = []
        for ws in wb.worksheets:
            rows_seen = []
            for row in ws.iter_rows():
                row_has_value = False
                for cell in row:
                    v = cell.value
                    if isinstance(v, bool) or v is None:
                        continue
                    if isinstance(v, str) and not v.startswith("=") and v.strip():
                        t = v
                    elif isinstance(v, int):
                        t = str(v)
                    else:
                        continue
                    key = (ws.title, cell.row, cell.column - 1)
                    doc._cells[key] = t
                    order.append(key)
                    row_has_value = True
                if row_has_value:
                    rows_seen.append(row[0].row)
            if len(rows_seen) >= 2:
                hdr_row = rows_seen[0]
                hdr_cells = [c for c in ws[hdr_row] if c.value is not None]
                if hdr_cells and all(isinstance(c.value, str) for c in hdr_cells):
                    headers = {c.column - 1: c.value.strip() for c in hdr_cells}
                    doc.tables.append(Table(ws.title, hdr_row, headers, rows_seen[1:]))
        doc._build_text(order, lambda k: f"{k[0]}!{_col_letter(k[2])}{k[1]}")
        return doc

    def column_location(self, t, col):
        tbl = self.tables[t]
        return f"sheet '{tbl.name}', column {_col_letter(col)} '{tbl.headers[col]}'"

    def write_redacted(self, edits, dest, cell_overrides=None, strip_metadata=True, **opts):
        for (sheet, r, c), v in self._new_cell_values(edits, cell_overrides).items():
            self._wb[sheet].cell(row=r, column=c + 1).value = v
        if strip_metadata:
            self._wb.properties.creator = ""
            self._wb.properties.lastModifiedBy = ""
        self._wb.save(str(dest))


# --------------------------------------------------------------------------- images
class ImageDoc(LoadedDoc):
    """OCR every frame (multi-page TIFFs have several). Output: same format with black
    boxes; EXIF and other embedded metadata are not carried over."""

    @classmethod
    def load(cls, path: Path, ocr: OcrSettings) -> "ImageDoc":
        from PIL import Image, ImageSequence
        doc = cls(path=path, text="")
        img = Image.open(path)
        doc._format = img.format
        doc._frames = [f.copy() for f in ImageSequence.Iterator(img)]
        tb = _TextBuilder(doc)
        if ocr.mode == "off" or not tesseract_available():
            doc.warnings.append("image file was NOT analyzed (OCR is off or Tesseract is not installed).")
            tb.finish()
            return doc
        for i, frame in enumerate(doc._frames):
            label = f"page {i + 1}" if len(doc._frames) > 1 else "image"
            tb.unit(label)
            result = ocr_image(frame, ocr)
            _add_ocr(tb, result, i)
            tb.add("\n\f\n")
            _ocr_page_warnings(doc, label, result, ocr)
        tb.finish()
        return doc

    def write_redacted(self, edits, dest, **opts):
        from PIL import ImageDraw
        rects = _rects_for_edits(self._boxes, edits, pad=3)
        out = []
        for i, frame in enumerate(self._frames):
            frame = frame.convert("RGB")
            draw = ImageDraw.Draw(frame)
            for x0, y0, x1, y1 in rects.get(i, []):
                draw.rectangle([x0, y0, x1, y1], fill="black")
            out.append(frame)
        fmt = self._format or None
        kwargs = {"quality": 95} if fmt == "JPEG" else {}
        if len(out) > 1:
            out[0].save(dest, fmt, save_all=True, append_images=out[1:], **kwargs)
        else:
            out[0].save(dest, fmt, **kwargs)


# --------------------------------------------------------------------------- PDF
class PdfDoc(LoadedDoc):
    """
    Text comes from pdfminer.six with a bounding box per character, so a span of
    text maps to rectangles on the page. Pages with no text layer (scans) are
    rendered and OCR'd with Tesseract instead. Redaction renders every page to an
    image (pypdfium2), paints the rectangles black and saves an image-only PDF.
    Rasterizing is what makes it a real redaction: the text layer, hidden text,
    annotations, form fields, attachments and metadata are all gone from the output.
    Character boxes are stored in PDF user space so any page rotation works.
    """

    @classmethod
    def load(cls, path: Path, ocr: OcrSettings) -> "PdfDoc":
        import pypdfium2 as pdfium
        from pdfminer.converter import PDFPageAggregator
        from pdfminer.layout import LAParams, LTChar, LTFigure, LTImage, LTTextContainer
        from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
        from pdfminer.pdfpage import PDFPage

        doc = cls(path=path, text="")
        tb = _TextBuilder(doc)
        can_ocr = ocr.mode != "off" and tesseract_available()
        pdf = pdfium.PdfDocument(str(path)) if can_ocr else None

        def walk_images(obj):
            if isinstance(obj, LTImage):
                return True
            if isinstance(obj, LTFigure):
                return any(walk_images(c) for c in obj)
            return False

        def layout_quality(layout):
            """Average characters per text line; sideways text read in the wrong
            orientation collapses to ~1 char per line."""
            chars = lines = 0
            for el in layout:
                if isinstance(el, LTTextContainer):
                    for line in el:
                        lines += 1
                        chars += sum(isinstance(c, LTChar) for c in getattr(line, "_objs", []))
            return chars / lines if lines else 0

        rsrc = PDFResourceManager()
        device = PDFPageAggregator(rsrc, laparams=LAParams(all_texts=True))
        interp = PDFPageInterpreter(rsrc, device)
        line_id = 0

        with open(path, "rb") as fp:
            for page_idx, page in enumerate(PDFPage.get_pages(fp)):
                label = f"page {page_idx + 1}"
                tb.unit(label)

                # Text may be upright in the PDF's own coordinates or only after the
                # page's /Rotate is applied. Try both, keep the one that reads as lines.
                display_rot = page.rotate % 360
                best = None
                for rot in dict.fromkeys([display_rot, 0]):
                    page.rotate = rot
                    interp.process_page(page)
                    layout = device.get_result()
                    q = layout_quality(layout)
                    if best is None or q > best[0]:
                        best = (q, rot, layout)
                page.rotate = display_rot
                _, used_rot, layout = best
                to_user = _pdfminer_user_transform(page.mediabox, used_rot)

                has_text = any(isinstance(el, LTTextContainer) and el.get_text().strip() for el in layout)
                has_image = any(walk_images(el) for el in layout)

                if can_ocr and (ocr.mode == "always" or not has_text):
                    result = cls._ocr_page(pdf, page_idx, ocr)
                    _add_ocr(tb, result[0], page_idx, result[1])
                    _ocr_page_warnings(doc, label, result[0], ocr)
                else:
                    for element in layout:
                        if not isinstance(element, LTTextContainer):
                            continue
                        for line in element:
                            line_id += 1
                            for ch in (line if hasattr(line, "__iter__") else [line]):
                                t = ch.get_text()
                                if isinstance(ch, LTChar):
                                    pts = [to_user(x, y) for x, y in _corners(ch.bbox)]
                                    ubox = (min(p[0] for p in pts), min(p[1] for p in pts),
                                            max(p[0] for p in pts), max(p[1] for p in pts))
                                    tb.add(t, (page_idx, line_id, ubox, None))  # ligatures share a box
                                else:  # LTAnno: spaces/newlines pdfminer inferred from layout
                                    tb.add(t)
                    if not has_text and has_image:
                        doc.warnings.append(
                            f"{label} has images but no text layer (scanned?) and OCR is "
                            f"{'unavailable' if ocr.mode != 'off' else 'off'}. It was NOT analyzed.")
                    elif has_image:
                        doc.warnings.append(
                            f"{label} contains images; text inside them is not analyzed "
                            f"(use --ocr always to OCR the whole page).")
                tb.add("\n\f\n")  # page break

        if pdf is not None:
            pdf.close()
        tb.finish()
        if not doc.text.strip():
            doc.warnings.insert(0, "no text found at all (blank, or scanned with OCR unavailable).")
        return doc

    @staticmethod
    def _ocr_page(pdf, page_idx, ocr: OcrSettings):
        """Render one page, OCR it, and return (result, pixel-box -> user-space function)."""
        page = pdf[page_idx]
        scale = ocr.dpi / 72
        img = page.render(scale=scale).to_pil()
        to_display = _pdfminer_display_transform(page.get_mediabox(), page.get_rotation())
        to_user = _pdfminer_user_transform(page.get_mediabox(), page.get_rotation())
        crop_pts = [to_display(x, y) for x, y in _corners(page.get_cropbox())]
        crop_left = min(p[0] for p in crop_pts)
        crop_top = max(p[1] for p in crop_pts)

        def px_to_user(box):
            x0, y0, x1, y1 = box
            pts = [to_user(crop_left + x / scale, crop_top - y / scale)
                   for x, y in ((x0, y0), (x1, y1))]
            return (min(p[0] for p in pts), min(p[1] for p in pts),
                    max(p[0] for p in pts), max(p[1] for p in pts))

        return ocr_image(img, ocr), px_to_user

    def write_redacted(self, edits, dest, dpi: int = 200, text_layer: bool = True,
                       ocr_lang: str = "eng", **opts):
        """Returns a list of warnings (empty if all went well)."""
        import pypdfium2 as pdfium
        from PIL import ImageDraw

        rects = _rects_for_edits(self._boxes, edits, pad=1.0)
        scale = dpi / 72
        pdf = pdfium.PdfDocument(str(self.path))
        images, ocr_images, rotations = [], [], []
        for i in range(len(pdf)):
            page = pdf[i]
            # pdfium renders the CropBox with the page's /Rotate applied
            img = page.render(scale=scale).to_pil().convert("RGB")
            to_display = _pdfminer_display_transform(page.get_mediabox(), page.get_rotation())

            def display_box(box):
                pts = [to_display(x, y) for x, y in _corners(box)]
                return (min(p[0] for p in pts), min(p[1] for p in pts),
                        max(p[0] for p in pts), max(p[1] for p in pts))

            crop_left, _, _, crop_top = display_box(page.get_cropbox())
            px_rects = []
            for rect in rects.get(i, []):
                x0, y0, x1, y1 = display_box(rect)
                px_rects.append([(x0 - crop_left) * scale, (crop_top - y1) * scale,
                                 (x1 - crop_left) * scale, (crop_top - y0) * scale])
            # the text layer is OCR'd from a copy with the redactions whited out, so
            # neither the hidden text nor black-box "junk characters" can enter it
            ocr_img = img.copy()
            # orientation is judged on the full page (only the angle is kept): with
            # redactions removed there may be too little text left to decide
            if text_layer and tesseract_available():
                from ocr import _detect_rotation
                rotations.append(_detect_rotation(img))
            draw, draw_ocr = ImageDraw.Draw(img), ImageDraw.Draw(ocr_img)
            for r in px_rects:
                draw.rectangle(r, fill="black")
                draw_ocr.rectangle(r, fill="white")
            images.append(img)
            ocr_images.append(ocr_img)
        pdf.close()

        warnings = []
        if text_layer:
            if tesseract_available():
                layered = self._pdf_with_text_layer(images, ocr_images, rotations, dpi, ocr_lang)
                leaked = self._leaked_strings(layered, edits)
                if leaked:
                    # Should be impossible (OCR only sees the blacked-out pages), but a
                    # redaction tool must not trust that: fall back to image-only output.
                    warnings.append(
                        f"searchable layer contained redacted text ({', '.join(sorted(leaked))[:80]}); "
                        f"saved WITHOUT a text layer instead. Check this file.")
                else:
                    layered.save(str(dest))
                    layered.close()
                    return warnings
            else:
                warnings.append("Tesseract not available; saved without a searchable text layer.")
        images[0].save(dest, "PDF", save_all=True, append_images=images[1:], resolution=dpi,
                       title="Redacted document", author="", producer="")
        return warnings

    @staticmethod
    def _pdf_with_text_layer(images, ocr_images, rotations, dpi, lang):
        """Build the output PDF: each page is the redacted (black-box) image with an
        invisible OCR text layer on it, so the PDF is searchable and selectable.
        The text comes from Tesseract run on a copy with the redactions whited out,
        so it never contains redacted text."""
        import io
        import pypdfium2 as pdfium
        import pytesseract
        out = pdfium.PdfDocument.new()
        for i, (img, ocr_img, rot) in enumerate(zip(images, ocr_images, rotations)):
            # OCR upright (sideways/upside-down pages otherwise give gibberish), then
            # flag the page with /Rotate so it still displays exactly as before
            if rot:
                img, ocr_img = img.rotate(-rot, expand=True), ocr_img.rotate(-rot, expand=True)
            text_pdf = pytesseract.image_to_pdf_or_hocr(
                ocr_img, extension="pdf", lang=lang, config=f"--dpi {dpi} -c textonly_pdf=1")
            src = pdfium.PdfDocument(text_pdf)
            out.import_pages(src)
            src.close()
            page = out[i]
            w, h = page.get_size()
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85)
            buf.seek(0)
            pic = pdfium.PdfImage.new(out)
            pic.load_jpeg(buf, inline=False, autoclose=False)
            pic.set_matrix(pdfium.PdfMatrix().scale(w, h))
            page.insert_obj(pic)
            page.gen_content()
            if rot:
                page.set_rotation((360 - rot) % 360)
        return out

    def _leaked_strings(self, pdf, edits):
        """Redacted strings that show up in the new text layer. Strings that also
        occur un-redacted elsewhere in the document (a KEEP elsewhere) are ignored."""
        norm = lambda s: " ".join(s.split()).lower()
        layer = norm(" ".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf))))
        source = norm(self.text)
        leaked = set()
        for s, e, _ in edits:
            target = norm(self.text[s:e])
            if len(target) < 3:
                continue
            redacted_count = sum(1 for a, b, _ in edits if norm(self.text[a:b]) == target)
            if source.count(target) <= redacted_count and target in layer:
                leaked.add(self.text[s:e])
        return leaked


def _corners(box):
    x0, y0, x1, y1 = box
    return [(x0, y0), (x0, y1), (x1, y0), (x1, y1)]


def _pdfminer_display_transform(mediabox, rotation):
    """Same matrix pdfminer's PDFPageInterpreter.process_page uses for /Rotate."""
    x0, y0, x1, y1 = mediabox
    r = rotation % 360
    if r == 90:
        return lambda x, y: (y - y0, x1 - x)
    if r == 180:
        return lambda x, y: (x1 - x, y1 - y)
    if r == 270:
        return lambda x, y: (y1 - y, x - x0)
    return lambda x, y: (x - x0, y - y0)


def _pdfminer_user_transform(mediabox, rotation):
    """Inverse of _pdfminer_display_transform: pdfminer coords -> PDF user space."""
    x0, y0, x1, y1 = mediabox
    r = rotation % 360
    if r == 90:
        return lambda dx, dy: (x1 - dy, dx + y0)
    if r == 180:
        return lambda dx, dy: (x1 - dx, y1 - dy)
    if r == 270:
        return lambda dx, dy: (dy + x0, y1 - dx)
    return lambda dx, dy: (dx + x0, dy + y0)


# --------------------------------------------------------------------------- DOCX
class DocxDoc(LoadedDoc):
    """
    Text is built from Word *runs* (stretches of same-formatted text), one paragraph
    per line. Redaction rewrites only the affected runs, so formatting is preserved.
    Text boxes, comments, footnotes and embedded objects are not covered.
    """

    @staticmethod
    def _iter_paragraphs(doc):
        def from_container(container, label):
            for p in container.paragraphs:
                yield label, p
            for t_idx, table in enumerate(container.tables, start=1):
                for r_idx, row in enumerate(table.rows, start=1):
                    seen = set()
                    for c_idx, cell in enumerate(row.cells, start=1):
                        if id(cell._tc) in seen:  # merged cells repeat
                            continue
                        seen.add(id(cell._tc))
                        yield from from_container(cell, f"{label} table {t_idx} r{r_idx}c{c_idx}")

        yield from from_container(doc, "body")
        for s_idx, section in enumerate(doc.sections, start=1):
            for kind in ("header", "first_page_header", "even_page_header",
                         "footer", "first_page_footer", "even_page_footer"):
                part = getattr(section, kind)
                if not part.is_linked_to_previous:
                    yield from from_container(part, f"section {s_idx} {kind.replace('_', ' ')}")

    @staticmethod
    def _runs(paragraph):
        from docx.text.hyperlink import Hyperlink
        for item in paragraph.iter_inner_content():
            if isinstance(item, Hyperlink):
                yield from item.runs
            else:
                yield item

    @classmethod
    def load(cls, path: Path) -> "DocxDoc":
        import docx
        d = docx.Document(str(path))
        doc = cls(path=path, text="")
        doc._docx = d
        doc._segments = []  # (start, end, run)
        parts, offset, n = [], 0, 0
        for label, para in cls._iter_paragraphs(d):
            n += 1
            doc.unit_starts.append(offset)
            doc.unit_labels.append(f"{label}, paragraph {n}")
            for run in cls._runs(para):
                t = run.text
                if t:
                    doc._segments.append((offset, offset + len(t), run))
                    parts.append(t)
                    offset += len(t)
            parts.append("\n")
            offset += 1
        doc.text = "".join(parts)
        return doc

    def write_redacted(self, edits, dest, strip_metadata: bool = True, **opts):
        new = _rebuild_segments([(s, e) for s, e, _ in self._segments], edits)
        for (s, e, run), pieces in zip(self._segments, new):
            new_text = _materialize(self.text, pieces)
            if new_text != self.text[s:e]:
                run.text = new_text
        if strip_metadata:
            cp = self._docx.core_properties
            cp.author = cp.last_modified_by = ""
        self._docx.save(str(dest))
