"""Render the synthetic sample documents (PDF and image files) from the canonical dataset files.

This is a controlled, truth-side process: it reads `documents_truth` and renders exactly what `expected_fields` says
(including injected discrepancies). Every page carries a diagonal watermark, a footer notice and file metadata saying
the document is synthetic. Output is local and gitignored.

Quality -> file (see documents_truth.file_name):
  CLEAN          .pdf       born-digital, with a text layer
  SKEWED         .png       rasterised, rotated a few degrees
  LOW_RES        .jpg       low-resolution, heavily compressed
  SCANNED_NOISE  .scan.pdf  image-only PDF with noise (no text layer)
Multi-page documents in .png/.jpg are stacked vertically in one image.
"""

import csv
import hashlib
import io
import json
from datetime import date
from pathlib import Path

import reportlab
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from PIL.PngImagePlugin import PngInfo
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .rng import Rng

W, H = 595, 842  # A4 in points
NOTICE = "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT"
FOOT = "Generated synthetic demonstration data. No real person, account or identity. Not valid for any purpose."
STMT_FROM = "2026-05-01"
ROWS_PER_PAGE = 40
FONT_DIR = Path(reportlab.__file__).parent / "fonts"
pdfmetrics.registerFont(TTFont("Vera", str(FONT_DIR / "Vera.ttf")))
pdfmetrics.registerFont(TTFont("VeraBd", str(FONT_DIR / "VeraBd.ttf")))


def inr(n) -> str:
    n = int(str(n).split(".")[0])  # accepts ints and canonical "123.00" strings
    s = str(abs(n))
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ("-" if n < 0 else "") + "Rs. " + ",".join(parts + [tail]) + ".00"


def dmy(iso: str) -> str:
    return f"{iso[8:10]}-{iso[5:7]}-{iso[:4]}"


class Page:
    def __init__(self):
        self.ops: list[tuple] = []

    def text(self, x, y, s, size=10, bold=False, align="l", gray=0.0):
        self.ops.append(("text", x, y, str(s), size, bold, align, gray))

    def line(self, x1, y1, x2, y2, w=0.6):
        self.ops.append(("line", x1, y1, x2, y2, w))

    def rect(self, x, y, w, h, w_line=0.8):
        self.ops.append(("rect", x, y, w, h, w_line))


def frame(title: str, subtitle: str = "") -> Page:
    p = Page()
    p.rect(30, 30, W - 60, H - 60)
    p.text(40, 62, title, 15, True)
    if subtitle:
        p.text(40, 78, subtitle, 8.5, gray=0.35)
    p.line(40, 88, W - 40, 88)
    p.text(W / 2, H - 50, NOTICE, 9, True, "c")
    p.text(W / 2, H - 39, FOOT, 6.5, False, "c", 0.3)
    return p


# ------------------------------------------------------------------ document layouts
def pan_card(f: dict) -> list[Page]:
    p = frame("SAMPLE PERMANENT ACCOUNT NUMBER CARD", "Synthetic specimen. Not issued by any authority.")
    y = 140
    for label, key in (("Name", "name"), ("Father's name", "father_name"), ("Date of birth", "dob"), ("Permanent Account Number (synthetic)", "pan")):
        p.text(60, y, label, 8.5, gray=0.35)
        p.text(60, y + 17, dmy(f[key]) if key == "dob" else f[key], 13, True)
        y += 52
    p.rect(400, 130, 130, 150)
    p.text(465, 208, "PHOTO", 9, False, "c", 0.4)
    p.text(465, 222, "(synthetic, none)", 7, False, "c", 0.4)
    return [p]


def aadhaar(f: dict) -> list[Page]:
    p = frame("SAMPLE MASKED IDENTITY CARD", "Synthetic specimen. Number is masked. Not issued by any authority.")
    y = 140
    for label, val in (("Name", f["name"]), ("Date of birth", dmy(f["dob"])), ("Gender", {"M": "Male", "F": "Female", "O": "Other"}[f["gender"]]),
                       ("Identity number (masked)", f["aadhaar_masked"]), ("Address", f["address"])):
        p.text(60, y, label, 8.5, gray=0.35)
        p.text(60, y + 17, val, 13, True)
        y += 52
    p.rect(400, 130, 130, 150)
    p.text(465, 208, "PHOTO", 9, False, "c", 0.4)
    p.text(465, 222, "(synthetic, none)", 7, False, "c", 0.4)
    return [p]


