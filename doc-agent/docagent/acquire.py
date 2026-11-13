"""Flow 3: get text out of every supported file type -> normalized record."""
from __future__ import annotations

import csv
import email
import email.policy
import io
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

import pymupdf
from PIL import Image

from . import config
from .detect import Detected
from .ocr import ocr_image


@dataclass
class Page:
    lines: list[str]
    method: str                 # direct | native | pdf_text | ocr
    confidence: float = 1.0


@dataclass
class Record:
    pages: list[Page] = field(default_factory=list)
    meta: dict = field(default_factory=dict)       # e.g. email header date
    warnings: list[str] = field(default_factory=list)

    @property
    def lines(self) -> list[str]:
        return [ln for p in self.pages for ln in p.lines]

    @property
    def method(self) -> str:
        ms = sorted({p.method for p in self.pages})
        return "+".join(ms) if ms else "none"

    @property
    def ocr_confidence(self) -> float | None:
        cs = [p.confidence for p in self.pages if p.method == "ocr"]
        return round(sum(cs) / len(cs), 3) if cs else None


# ----------------------------------------------------------------- helpers
def _clean(lines) -> list[str]:
    out = []
    for ln in lines:
        ln = " ".join(str(ln).replace(" ", " ").split())
        if ln:
            out.append(ln)
    return out


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def _row_line(cells: list[str]) -> str | None:
    cells = [c for c in cells if c]
    if not cells:
        return None
    if len(cells) == 2 and cells[0].endswith(":"):
        return f"{cells[0]} {cells[1]}"
    return " | ".join(cells)


def _table_lines(rows: list[list[str]]) -> list[str]:
    """Header-style tables (row 1 = names, rows 2..n = values) become 'name: value' lines."""
    rows = [r for r in rows if any(c for c in r)]
    if len(rows) >= 2:
        hdr = rows[0]
        headerish = all(h and not any(ch.isdigit() for ch in h) for h in hdr) and len(hdr) >= 3
        if headerish and len(rows) <= 4:
            out = []
            for r in rows[1:]:
                for h, v in zip(hdr, r):
                    if v:
                        out.append(f"{h.replace('_', ' ')}: {v}")
            return out
    return [ln for r in rows if (ln := _row_line(r))]


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section",
             "header", "footer", "title"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _html_to_lines(html: str) -> list[str]:
    p = _HTMLText()
    p.feed(html)
    lines = []
    for ln in "".join(p.parts).split("\n"):
        ln = ln.strip().strip("|").strip()
        if ln:
            lines.append(ln)
    return _clean(lines)


# ---------------------------------------------------------------- path A
def _path_a(path: Path, ext: str, rec: Record) -> None:
    if ext == "eml":
        msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
        rec.meta["email_date"] = msg.get("Date")
        lines = [f"Subject: {msg.get('Subject', '')}"]
        body = msg.get_body(preferencelist=("plain", "html"))
        if body is not None:
            content = body.get_content()
            lines += _html_to_lines(content) if body.get_content_subtype() == "html" else content.splitlines()
        rec.pages.append(Page(_clean(lines), "direct"))
        return

    text = _read_text(path)
    if ext in ("html", "htm"):
        lines = _html_to_lines(text)
    elif ext in ("csv", "tsv"):
        dialect = csv.excel_tab if ext == "tsv" else csv.excel
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), dialect)]
        lines = _table_lines(rows)
    elif ext == "md":
        lines = []
        for ln in text.splitlines():
            ln = ln.strip().lstrip("#").strip().replace("**", "").replace("__", "")
            if set(ln) <= set("|-: "):
                continue
            lines.append(ln.strip("|").strip())
    else:
        lines = text.splitlines()
    rec.pages.append(Page(_clean(lines), "direct"))


