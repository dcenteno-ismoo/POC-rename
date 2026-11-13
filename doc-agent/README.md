# doc-agent — POC renamer (Rescon flow)

Upload one or many documents. Each file is renamed to

```
CODE_MMDDYYYY.ext      e.g.  Goulds Pumps proposal dated 08/24/2016  ->  RFQABC543_08242016.pdf
```

- **CODE** = the client's code from the **lookup sheet** (`lookup.xlsx`)
- **MMDDYYYY** = the document's own date
- **ext** = the file's *real* type

Fully deterministic: no LLM. Same file in, same name out. Originals are never changed.

## The flow (one file)

1. **Check record** — `record.jsonl` keeps the original name, a content fingerprint (SHA-256), the date and the new name of every file. A file seen before is flagged "Already processed" (it is still processed; this is a flag, not a block).
2. **OCR + capture** — read the text, find the date, **categorize** the document.
3. **Lookup sheet** — find the client's name, or its code, anywhere in the text.
4. **Three checks** — code format (`RFQ…` or `PO…`, set in `config.py`), client found in the sheet, required fields present (the date). Any failure = **Error renaming file** + the reason; nothing is renamed.
5. **Name already used?** — a clash is never overwritten. Both files are held back and reported. The very same file uploaded twice is reported once; the first stays renamed.
6. **Write copy** — renamed copy in `out\<batch>\`. Files that failed are copied to `review\<batch>\` with a `.json` explaining why.
7. **Update record.**

## Lookup sheet

`lookup.xlsx`, one row per client, header row with **Company** and **Code**:

| Company | Code |
|---|---|
| Container Corporation Philippines | POCCC123 |
| Goulds Pumps | RFQABC543 |

Edit it in Excel (close it before uploading; Excel locks the file). Changes are picked up on the next file, no restart. To add a client, add a row. `companies.json` is no longer used.

## Categories

Read from the document text:

| Text found | Category |
|---|---|
| `Proposal` | Request For Quotation |
| `Purchase Order` | Purchase Order |
| `Purchase Requisition` | Purchase Requisition |

If a document contains more than one of these, **"Proposal" always wins** (Request For Quotation), whatever else the text says. Without "Proposal", the phrase that appears **first** wins (a title comes first). The others are shown as a warning. No match = "Uncategorized" (still renamed; the category is not part of the name).
A warning also appears when the category does not fit the sheet code (for example a Purchase Order for a client whose code starts with `RFQ`).

## Run

Double-click **`start.bat`**. First run installs the missing packages (~2 min), then the browser opens at http://127.0.0.1:8000.

Drop files → **Rename files** → download each file or **Download all (ZIP)**.
Renamed copies are saved in `out\<batch>\`. Originals are never changed. Files that can't be named go to `review\<batch>\` with a `.json` explaining why.

Command line:

```powershell
.venv\Scripts\python -m docagent.cli samples           # a folder or files
```

## How it decides

**Client** — the sheet decides. A code printed on the document is the strongest evidence, then the company name anywhere in the text (OCR-tolerant). A name that is not in the sheet is never renamed.

**Date** — labeled document dates (`Invoice Date`, `Date of Invoice`, `OR Date`, `PO Date`, `Statement Date`, `Dated`, `As of`, `Date`) beat unlabeled ones; `Due`, `Delivery`, `Valid until`, etc. are excluded. Formats: `01/15/2026`, `2026-01-15`, `15 Jan 2026`, `January 15, 2026`, `15-Jan-26`, `15.01.2026`, real Excel date cells. Slash dates are read **MM/DD**; when both numbers are ≤ 12 the UI shows a warning. Set `DOCAGENT_DATE_ORDER=DMY` to flip.

**Reading the file** — TXT/MD/CSV/HTML/EML read directly; DOCX/XLSX/PPTX read natively (incl. Word page headers); DOC/RTF/ODT converted by LibreOffice; PDF text layer, or OCR for scanned pages; JPG/PNG/HEIC/TIFF via RapidOCR (EXIF rotation and skew handled).

## Layout

```
docagent/      detect.py (type) · acquire.py (text) · ocr.py · extract.py (dates) · categorize.py · lookup.py · record.py · pipeline.py · web.py · cli.py
tools/         generate_samples.py
samples/       24 mock files + answer_key.json
lookup.xlsx    clients and codes
record.jsonl   created on first run: every processed file
```
