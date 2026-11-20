# Document reference rules

Edit `reference-rules.json`. Each rule defines a stable ID, category aliases,
exact document headings, primary labels and standalone code prefixes. The file
is read on each extraction; no restart is needed for JSON changes. Override its
location with `DOCAGENT_REFERENCE_RULES` when needed.

| Category | Recognized labels | Example printed reference |
|---|---|---|
| RFQ | RFQ, Request for Quotation, Enquiry, Inquiry | RFQ-2026-0142 |
| PO | PO, P.O., Purchase Order, Order | PO-2026-0088 |
| PR | PR, P.R., Purchase Requisition, Purchase Request, Requisition | PR-000321 |
| Invoice | Invoice, INV, Sales Invoice, Tax Invoice | INV-000045 |
| Quotation | Quotation, Quote, QT, QTN, Reference, Ref. | QTN-2026-0912 |
| Design Document | Drawing, DWG, Specification, Document | ME-GA-0012 |
| BOM | BOM, Material List, Document | BOM-0042 |
| Mixed | Rules applied to each identified segment independently | Separate segment references |
| Unknown | No automatic reference assignment | null |

Labels accept suffixes `No.`, `Number`, `#`, `Ref.`, `Reference` or `ID`,
case-insensitively, with common separators. A value may follow on the same line,
in the adjacent spreadsheet row field, or on the immediately following line in
the same supplied source region. Another colon/pipe field is not crossed.
Bare prefixed codes are accepted only when they stand alone on a line.

Reference lengths vary. Raw values, leading zeros, meaningful spaces, dots,
hyphens and slashes remain intact. A bare printed `000123` stays `000123`;
the document category identifies its type. Prefixes are never invented. Suspected
O/0 and I/1 OCR ambiguity is flagged without replacement. Revision/date/sheet
fields remain separate. A BOM's related assembly drawing is never used as its
primary reference.

More than one distinct primary candidate requires review; the library does not
silently select one. Missing references stay unassigned by the rules. All returned
values carry exact source excerpts and source IDs. The library's existing review
editor can correct model/field values, and those corrections survive reprocessing.

Title recognition runs before the legacy category fallback, so a quotation with
`Your RFQ: ...` remains a quotation. Different explicit document headings produce
Mixed; complex real layouts still require review. Generic labels are restricted to
the already-established category. These are editable heuristics, not a guarantee
of complete coverage of arbitrary document layouts.

Use **Refresh saved files** in Training or **Reprocess** in the Library to apply
the rules to older uploads. Approved Training labels and reviewed Library values
are retained; newly extracted suggestions do not overwrite them.