# ---------------------------------------------------------------- path B
def _docx(path: Path, rec: Record) -> None:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(str(path))
    lines: list[str] = []
    for s in d.sections:                         # letterhead often lives in the page header
        for p in s.header.paragraphs:
            lines.append(p.text)
    for child in d.element.body.iterchildren():  # body in document order
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append(Paragraph(child, d).text)
        elif tag == "tbl":
            t = Table(child, d)
            rows = [[c.text.strip() for c in r.cells] for r in t.rows]
            lines += _table_lines(rows)
    for s in d.sections:
        for p in s.footer.paragraphs:
            lines.append(p.text)
    rec.pages.append(Page(_clean(lines), "native"))


def _xlsx(path: Path, rec: Record) -> None:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    for ws in wb.worksheets:
        rows = [[_cell(v) for v in r] for r in ws.iter_rows(values_only=True)]
        lines = [ln for r in rows if (ln := _row_line(r))]
        if lines:
            rec.pages.append(Page(_clean(lines), "native"))
    wb.close()


def _pptx(path: Path, rec: Record) -> None:
    from pptx import Presentation
    prs = Presentation(str(path))
    for slide in prs.slides:
        lines: list[str] = []
        for sh in slide.shapes:
            if sh.has_text_frame:
                lines += [p.text for p in sh.text_frame.paragraphs]
            if getattr(sh, "has_table", False) and sh.has_table:
                rows = [[c.text.strip() for c in r.cells] for r in sh.table.rows]
                lines += _table_lines(rows)
        rec.pages.append(Page(_clean(lines), "native"))


# ---------------------------------------------------------------- path C
def _to_pdf_with_libreoffice(path: Path, workdir: Path) -> Path:
    exe = config.soffice_path()
    if not exe:
        raise RuntimeError("LibreOffice not found. Install it or set DOCAGENT_SOFFICE.")
    profile = (config.WORK_DIR / "lo_profile").resolve()
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [exe, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
           "--convert-to", "pdf", "--outdir", str(workdir), str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=config.LIBREOFFICE_TIMEOUT_S)
    out = workdir / f"{path.stem}.pdf"
    if not out.exists():
        raise RuntimeError("LibreOffice did not produce a PDF.")
    return out


# ---------------------------------------------------------------- path D / E
def _pdf(path: Path, rec: Record) -> None:
    doc = pymupdf.open(str(path))
    for page in doc:
        text = page.get_text("text", sort=True)
        if len(text.strip()) > config.PDF_TEXT_MIN_CHARS:
            rec.pages.append(Page(_clean(text.splitlines()), "pdf_text"))
            continue
        pix = page.get_pixmap(dpi=config.RENDER_DPI)
        img = Image.frombytes("RGB" if pix.n < 4 else "RGBA", (pix.width, pix.height), pix.samples)
        _image(img, rec)
    doc.close()


def _image(img: Image.Image, rec: Record) -> None:
    r = ocr_image(img)
    rec.pages.append(Page(_clean(r.lines), "ocr", r.confidence))
    if r.confidence < config.OCR_MIN_CONFIDENCE:
        rec.warnings.append(f"low OCR confidence ({r.confidence:.2f})")


def _open_image(path: Path) -> Image.Image:
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    img = Image.open(str(path))
    img.load()
    return img


# ---------------------------------------------------------------- entry
def acquire(path: Path, det: Detected) -> Record:
    rec = Record()
    if det.path == "A":
        _path_a(path, det.ext, rec)
    elif det.path == "B":
        {"docx": _docx, "xlsx": _xlsx, "pptx": _pptx}[det.ext](path, rec)
    elif det.path == "C":
        with tempfile.TemporaryDirectory(dir=config.WORK_DIR) as td:
            pdf = _to_pdf_with_libreoffice(path, Path(td))
            _pdf(pdf, rec)
    elif det.path == "D":
        _pdf(path, rec)
    elif det.path == "E":
        _image(_open_image(path), rec)
    else:
        raise ValueError(f"unsupported file type: .{det.ext}")
    return rec
