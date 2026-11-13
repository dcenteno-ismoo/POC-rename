"""All tunable settings in one place."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

ROOT = Path(os.environ.get("DOCAGENT_ROOT", Path(__file__).resolve().parent.parent))
WORK_DIR = ROOT / "work"
OUT_DIR = ROOT / "out"
REVIEW_DIR = ROOT / "review"
COMPANIES_FILE = ROOT / "companies.json"      # no longer used: the lookup sheet replaced it

# The lookup sheet: one row per client, columns "Company" and "Code".
LOOKUP_FILE = ROOT / "lookup.xlsx"
# Record of every processed file: original name, fingerprint (SHA-256), date, new name.
RECORD_FILE = ROOT / "record.jsonl"

# A sheet code must look like this (Rescon naming rule: RFQ... or PO...). Edit if the rules grow.
CODE_FORMAT = re.compile(r"(RFQ|PO)[A-Z0-9]+")

# Slash/dash numeric dates like 03/04/2026: "MDY" (PH/US) or "DMY".
SLASH_DATE_ORDER = os.environ.get("DOCAGENT_DATE_ORDER", "MDY").upper()

PDF_TEXT_MIN_CHARS = 20      # page text shorter than this = treat page as scanned
RENDER_DPI = 200             # resolution for rendering scanned PDF pages
OCR_MIN_CONFIDENCE = 0.80    # below this, add a low-confidence warning
LIBREOFFICE_TIMEOUT_S = 90
REGISTRY_MATCH_MIN = 88      # rapidfuzz score (0-100) for an OCR-tolerant client name match

# Output name pattern: {code from lookup sheet}_{MMDDYYYY}{ext}
DATE_FORMAT_IN_NAME = "%m%d%Y"


def soffice_path() -> str | None:
    """Locate LibreOffice. Works on Windows, macOS and Linux."""
    env = os.environ.get("DOCAGENT_SOFFICE")
    if env and Path(env).exists():
        return env
    for c in (
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if c and Path(c).exists():
            return c
    return None


for d in (WORK_DIR, OUT_DIR, REVIEW_DIR):
    d.mkdir(parents=True, exist_ok=True)