def salary_slip(f: dict) -> list[Page]:
    p = frame(f["employer"].upper(), f"Payslip for {f['month']}")
    p.text(60, 120, "Employee name", 8.5, gray=0.35)
    p.text(60, 136, f["employee_name"], 13, True)
    p.text(380, 120, "Pay period", 8.5, gray=0.35)
    p.text(380, 136, f["month"], 13, True)
    p.line(60, 170, W - 60, 170)
    p.text(60, 190, "Earnings", 10, True)
    p.text(W - 60, 190, "Amount", 10, True, "r")
    p.text(60, 212, "Gross salary", 10)
    p.text(W - 60, 212, inr(f["gross"]), 10, False, "r")
    p.text(60, 236, "Total deductions", 10)
    p.text(W - 60, 236, inr(f["deductions"]), 10, False, "r")
    p.line(60, 252, W - 60, 252)
    p.text(60, 274, "NET PAY", 12, True)
    p.text(W - 60, 274, inr(f["net"]), 12, True, "r")
    p.text(60, 330, "This payslip is computer generated sample data.", 8, gray=0.4)
    return [p]


def itr(f: dict) -> list[Page]:
    p1 = frame("SAMPLE INCOME TAX RETURN SUMMARY", f"Assessment year {f['assessment_year']}. Synthetic specimen.")
    y = 130
    for label, val in (("Name", f["name"]), ("Permanent Account Number (synthetic)", f["pan"]), ("Assessment year", f["assessment_year"]), ("Gross total income", inr(f["gross_total_income"]))):
        p1.text(60, y, label, 8.5, gray=0.35)
        p1.text(60, y + 17, val, 13, True)
        y += 52
    p2 = frame("COMPUTATION OF TOTAL INCOME", "Page 2 of 2. Derived from the gross total income, for illustration only.")
    gti = int(f["gross_total_income"])
    std = min(50000, gti)
    p2.text(60, 130, "Gross total income", 10)
    p2.text(W - 60, 130, inr(gti), 10, False, "r")
    p2.text(60, 154, "Standard deduction", 10)
    p2.text(W - 60, 154, inr(std), 10, False, "r")
    p2.line(60, 170, W - 60, 170)
    p2.text(60, 192, "Total taxable income", 11, True)
    p2.text(W - 60, 192, inr(gti - std), 11, True, "r")
    return [p1, p2]


