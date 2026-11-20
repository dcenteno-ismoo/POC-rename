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

For a local token-free demonstration, run **`start-demo.bat`** in the same environment
where your source allowed roots are configured. It sets `DOCAGENT_DEMO_MODE=1` and
uses the existing local AI launcher. Token forms are hidden and document/source/export
token checks are bypassed. Allowed-folder checks and read-only scanning remain enforced.
Keep the localhost binding. Run with `DOCAGENT_DEMO_MODE=0` to restore token checks.

Assistant phrase searches group matches by canonical file, count exact case-insensitive
phrase occurrences across stored passages, and show the top three documents. Document
lists also show at most three distinct files; record totals/comparisons remain separate.

All workspaces share one navigation/header renderer (`docagent/ui.py`) and common
styles (`docagent/static/app.css`): Sources, Documents, Upload & Review, AI Training,
AI Assistant and Renamer. Page templates retain their workflow-specific controls.
After code updates, restart using the same configured launcher/environment so
document/source access tokens and allowed roots are retained.

### Unified Documents

Visit `/documents` for full-text search across canonical uploads and indexed Sources,
with metadata/source/date filters, evidence snippets and a document inspector. Configure
`DOCAGENT_DOCUMENTS_TOKEN` or use the Sources administrator token to unlock document
access. Search is independent of the AI Assistant. See [DOCUMENTS.md](DOCUMENTS.md)
for authorization, additive indexing migrations, source provenance and validation.

### Project Sources

Open **Sources** (`/sources`) to register, edit, disable or remove authorized local
and network/NAS folder registrations. Use **Scan Source** or **Scan All Enabled Sources**
for explicit read-only discovery and indexing, with incremental/full modes, per-file
results, cancellation and restart recovery. Source documents are never modified.
Management requires `DOCAGENT_SOURCE_ROOTS` (a JSON array of allowed
absolute roots) and `DOCAGENT_SOURCES_ADMIN_TOKEN`, both configured on the server.
There is no unrestricted default root. See [SOURCES.md](SOURCES.md) for configuration,
security boundaries, migration details, and the Rescon demo checklist.

### Conversational Assistant

Visit `/assistant` for persistent conversations, follow-up questions, natural-language
structured replies, amount/date/supplier filters, comparisons, and a consolidated
Reference documents panel. See `CONVERSATIONAL_ASSISTANT.md` for supported queries,
configuration, migration details, and verification steps.

Document-grounded generation now retrieves ranked passages from the unified FTS5
index across uploads, Sources and Training context. Reader authorization is enforced
at the chat API before retrieval when configured. Responses include validated source
IDs, excerpts, locations and file links; unreviewed information is explicitly flagged.
Training examples and notes are not company-policy authority. Lists/counts/amounts
use deterministic operations; Ollama only explains bounded evidence and cannot run
tools, SQL or document instructions. Existing conversation history stays separate
from evidence. Restart using your existing token/allowed-root configuration after updating.

### Engineering document library

Invoices are supported in training, classification, extraction and search. Extracted
invoice fields include invoice number/date, due date, buyer/supplier, billing/shipping
addresses, PO reference, payment terms/instructions, subtotal, tax, discounts, shipping,
stated total, amount paid, balance due and line items. Missing amounts remain null;
no payment status or totals are inferred. New documents use extraction schema version 2;
version 1 results remain reviewable without rewriting stored machine output.

Reference rules cover RFQ, PO, PR, Invoice, Quotation, Design Document and BOM;
Mixed is handled per segment and Unknown stays unassigned. Edit `reference-rules.json`;
see `REFERENCE_RULES.md` for labels and examples. Printed references and punctuation
are preserved, including leading zeros. Prefixes are not invented. Multiple candidates require review.
These document identifiers are separate from the client code used by the renamer.
XLSX ingestion reads sheets natively, retains row cell references and simple zero-padded
numeric formats, and never executes macros. Formula cells use saved cached values.
Updated spreadsheet extraction uses `work/ocr-cache-v3/`; re-upload/reprocess existing
documents to refresh stored source locations (existing reviewed values are preserved).

### Local AI and separate workspaces

- `/training`: **AI Training**, collecting and approving examples (no model fine-tuning).
- `/assistant`: **AI Assistant**, natural-language questions with linked file evidence.

The Assistant also supports greetings, thanks, farewells, capability questions and
requests for jokes without document citations. It uses the configured local model
with friendly fallback replies when unavailable. Messages containing document questions
continue through evidence-backed retrieval, including greetings followed by a document
question. The `/assistant` chat now persists conversation history and discussed records;
the older `/api/library-ask` endpoint remains stateless for compatibility.
- `/library`: upload, process, search and review the documents used by the assistant.

