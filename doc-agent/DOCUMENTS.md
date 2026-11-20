# Unified Documents workspace

Open `/documents` after starting Project RICH. It searches actual canonical Library
records, including uploads, Training context copies and indexed Project Sources.
No model calls are involved in search, snippets, filters or the inspector.
The existing `/library` upload/review page and Assistant remain available.

## Access setup

Set a private reader token in the PowerShell session used to start the application:

```powershell
$env:DOCAGENT_DOCUMENTS_TOKEN = 'documents'
.\start-local-ai.bat
```

An existing `DOCAGENT_SOURCES_ADMIN_TOKEN` is also accepted for document access.
Sources management still requires its own administrator credential. The Documents
workspace fails closed if neither credential is configured.

Enter the token on Documents. A signed, HttpOnly, SameSite=Strict cookie grants
eight hours of access, allowing PDF previews and ordinary citation links without
putting tokens in URLs or browser persistent storage. Use Lock to clear the cookie.
Changing the signing credential invalidates existing sessions. HTTPS sessions use
Secure cookies; the existing localhost HTTP launch remains supported.

When either credential is configured, existing Library, Assistant chat and Training
data APIs also require a valid reader/admin header or document session, preventing
legacy citation endpoints from bypassing document authorization. Their HTML shells
remain public so navigation can lead to the unlock page. New authenticated clients
may send `X-Documents-Token`; Sources tools may use `X-Sources-Admin-Token`.
Without configured credentials, the older localhost APIs retain their existing
behavior, but the new Documents API remains locked. Keep the localhost binding.
This is a shared local-reader capability, not multi-user or per-project RBAC.

## Search and inspection

- Keywords use SQLite FTS5 when available; an explicit substring fallback is used
  on SQLite builds without FTS5. Exact reference search preserves leading zeros
  and punctuation and remains distinct from tokenized full-text search.
- Filters cover type, company/customer/supplier, document dates, source, processing
  status and review status. Ambiguous dates are excluded from date filters.
- Existing reviewer corrections take precedence over machine values for metadata.
- Snippets are actual stored source text or the stored evidence supporting a
  matching metadata value. Filename-only matches are explicitly labelled.
- Inspector displays metadata, text, quality flags, processing state, review state,
  file hash, source/upload references, and OCR confidence when recorded. Recognition
  confidence is not a calibrated extraction/engineering correctness score.
- Native PDF sources can open at `#page=N`. XLSX sheet/cell and limited-precision
  locations are displayed without inventing coordinates.
- The existing extraction-review page is linked from each inspector.

The standard document view now uses readable fields and a single status such as
**Ready for review** (processing complete, review pending), **Verified**, or
**Needs clarification**. Raw JSON, OCR scores, content hashes, source IDs and attempt
counts are omitted from the normal view. Original links and readable page/sheet
references remain. Technical values stay in storage/API responses; the review page's
JSON correction editor is collapsed under an advanced section.

## Storage and synchronization

Canonical `documents.id`, content SHA-256, source IDs, stored files and reviewed
values are reused. This feature makes no additional archive copies. Existing
content-hash deduplication remains in force; duplicate uploads retain additional
filename/upload origins instead of creating another document record.

Additive module-owned migrations create `document_origins`, `document_search`,
`document_library_migrations`, and (where supported) `document_search_fts`.
Existing source-file observations are backfilled as origins. Older records whose
upload origin was not retained are labelled `legacy`, rather than inventing an
original path. SQLite triggers keep text, reviewed metadata and FTS entries in sync
within the same transaction. Deleted documents are removed from the index.

Source registrations and physical paths are provenance, not unrestricted file-read
instructions. Original-file endpoints accept a document ID only and serve the
authorized managed snapshot. They reject paths outside managed storage. Missing
snapshots return 404 while retained text/extraction remains searchable. Source-folder
disappearance does not remove its already-stored snapshot or break archived evidence.

Legacy renamer-only batches remain in their original workflow; their files are not
automatically reimported by this migration. The unified workspace indexes canonical
Library records. Use the existing Library upload or Sources scan to centralize files
that have never entered that store.

## Validation

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe tools/test_documents_live.py
```

The isolated live test uses a temporary server/database, a real upload, a real source
scan, explicit synthetic review data, FTS5, cookies and anonymous requests. It checks
filters, duplicate aliases, missing originals and legacy-route access restrictions.
It performs no AI calls and leaves the production Library untouched.

Native application dependencies include SQLite JSON functions. No embeddings, new
database service or frontend framework are introduced. Filter evaluation currently
materializes matching records in Python; large-collection pagination and metadata
normalization remain future performance work. Browser visual and real NAS checks
are separate from automated HTTP tests.