def bank_statement(f: dict, rows: list[dict]) -> list[Page]:
    n_pages = max(1, -(-len(rows) // ROWS_PER_PAGE))
    pages = []
    for i in range(n_pages):
        p = frame(f"{f['institution'].upper()} ACCOUNT STATEMENT", f"Account {f['account_id']}  |  Holder {f['account_holder']}  |  Period {f['period']}  |  Page {i + 1} of {n_pages}")
        y0 = 130
        p.text(40, y0, "Date", 7.5, True)
        p.text(100, y0, "Narration", 7.5, True)
        p.text(415, y0, "Debit", 7.5, True, "r")
        p.text(480, y0, "Credit", 7.5, True, "r")
        p.text(550, y0, "Balance", 7.5, True, "r")
        p.line(40, y0 + 6, W - 40, y0 + 6)
        y = y0 + 20
        for r in rows[i * ROWS_PER_PAGE:(i + 1) * ROWS_PER_PAGE]:
            amt = inr(r["amount"]).replace("Rs. ", "")
            returned = r.get("txn_status") == "RETURNED"
            p.text(40, y, dmy(r["txn_date"]), 7)
            p.text(100, y, r["narration"][:42] + (" [RETURNED]" if returned else ""), 7)
            if r["direction"] == "DEBIT":
                p.text(415, y, amt, 7, False, "r")
            else:
                p.text(480, y, amt, 7, False, "r")
            p.text(550, y, inr(r["balance_after"]).replace("Rs. ", ""), 7, False, "r")
            y += 14
        if i == n_pages - 1:
            y += 12
            p.line(40, y - 8, W - 40, y - 8)
            for label, val in (("Opening balance", f["opening_balance"]), ("Total credits", f["total_credits"]), ("Total debits", f["total_debits"]), ("Closing balance", f["closing_balance"])):
                p.text(40, y, label, 8, True)
                p.text(550, y, inr(val), 8, True, "r")
                y += 13
        pages.append(p)
    return pages


# ------------------------------------------------------------------ renderers
def draw_pdf(c: canvas.Canvas, p: Page) -> None:
    for op in p.ops:
        if op[0] == "text":
            _, x, y, s, size, bold, align, gray = op
            c.setFont("VeraBd" if bold else "Vera", size)
            c.setFillGray(gray)
            {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[align](x, H - y, s)
        elif op[0] == "line":
            _, x1, y1, x2, y2, w = op
            c.setLineWidth(w)
            c.line(x1, H - y1, x2, H - y2)
        else:
            _, x, y, w, h, wl = op
            c.setLineWidth(wl)
            c.rect(x, H - y - h, w, h)
    c.saveState()
    c.setFillColorRGB(0.8, 0.1, 0.1)
    c.setFillAlpha(0.13)
    c.translate(W / 2, H / 2)
    c.rotate(35)
    c.setFont("VeraBd", 30)
    c.drawCentredString(0, 0, NOTICE)
    c.restoreState()
    c.setFillGray(0)


def pdf_bytes_text(pages: list[Page], title: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H), invariant=1)  # invariant: fixed dates and ids, so output is reproducible
    c.setTitle(title)
    c.setSubject(NOTICE)
    c.setKeywords("synthetic, sample, not a real document")
    c.setAuthor("Synthetic data generator")
    for p in pages:
        draw_pdf(c, p)
        c.showPage()
    c.save()
    return buf.getvalue()


def raster(p: Page, dpi: int) -> Image.Image:
    s = dpi / 72
    img = Image.new("RGB", (round(W * s), round(H * s)), "white")
    d = ImageDraw.Draw(img)
    fonts: dict = {}
    for op in p.ops:
        if op[0] == "text":
            _, x, y, txt, size, bold, align, gray = op
            key = (round(size * s, 1), bold)
            if key not in fonts:
                fonts[key] = ImageFont.truetype(str(FONT_DIR / ("VeraBd.ttf" if bold else "Vera.ttf")), key[0])
            f = fonts[key]
            wdt = d.textlength(txt, font=f)
            px = x * s - (wdt if align == "r" else wdt / 2 if align == "c" else 0)
            g = int(gray * 255)
            d.text((px, y * s), txt, font=f, fill=(g, g, g), anchor="ls")
        elif op[0] == "line":
            _, x1, y1, x2, y2, w = op
            d.line([(x1 * s, y1 * s), (x2 * s, y2 * s)], fill=(0, 0, 0), width=max(1, round(w * s)))
        else:
            _, x, y, w, h, wl = op
            d.rectangle([x * s, y * s, (x + w) * s, (y + h) * s], outline=(0, 0, 0), width=max(1, round(wl * s)))
    layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
    ld = ImageDraw.Draw(layer)
    f = ImageFont.truetype(str(FONT_DIR / "VeraBd.ttf"), 30 * s)
    ld.text((img.width / 2, img.height / 2), NOTICE, font=f, fill=(204, 26, 26, 36), anchor="mm")
    layer = layer.rotate(35, resample=Image.BICUBIC)
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def keystream(seed: str, n: int) -> bytes:
    out, i = bytearray(), 0
    while len(out) < n:
        out += hashlib.sha256(f"{seed}:{i}".encode()).digest()
        i += 1
    return bytes(out[:n])


def stack(images: list[Image.Image]) -> Image.Image:
    if len(images) == 1:
        return images[0]
    out = Image.new("RGB", (images[0].width, sum(i.height for i in images) + 6 * (len(images) - 1)), (150, 150, 150))
    y = 0
    for i in images:
        out.paste(i, (0, y))
        y += i.height + 6
    return out


def degrade(img: Image.Image, seed: str, kind: str) -> Image.Image:
    r = Rng.labelled(0, seed)
    if kind == "SKEWED":
        angle = (r.randint(15, 40) / 10) * (1 if r.chance(50) else -1)
        return img.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255)).filter(ImageFilter.GaussianBlur(0.5))
    # SCANNED_NOISE
    g = img.convert("L").rotate((r.randint(5, 15) / 10) * (1 if r.chance(50) else -1), resample=Image.BICUBIC, fillcolor=255)
    noise = Image.frombytes("L", g.size, keystream(seed, g.width * g.height))
    g = Image.blend(g, noise, 0.10).filter(ImageFilter.GaussianBlur(0.6))
    return ImageEnhance.Contrast(g).enhance(0.85).convert("RGB")


