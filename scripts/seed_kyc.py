"""Load the account-opening KYC of the synthetic existing customers (owner tool). Idempotent: customers who already have a KYC record are skipped.

For every existing (ETB) customer in data/out: a KYC record (kyc_profiles, version 1, status from the bank's records) and three generated
documents stored in the private kyc-documents bucket: a PAN-style card, a masked Aadhaar-style card and an address proof (a utility-bill-style
letter). Every value matches the customer's record. New-to-bank customers have no account and so no account-opening KYC: theirs is created
when they apply (from their verified documents).

The documents are deliberately unlike real ones: no emblem, no issuing-authority name, SAMPLE / DEMO watermarks on every page, the synthetic
SYNP PAN (12 characters, so it can never be a real PAN) and only the last four Aadhaar digits. No photographs are generated.

    backend/.venv/Scripts/python scripts/seed_kyc.py [--limit N]
"""

import argparse
import csv
import hashlib
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import kyc  # noqa: E402
from app.config import settings  # noqa: E402
from app.storage import KYC_BUCKET, get_kyc_storage  # noqa: E402

CARD = (86 * mm, 54 * mm)
WATERMARK = "SAMPLE / DEMO - NOT A GOVERNMENT DOCUMENT"
GENDER = {"M": "Male", "F": "Female", "O": "Other"}


def _watermark(c, w, h, size=11):
    c.saveState()
    c.setFillColor(colors.Color(0.75, 0.1, 0.1, alpha=0.18))
    c.setFont("Helvetica-Bold", size)
    c.translate(w / 2, h / 2)
    c.rotate(28)
    c.drawCentredString(0, 0, WATERMARK)
    c.restoreState()


