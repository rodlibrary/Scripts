"""
Tesseract OCR helpers.

ocr_image() turns a PIL image into plain text plus, for every character, the pixel
box of the word it belongs to and Tesseract's confidence for that word. The text is
what Presidio analyzes; the boxes are where redaction rectangles get drawn.

Results are cached on disk (keyed by image content + settings) so anonymize.py
reuses analyze.py's OCR instead of running it again, and gets identical text.
The cache holds extracted text, so treat it as sensitive as the documents
themselves; delete it when the job is done.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytesseract
from PIL import Image


@dataclass
class OcrSettings:
    mode: str = "auto"         # off | auto (only pages/images without a text layer) | always
    lang: str = "eng"          # Tesseract language(s), e.g. "eng+spa"
    dpi: int = 300             # render resolution for PDF pages
    min_conf: int = 0          # ignore words below this confidence (0-100)
    auto_rotate: bool = True   # use Tesseract OSD to fix sideways/upside-down scans
    cache_dir: Path | None = None

    def to_dict(self):
        return {"mode": self.mode, "lang": self.lang, "dpi": self.dpi,
                "min_conf": self.min_conf, "auto_rotate": self.auto_rotate}


@dataclass
class OcrResult:
    text: str
    # per character: (line_id, (x0, y0, x1, y1) in ORIGINAL image pixels, conf) or None
    chars: list = field(default_factory=list)
    mean_conf: float | None = None
    rotation: int = 0


def tesseract_available() -> bool:
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _detect_rotation(img: Image.Image) -> int:
    """Degrees clockwise the image must be turned to read upright (0/90/180/270)."""
    try:
        osd = pytesseract.image_to_osd(img, config="--psm 0")
        m = re.search(r"Rotate:\s+(\d+)", osd)
        conf = re.search(r"Orientation confidence:\s+([\d.]+)", osd)
        if m and conf and float(conf.group(1)) >= 2.0:
            return int(m.group(1)) % 360
    except pytesseract.TesseractError:
        pass  # too little text to decide; assume upright
    return 0


def _unrotate_box(box, rotation, w, h):
    """Map a box from the upright (rotated-clockwise) image back to the original image."""
    x0, y0, x1, y1 = box
    if rotation == 90:      # upright(x,y) <- original(y, h - x)
        pts = [(y, h - x) for x, y in ((x0, y0), (x1, y1))]
    elif rotation == 180:
        pts = [(w - x, h - y) for x, y in ((x0, y0), (x1, y1))]
    elif rotation == 270:
        pts = [(w - y, x) for x, y in ((x0, y0), (x1, y1))]
    else:
        return box
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _cache_key(img: Image.Image, settings: OcrSettings) -> str:
    h = hashlib.sha256()
    h.update(img.mode.encode() + str(img.size).encode())
    h.update(img.tobytes())
    h.update(json.dumps(settings.to_dict(), sort_keys=True).encode())
    h.update(str(pytesseract.get_tesseract_version()).encode())
    return h.hexdigest()


def ocr_image(img: Image.Image, settings: OcrSettings) -> OcrResult:
    img = img.convert("RGB")
    cache_file = None
    if settings.cache_dir:
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = settings.cache_dir / f"{_cache_key(img, settings)}.json"
        if cache_file.exists():
            d = json.loads(cache_file.read_text(encoding="utf-8"))
            chars = [None if c is None else (c[0], tuple(c[1]), c[2]) for c in d["chars"]]
            return OcrResult(d["text"], chars, d["mean_conf"], d["rotation"])

    w, h = img.size
    rotation = _detect_rotation(img) if settings.auto_rotate else 0
    upright = img.rotate(-rotation, expand=True) if rotation else img

    data = pytesseract.image_to_data(upright, lang=settings.lang,
                                     output_type=pytesseract.Output.DICT)
    parts, chars, confs = [], [], []
    prev_line, prev_block, line_id = None, None, 0
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        conf = float(data["conf"][i])
        if not word or conf < settings.min_conf:
            continue
        block = (data["page_num"][i], data["block_num"][i])
        line = block + (data["par_num"][i], data["line_num"][i])
        if prev_line is not None:
            sep = " " if line == prev_line else ("\n\n" if block != prev_block else "\n")
            parts.append(sep)
            chars.extend([None] * len(sep))
        if line != prev_line:
            line_id += 1
        box = (data["left"][i], data["top"][i],
               data["left"][i] + data["width"][i], data["top"][i] + data["height"][i])
        box = _unrotate_box(box, rotation, w, h)
        parts.append(word)
        chars.extend([(line_id, box, conf)] * len(word))
        confs.append(conf)
        prev_line, prev_block = line, block

    result = OcrResult("".join(parts), chars,
                       round(sum(confs) / len(confs), 1) if confs else None, rotation)
    if cache_file:
        cache_file.write_text(json.dumps({
            "text": result.text, "chars": result.chars,
            "mean_conf": result.mean_conf, "rotation": result.rotation}), encoding="utf-8")
    return result
