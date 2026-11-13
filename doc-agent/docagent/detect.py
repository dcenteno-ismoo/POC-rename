"""Flow 2: find the real file type from its bytes, not its extension."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import filetype

TEXT_EXTS = {"txt", "md", "csv", "tsv", "html", "htm", "eml", "json", "xml", "log"}
OFFICE_NATIVE = {"docx", "xlsx", "pptx"}
OFFICE_CONVERT = {"doc", "rtf", "odt", "ods", "odp", "xls", "ppt", "wpd"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "bmp", "webp", "gif"}

# filetype names -> our canonical extension
CANON = {"jpeg": "jpg", "tif": "tiff", "heif": "heic", "htm": "html"}


@dataclass
class Detected:
    ext: str            # canonical extension of the REAL type (used in the new file name)
    path: str           # A text, B office native, C office convert, D pdf, E image, X unsupported
    by: str             # "bytes" or "extension"
    claimed_ext: str    # extension in the original file name
    mismatch: bool


def _route(ext: str) -> str:
    if ext in TEXT_EXTS:
        return "A"
    if ext in OFFICE_NATIVE:
        return "B"
    if ext in OFFICE_CONVERT:
        return "C"
    if ext == "pdf":
        return "D"
    if ext in IMAGE_EXTS:
        return "E"
    return "X"


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return sum(32 <= b < 127 or b in (9, 10, 13) for b in head) / len(head) > 0.85


def detect(path: Path, original_name: str | None = None) -> Detected:
    claimed = Path(original_name or path.name).suffix.lower().lstrip(".")
    claimed = CANON.get(claimed, claimed)
    head = path.read_bytes()[:8192]

    kind = filetype.guess(head)
    ext, by = None, "bytes"
    if kind is not None:
        ext = CANON.get(kind.extension, kind.extension)
        # OOXML files are ZIPs; filetype may say "zip" for some writers.
        if ext == "zip" and claimed in OFFICE_NATIVE:
            ext = claimed
        # RTF and old .doc
        if ext == "rtf" or head.startswith(b"{\\rtf"):
            ext = "rtf"
    elif head.startswith(b"{\\rtf"):
        ext = "rtf"
    elif head.startswith(b"%PDF"):
        ext = "pdf"

    if ext is None:
        by = "extension"
        if claimed in TEXT_EXTS or (_looks_like_text(head) and claimed not in OFFICE_NATIVE | {"pdf"}):
            ext = claimed if claimed in TEXT_EXTS else "txt"
        else:
            ext = claimed

    # A text file whose extension says html/eml/csv/md: keep that sub-type.
    if ext == "txt" and claimed in TEXT_EXTS:
        ext = claimed

    return Detected(ext=ext, path=_route(ext), by=by, claimed_ext=claimed,
                    mismatch=bool(claimed) and claimed != ext)
