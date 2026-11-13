"""One file through the technical flow:

    1. Check record      - flag files that were already processed (by content fingerprint)
    2. OCR + capture     - read the text, find the date, categorize the document
    3. Lookup sheet      - find the client (or its code) in lookup.xlsx
    4. Three checks      - code format, found in the sheet, required fields present
    5. Name clash?       - a name that is already used is never overwritten
    6. Write copy        - renamed copy goes to the new folder; the original is never touched
    7. Update record     - original name, date, new name

Anything that fails a check is NOT renamed. It shows "Error renaming file" with the reason
and the original is copied to the review folder.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config, lookup
from .acquire import acquire
from .categorize import find_category
from .detect import detect
from .extract import find_date
from .record import get_record, sha256_file

STAGE_PREFIX = {"Request For Quotation": "RFQ", "Purchase Order": "PO"}


@dataclass
class Result:
    original_name: str
    status: str = "exception"             # renamed | exception | ready (preview only)
    new_name: str | None = None
    category: dict | None = None
    reason: str | None = None             # why it was not renamed (plain words)
    reasons: list[str] = field(default_factory=list)
    checks: dict | None = None            # the three checks: True / False / None (not reached)
    already_processed: dict | None = None
    held_with: list[str] = field(default_factory=list)   # other files held because of a name clash
    sha256: str | None = None
    detected_type: str | None = None
    type_mismatch: bool = False
    method: str | None = None
    company: dict | None = None
    date: dict | None = None
    ocr_confidence: float | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    ms: int = 0
    upload_id: int | None = None
    text_preview: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class NotRenamed(Exception):
    """Stops the flow for this file with one or more plain-language reasons."""

    def __init__(self, *reasons: str):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def process_file(src: Path, original_name: str, out_dir: Path, review_dir: Path,
                 taken: dict[str, dict] | None = None, dry_run: bool = False) -> Result:
    """Never modifies `src`. Writes a renamed copy to out_dir, or the original to review_dir.

    `taken` remembers the names already used in this destination folder: name -> {original, held}.
    dry_run: analyse only (preview). Nothing is written: no copy, no review file, no record entry.
    """
    t0 = time.perf_counter()
    taken = taken if taken is not None else {}
    r = Result(original_name)
    rec = get_record()
    try:
        # ---- 1. Check record
        r.sha256 = sha256_file(src)
        prev = rec.latest(r.sha256)
        if prev and prev.get("status") == "renamed":
            r.already_processed = {"on": prev["processed_at"], "as": prev.get("new_name"),
                                   "original_name": prev.get("original_name")}
            r.warnings.append(f"Already processed on {prev['processed_at'][:10]} as {prev.get('new_name')}")

        # ---- 2. OCR + capture
        det = detect(src, original_name)
        r.detected_type, r.type_mismatch = det.ext, det.mismatch
        if det.mismatch:
            r.warnings.append(f"extension says .{det.claimed_ext}, content is .{det.ext}")
        if det.path == "X":
            raise NotRenamed(f"File type not supported (.{det.ext or 'unknown'})")
        if not lookup.sheet_exists():
            raise NotRenamed(f"Lookup sheet file is missing ({config.LOOKUP_FILE.name})")

        acq = acquire(src, det)
        lines = acq.lines
        r.method, r.ocr_confidence = acq.method, acq.ocr_confidence
        r.warnings += acq.warnings
        r.text_preview = lines[:40]
        if not any(ln.strip() for ln in lines):
            raise NotRenamed("No text found in the file")

        dt = find_date(lines, acq.meta)
        if dt:
            r.date = {"iso": dt.value.isoformat(), "as_printed": dt.raw, "label": dt.label,
                      "ambiguous": dt.ambiguous, "evidence": dt.evidence, "line": dt.line_no + 1}
            if dt.ambiguous:
                r.warnings.append(f"'{dt.raw}' could be day-first or month-first; read as "
                                  f"{'MM/DD' if config.SLASH_DATE_ORDER == 'MDY' else 'DD/MM'}")
            if dt.score < 1:
                r.warnings.append("no date label found; used the first date in the file")

        cat = find_category(lines)
        if cat:
            r.category = {"name": cat.name, "keyword": cat.keyword, "evidence": cat.evidence,
                          "line": cat.line_no + 1, "also": cat.also}
            if cat.also:
                r.warnings.append(f"also mentions: {', '.join(cat.also)}; "
                                  f"{'Proposal takes precedence' if cat.name == 'Request For Quotation' else 'used the one that appears first'}")
        else:
            r.warnings.append("No category found (no Proposal, Purchase Order or Purchase Requisition text)")

        # ---- 3. Lookup sheet
        try:
            comp, others = lookup.find_client(lines)
        except ValueError as e:
            raise NotRenamed(f"Lookup sheet cannot be read: {e}") from e
        if comp:
            r.company = {"name": comp.name, "code": comp.code, "rule": comp.rule, "evidence": comp.evidence,
                         "line": comp.line_no + 1}
            if others:
                r.warnings.append(f"also mentions: {', '.join(others)}; used {comp.name}")
            want = STAGE_PREFIX.get(cat.name) if cat else None
            if want and not comp.code.startswith(want):
                r.warnings.append(f"Document is a {cat.name}, but the lookup sheet code for {comp.name} "
                                  f"is {comp.code}")

        # ---- 4. Three checks
        reasons: list[str] = []
        in_sheet = comp is not None
        code_ok = bool(comp and config.CODE_FORMAT.fullmatch(comp.code))
        have_date = dt is not None
        r.checks = {"code_format": code_ok if in_sheet else None, "found_in_lookup": in_sheet,
                    "required_fields": have_date}
        if not in_sheet:
            reasons.append("Not found in lookup sheet")
        elif not code_ok:
            reasons.append(f"Code '{comp.code}' in lookup sheet has the wrong format (must start with RFQ or PO)")
        if not have_date:
            reasons.append("Date not found")
        if reasons:
            raise NotRenamed(*reasons)

        # ---- 5. Name clash?
        name = f"{comp.code}_{dt.value.strftime(config.DATE_FORMAT_IN_NAME)}.{det.ext}"
        key = name.lower()
        holder = taken.get(key)
        if holder is None and not dry_run and (out_dir / name).exists():
            holder = {"original": "a file already in the destination folder", "held": True}
        if holder is not None:
            if not holder["held"] and holder.get("sha256") == r.sha256:
                # the very same file uploaded twice: the first one stays, this one is reported
                raise NotRenamed(f"Same file as {holder['original']}, which was already renamed to {name}")
            if not holder["held"] and (dry_run or (out_dir / name).exists()):
                if not dry_run:
                    _hold_earlier(out_dir, name, holder["original"], review_dir, original_name)
                holder["held"] = True
                r.held_with = [holder["original"]]
            raise NotRenamed(f"Same new name as {holder['original']}: {name}")

        # ---- 6. Write copy
        taken[key] = {"original": original_name, "held": False, "sha256": r.sha256}
        if dry_run:
            r.new_name, r.status = name, "ready"
            r.ms = round((time.perf_counter() - t0) * 1000)
            return r
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out_dir / name)
        r.new_name, r.status = name, "renamed"

        # ---- 7. Update record
        rec.add(sha256=r.sha256, original_name=original_name, status="renamed", new_name=name,
                category=cat.name if cat else None, batch=out_dir.name)
    except NotRenamed as e:
        r.status, r.reasons, r.reason = "exception", e.reasons, "; ".join(e.reasons)
        if not dry_run:
            _to_review(src, original_name, review_dir, r, rec, out_dir.name)
    except Exception as e:  # noqa: BLE001 - every failure becomes a reviewable result
        r.status, r.error = "exception", f"{type(e).__name__}: {e}"
        r.reasons = ["The file could not be processed (unexpected error)"]
        r.reason = r.reasons[0]
        if not dry_run:
            _to_review(src, original_name, review_dir, r, rec, out_dir.name)
    r.ms = round((time.perf_counter() - t0) * 1000)
    return r


def _to_review(src: Path, original_name: str, review_dir: Path, r: Result, rec, batch: str) -> None:
    review_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, review_dir / original_name)
    (review_dir / (original_name + ".json")).write_text(json.dumps(r.to_dict(), indent=2, default=str),
                                                          encoding="utf-8")
    if r.sha256:
        rec.add(sha256=r.sha256, original_name=original_name, status="exception", category=(r.category or {}).get("name"),
                reason=r.reason, batch=batch)


def _hold_earlier(out_dir: Path, name: str, original: str, review_dir: Path, other: str = "another file") -> None:
    """Two files want the same name: neither is renamed. Move the earlier copy back out of the destination."""
    review_dir.mkdir(parents=True, exist_ok=True)
    moved = out_dir / name
    sha = sha256_file(moved)
    shutil.move(str(moved), str(review_dir / original))
    reason = f"Same new name as {other}: {name}"
    (review_dir / (original + ".json")).write_text(
        json.dumps({"original_name": original, "status": "exception", "reason": reason, "reasons": [reason]},
                   indent=2), encoding="utf-8")
    get_record().add(sha256=sha, original_name=original, status="exception", reason=reason, batch=out_dir.name)
