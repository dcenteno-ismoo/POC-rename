"""Rename files from the command line.

Usage:  python -m docagent.cli <file-or-folder> [more ...] [--out OUT_FOLDER]
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from . import config
from .pipeline import process_file


def main() -> None:
    ap = argparse.ArgumentParser(description="Rename documents to CODE_MMDDYYYY.ext (code from lookup.xlsx)")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", help="output folder (default: out/cli-<timestamp>)")
    a = ap.parse_args()

    files: list[Path] = []
    for p in map(Path, a.paths):
        files += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix != ".json") if p.is_dir() else [p]
    stamp = f"cli-{datetime.now():%Y%m%d-%H%M%S}"
    out = Path(a.out) if a.out else config.OUT_DIR / stamp
    review = config.REVIEW_DIR / stamp
    taken: dict[str, dict] = {}
    ok = 0
    for f in files:
        r = process_file(f, f.name, out, review, taken)
        ok += r.status == "renamed"
        cat = (r.category or {}).get("name", "-")
        note = f"  ⚠ {'; '.join(r.warnings)}" if r.warnings else ""
        shown = r.new_name or f"Error renaming file: {r.reason}"
        print(f"{r.status:10} {f.name:30} {cat:22} -> {shown}{note}" + (f"  {r.error}" if r.error else ""))
    print(f"\n{ok}/{len(files)} renamed  ->  {out}")


if __name__ == "__main__":
    main()
