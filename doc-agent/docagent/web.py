"""Upload UI + API.  Run:  python -m docagent.web   then open http://127.0.0.1:8000"""
from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from . import __version__, config
from .pipeline import process_file
from .ui import render_page

app = FastAPI(title="doc-agent", version=__version__)
from .training import router as training_router
app.include_router(training_router)
from .library_api import router as library_router, start_worker, stop as library_stop
app.include_router(library_router)
from .sources_api import router as sources_router
app.include_router(sources_router)
from .documents_api import router as documents_router
app.include_router(documents_router)

@app.on_event('startup')
def library_startup():
    start_worker()
    from .sources_api import start_scanner
    start_scanner()

@app.on_event('shutdown')
def library_shutdown():
    library_stop.set()
    from .sources_api import scan_stop
    scan_stop.set()
STATIC = Path(__file__).parent / "static"
_uploads: dict[str, list[tuple[Path, str]]] = {}     # batch -> [(saved file, original name)]
_preview: dict[str, dict[str, dict]] = {}

SAFE = re.compile(r"[^A-Za-z0-9._ ()\-]+")


def _batch_dir(batch: str) -> Path:
    if not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", batch):
        raise HTTPException(400, "bad batch id")
    return config.OUT_DIR / batch


def _safe_name(name: str) -> str:
    name = Path(name).name                     # drop any client-side folders
    return SAFE.sub("_", name).strip() or "upload"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return render_page('renamer')


@app.get('/assets/app.css')
def shared_styles():
    return FileResponse(STATIC/'app.css',media_type='text/css')


@app.get('/assets/document-info.js')
def document_info_script():
    return FileResponse(STATIC/'document-info.js',media_type='application/javascript')


@app.get('/assets/document-workspace.js')
def document_workspace_script():
    return FileResponse(STATIC/'document-workspace.js',media_type='application/javascript')


@app.get('/assets/progress.js')
def progress_script():
    return FileResponse(STATIC / 'progress.js', media_type='application/javascript')


@app.post("/api/batch")
def new_batch() -> dict:
    b = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    _batch_dir(b).mkdir(parents=True, exist_ok=True)
    _uploads[b], _preview[b] = [], {}
    return {"batch": b}


@app.post("/api/batch/{batch}/file")
def upload_one(batch: str, file: UploadFile = File(...)) -> dict:
    out = _batch_dir(batch)
    if not out.exists():
        raise HTTPException(404, "unknown batch")
    original = _safe_name(file.filename or "upload")
    up = config.WORK_DIR / "uploads" / batch
    up.mkdir(parents=True, exist_ok=True)
    src = up / original
    n = 2
    while src.exists():
        src = up / f"{Path(original).stem}_{n}{Path(original).suffix}"
        n += 1
    with src.open("wb") as f:
        while chunk := file.file.read(1 << 20):
            f.write(chunk)
    # preview only: read the file and show everything; nothing is renamed or written yet
    uploads = _uploads.setdefault(batch, [])
    uploads.append((src, original))
    r = process_file(src, original, out, config.REVIEW_DIR / batch, _preview.setdefault(batch, {}), dry_run=True)
    r.upload_id = len(uploads) - 1
    return r.to_dict()


@app.post("/api/batch/{batch}/proceed")
def proceed(batch: str) -> list[dict]:
    """Rename for real: renamed copies go to the out folder, failures go to the review folder."""
    out = _batch_dir(batch)
    if not out.exists():
        raise HTTPException(404, "unknown batch")
    res_file = out / "_results.jsonl"
    if res_file.exists():
        raise HTTPException(409, "This batch was already renamed")
    taken: dict[str, dict] = {}
    results = []
    for i, (src, original) in enumerate(_uploads.get(batch, [])):
        r = process_file(src, original, out, config.REVIEW_DIR / batch, taken)
        r.upload_id = i
        d = r.to_dict()
        if r.held_with:
            held = f"Same new name as {original}: {(r.reason or '').split(': ')[-1]}"
            _mark_held(res_file, r.held_with, held)
            for prev in results:
                if prev["original_name"] in r.held_with and prev["status"] == "renamed":
                    prev.update(status="exception", new_name=None, reason=held, reasons=[held])
        with res_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(d, default=str) + "\n")
        results.append(d)
    return results


def _mark_held(path: Path, names: list[str], reason: str) -> None:
    if not path.exists():
        return
    rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
    for row in rows:
        if row.get("original_name") in names and row.get("status") == "renamed":
            row.update(status="exception", new_name=None, reason=reason, reasons=[reason])
    path.write_text("".join(json.dumps(x, default=str) + "\n" for x in rows), encoding="utf-8")


@app.get("/api/batch/{batch}/file/{name}")
def download_one(batch: str, name: str):
    p = _batch_dir(batch) / _safe_name(name)
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@app.get("/api/batch/{batch}/zip")
def download_zip(batch: str):
    out = _batch_dir(batch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.iterdir()):
            if p.is_file() and not p.name.startswith("_"):
                z.write(p, p.name)
        res = out / "_results.jsonl"
        if res.exists():
            z.writestr("report.json", json.dumps([json.loads(x) for x in res.read_text("utf-8").splitlines()],
                                                 indent=2))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="renamed_{batch}.zip"'})


@app.get("/api/info")
def info() -> dict:
    from . import lookup
    try:
        clients, problem = [{"name": e.name, "code": e.code} for e in lookup.entries()], None
    except ValueError as e:
        clients, problem = [], str(e)
    return {"version": __version__, "out_dir": str(config.OUT_DIR), "review_dir": str(config.REVIEW_DIR),
            "date_order": config.SLASH_DATE_ORDER, "libreoffice": bool(config.soffice_path()),
            "lookup_file": str(config.LOOKUP_FILE), "lookup_found": lookup.sheet_exists(),
            "lookup_problem": problem, "registry": clients}


def main() -> None:
    import argparse
    import threading
    import webbrowser

    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    url = f"http://127.0.0.1:{a.port}"
    print(f"doc-agent {__version__}  ->  {url}   (Ctrl+C to stop)")
    print(f"renamed files: {config.OUT_DIR}")
    if not a.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
