# Project Sources and read-only scanning

Open `/sources` from the application navigation. Registration never starts a scan.
Use **Scan Source** or **Scan All Enabled Sources** explicitly. Scanning recursively
reads authorized folders, copies stable snapshots into managed storage, and reuses
the existing readers, OCR, classification and reference rules. Originals are not modified.

## Administrator configuration

Set both variables in the PowerShell session that starts Project RICH:

```powershell
$env:DOCAGENT_SOURCE_ROOTS = '["C:\\Users\\Ervin\\Personal Projects\\Ismoo\\Project RICH\\POC-rename\\doc-agent\\demo-training-samples", "\\\\NAS\\Engineering"]'
$env:DOCAGENT_SOURCES_ADMIN_TOKEN = 'admin'
.\start-local-ai.bat
```

Replace example roots with actual folders explicitly approved for registration.
Use JSON escaping for backslashes. The app does not default to the workspace,
drive root or entire computer. Missing configuration fails closed. The token is
a local administrator capability: share it only with administrators. Enter it on
the Sources page; it remains in memory, not browser persistent storage. Reload
or Lock workspace to remove it. Do not put it in URLs or commit it to the repository.
If the app is already running, stop that instance before launching with new variables.

UNC shares and mapped drives use the Windows account running the app. The module
does not collect NAS passwords or execute mount commands. UNC access requires
working network connectivity and that account's share and filesystem permissions.

## Behavior and boundaries

- Source name, type (`local`/`network`), absolute path, enabled state and description
  are saved in the existing Library SQLite database.
- Registering a directory checks access without traversal. Explicit scans recursively
  discover supported file extensions and never invoke the renamer.
- Paths must first be lexically inside configured roots and then resolve inside
  the corresponding canonical allowed root. Escaping symlinks/junctions, relative
  paths, device paths and Windows alternate streams are rejected.
- Resolved targets are retained. Changed link targets are reported as inaccessible
  until edited and revalidated. Revalidation is required again before any future scan.
- Inaccessible/offline registrations remain listed and can still be disabled or
  removed. Editing a source path requires an accessible folder.
- Disabling requests an active scan to stop at its next checkpoint. Removing deletes one registration row;
  it never deletes or edits source files, existing Library records, or Training data.
- No arbitrary filesystem browsing, directory listings, or client-submitted shell
  commands are exposed. All source-data APIs require `X-Sources-Admin-Token`.

## Persistence and migration

`source_schema_migrations` records module-owned migrations. Version 1 creates
`project_sources` with a canonical-path uniqueness constraint and timestamps.
Migration is additive and transactional, preserves the existing Library schema,
and does not overwrite its legacy `PRAGMA user_version`.

Source accessibility is checked on authenticated listing and saving; it is a snapshot,
not a guarantee against a later network outage or filesystem change. Disconnected
network filesystems can take time to return an OS error. No automatic watcher,
periodic scheduler or project membership is included.

## Scans, progress and recovery

Scan jobs, per-file results, observed file metadata/hashes and a worker lease live
in the existing SQLite database. Migration 2 adds `source_scan_jobs`,
`source_scan_files`, `source_file_state`, and `source_scan_lease` without replacing
existing records. Files link to canonical Library document IDs; exact duplicate
content reuses those IDs. Modified contents retain previous snapshots in the Library
and previous per-scan results. Existing reviews are preserved, not copied to new content.

Incremental scans skip successfully indexed files whose size and modification time
are unchanged. A full rescan reads/hashes all supported files; valid acquisition and
extraction results can still be reused. Use full rescan if an external tool changed
content while preserving its size and timestamp. Missing/deleted source files do
not delete Library snapshots. Indexed count describes tracked successfully indexed
source paths, not a live count of currently existing files.

The Scan Results section shows discovery/processing counts, new/modified/unchanged/
unsupported results, independent failures, review flags, OCR confidence where available,
and links to the indexed Library document. Unknown discovery totals use an indeterminate
bar. Completion is displayed only after indexing and result records are committed.
Text/PDF/image/XLSX provenance is retained where readers supply it.

Source scans use existing lightweight rule extraction and mark results unverified.
This milestone does not promise comprehensive AI extraction of line items or engineering
correctness. Mixed or ambiguous documents require review. No model call is required
to finish a scan or make acquired text searchable by the Assistant.

One SQLite lease serializes all scans, with heartbeats and fenced writes preventing
a stale worker from overwriting a new owner's progress. Interrupted jobs resume
idempotently after restart (an abruptly stopped worker's lease can take 30 seconds
to expire). Cancellation is checked between files, copy chunks and extraction pages;
a running native reader/OCR call must return before cancellation completes.

Scans reject symlinks/reparse points and exclude app-managed work/output/review
folders. Windows file handles are checked against the registered root before reading.
Source changes during copying fail that file rather than indexing an unstable snapshot.
Large files are copied/hashed in bounded chunks. No document contents are logged.

## Tests and manual check

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe tools/test_sources_live.py
.venv\Scripts\python.exe tools/test_scanning_live.py
```

The live test creates an isolated server/database and temporary source folder,
checks authorization, CRUD, restart persistence and source-file preservation.
It does not test a real NAS or impersonate a different Windows account.
Permission failures and canonical-path escape guards are covered by unit tests.
The scan live test also exercises native PDF reading, real scanned-PDF/image OCR,
incremental/full scans, duplicates, corrupt/unsupported files, and unchanged originals.

For the Rescon demo: configure a narrow root and token, start the app, connect as
administrator, register a folder, edit its description, disable/re-enable it,
restart the app, then reconnect and confirm the registration persists. Removing
the registration must leave the original folder and its files intact.