For local Ollama, download `qwen2.5:3b` and run `start-local-ai.bat`. The launcher
uses `http://127.0.0.1:11434/v1/chat/completions`; no cloud AI key is used.
Override `DOCAGENT_AI_MODEL` to select another installed model. Small models can
fail strict extraction validation; failures remain visible and retryable.
The Assistant can answer from cached OCR even when structured extraction fails;
references display processing and review status. OCR must have succeeded first.
Local validation: `qwen2.5:3b` returned a supported payment-terms answer and file
reference for a synthetic PO. Full structured extraction did not pass validation
with this model. Real document OCR and browser validation remain outstanding.
`python tools/test_local_ai.py` runs actual local-model extraction and Q&A against
a clearly synthetic source in a temporary library. It does not exercise OCR.

**Ask AI beta** on `/library` answers natural-language questions using matching source
excerpts from completed documents. Each answer statement includes exact supporting
text and a link to its original file (and PDF page when available). It reuses the AI
configuration below. Retrieval selects up to 12 relevant excerpts within a default 5,000-character
budget; it is keyword-based and does not guarantee exhaustive comparisons or totals.
Unsupported answers and invalid citations are rejected. No model training is required.

Open **Document library · AI beta** from the home page or visit `/library`.
The original renaming workflow remains available. The library provides durable per-file
processing, polling progress, OCR/source caching, evidence-validated extraction, local
substring search with filters, review history, and approved-example export.

Configure these environment variables on the server before starting:

```powershell
$env:DOCAGENT_AI_URL = 'https://YOUR-PROVIDER/chat/completions'
$env:DOCAGENT_AI_MODEL = 'YOUR-MODEL'
$env:DOCAGENT_AI_KEY = 'YOUR-SERVER-KEY'
$env:DOCAGENT_EXPORT_TOKEN = 'YOUR-PRIVATE-EXPORT-TOKEN'
.\start.bat
```

The endpoint must support the chat-completions request/response format and JSON output.
No provider SDK is required. Model output is validated locally against the extraction
shape and exact source excerpts; each chunk has at most two request attempts and a
120-second request timeout. OCR text is sent to the configured provider. Keys stay on
the server and are excluded from example exports. Without configuration, extraction
fails clearly; after configuration, retry preserves cached OCR.

SQLite schema version 1 is created automatically at `work/library/library.sqlite3`.
Original files are copied into `work/library/`; shared OCR records are cached under
`work/ocr-cache-v2/`. Back up the entire `work` directory. Earlier `/training` examples
remain separate and are not silently migrated or approved for library export.

Run a single application process: one background thread processes the durable queue.
On restart, interrupted jobs become Failed and can be retried. Attempts guard against
stale processing writes; retries reuse document identity and cached source IDs. Human
corrections and review audit entries survive reprocessing. Export includes the machine
snapshot that was actually reviewed, rather than a later extraction.

Upload percentages measure browser transfer only. Server stages are indeterminate
unless actual source preparation or extraction chunk counts are available. OCR itself
currently exposes no live page callback, so the UI does not claim page-by-page OCR
progress. Completed is committed together with results and searchable text.

Training, Library and renamer uploads now show byte-based progress bars and estimated
transfer time remaining. Training also shows completed-file batch percentage and an
ETA based on observed per-file time (file complexity can vary). Server progress shows
stage percentages and stage ETA from measured counts; unmeasured OCR/queue stages
show an indeterminate bar and "estimating" rather than invented overall percentages.
Run upload progress checks with `node tests/progress.cjs`.

Review fields and line items in the JSON editor; every populated value requires raw
text and valid evidence. Verified is explicit human approval, not engineering certification.
Export uses `GET /api/library-export` with the `X-Export-Token` header (the UI supplies
this from the token input). Only Verified records with saved reviews and model output
are exported. Reviewer names are self-reported: this existing localhost app has no user
authentication system. Keep the existing localhost binding; multi-user deployment
requires authentication and authorization before exposing the service.

Validation without OCR/model dependencies:

```powershell
python -m unittest discover -s tests -v
```

Tests use explicitly identified source/model fixtures, not real AI results. Live OCR,
provider compatibility, and browser visual validation remain separate checks.

