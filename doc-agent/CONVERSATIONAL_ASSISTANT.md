# Conversational Assistant

Run `start-local-ai.bat`, then open `http://127.0.0.1:8000/assistant`.
Keep Ollama running with `qwen2.5:3b` installed. No larger model is downloaded.

## What is persisted

Additive SQLite tables `chats` and `chat_turns` live in the existing
`work/library/library.sqlite3`. Existing Library tables and files are retained.
Each turn stores the question, response, status, client request ID, timestamps,
validated file references, and ordered record keys (document ID plus segment index).
Messages survive page reload and app restart. Interrupted replies become retryable
on startup. Retrying the same request ID does not duplicate a completed turn.
Deleting a conversation leaves Library files and Training records untouched.

The browser stores only the selected conversation ID. The existing localhost app
still has no authenticated multi-user boundary; do not expose it to a network.
Chats can contain sensitive excerpts; protect them with the rest of `work/` backups.

## Queries and follow-ups

- `Show my five latest purchase orders.`
- `How much is my latest PO?`, then `Who is the supplier?`
- `Find PO-2026-0088.`, then `Open that document.`
- `What about the second one?` after a result list.
- `Compare those two.` or `Compare it with the previous PO.`
- `Find the latest version of PO-2026-0088.`
- `Show the most recently uploaded purchase orders.`
- `Which documents mention stainless steel?`
- `Sum all PO amounts.`
- `Explain the payment terms for PO-2026-0088.`
- `Show POs from Example Supply over PHP 5000 after 2026-11-01.`
- `Show invoices due before 2026-12-01.`
- `Find invoices referencing PO-2026-0088.`

Replies use conversational sentences for structured facts while keeping review labels
and exact evidence. Each conversation has a single **Reference documents** panel,
grouped by file with deduplicated excerpts and links to page/sheet locations. Buttons
in messages jump to that panel. Its Library link opens the full collection.

Advanced filters currently accept explicit supplier names, ISO dates, and `over/above`
or `under/below` amounts with a currency code. Amount thresholds are strict; date
bounds are inclusive. Due dates must be explicitly extracted and unambiguous.
Cross-document relationships use extracted reference fields, not similarity guesses.
These are supported query patterns, not arbitrary free-form database analysis.

Categories use the existing RFQ, PO, PR, Invoice, Quotation, Design Document, BOM,
Mixed and Unknown definitions. Exact reference matching preserves leading zeros
and punctuation and runs before keyword retrieval. Other structured operations can
filter by category, review status, and ISO document-date bounds. Dates with ambiguity
flags are excluded from latest-by-document-date queries. Upload order uses the original
queue timestamp, not the latest processing/review timestamp. Date ties stay visible;
the assistant does not infer approval, cancellation or revision precedence.

Review corrections take precedence over machine extraction. Only saved corrections
marked Verified are labeled Verified; machine suggestions are explicitly unverified.
Missing, conflicting or unsupported amounts are not zero. Money is parsed with Decimal;
ambiguous/composite totals are refused, currency groups remain separate, and each
calculation cites its input records. Repeated references in an aggregate require version
selection. Exact duplicate uploads are already deduplicated by the Library fingerprint.
Mixed-document segments remain separate business records. A source without structured
extraction can still participate in grounded OCR-text Q&A; structured fields may be missing.

## Local model and evidence

Grounded chat now retrieves through the unified `DocumentLibrary` FTS5 index,
with its substring fallback when FTS5 is unavailable. This includes canonical uploads,
scanned Sources and saved Training context. The existing reader/admin authorization
dependency runs before chat retrieval; it is a shared local-library permission model,
not per-project/multi-user access control. Acquired text is eligible once transactionally
indexed, even if later structured AI extraction failed; processing state remains visible.

Unreviewed citations carry an explicit response warning. Neither a Training example
nor a Verified extraction is automatically authoritative company policy. Exact counts,
company lists, review queues and explicitly extracted reference relationships use
deterministic operations. Unsupported relationships or absent evidence are reported
as unestablished. Connection/timeouts and malformed/citation-invalid replies have
different error messages and fall back only to real retrieved excerpts.

Supported lookups and calculations are deterministic backend operations. Qwen explains
retrieved evidence. Recent context contains up to three user/assistant turn pairs
(350 characters per user question, 650 per reply) plus up to 20 discussed record keys.
Follow-ups such as “tell me more”, “these” and “the same” retain document selection.
The whole chat is never sent, and conversation history is not document evidence.
Short internal source aliases map back to canonical OCR IDs. All factual model output
must pass exact-excerpt/source-ID validation. Backend code constructs file URLs.
Invalid output gets at most two attempts, then chat returns evidence-only passages
with a clear failure notice. Original-file links become unavailable when documents
are deleted. Existing PDF page and XLSX sheet/cell references are preserved.

All extraction/chat model requests share one lock. Configuration:

| Variable | Default/use |
|---|---|
| `DOCAGENT_AI_URL` | Set by local launcher to Ollama chat-completions endpoint |
| `DOCAGENT_AI_MODEL` | Local launcher defaults to `qwen2.5:3b` |
| `DOCAGENT_AI_KEY` | Optional server-side provider credential |
| `DOCAGENT_AI_TIMEOUT` | 90 seconds per grounded answer request |
| `DOCAGENT_AI_INPUT_CHARS` | 5000 source-text characters, using relevant excerpts |
| `DOCAGENT_AI_OUTPUT_TOKENS` | 700 generated tokens |
| `DOCAGENT_AI_EXTRACT_ENABLED` | 1; set 0 for OCR-only processing and manual review |

## Validation and practical limitations

Run from the project directory:

```powershell
python -m unittest discover -s tests -v
node tests/progress.cjs
.venv\Scripts\python.exe tools/test_conversation_live.py
```

The live test launches a temporary server on 8011 and uses synthetic PDFs, native PDF
text acquisition, reviewed fixture facts, and real local Qwen. It explicitly disables
structured AI extraction. It does not modify the real Library and does not validate
scanned-page OCR or the browser UI. Browser connectivity was unavailable during this change.

This POC uses the unified full-text index without introducing embedding services.
Structured filters still materialize parts of the collection in Python; it is
not optimized for large libraries. The routing grammar covers the examples above,
not arbitrary natural-language filters. Material lookup requires keyword overlap.
Money parsing supports explicit USD/PHP/EUR/GBP/JPY codes and dot-decimal amounts;
other formats return unavailable instead of guessing. Context and source budgets can
omit passages. Qwen 3B can still fail validation and fall back to evidence-only results.
The previous full structured-extraction reliability limitation remains.

Manual check: upload and review two POs with different dates and totals; start a chat;
ask for the latest PO, its amount, supplier, and comparison with the previous PO;
open citations, refresh, reopen the chat, then delete it and confirm the files remain
in the Library. On a date tie or ambiguous selection, choose an explicit reference.
