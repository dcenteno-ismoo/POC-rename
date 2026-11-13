"""The record of processed files (record.jsonl, one JSON line per event).

Each line: when it was processed, the original filename, the file's fingerprint (SHA-256 of its
contents), the status, and the new filename. The fingerprint means a file is still recognised
after someone renames or moves it. The newest line for a fingerprint is its current state.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path

from . import config


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


class Record:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._latest: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("sha256"):
                    self._latest[e["sha256"]] = e

    def latest(self, sha: str) -> dict | None:
        return self._latest.get(sha)

    def add(self, *, sha256: str, original_name: str, status: str, new_name: str | None = None,
            category: str | None = None, reason: str | None = None, batch: str | None = None) -> None:
        e = {"processed_at": datetime.now().isoformat(timespec="seconds"), "batch": batch,
             "original_name": original_name, "sha256": sha256, "status": status,
             "new_name": new_name, "category": category, "reason": reason}
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
            self._latest[sha256] = e


_record: Record | None = None


def get_record() -> Record:
    global _record
    if _record is None or _record.path != config.RECORD_FILE:
        _record = Record(config.RECORD_FILE)
    return _record
