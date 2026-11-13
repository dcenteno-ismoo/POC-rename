"""Flow 5: deterministic rules that find the document date and the issuing company."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from email.utils import parsedate_to_datetime

from rapidfuzz import fuzz

from . import config


def compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ===================================================================== DATES
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"

RX_ISO = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
RX_NUM = re.compile(r"(?<![\d.])(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})(?![\d/-])")
RX_DOT = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?![\d.])")
RX_DMY = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s\-/.]*" + MON + r"[\s\-/.,]*(\d{4}|\d{2})(?!\d)", re.I)
RX_MDY = re.compile(MON + r"\s*(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})(?!\d)", re.I)

# Labels that name the document's own date. Compact form (lowercase, no spaces/punctuation).
DATE_LABELS_STRONG = ["invoicedate", "dateofinvoice", "invoicedt", "receiptdate", "ordate", "datepaid",
                      "podate", "orderdate", "statementdate", "dateissued", "issuedate", "issueddate",
                      "documentdate", "billingdate", "transactiondate", "dated", "asof"]
DATE_LABELS_WEAK = ["date"]
DATE_EXCLUDE = ["due", "deliver", "ship", "expir", "valid", "period", "birth", "maturity",
                "cutoff", "until", "start", "end", "received", "printed", "sent"]


@dataclass
class DateHit:
    value: date
    raw: str
    label: str
    line_no: int
    ambiguous: bool
    score: float = 0.0
    evidence: str = ""


def _fix_ocr_digits(s: str) -> str:
    s = re.sub(r"(?<=[\d./-])[Oo](?=[\d./-])", "0", s)
    s = re.sub(r"(?<=[\d./-])[lI|](?=[\d./-])", "1", s)
    return s


def _year(y: str) -> int:
    v = int(y)
    return v + 2000 if v < 100 else v


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    return dt if 1990 <= dt.year <= date.today().year + 1 else None


def _dates_in_line(line: str) -> list[tuple[int, int, date, str, bool]]:
    """(start, end, date, raw, ambiguous) for every date in the line, non-overlapping."""
    s = _fix_ocr_digits(line)
    found: list[tuple[int, int, date, str, bool]] = []

    def free(a, b):
        return all(b <= x or a >= y for x, y, *_ in found)

    for m in RX_ISO.finditer(s):
        d = _mk(int(m[1]), int(m[2]), int(m[3]))
        if d and free(*m.span()):
            found.append((*m.span(), d, m[0], False))
    for m in RX_DMY.finditer(s):
        d = _mk(_year(m[3]), MONTHS[m[2][:3].lower()], int(m[1]))
        if d and free(*m.span()):
            found.append((*m.span(), d, m[0], False))
    for m in RX_MDY.finditer(s):
        d = _mk(_year(m[3]), MONTHS[m[1][:3].lower()], int(m[2]))
        if d and free(*m.span()):
            found.append((*m.span(), d, m[0], False))
    for m in RX_NUM.finditer(s):
        a, b, y = int(m[1]), int(m[2]), _year(m[3])
        mdy = config.SLASH_DATE_ORDER == "MDY"
        m_, d_ = (a, b) if mdy else (b, a)
        if m_ > 12 and d_ <= 12:            # unambiguous the other way round
            m_, d_ = d_, m_
        d = _mk(y, m_, d_)
        if d and free(*m.span()):
            found.append((*m.span(), d, m[0], a <= 12 and b <= 12 and a != b))
    for m in RX_DOT.finditer(s):            # dotted dates are day-first (15.01.2026)
        d_, m_, y = int(m[1]), int(m[2]), _year(m[3])
        if m_ > 12 and d_ <= 12:
            m_, d_ = d_, m_
        d = _mk(y, m_, d_)
        if d and free(*m.span()):
            found.append((*m.span(), d, m[0], False))
    return sorted(found)


def _score_label(label: str) -> float:
    c = compact(label)[-30:]
    if not c:
        return 1.0
    if any(x in c for x in DATE_EXCLUDE):
        return -20.0
    if any(c.endswith(x) or x in c[-16:] for x in DATE_LABELS_STRONG):
        return 12.0
    if any(c.endswith(x) for x in DATE_LABELS_WEAK):
        return 8.0
    return 0.5


def find_date(lines: list[str], meta: dict) -> DateHit | None:
    hits: list[DateHit] = []
    for i, line in enumerate(lines):
        prev_end = 0
        for a, b, d, raw, amb in _dates_in_line(line):
            label = line[prev_end:a]
            prev_end = b
            if not compact(label) and i > 0 and lines[i - 1].rstrip().endswith(":") and len(lines[i - 1]) < 40:
                label = lines[i - 1]            # label on the previous line
            h = DateHit(d, raw, label.strip(" :|-"), i, amb, evidence=line)
            h.score = _score_label(label) - 0.01 * i
            hits.append(h)
    if hits:
        best = max(hits, key=lambda h: (h.score, -h.line_no))
        if best.score > -10:
            return best
    ed = meta.get("email_date")
    if ed:
        try:
            dt = parsedate_to_datetime(str(ed)).date()
            return DateHit(dt, str(ed), "email header Date", -1, False, -5, f"Date: {ed}")
        except (TypeError, ValueError):
            pass
    return None


# ================================================================= COMPANY
LEGAL = r"(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|opc|plc)"
RX_ISSUER_LABEL = re.compile(
    r"(?:^|[\s|])(from|supplier(?:\s*name)?|vendor(?:\s*name)?|issued\s*by|seller|company(?:\s*name)?"
    r"|payee|biller|merchant|billed\s*by|sold\s*by)\s*[:\-]\s*(.*)$", re.I)
RX_CUSTOMER_LABEL = re.compile(
    r"(?:^|[\s|])(bill(?:ed)?\s*to|sold\s*to|ship\s*to|deliver(?:ed)?\s*to|customer(?:\s*name)?|client(?:\s*name)?"
    r"|consignee|attention|attn|buyer|recipient)\s*[:\-]?\s*(.*)$", re.I)
RX_SUFFIX_SPACED = re.compile(r"^(.{2,60}?)[\s,]+" + LEGAL + r"\.?$", re.I)
RX_SUFFIX_GLUED = re.compile(r"^(.{2,60}?[a-z])(Co|Inc|Corp|Ltd|LLC)\.?$")
SEPARATORS = re.compile(r"\s+[—–|]\s+|\s+-\s+|:\s*|\.\s+(?=[A-Z])")
NOT_LETTERHEAD = ["invoice", "receipt", "statement", "order", "memo", "account", "billing", "quotation",
                  "delivery", "subject", "page", "city", "street", "avenue", "road", "blvd", "barangay",
                  "brgy", "tel", "tin", "phone", "email", "floor", "bldg", "building", "metro", "manila",
                  "philippines", "description", "total", "date", "thank", "dear", "good day", "regards"]
RX_PREAMBLE = re.compile(r"^\s*(subject|re|fwd?|fw)\s*:|^\s*(good\s+(day|morning|afternoon|evening)|dear|hi|hello"
                         r"|greetings|please|kindly|attached|see\s+below|forwarded)\b", re.I)
STRIP_SUFFIX = re.compile(r"[\s,]*\b" + LEGAL + r"\b\.?\s*$", re.I)


@dataclass
class Company:
    name: str
    code: str
    rule: str                 # labeled | registry | legal_suffix | letterhead
    line_no: int
    evidence: str
    registry: bool = False
    score: float = 0.0


@dataclass
class RegistryEntry:
    name: str
    code: str | None
    keys: list[str] = field(default_factory=list)     # compact core names


def core_name(name: str) -> str:
    n = re.sub(r"^\s*the\s+", "", name, flags=re.I)
    prev = None
    while prev != n:
        prev, n = n, STRIP_SUFFIX.sub("", n)
    return compact(n)


def code_from_name(name: str) -> str:
    n = re.sub(r"^\s*the\s+", "", name, flags=re.I)
    letters = "".join(ch for ch in n.upper() if "A" <= ch <= "Z")
    return letters[:3] if len(letters) >= 3 else letters.ljust(3, "X")


def registry() -> tuple[RegistryEntry, ...]:
    """Known clients = the rows of the lookup sheet (lookup.xlsx)."""
    from .lookup import entries          # imported here: lookup.py imports this module
    return entries()


def _registry_match(text: str) -> RegistryEntry | None:
    c = compact(text)
    if not c:
        return None
    best, best_score = None, 0.0
    for e in registry():
        for k in e.keys:
            if k in c:
                return e
            if len(k) >= 6:
                sc = fuzz.partial_ratio(k, c)
                if sc > best_score:
                    best, best_score = e, sc
    return best if best_score >= config.REGISTRY_MATCH_MIN else None


def _customer_lines(lines: list[str]) -> set[int]:
    marked: set[int] = set()
    for i, ln in enumerate(lines):
        m = RX_CUSTOMER_LABEL.search(ln)
        if not m:
            continue
        marked.add(i)
        if not compact(m.group(2)):          # "Bill To:" alone -> name is on the next lines
            marked.update({i + 1, i + 2})
    return marked


def _clean_name(v: str) -> str:
    v = v.split(" | ")[0]
    v = re.split(r"\s{2,}|\s+(?=[A-Z][a-z]+\s*#|\b(?:Tel|TIN|Date|Ref)\b)", v)[0]
    return v.strip(" :-|,;")


def _suffix_name(segment: str) -> str | None:
    seg = segment.strip(" .|-—–")
    if not seg or len(seg.split()) > 7:
        return None
    if RX_SUFFIX_SPACED.match(seg) or RX_SUFFIX_GLUED.match(seg):
        if seg[0].isupper() or seg[0].isdigit():
            return seg
    return None


def _letterhead(line: str) -> bool:
    words = line.split()
    if not 1 <= len(words) <= 6:
        return False
    if not re.fullmatch(r"[A-Za-z&'.,\- ]+", line):
        return False
    if any(w in line.lower() for w in NOT_LETTERHEAD):
        return False
    return all(w[0].isupper() or w.lower() in ("and", "of", "de", "&") for w in words)


def _make(name: str, rule: str, i: int, line: str, base: float) -> Company:
    entry = _registry_match(name)
    if entry:
        return Company(entry.name, entry.code or code_from_name(entry.name), rule, i, line, True,
                       base + 10 - 0.2 * i)
    return Company(name, code_from_name(name), rule, i, line, False, base - 0.2 * i)


def find_company(lines: list[str]) -> Company | None:
    cust = _customer_lines(lines)
    cands: list[Company] = []

    for i, ln in enumerate(lines):
        # 1. labeled issuer: "Supplier: X", "From: X", "vendor name: X"
        m = RX_ISSUER_LABEL.search(ln)
        if m and i not in cust:
            val = _clean_name(m.group(2))
            if not compact(val) and i + 1 < len(lines):
                val = _clean_name(lines[i + 1])
            if compact(val) and "@" not in val:
                cands.append(_make(val, "labeled", i, ln, 100))
        if i in cust:
            continue
        # 2. known company from the registry
        e = _registry_match(ln)
        if e:
            cands.append(Company(e.name, e.code or code_from_name(e.name), "registry", i, ln, True, 60 - 0.2 * i))
        # 3. legal suffix: "... Inc.", "... Corp.", "... Co."
        for seg in SEPARATORS.split(ln):
            nm = _suffix_name(seg)
            if nm:
                cands.append(_make(nm, "legal_suffix", i, ln, 45))

    # 4. letterhead: a short title-case line among the first 3 content lines
    #    (greetings / e-mail preambles are skipped, they are not content)
    content = [i for i, ln in enumerate(lines[:12]) if not RX_PREAMBLE.match(ln)][:3]
    for i in content:
        if i not in cust and _letterhead(lines[i]):
            cands.append(_make(lines[i], "letterhead", i, lines[i], 30))
            break

    if not cands:
        return None
    return max(cands, key=lambda c: (c.score, -c.line_no))