Current precision and scale limits: source references preserve native PDF page numbers,
spreadsheet sheet names, and image names; bounding boxes and cell coordinates are not
retained by the existing OCR/text adapters. Converted office pages are labeled as
converted locations. Long files are fully chunked; segments spanning chunk boundaries
need review and are flagged. Search is a local substring scan, suitable for this POC.
The original viewer opens PDF pages where supported and otherwise displays source
locations alongside the link. The review editor is JSON-based rather than a spreadsheet.

### AI beta training

Open **AI Training** from the home page (or `/training`). Upload documents or a folder, review the extracted text, correct company/reference number/category/date, and save approved examples. The document reference number is the sole Training identifier; no separate company code or lookup-code validation is required. Folder paths are retained as context; labels still require human approval. Previously approved labels remain stored; legacy company codes are hidden from the Training form.

Examples persist in `work/training.sqlite3` and source copies in `work/training-files/`, including after restart. Duplicate document contents reuse the existing example. This first milestone collects reviewed examples locally; it does not yet train a model or use AI predictions for renaming. The existing rename batch state still lives in memory.

Use **Refresh saved files** to re-read stored sources with current extraction rules
and the shared OCR cache. Unapproved labels are refreshed; approved labels are retained
and fresh machine suggestions are stored separately. Each file records its refresh
timestamp/error. Missing or changed sources are reported without replacing prior labels.

Training shows local-save, review, and Assistant-context statuses separately. Approval
in the UI queues the source for Library context, and **Save / retry Assistant context**
also works independently. Ready (100%) means cached text is retrievable by the Assistant,
not that model weights changed. Processing stays indeterminate where unmeasured.
Approved entries stay visible so status can be checked. **Clear from view (keep context)**
and per-file **Hide from view** archive records without deleting files or Library context.
Select **Show hidden / archived examples** to restore entries. Older archived records
stay hidden until restored. Clearing the Library separately removes its Assistant context.

Training uploads now automatically link their saved text to Assistant context in a
background task. Existing acquired Training text is reused without another OCR run;
when page/cell provenance was not stored, references explicitly state that precision
limitation. Saved text can be searched while structured AI extraction is still queued
or running. Try **List my available documents** to check the Assistant's context.

Saved Training text uses deterministic, unverified reference/date/label indexing rather
than queueing full model extraction. This keeps Qwen available for explanations.
`Which documents mention ...?` performs a direct whole-word text lookup and never
requires model-generated citations. It reports no matches when the requested words
are absent instead of sending irrelevant passages to the model.

**Clear Training documents** removes Training examples and stored source copies after
confirmation. **Clear Library documents** removes Library files, extraction results,
reviews and history; it refuses while processing is active. These controls are separate
and leave original files, demo samples and shared OCR caches untouched. Clearing is a
workspace reset, not secure erasure of cached text or backups. The localhost app has
the same access model for these endpoints as its existing uploads and reviews.

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


### Unified Documents workspace

Documents is now the single place to upload files/folders, search uploaded and
scanned documents, inspect previews, and review or correct extracted information.
The navigation contains Documents, Sources, AI Assistant, and Renamer. Existing
`/library?document=...` links redirect to the same document in Documents; `/training`
redirects to the earlier reference activity section. Existing API endpoints and
stored records remain compatible.

Document activity shows upload/processing progress and Assistant-context status.
Hiding finished activity only changes the browser activity view; documents remain
searchable. Earlier reference uploads retain save/retry, approval, refresh, and
hide/restore controls under Documents > Document activity > Earlier reference
uploads. Hiding those entries preserves their stored files and Assistant context.
New uploads use the existing document pipeline directly; no separate training step
or model fine-tuning is required.

UI interaction checks: `node tests/document-workspace.cjs`.


### Procurement fields in source scans

Read-only scans now extract explicit procurement fields through the shared saved-text
rules: item/part descriptions, material specifications, quantities and units, stated
unit prices and amounts, subtotal, tax/VAT rate and amount, stated totals, currencies,
payment terms, delivery information, and quotation references. Materials mentioned
in text also appear in the document inspector. Rules use Rescon-style PR, PO and
quotation layouts; the interview/reference file informs layouts, not company policy.

Values retain source IDs and exact excerpts. Missing/redacted values remain empty;
amounts are not calculated from quantities or tax. Conflicting values, unclear table
alignment and inferred columns in older unlabelled exports require review. Header
alignment supports worksheet cell addresses; flattened OCR without reliable row
alignment remains available as text instead of assigning prices to the wrong item.

Existing deterministic scan records upgrade from their saved text on the next scan,
including an unchanged incremental scan. Human reviews remain intact, and the prior
machine extraction is retained in review history. Original documents are untouched.
No new database schema or model training is needed.
