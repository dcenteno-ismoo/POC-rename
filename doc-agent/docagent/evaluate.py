"""Flow 7: run every sample through the pipeline and score against answer_key.json.

Runs as a preview (dry run) against a temporary lookup sheet built from the answer key, so the
real lookup.xlsx, record.jsonl, out\\ and review\\ are never read or changed.

Usage:  python -m docagent.evaluate [samples_folder]
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook

from . import config
from .pipeline import process_file


def _write_lookup(path: Path, key: dict) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["Company", "Code"])
    for issuer, code in sorted({(e["issuer"], e["code"]) for e in key.values()}):
        ws.append([issuer, code])
    wb.save(path)


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows consoles default to cp1252
    folder = Path(argv[0] if argv else "samples")
    key = json.loads((folder / "answer_key.json").read_text(encoding="utf-8"))
    rows = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        config.LOOKUP_FILE, config.RECORD_FILE = tmp / "lookup.xlsx", tmp / "record.jsonl"
        _write_lookup(config.LOOKUP_FILE, key)
        out, rev = tmp / "out", tmp / "review"
        taken: dict[str, dict] = {}
        for fname, exp in key.items():
            r = process_file(folder / fname, fname, out, rev, taken, dry_run=True)
            got_code = (r.company or {}).get("code")
            got_date = (r.date or {}).get("iso")
            rows.append({
                "file": fname,
                "type": r.detected_type,
                "method": r.method,
                "expected": exp["expected_name"],
                "got": r.new_name or f"[{r.status}]",
                "name_ok": r.new_name == exp["expected_name"],
                "company_ok": got_code == exp["code"],
                "date_ok": got_date == exp["date"],
                "company_rule": (r.company or {}).get("rule"),
                "ms": r.ms,
                "warnings": "; ".join(r.warnings + r.reasons) + (f" ERROR {r.error}" if r.error else ""),
            })

    w = max(len(r["file"]) for r in rows)
    print(f"\n{'file':{w}}  {'expected':22} {'got':22} co  dt  rule          ms")
    print("-" * (w + 70))
    for r in rows:
        mark = lambda b: "ok" if b else "XX"  # noqa: E731
        print(f"{r['file']:{w}}  {r['expected']:22} {r['got']:22} {mark(r['company_ok'])}  "
              f"{mark(r['date_ok'])}  {str(r['company_rule']):12} {r['ms']:>6}")
        if not r["name_ok"] and r["warnings"]:
            print(f"{'':{w}}  ↳ {r['warnings']}")

    by = defaultdict(lambda: [0, 0])
    for r in rows:
        grp = "ocr" if "ocr" in (r["method"] or "") else "text"
        by[grp][0] += r["name_ok"]
        by[grp][1] += 1
    n_ok = sum(r["name_ok"] for r in rows)
    print("-" * (w + 70))
    print(f"Correct file names: {n_ok}/{len(rows)}  ({100 * n_ok / len(rows):.0f}%)")
    for g, (ok, n) in sorted(by.items()):
        print(f"  {g:5}: {ok}/{n}")
    print(f"  company: {sum(r['company_ok'] for r in rows)}/{len(rows)}   "
          f"date: {sum(r['date_ok'] for r in rows)}/{len(rows)}")

    report = folder.parent / "report.csv"
    with report.open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    print(f"\nDetails: {report}")
    return 0 if n_ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
