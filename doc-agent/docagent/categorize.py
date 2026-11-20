"""Categorization: read the document text and decide what kind of document it is.

Rules (from Rescon):
    text has "Proposal"              -> Request For Quotation
    text has "Purchase Order"        -> Purchase Order
    text has "Purchase Requisition"  -> Purchase Requisition

A document can mention more than one of these words.
Precedence: if "Proposal" is present anywhere, the document is a Request For Quotation, whatever
else it says (quantity, unit, "purchase order" ...). Otherwise the phrase that appears FIRST wins.
Any other phrases found are reported, so nothing is hidden.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RULES: list[tuple[str, str, re.Pattern]] = [
    ("Invoice", "Invoice", re.compile(r"\b(?:tax\s+|commercial\s+|sales\s+)?invoices?\b", re.I)),
    ("Request For Quotation", "Proposal", re.compile(r"\bproposals?\b", re.I)),
    ("Purchase Order", "Purchase Order", re.compile(r"\bpurchase\s*orders?\b", re.I)),
    ("Purchase Requisition", "Purchase Requisition", re.compile(r"\bpurchase\s*requisitions?\b", re.I)),
]


@dataclass
class Category:
    name: str                 # Request For Quotation | Purchase Order | Purchase Requisition
    keyword: str              # the rule's word, e.g. "Proposal"
    line_no: int
    evidence: str
    also: list[str] = field(default_factory=list)    # other categories whose words also appear


def find_category(lines: list[str]) -> Category | None:
    from .references import heading_category
    heading=heading_category(lines)
    if heading:
        name,index,text=heading
        return Category(name,text,text,index)
    first: dict[str, tuple[int, int, str]] = {}       # category -> (line, column, line text)
    for i, ln in enumerate(lines):
        for name, _kw, rx in RULES:
            m = rx.search(ln)
            if m and name not in first:
                first[name] = (i, m.start(), ln)
    if not first:
        return None
    if "Request For Quotation" in first:                # "Proposal" always takes precedence
        best = "Request For Quotation"
    else:
        best = min(first, key=lambda n: first[n][:2])
    kw = next(k for n, k, _ in RULES if n == best)
    i, _col, ln = first[best]
    return Category(best, kw, i, ln, also=[n for n in first if n != best])