def render_file(doc: dict, pages: list[Page], out_dir: Path) -> Path:
    name, q = doc["file_name"], doc["quality"]
    path = out_dir / name
    title = f"{doc['doc_id']} synthetic sample {doc['doc_type']}"
    if q == "CLEAN":
        path.write_bytes(pdf_bytes_text(pages, title))
    elif q == "SKEWED":
        img = stack([degrade(raster(p, 150), f"{doc['doc_id']}:{i}", "SKEWED") for i, p in enumerate(pages)])
        info = PngInfo()
        info.add_text("Description", NOTICE)
        img.save(path, "PNG", pnginfo=info)
    elif q == "LOW_RES":
        stack([raster(p, 80) for p in pages]).save(path, "JPEG", quality=40, comment=NOTICE.encode())
    else:  # SCANNED_NOISE: image-only PDF, no text layer
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(W, H), invariant=1)
        c.setTitle(title)
        c.setSubject(NOTICE)
        c.setKeywords("synthetic, sample, not a real document")
        for i, p in enumerate(pages):
            jb = io.BytesIO()
            degrade(raster(p, 150), f"{doc['doc_id']}:{i}", "SCANNED_NOISE").save(jb, "JPEG", quality=70)
            jb.seek(0)
            c.drawImage(ImageReader(jb), 0, 0, W, H)
            c.showPage()
        c.save()
        path.write_bytes(buf.getvalue())
    return path


def read(out: Path, ds: str, table: str) -> list[dict]:
    with open(out / ds / f"{table}.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def render_all(out: Path, only: set | None = None) -> dict:
    docs = [d for d in read(out, "lh_truth", "documents_truth") if only is None or d["doc_id"] in only]
    acc = {r["account_id"]: r for r in read(out, "lh_raw", "accounts")}
    tx = read(out, "lh_raw", "transactions")
    lines = read(out, "lh_truth", "external_statement_lines")
    by_acc: dict[str, list[dict]] = {}
    for r in tx:
        if r["txn_date"] >= STMT_FROM:
            by_acc.setdefault(r["account_id"], []).append(r)
    for r in lines:
        if r["txn_date"] >= STMT_FROM:
            by_acc.setdefault(r["account_id"], []).append(r)
    docdir = out / "documents"
    docdir.mkdir(parents=True, exist_ok=True)
    for old in docdir.iterdir():
        old.unlink()
    files = {}
    for d in docs:
        f = json.loads(d["expected_fields"])
        t = d["doc_type"]
        if t == "PAN_CARD":
            pages = pan_card(f)
        elif t == "AADHAAR_MASKED":
            pages = aadhaar(f)
        elif t == "SALARY_SLIP":
            pages = salary_slip(f)
        elif t == "ITR":
            pages = itr(f)
        else:
            rows = sorted(by_acc[f["account_id"]], key=lambda r: r.get("txn_id") or r.get("line_id"))
            pages = bank_statement(f, rows)
        if len(pages) != int(d["page_count"]):
            raise AssertionError(f"{d['doc_id']}: rendered {len(pages)} pages, truth says {d['page_count']}")
        p = render_file(d, pages, docdir)
        data = p.read_bytes()
        files[p.name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "pages": len(pages)}
    total = hashlib.sha256("\n".join(f"{k} {v['sha256']}" for k, v in sorted(files.items())).encode()).hexdigest()
    manifest = {"synthetic_notice": NOTICE, "document_count": len(files), "documents_sha256": total, "files": dict(sorted(files.items())),
                "note": "Byte-identical output requires the same reportlab and Pillow versions; both are recorded in documents_run_info.json."}
    (out / "documents_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    import PIL

    (out / "documents_run_info.json").write_text(json.dumps({"reportlab": reportlab.Version, "pillow": PIL.__version__}, indent=1) + "\n", encoding="utf-8")
    return manifest