def _card(title: str, band, lines: list[tuple[str, str]], footer: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=CARD)
    w, h = CARD
    c.setFillColor(colors.HexColor("#FBFAF7"))
    c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(colors.HexColor(band))
    c.rect(0, h - 11 * mm, w, 11 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(4 * mm, h - 6 * mm, title)
    c.setFont("Helvetica", 5.5)
    c.drawString(4 * mm, h - 9.3 * mm, "SAMPLE / DEMO document for a synthetic customer")
    y = h - 16 * mm
    for label, value in lines:
        c.setFillColor(colors.HexColor("#6B6B74"))
        c.setFont("Helvetica", 5.5)
        c.drawString(4 * mm, y, label)
        c.setFillColor(colors.HexColor("#1C1917"))
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(24 * mm, y, value[:46])
        y -= 5.2 * mm
    c.setFillColor(colors.HexColor("#6B6B74"))
    c.setFont("Helvetica", 5)
    c.drawString(4 * mm, 3 * mm, footer)
    _watermark(c, w, h, 9)
    c.save()
    return buf.getvalue()


def pan_card(cu: dict) -> bytes:
    return _card("PAN-STYLE CARD", "#1E3A8A", [("Name", cu["full_name"].upper()), ("Date of birth", _dmy(cu["date_of_birth"])),
                                                ("PAN (synthetic)", cu["pan"])], "Synthetic SYNP number: not a valid PAN. For the demo only.")


def aadhaar_card(cu: dict) -> bytes:
    return _card("AADHAAR-STYLE CARD (MASKED)", "#B45309", [("Name", cu["full_name"]), ("Date of birth", _dmy(cu["date_of_birth"])),
                                                              ("Gender", GENDER.get(cu["gender"], "-")), ("Number", f"XXXX XXXX {cu['aadhaar_last4']}"),
                                                              ("Address", f"{cu['address_line']}, {cu['city']}")],
                 "Masked: only the last 4 digits are shown. Not an Aadhaar card.")


def address_proof(cu: dict, cid: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(148 * mm, 105 * mm))
    w, h = 148 * mm, 105 * mm
    c.setFillColor(colors.HexColor("#0F766E"))
    c.rect(0, h - 16 * mm, w, 16 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(8 * mm, h - 9 * mm, "ELECTRICITY BILL-STYLE ADDRESS PROOF")
    c.setFont("Helvetica", 7)
    c.drawString(8 * mm, h - 13.5 * mm, "SAMPLE / DEMO address proof for a synthetic customer (no real utility)")
    c.setFillColor(colors.HexColor("#1C1917"))
    rows = [("Consumer name", cu["full_name"]), ("Service address", cu["address_line"]), ("", f"{cu['city']}, {cu['state']} {cu['pincode']}"),
            ("Consumer number", f"SYN-{cid[-4:]}-{hashlib.sha256(cid.encode()).hexdigest()[:6].upper()}"), ("Bill month", "October 2026"),
            ("Amount (Rs)", f"{(int(cid[-4:]) % 900) + 450}.00")]
    y = h - 26 * mm
    for label, value in rows:
        c.setFont("Helvetica", 8)
        c.drawString(8 * mm, y, label)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(45 * mm, y, value)
        y -= 8 * mm
    _watermark(c, w, h, 14)
    c.save()
    return buf.getvalue()


def _dmy(d: str) -> str:
    return date.fromisoformat(d).strftime("%d/%m/%Y")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    customers = [r for r in csv.DictReader(open(ROOT / "data" / "out" / "lh_raw" / "customers.csv", encoding="utf-8")) if r["bank_relationship"] == "ETB"]
    storage = get_kyc_storage()
    print("bucket", KYC_BUCKET, storage.ensure_bucket())
    with psycopg.connect(settings.supabase_db_url, row_factory=dict_row, autocommit=True) as conn:
        have = {r["customer_id"] for r in conn.execute("SELECT customer_id FROM kyc_profiles WHERE customer_id IS NOT NULL").fetchall()}
        todo = [cu for cu in customers if cu["customer_id"] not in have][: a.limit or None]
        print(f"{len(customers)} existing customers, {len(have)} already loaded, {len(todo)} to load")

        def files(cu):
            cid = cu["customer_id"]
            out = []
            for doc_type, data in (("PAN_CARD", pan_card(cu)), ("AADHAAR_MASKED", aadhaar_card(cu)), ("ADDRESS_PROOF", address_proof(cu, cid))):
                path = f"account-opening/{cid}/{doc_type.lower()}.pdf"
                try:
                    storage.put(path, data, "application/pdf")
                except Exception as e:  # noqa: BLE001  already uploaded by an earlier, interrupted run
                    if "409" not in str(e) and "Duplicate" not in str(e) and "already exists" not in str(e):
                        raise
                out.append((doc_type, path, hashlib.sha256(data).hexdigest()))
            return cu, out

        done = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            for cu, out in pool.map(files, todo):
                status = {"VERIFIED": "VERIFIED", "EXPIRED": "EXPIRED"}.get(cu["kyc_status"], "PENDING")
                with conn.transaction():
                    p = kyc.create(conn, {"customer_id": cu["customer_id"], "pan": cu["pan"], "full_name": cu["full_name"], "date_of_birth": cu["date_of_birth"],
                                          "gender": cu["gender"] or None, "aadhaar_last4": cu["aadhaar_last4"], "address_line": cu["address_line"], "city": cu["city"],
                                          "state": cu["state"], "pincode": cu["pincode"], "kyc_status": status,
                                          "verified_on": cu["kyc_last_verified_on"] or (cu["customer_since"] if status != "PENDING" else None)},
                                   "ACCOUNT_OPENING", "KYC at account opening (synthetic customer, SAMPLE / DEMO documents)")
                    for doc_type, path, sha in out:
                        kyc.add_document(conn, p["id"], doc_type, bucket=KYC_BUCKET, path=path, name=f"{doc_type.lower()}_{cu['customer_id']}.pdf",
                                         mime="application/pdf", sha256=sha, source="ACCOUNT_OPENING", is_sample=True)
                done += 1
                if done % 100 == 0:
                    print(f"  {done}/{len(todo)}", flush=True)
    print(f"loaded {done} KYC records with {done * 3} SAMPLE / DEMO documents")
    return 0


if __name__ == "__main__":
    sys.exit(main())
