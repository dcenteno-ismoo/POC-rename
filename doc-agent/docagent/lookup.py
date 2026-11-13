"""The lookup sheet (lookup.xlsx): which client a document belongs to, and that client's code.

The sheet has one row per client and two columns, "Company" and "Code":

    Company                             Code
    Container Corporation Philippines   POCCC123
    Goulds Pumps                        RFQABC543

The system looks for a client's name, or its code, anywhere in the document text.
Edit the sheet in Excel; the change is picked up on the next file (no restart).
"""
from __future__ import annotations

import re
from functools import lru_cache

from openpyxl import load_workbook
from rapidfuzz import fuzz

from . import config
from .extract import Company, RegistryEntry, compact, core_name

NAME_HEADERS = {"company", "companyname", "client", "clientname", "customer", "name"}
CODE_HEADERS = {"code", "companycode", "clientcode"}


def sheet_exists() -> bool:
    return config.LOOKUP_FILE.exists()


def _norm_code(v) -> str:
    return re.sub(r"\s+", "", str(v or "")).upper()


@lru_cache(maxsize=2)
def _load(mtime: float, size: int) -> tuple[RegistryEntry, ...]:
    try:
        wb = load_workbook(config.LOOKUP_FILE, read_only=True, data_only=True)
    except PermissionError as e:
        raise ValueError(f"{config.LOOKUP_FILE.name} is locked by another program; close it and try again") from e
    try:
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    ci = ki = hdr = None
    for n, row in enumerate(rows[:10]):
        heads = [compact(str(c or "")) for c in row]
        c = next((j for j, h in enumerate(heads) if h in NAME_HEADERS), None)
        k = next((j for j, h in enumerate(heads) if h in CODE_HEADERS), None)
        if c is not None and k is not None:
            ci, ki, hdr = c, k, n
            break
    if hdr is None:
        raise ValueError(f"{config.LOOKUP_FILE.name} needs a header row with the columns 'Company' and 'Code'")
    out: list[RegistryEntry] = []
    for row in rows[hdr + 1:]:
        name = str(row[ci] if ci < len(row) and row[ci] is not None else "").strip()
        code = _norm_code(row[ki] if ki < len(row) else "")
        if name and code:
            out.append(RegistryEntry(name, code, [k for k in [core_name(name)] if k]))
    return tuple(out)


def entries() -> tuple[RegistryEntry, ...]:
    """Every client in the sheet. Empty if the sheet is missing; raises ValueError if it is unreadable."""
    if not sheet_exists():
        return ()
    st = config.LOOKUP_FILE.stat()
    return _load(st.st_mtime, st.st_size)


def find_client(lines: list[str]) -> tuple[Company | None, list[str]]:
    """Find the sheet's client in the document text. Returns (best match, names of other clients also seen).

    A code printed on the document is the strongest evidence, then the company name.
    A fuzzy (OCR-tolerant) name match is only tried when nothing matched exactly.
    """
    ents = entries()
    if not ents or not lines:
        return None, []
    hits: dict[str, tuple[float, int, str, str, RegistryEntry]] = {}

    def note(e: RegistryEntry, score: float, i: int, how: str, ev: str) -> None:
        cur = hits.get(e.code)
        if cur is None or (score, -i) > (cur[0], -cur[1]):
            hits[e.code] = (score, i, how, ev, e)

    n = len(lines)
    code_rx = {e.code: re.compile(r"(?<![A-Z0-9])" + re.escape(e.code) + r"(?![A-Z0-9])") for e in ents}
    for i in range(n):
        for span in (1, 2):                              # a name can wrap onto the next line
            if i + span > n:
                continue
            text = " ".join(lines[i:i + span])
            ctext, up = compact(text), text.upper()
            for e in ents:
                if code_rx[e.code].search(up):
                    note(e, 110, i, "lookup_code", lines[i])
                if any(len(k) >= 4 and k in ctext for k in e.keys):
                    note(e, 100, i, "lookup_name", lines[i])
    if not hits:
        for i, ln in enumerate(lines):
            c = compact(ln)
            for e in ents:
                for k in e.keys:
                    if len(k) >= 6 and len(c) >= 0.8 * len(k):
                        sc = fuzz.partial_ratio(k, c)
                        if sc >= config.REGISTRY_MATCH_MIN:
                            note(e, sc, i, "lookup_fuzzy", ln)
    if not hits:
        return None, []
    ranked = sorted(hits.values(), key=lambda h: (-h[0], h[1]))
    score, i, how, ev, e = ranked[0]
    others = [h[4].name for h in ranked[1:]]
    return Company(e.name, e.code, how, i, ev, True, score), others
