"""
Generate deliberately inconsistent mock documents for testing the renamer.

Every file names an ISSUER company (the one the file should be named after)
and usually a CUSTOMER company (a distractor the renamer must ignore).
Layouts, label wording, date formats, field order and file formats vary.

Writes:  samples/<files>  and  samples/answer_key.json
Usage:   python tools/generate_samples.py [--out samples] [--seed 42]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

import pymupdf as fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --------------------------------------------------------------------------- data
ISSUERS = [
    "Macintosh Trading Inc.",
    "Bluewave Logistics Corp.",
    "Sampaguita Foods Co.",
    "Kalayaan Steel Works, Inc.",
    "Northstar Pharma Ltd.",
    "Pacific Rim Builders Corp.",
    "Orchid Lane Textiles",      # NOT in the registry: must be found by rules
    "Zenith Paper Co.",          # NOT in the registry: must be found by rules
]
CUSTOMERS = [
    "Greenfield Retail Corp.",
    "Riverside Hardware Inc.",
    "Lakeshore Medical Clinic",
    "Summit Bakeshop Co.",
    "Tagaytay Highlands Resort",
]
DOC_TYPES = {
    "invoice": ["Invoice Date", "Date of Invoice", "Date", "Invoice Dt."],
    "receipt": ["Receipt Date", "Date", "OR Date", "Date Paid"],
    "purchase_order": ["PO Date", "Order Date", "Date"],
    "statement": ["Statement Date", "Date Issued", "As of"],
    "memo": ["Date", "Dated"],
}
TITLES = {
    "invoice": ["SALES INVOICE", "Invoice", "TAX INVOICE", "BILLING INVOICE"],
    "receipt": ["OFFICIAL RECEIPT", "Receipt", "ACKNOWLEDGEMENT RECEIPT"],
    "purchase_order": ["PURCHASE ORDER", "Purchase Order"],
    "statement": ["STATEMENT OF ACCOUNT", "Statement of Account"],
    "memo": ["MEMORANDUM", "Memo"],
}
REF_LABELS = ["Invoice #", "Inv. No.", "Reference", "Ref #", "Doc No.", "Control No."]
ISSUER_LABELS = ["From:", "Supplier:", "Vendor:", "Issued by:", "Seller:"]
CUSTOMER_LABELS = ["Bill To:", "Sold To:", "Customer:", "Ship To:", "Client:"]
ITEMS = ["Bond paper A4", "Steel rebar 10mm", "Freight charges", "Paracetamol 500mg",
         "Cotton fabric (m)", "Dried mangoes", "Cement 40kg", "Consulting hours",
         "Packing tape", "Forklift rental"]

# Date styles. Slash dates are MM/DD (PH/US). Dot dates are DD.MM.
DATE_STYLES = [
    lambda d: d.strftime("%m/%d/%Y"),          # 01/15/2026
    lambda d: d.strftime("%Y-%m-%d"),          # 2026-01-15
    lambda d: f"{d.day} {d.strftime('%b %Y')}",  # 15 Jan 2026
    lambda d: f"{d.strftime('%B')} {d.day}, {d.year}",  # January 15, 2026
    lambda d: d.strftime("%d-%b-%y"),          # 15-Jan-26
    lambda d: d.strftime("%d.%m.%Y"),          # 15.01.2026
    lambda d: f"{d.strftime('%b')}. {d.day}, {d.year}",  # Jan. 15, 2026
]


@dataclass
class Spec:
    fname: str
    issuer: str
    customer: str | None
    doc_type: str
    doc_date: date
    date_label: str
    date_str: str
    due_str: str | None
    ref_label: str
    ref_no: str
    items: list[tuple[str, int, float]]
    currency: str
    issuer_mode: str          # header | labeled | footer
    title: str
    lines: list[str] = field(default_factory=list)


def code_for(name: str) -> str:
    letters = "".join(c for c in name.upper() if c.isalpha())
    return letters[:3]


def money(v: float, cur: str) -> str:
    return {"PHP": f"₱{v:,.2f}", "USD": f"USD {v:,.2f}", "PHP2": f"PHP {v:,.0f}"}[cur]


def make_spec(rng: random.Random, fname: str, issuer: str, *, issuer_mode: str | None = None) -> Spec:
    doc_type = rng.choice(list(DOC_TYPES))
    d = date(2025, 1, 1) + timedelta(days=rng.randint(0, 640))
    style = rng.choice(DATE_STYLES)
    due = d + timedelta(days=rng.choice([15, 30, 45])) if doc_type in ("invoice", "statement") else None
    items = [(rng.choice(ITEMS), rng.randint(1, 40), round(rng.uniform(50, 4500), 2))
             for _ in range(rng.randint(1, 4))]
    return Spec(
        fname=fname, issuer=issuer,
        customer=rng.choice(CUSTOMERS) if rng.random() < 0.85 else None,
        doc_type=doc_type, doc_date=d,
        date_label=rng.choice(DOC_TYPES[doc_type]), date_str=style(d),
        due_str=style(due) if due else None,
        ref_label=rng.choice(REF_LABELS), ref_no=f"{rng.choice(['INV','OR','PO','SOA','M'])}-{rng.randint(1000, 99999)}",
        items=items, currency=rng.choice(["PHP", "USD", "PHP2"]),
        issuer_mode=issuer_mode or rng.choice(["header", "header", "labeled", "footer"]),
        title=rng.choice(TITLES[doc_type]),
    )


def build_lines(rng: random.Random, s: Spec) -> list[str]:
    """Text lines in a randomized order. The issuer position depends on issuer_mode."""
    head: list[str] = []
    if s.issuer_mode == "header":
        head += [s.issuer, rng.choice(["Makati City, Metro Manila", "Pasig City", "Cebu City", "Quezon City"]),
                 rng.choice(["Tel. (02) 8812-3456", "+63 917 555 0142", "TIN 123-456-789-000"])]
    head.append(s.title)

    fields = [f"{s.ref_label} {s.ref_no}" if s.ref_label.endswith(("#", ".")) else f"{s.ref_label}: {s.ref_no}",
              f"{s.date_label}: {s.date_str}"]
    if s.due_str:
        fields.append(f"{rng.choice(['Due Date', 'Payment Due', 'Due'])}: {s.due_str}")
    if s.issuer_mode == "labeled":
        fields.append(f"{rng.choice(ISSUER_LABELS)} {s.issuer}")
    rng.shuffle(fields)

    cust: list[str] = []
    if s.customer:
        lab = rng.choice(CUSTOMER_LABELS)
        if rng.random() < 0.5:
            cust = [f"{lab} {s.customer}"]
        else:
            cust = [lab, s.customer, rng.choice(["Taguig City", "Mandaluyong City", "Davao City"])]

    body = ["Description | Qty | Unit Price | Amount"]
    total = 0.0
    for name, q, p in s.items:
        amt = q * p
        total += amt
        body.append(f"{name} | {q} | {money(p, s.currency)} | {money(amt, s.currency)}")
    body.append(f"{rng.choice(['TOTAL', 'Total Amount Due', 'Grand Total'])}: {money(total, s.currency)}")
    if s.doc_type == "memo":
        body = ["Subject: " + rng.choice(["Price adjustment", "Delivery schedule", "Account reconciliation"]),
                "Please be advised of the updated terms effective on the date above."]

    order = [head, fields, cust, body] if rng.random() < 0.6 else [head, cust, fields, body]
    lines = [ln for block in order for ln in block]
    if s.issuer_mode == "footer":
        lines += ["", f"Thank you for your business. — {s.issuer}"]
    return lines


# ---------------------------------------------------------------------- renderers
def font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("arial.ttf", "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_page_image(lines: list[str], rng: random.Random, width=1240, height=1754) -> Image.Image:
    img = Image.new("RGB", (width, height), "white")
    dr = ImageDraw.Draw(img)
    y = rng.randint(70, 140)
    x0 = rng.randint(70, 140)
    for i, ln in enumerate(lines):
        big = i == 0 or ln.isupper()
        f = font(40 if big else 28)
        dr.text((x0, y), ln, fill=(20, 20, 20), font=f)
        y += 56 if big else 44
    return img


def photo_effects(img: Image.Image, rng: random.Random, *, angle: float, blur: float, noise: int) -> Image.Image:
    bg = Image.new("RGB", (img.width + 240, img.height + 240), (rng.randint(90, 140),) * 3)
    bg.paste(img, (120, 120))
    out = bg.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=(110, 110, 110))
    # uneven lighting
    grad = Image.linear_gradient("L").resize(out.size).rotate(rng.choice([0, 90, 180, 270]))
    out = Image.blend(out, Image.merge("RGB", [grad] * 3), 0.12)
    if blur:
        out = out.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        px = out.load()
        for _ in range(out.width * out.height // 40):
            x, y = rng.randrange(out.width), rng.randrange(out.height)
            v = max(0, min(255, px[x, y][0] + rng.randint(-noise, noise)))
            px[x, y] = (v, v, v)
    return out


def write_txt(p: Path, lines): p.write_text("\n".join(lines), encoding="utf-8")


def write_md(p: Path, lines):
    out = []
    for i, ln in enumerate(lines):
        if i == 0:
            out.append(f"# {ln}")
        elif "|" in ln:
            out.append(f"| {ln} |")
        else:
            out.append(ln + "  ")
    p.write_text("\n".join(out), encoding="utf-8")


def write_html(p: Path, lines):
    rows = "".join(f"<tr><td>{'</td><td>'.join(c.strip() for c in ln.split('|'))}</td></tr>"
                   for ln in lines if "|" in ln)
    other = "".join(f"<p>{ln}</p>" for ln in lines if "|" not in ln)
    p.write_text(f"<html><head><title>doc</title><style>td{{padding:4px}}</style></head>"
                 f"<body><div class='hdr'>{other}</div><table border=1>{rows}</table></body></html>",
                 encoding="utf-8")


def write_eml(p: Path, s: Spec, lines):
    m = EmailMessage()
    m["From"] = f"billing@{code_for(s.issuer).lower()}-mail.ph"
    m["To"] = "accounts@client.ph"
    m["Subject"] = f"{s.title} {s.ref_no}"
    m["Date"] = "Mon, 13 Oct 2026 09:12:00 +0800"   # send date != document date on purpose
    m.set_content("Good day,\n\nPlease see the details below.\n\n" + "\n".join(lines) + "\n\nRegards,\nBilling Team")
    p.write_bytes(bytes(m))


def write_csv(p: Path, s: Spec):
    cols = ["vendor_name", "customer", "doc_type", "ref_no", s.date_label.lower().replace(" ", "_"), "currency", "total"]
    total = sum(q * pr for _, q, pr in s.items)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerow([s.issuer, s.customer or "", s.doc_type, s.ref_no, s.date_str, s.currency[:3], f"{total:.2f}"])


def write_docx(p: Path, s: Spec, lines, rng):
    import docx
    d = docx.Document()
    if s.issuer_mode == "header" and rng.random() < 0.5:
        # put the issuer in the page header instead of the body
        d.sections[0].header.paragraphs[0].text = s.issuer
        lines = [ln for ln in lines if ln != s.issuer]
    table_rows = [ln for ln in lines if "|" in ln]
    first_table = True
    for ln in lines:
        if "|" in ln:
            if first_table:
                t = d.add_table(rows=0, cols=4)
                for r in table_rows:
                    cells = t.add_row().cells
                    for c, v in zip(cells, r.split("|")):
                        c.text = v.strip()
                first_table = False
            continue
        if ln == s.title:
            d.add_heading(ln, level=1)
        else:
            d.add_paragraph(ln)
    d.save(p)


def write_xlsx(p: Path, s: Spec, lines, rng, real_date_cell: bool):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = rng.choice(["Sheet1", "Invoice", "SOA", "Data"])
    r = rng.randint(1, 3)
    c = rng.randint(1, 2)
    for ln in lines:
        if "|" in ln:
            for j, v in enumerate(ln.split("|")):
                ws.cell(row=r, column=c + j, value=v.strip())
        elif ":" in ln and not ln.startswith("Thank"):
            k, v = ln.split(":", 1)
            ws.cell(row=r, column=c, value=k.strip() + ":")
            if real_date_cell and k.strip() == s.date_label:
                cell = ws.cell(row=r, column=c + 1, value=s.doc_date)
                cell.number_format = "mm/dd/yyyy"
            else:
                ws.cell(row=r, column=c + 1, value=v.strip())
        else:
            ws.cell(row=r, column=c, value=ln)
        r += 1
    wb.save(p)


def write_pptx(p: Path, lines):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    box = sl.shapes.add_textbox(Inches(0.5), Inches(0.4), Inches(9), Inches(6.5))
    tf = box.text_frame
    for i, ln in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.text = ln
        para.font.size = Pt(14 if i else 24)
    prs.save(p)


def write_pdf_text(p: Path, lines, rng):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = rng.randint(50, 90)
    x = rng.randint(40, 70)
    for i, ln in enumerate(lines):
        size = 16 if (i == 0 or ln.isupper()) else 10.5
        page.insert_text((x, y), ln, fontsize=size, fontname="helv")
        y += size + 8
    doc.save(p)


def write_pdf_scanned(p: Path, img: Image.Image):
    tmp = p.with_suffix(".tmp.png")
    img.convert("L").save(tmp)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(tmp))
    doc.save(p)
    tmp.unlink()


def write_rtf(p: Path, lines):
    def esc(t: str) -> str:
        out = []
        for ch in t:
            if ch in "\\{}":
                out.append("\\" + ch)
            elif ord(ch) > 127:
                out.append(f"\\u{ord(ch)}?")
            else:
                out.append(ch)
        return "".join(out)
    body = "\\par\n".join(esc(ln) for ln in lines)
    p.write_text("{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Arial;}}\\f0\\fs22\n" + body + "\\par\n}", encoding="ascii")


def soffice_convert(src: Path, fmt: str, out_dir: Path) -> Path | None:
    exe = shutil.which("soffice") or r"C:\Program Files\LibreOffice\program\soffice.exe"
    try:
        subprocess.run([exe, "--headless", "--convert-to", fmt, "--outdir", str(out_dir), str(src)],
                       check=True, capture_output=True, timeout=120)
    except Exception as e:  # noqa: BLE001
        print(f"  ! LibreOffice conversion to {fmt} failed: {e}", file=sys.stderr)
        return None
    return out_dir / f"{src.stem}.{fmt}"


# --------------------------------------------------------------------------- main
PLAN = [
    # (file name, writer key, extra)
    ("acct_notes.txt", "txt", {}),
    ("README_vendor.md", "md", {}),
    ("export_0931.csv", "csv", {}),
    ("billing_page.html", "html", {}),
    ("Fwd_ statement.eml", "eml", {}),
    ("SOA final FINAL.docx", "docx", {}),
    ("invoice-copy.docx", "docx", {}),
    ("ledger_entry.xlsx", "xlsx", {"real_date_cell": True}),
    ("q3 billing.xlsx", "xlsx", {"real_date_cell": False}),
    ("deck_summary.pptx", "pptx", {}),
    ("scan0001.pdf", "pdf_text", {}),
    ("Invoice (2).pdf", "pdf_text", {}),
    ("receipt_digital.pdf", "pdf_text", {}),
    ("SCAN_20251102.pdf", "pdf_scan", {"angle": 0.0, "blur": 0.0, "noise": 0}),
    ("scanned_po.pdf", "pdf_scan", {"angle": 1.5, "blur": 0.6, "noise": 25}),
    ("IMG_4471.jpg", "photo", {"angle": 3.0, "blur": 0.8, "noise": 30}),
    ("IMG_4472.jpg", "photo", {"angle": -5.0, "blur": 1.0, "noise": 20}),
    ("IMG_4480.JPG", "photo_exif", {"angle": 1.0, "blur": 0.5, "noise": 15}),
    ("screenshot.png", "png", {}),
    ("phone_capture.heic", "heic", {"angle": 2.0, "blur": 0.6, "noise": 15}),
    ("old_memo.rtf", "rtf", {}),
    ("contract_page.odt", "odt", {}),
    ("legacy_invoice.doc", "doc", {}),
    ("not_really_a.pdf", "png_misnamed", {}),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="samples")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    rng = random.Random(a.seed)
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    tmp = out / "_tmp"
    tmp.mkdir()

    issuers = (ISSUERS * 4)[: len(PLAN)]
    rng.shuffle(issuers)
    key: dict[str, dict] = {}

    for (fname, kind, extra), issuer in zip(PLAN, issuers):
        # Unknown issuers get a labeled or header position so a human could also find them.
        mode = rng.choice(["labeled", "header"]) if issuer in ISSUERS[6:] else None
        if kind == "csv":
            mode = "labeled"
        s = make_spec(rng, fname, issuer, issuer_mode=mode)
        lines = build_lines(rng, s)
        p = out / fname
        true_ext = p.suffix.lower().lstrip(".")

        if kind == "txt":
            write_txt(p, lines)
        elif kind == "md":
            write_md(p, lines)
        elif kind == "csv":
            write_csv(p, s)
        elif kind == "html":
            write_html(p, lines)
        elif kind == "eml":
            write_eml(p, s, lines)
        elif kind == "docx":
            write_docx(p, s, lines, rng)
        elif kind == "xlsx":
            write_xlsx(p, s, lines, rng, **extra)
            if extra.get("real_date_cell"):
                s.date_str = s.doc_date.strftime("%m/%d/%Y")
        elif kind == "pptx":
            write_pptx(p, lines)
        elif kind == "pdf_text":
            write_pdf_text(p, lines, rng)
        elif kind == "pdf_scan":
            img = render_page_image(lines, rng)
            if extra["angle"] or extra["blur"] or extra["noise"]:
                img = photo_effects(img, rng, **extra)
            write_pdf_scanned(p, img)
        elif kind in ("photo", "photo_exif"):
            img = photo_effects(render_page_image(lines, rng), rng, **extra)
            if kind == "photo_exif":
                # Store the pixels sideways and let EXIF say "rotate 90° CW".
                img = img.transpose(Image.ROTATE_90)
                exif = Image.Exif()
                exif[0x0112] = 6
                img.save(p, "JPEG", quality=70, exif=exif.tobytes())
            else:
                img.save(p, "JPEG", quality=65)
            true_ext = "jpg"
        elif kind == "png":
            render_page_image(lines, rng).save(p, "PNG")
        elif kind == "heic":
            import pillow_heif
            pillow_heif.register_heif_opener()
            photo_effects(render_page_image(lines, rng), rng, **extra).save(p, "HEIF", quality=70)
        elif kind == "png_misnamed":
            render_page_image(lines, rng).save(p, "PNG")   # PNG bytes, .pdf name
            true_ext = "png"
        elif kind == "rtf":
            write_rtf(p, lines)
        elif kind in ("odt", "doc"):
            src = tmp / (Path(fname).stem + ".docx")
            write_docx(src, s, lines, rng)
            conv = soffice_convert(src, kind, tmp)
            if not conv or not conv.exists():
                print(f"  - skipped {fname} (needs LibreOffice)")
                continue
            shutil.move(str(conv), p)

        code = "PO" + code_for(issuer)          # lookup-sheet codes must start with RFQ or PO
        key[fname] = {
            "issuer": issuer,
            "customer": s.customer,
            "code": code,
            "date": s.doc_date.isoformat(),
            "date_as_printed": s.date_str,
            "date_label": s.date_label,
            "issuer_position": s.issuer_mode,
            "expected_name": f"{code}_{s.doc_date.strftime('%m%d%Y')}.{true_ext}",
        }
        print(f"  {fname:28s} -> {key[fname]['expected_name']}")

    shutil.rmtree(tmp)
    (out / "answer_key.json").write_text(json.dumps(key, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(key)} files written to {out}/ (seed {a.seed})")


if __name__ == "__main__":
    main()
