"""End-to-end check of intake and document upload against the live services: Supabase Auth, the real API code, BigQuery as the app
identity, the Supabase database and the private Supabase Storage bucket.

It takes an existing synthetic customer who has rendered sample documents (data/out, produced by data/render_documents.py), runs
enquiry -> explicit conversion -> intake -> uploads of the documents that customer's frozen checklist asks for, then has an Assistant
Manager open one through a signed link and checks the bytes. The application is left in DOCS_PENDING as normal demo data for the
next phase (OCR and verification). Synthetic data only.

    backend/.venv/Scripts/python scripts/live_check_intake.py
"""

import csv
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.storage import BUCKET  # noqa: E402
from live_check_enquiry import token  # noqa: E402

OUT = ROOT / "data" / "out"
REQUIREMENT = {"PAN_CARD": "PAN_CARD", "AADHAAR_MASKED": "ADDRESS_PROOF", "SALARY_SLIP": "SALARY_SLIPS_3M", "ITR": "ITR_1Y", "BANK_STATEMENT": "BANK_STATEMENT_6M"}
MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg"}


def main() -> int:
    api = TestClient(app)
    lo = {"Authorization": "Bearer " + token("loan.officer@demo.example.com")}
    am = {"Authorization": "Bearer " + token("assistant.manager@demo.example.com")}
    ok = []

    def check(name, cond, detail=""):
        ok.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))
        return cond

    docs = defaultdict(list)
    for r in csv.DictReader(open(OUT / "lh_truth" / "documents_truth.csv", encoding="utf-8")):
        docs[r["customer_id"]].append(r)
    customers = {r["customer_id"]: r for r in csv.DictReader(open(OUT / "lh_raw" / "customers.csv", encoding="utf-8"))}
    # an existing customer with lapsed KYC brings identity documents as well as income proof, so more of the checklist is exercised
    pool = sorted(c for c, d in docs.items() if customers[c]["bank_relationship"] == "ETB" and {"PAN_CARD", "AADHAAR_MASKED", "SALARY_SLIP"} <= {x["doc_type"] for x in d})
    pool = sorted(pool, key=lambda c: customers[c]["kyc_status"] == "VERIFIED")
    cid = next(c for c in pool if not api.get("/applications", params={"q": c}, headers=lo).json()["applications"])
    print(f"customer {cid}  KYC {customers[cid]['kyc_status']}  documents on file: {sorted(x['doc_type'] for x in docs[cid])}")

    e = api.post("/enquiries", headers=lo, json={"customer_id": cid, "declared_credit_history": "ESTABLISHED", "requested_amount": "300000", "requested_tenure_months": 36})
    if not check("enquiry created", e.status_code == 201, e.text[:200]):
        return 1
    conv = api.post(f"/enquiries/{e.json()['id']}/convert", headers=lo, json={"expected_version": 1, "confirm": True})
    if not check("converted on explicit confirmation", conv.status_code == 201, conv.text[:200]):
        return 1
    aid = conv.json()["application_id"]
    a = api.get(f"/applications/{aid}", headers=lo).json()
    check("intake starts with identity and address from the bank's records", a["status"] == "INTAKE" and a["intake"]["missing_details"] == [], str(a["intake"]))
    done = api.post(f"/applications/{aid}/intake/complete", headers=lo, json={"expected_version": a["version"]})
    if not check("intake completed, checklist frozen, status DOCS_PENDING", done.status_code == 200 and done.json()["status"] == "DOCS_PENDING", done.text[:300]):
        return 1
    ck = done.json()["checklist"]
    print(f"      {a['reference']}  required: {[i['code'] for i in ck['items']]}  waived: {[w['code'] for w in ck['waived']]}  basis: {ck['basis']}")
    wanted = {i["code"] for i in ck["items"]}
    uploaded = []
    for d in sorted(docs[cid], key=lambda x: x["doc_id"]):
        req = REQUIREMENT[d["doc_type"]]
        if req not in wanted:
            continue
        path = OUT / "documents" / d["file_name"]
        r = api.put(f"/applications/{aid}/documents", headers=lo | {"Content-Type": MIME[path.suffix[1:]]}, content=path.read_bytes(),
                    params={"requirement": req, "doc_type": d["doc_type"], "file_name": path.name})
        check(f"uploaded {path.name} as {req}", r.status_code == 201, r.text[:200])
        uploaded.append((r.json(), path))
    again = api.put(f"/applications/{aid}/documents", headers=lo | {"Content-Type": MIME[uploaded[0][1].suffix[1:]]}, content=uploaded[0][1].read_bytes(),
                    params={"requirement": uploaded[0][0]["requirement"], "doc_type": uploaded[0][0]["doc_type"], "file_name": uploaded[0][1].name})
    check("the same file is not accepted twice", again.status_code == 409)
    a = api.get(f"/applications/{aid}", headers=am).json()
    print(f"      checklist: {[(i['code'], i['uploaded']) for i in a['checklist']['items']]}")
    doc, path = uploaded[0]
    u = api.get(f"/applications/{aid}/documents/{doc['id']}/url", headers=am)
    check("Assistant Manager gets a 60-second signed link", u.status_code == 200 and u.json()["expires_in_seconds"] == 60, u.text[:200])
    got = httpx.get(u.json()["url"], timeout=30)
    check("the stored file is byte-for-byte the uploaded one", got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == doc["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest())
    anon = {"Authorization": f"Bearer {settings.supabase_anon_key}", "apikey": settings.supabase_anon_key}
    base = settings.supabase_url.rstrip("/") + "/storage/v1"
    check("the browser's anon key cannot list the bucket", httpx.post(f"{base}/object/list/{BUCKET}", headers=anon, json={"prefix": aid}).json() == [])
    check("there is no public URL for the file", httpx.get(f"{base}/object/public/{BUCKET}/{aid}/{doc['id']}.{path.suffix[1:]}").status_code >= 400)
    print(f"\n{sum(ok)}/{len(ok)} live checks passed; {a['reference']} is left in {a['status']} for the next phase")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
