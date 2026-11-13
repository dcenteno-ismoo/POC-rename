"""Flow 4: image preparation and OCR (RapidOCR, CPU, bundled models = deterministic)."""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps

_engine = None
_lock = threading.Lock()


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        _engine = RapidOCR(params={"Global.log_level": "critical"})
    return _engine


@dataclass
class OcrResult:
    lines: list[str]
    confidence: float      # mean score over all text boxes, 0..1
    skew_deg: float


def prepare(img: Image.Image) -> Image.Image:
    """EXIF rotate, flatten transparency, grayscale+autocontrast, upscale small images."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = bg
    img = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    long_side = max(img.size)
    if long_side < 1400:
        s = 1400 / long_side
        img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    elif long_side > 3500:
        s = 3500 / long_side
        img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    return img.convert("RGB")


def _group_lines(boxes, txts) -> tuple[list[str], float]:
    """Join text boxes into reading-order lines. Corrects skew using the boxes' own angle."""
    if not len(boxes):
        return [], 0.0
    angles = []
    for b in boxes:
        (x0, y0), (x1, y1) = b[0], b[1]
        if x1 - x0 > 20:
            angles.append(math.atan2(y1 - y0, x1 - x0))
    ang = float(np.median(angles)) if angles else 0.0
    ca, sa = math.cos(-ang), math.sin(-ang)

    items = []
    for b, t in zip(boxes, txts):
        pts = np.asarray(b, dtype=float)
        xs = pts[:, 0] * ca - pts[:, 1] * sa
        ys = pts[:, 0] * sa + pts[:, 1] * ca
        items.append((ys.mean(), xs.min(), ys.max() - ys.min(), t))
    items.sort(key=lambda i: (i[0], i[1]))

    lines: list[list] = []
    for it in items:
        if lines and abs(it[0] - lines[-1][0][0]) < 0.6 * max(it[2], lines[-1][0][2]):
            lines[-1].append(it)
        else:
            lines.append([it])
    out = [" ".join(t for *_, t in sorted(ln, key=lambda i: i[1])) for ln in lines]
    return out, math.degrees(ang)


def ocr_image(img: Image.Image) -> OcrResult:
    img = prepare(img)
    with _lock:
        r = _get_engine()(np.array(img))
    if r is None or r.boxes is None or not len(r.txts):
        return OcrResult([], 0.0, 0.0)
    lines, skew = _group_lines(r.boxes, r.txts)
    conf = float(np.mean([float(s) for s in r.scores])) if len(r.scores) else 0.0
    return OcrResult(lines, conf, skew)
