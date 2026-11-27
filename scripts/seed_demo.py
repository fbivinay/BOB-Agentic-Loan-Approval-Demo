"""Seed demo volume through the real application (owner tool). Synthetic people and documents only.

Each case is a synthetic customer from data/out (rendered documents) taken from enquiry towards a target stage by the three demo staff,
signed in with Supabase Auth and calling the real API code in-process: real document processing (embedded text, Gemini reading of scans,
Gemini field extraction), the real assessment, real decisions, sanction packs, emails and simulated disbursements.
Every rule applies as it does for a person at the desk, including four-eyes and maker-checker.

The staff work is what a person would do: documents are checked against the image (the value printed on the synthetic document, from the
generator's record of what it printed, is keyed or confirmed), discrepancies are resolved with a note, decisions follow the AI
recommendation except where a reviewer has a reason. Each synthetic customer also gets a generated application form (PDF with a text layer)
and a placeholder photograph, both marked synthetic, because the dataset has none.

    backend/.venv/Scripts/python scripts/seed_demo.py --plan                   # show the plan, change nothing
    backend/.venv/Scripts/python scripts/seed_demo.py --cases 1 --target FORWARDED
    backend/.venv/Scripts/python scripts/seed_demo.py                          # the whole plan, resumable
Options: --applications N (default 60) --enquiries M (default 100) --gemini-cap K (this run's daily Gemini budget) --seed S.
Progress is kept in data/out/seed_demo_state.json (git-ignored); a re-run continues where it stopped.

Daily activity (GitHub Actions, .github/workflows/demo-daily.yml):
    backend/.venv/Scripts/python scripts/seed_demo.py --upload-source      # owner, once: synthetic documents to the private demo-source bucket
    backend/.venv/Scripts/python scripts/seed_demo.py --daily              # 4-5 new enquiries (2-3 taken to forwarded); yesterday's files decided
                                                                           # and disbursed, now and then one referred to the Branch Manager
Without data/out (as on a GitHub runner) the customer list comes from data/customers_1000.csv and the documents from the demo-source bucket.
"""

import argparse
import csv
import io
import json
import os
import random
import sys
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "out"
STATE = OUT / "seed_demo_state.json"
REQUIREMENT = {"PAN_CARD": "PAN_CARD", "AADHAAR_MASKED": "ADDRESS_PROOF", "SALARY_SLIP": "SALARY_SLIPS_3M", "ITR": "ITR_1Y", "BANK_STATEMENT": "BANK_STATEMENT_6M"}
MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg"}
EMAILS = {"LO": "loan.officer@demo.example.com", "AM": "assistant.manager@demo.example.com", "BM": "branch.manager@demo.example.com"}
ALL_ITEMS = {"kfs_acknowledged": True, "loan_agreement_executed": True, "repayment_mandate_registered": True, "original_kyc_verified": True}
ATTESTATION = "I have checked every document against the originals and the details match."
# targets, in workflow order; the counts for --applications 60 (scaled for other sizes)
TARGETS = [("DOCS_PENDING", 5), ("AI_ASSESSED", 4), ("FORWARDED", 6), ("UNDER_REVIEW", 4), ("REFERRED", 3), ("SANCTIONED", 9), ("DECLINED", 6),
           ("DISBURSED", 19), ("REFERRED_DISBURSED", 4)]
ORDER = ["INTAKE", "DOCS_PENDING", "DISCREPANCY", "DOCS_VERIFIED", "AI_ASSESSED", "FORWARDED", "UNDER_REVIEW", "APPROVED", "SANCTIONED", "DISBURSED"]
NOTES = ["Salary credits at our bank match the slip; customer prefers the tenure asked for.", "Documents complete; customer is a long-standing account holder.",
         "Income proof checked against the bank statement; no other loans declared.", "Customer asked for an early decision; file is complete."]
REFER_NOTES = ["Customer asked for a smaller amount to keep the EMI lower; please adjust the terms.",
               "EMI looks high against take-home pay; Branch Manager to consider a lower amount.",
               "Customer requested a revised amount after seeing the KFS; please review the terms."]


def plan(n_apps: int, n_enq: int, seed: int, customers: dict, docs: dict) -> list[dict]:
    rnd = random.Random(seed)
    scale = n_apps / sum(c for _, c in TARGETS)
    targets = [t for t, c in TARGETS for _ in range(max(1, round(c * scale)))][:n_apps]
    while len(targets) < n_apps:
        targets.append("DISBURSED")
    rnd.shuffle(targets)
    with_docs = sorted(docs)
    rnd.shuffle(with_docs)
    cases = []
    for i, t in enumerate(targets):
        processor = rnd.choices(["LO", "AM", "BM"], weights=[78, 14, 8])[0]
        if t.startswith("REFERRED"):
            processor = rnd.choice(["LO", "LO", "LO", "BM"])  # the Assistant Manager reviews these, so did not process them
        reviewer = "BM" if processor == "AM" else ("AM" if processor == "BM" or rnd.random() < 0.85 else "BM")
        if t.startswith("REFERRED"):
            reviewer = "AM"
        cases.append({"kind": "APPLICATION", "customer_id": with_docs[i], "target": t, "processor": processor, "reviewer": reviewer,
                      "amount": rnd.choice([150000, 200000, 250000, 300000, 350000, 400000, 500000, 600000, 750000]),
                      "tenure": rnd.choice([12, 24, 36, 36, 48, 60]), "note": rnd.choice(NOTES), "refer_note": rnd.choice(REFER_NOTES),
                      "adjust": t.startswith("REFERRED") or rnd.random() < 0.15, "app_id": None, "reached": None, "done": False})
    others = sorted(c for c in customers if c not in docs and customers[c]["bank_relationship"] == "ETB")
    rnd.shuffle(others)
    for i in range(max(0, n_enq - n_apps)):
        cases.append({"kind": "ENQUIRY", "customer_id": others[i], "target": rnd.choice(["DRAFT", "DRAFT", "CLOSED"]),
                      "processor": rnd.choices(["LO", "AM", "BM"], weights=[80, 12, 8])[0],
                      "amount": rnd.choice([100000, 200000, 300000, 500000, 800000]), "tenure": rnd.choice([12, 24, 36, 48, 60]), "done": False})
    rnd.shuffle(cases)
    return cases


# ---------------------------------------------------------------- synthetic form and photograph
def application_form(name: str, amount: int, tenure: int) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for size, text in [(14, "Personal Loan Application Form"), (9, "SYNTHETIC DEMO DOCUMENT - not a Bank of Baroda form"), (11, ""),
                       (11, f"Applicant name: {name}"), (11, f"Loan amount applied for: {amount}"), (11, f"Tenure requested: {tenure} months"),
                       (11, "Purpose: Personal use"), (11, ""), (11, "Declaration: I confirm the information given is true."), (11, "Signed: yes"),
                       (11, f"Signature: {name}")]:
        c.setFont("Helvetica-Bold" if size == 14 else "Helvetica", size)
        c.drawString(60, y, text)
        y -= 26
    c.save()
    return buf.getvalue()


def certificate(title: str, lines: list[tuple[str, str]]) -> bytes:
    """A simple born-digital certificate (business registration or pension payment order), marked SAMPLE / DEMO."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, 800, title)
    c.setFont("Helvetica", 9)
    c.drawString(60, 784, "SAMPLE / DEMO DOCUMENT for a synthetic customer - not issued by any authority")
    y = 750
    for label, value in lines:
        c.setFont("Helvetica", 11)
        c.drawString(60, y, f"{label}: {value}")
        y -= 24
    c.save()
    return buf.getvalue()


def extra_documents(cust: dict, cid: str, wanted: set, have: set) -> list[tuple]:
    """Business proof and pension order, which the synthetic dataset does not render, built from the customer's own employment record."""
    out = []
    reg = f"SYN-UDYAM-{cid[-4:]}-{abs(hash(cid)) % 9000 + 1000}"
    if "BUSINESS_PROOF" in wanted and "BUSINESS_PROOF" not in have:
        biz = cust.get("employer_name") or f"{cust['full_name'].split()[-1]} Enterprises"
        f = {"business_name": biz, "registration_number": reg, "proprietor_name": cust["full_name"]}
        out.append(("BUSINESS_PROOF", "BUSINESS_PROOF", "business_registration.pdf",
                    certificate("Business Registration Certificate", [("Business name", biz), ("Registration number", reg), ("Proprietor", cust["full_name"]),
                                                                      ("Address", f"{cust['address_line']}, {cust['city']}")]), f))
    if "PENSION_PROOF" in wanted and "PENSION_PROOF" not in have:
        amount = str(int(float(cust.get("declared_monthly_net") or 30000)))
        ppo = f"SYN-PPO-{cid[-4:]}"
        f = {"pensioner_name": cust["full_name"], "ppo_number": ppo, "monthly_pension": amount}
        out.append(("PENSION_PROOF", "PENSION_ORDER", "pension_order.pdf",
                    certificate("Pension Payment Order", [("Pensioner", cust["full_name"]), ("PPO number", ppo), ("Monthly pension (Rs)", amount)]), f))
    return out


def photograph(name: str) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (360, 450), (226, 222, 216))
    d = ImageDraw.Draw(img)
    d.ellipse((120, 70, 240, 200), fill=(150, 140, 132))
    d.ellipse((60, 220, 300, 520), fill=(150, 140, 132))
    d.rectangle((0, 400, 360, 450), fill=(255, 255, 255))
    d.text((12, 410), "SYNTHETIC PHOTO - not a real person", fill=(90, 90, 90))
    d.text((12, 428), name[:40], fill=(90, 90, 90))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- driver
class Desk:
    def __init__(self, gemini_cap: int | None):
        if gemini_cap:
            os.environ["GEMINI_DAILY_CAP"] = str(gemini_cap)
        sys.path.insert(0, str(ROOT / "backend"))
        sys.path.insert(0, str(ROOT / "scripts"))
        from fastapi.testclient import TestClient

        from app import jobs
        from app.main import app
        from live_check_enquiry import token

        self.jobs, self.token, self.api = jobs, token, TestClient(app)
        self.deps = jobs.get_job_deps()
        self.h, self.at = {}, 0.0

    def hdr(self, who: str) -> dict:
        if time.time() - self.at > 1800:  # tokens last an hour; refresh every half hour
            self.h = {k: {"Authorization": "Bearer " + self.token(e)} for k, e in EMAILS.items()}
            self.at = time.time()
        return self.h[who]

    def call(self, who, method, path, **kw):
        r = self.api.request(method, path, headers=self.hdr(who) | kw.pop("headers", {}), **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{who} {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def app(self, aid):
        return self.call("LO", "GET", f"/applications/{aid}")

    def drain_until(self, cond, tries=60):
        for _ in range(tries):
            if cond():
                return True
            if not self.jobs.drain(self.deps, limit=10):
                time.sleep(3)
        return cond()


def declared_income(customer_docs: list[dict]) -> int:
    """What the customer would say they take home: the net pay on their slip, else a month of income credits on their statement."""
    for x in customer_docs:
        f = json.loads(x["expected_fields"])
        if x["doc_type"] == "SALARY_SLIP" and f.get("net"):
            return int(Decimal(str(f["net"]).replace(",", "")))
    for x in customer_docs:
        f = json.loads(x["expected_fields"])
        if x["doc_type"] == "BANK_STATEMENT" and f.get("income_credit_total") and f.get("income_credit_count"):
            return int(Decimal(str(f["income_credit_total"]).replace(",", "")) / max(1, int(f["income_credit_count"])))
    return 60000


SOURCE_BUCKET = "demo-source"


def source_storage():
    from app.config import settings
    from app.storage import Storage

    return Storage(settings.supabase_url, settings.supabase_service_role_key, bucket=SOURCE_BUCKET, max_bytes=20 * 1024 * 1024,
                   mime_types=("application/pdf", "image/png", "image/jpeg", "text/csv"))


def read_doc(name: str) -> bytes:
    p = OUT / "documents" / name
    return p.read_bytes() if p.exists() else source_storage().get(f"documents/{name}")


def load_inputs() -> tuple[dict, dict]:
    """Customers and their rendered documents: from data/out when present, otherwise the committed list and the demo-source bucket."""
    if (OUT / "lh_raw" / "customers.csv").exists():
        customers = {r["customer_id"]: r for r in csv.DictReader(open(OUT / "lh_raw" / "customers.csv", encoding="utf-8"))}
        for e in csv.DictReader(open(OUT / "lh_raw" / "employment.csv", encoding="utf-8")):
            if e["is_current"] == "true" and e["customer_id"] in customers:
                customers[e["customer_id"]] |= {"employer_name": e["employer_name"], "declared_monthly_net": e["declared_monthly_net"]}
        truth = open(OUT / "lh_truth" / "documents_truth.csv", encoding="utf-8").read()
    else:
        customers = {r["customer_id"]: r for r in csv.DictReader(open(ROOT / "data" / "customers_1000.csv", encoding="utf-8"))}
        truth = source_storage().get("documents_truth.csv").decode("utf-8")
    docs = defaultdict(list)
    for r in csv.DictReader(io.StringIO(truth)):
        docs[r["customer_id"]].append(r)
    return customers, docs


def upload_source() -> int:
    s = source_storage()
    print("bucket", SOURCE_BUCKET, s.ensure_bucket())
    files = sorted((OUT / "documents").iterdir())
    for i, p in enumerate(files, 1):
        try:
            s.put(f"documents/{p.name}", p.read_bytes(), MIME.get(p.suffix[1:], "application/pdf"))
        except Exception as e:  # noqa: BLE001  already there from an earlier run
            if "409" not in str(e) and "Duplicate" not in str(e):
                raise
        if i % 100 == 0:
            print(f"  {i}/{len(files)}", flush=True)
    s.remove("documents_truth.csv")
    s.put("documents_truth.csv", (OUT / "lh_truth" / "documents_truth.csv").read_bytes(), "text/csv")
    print(f"uploaded {len(files)} documents and documents_truth.csv")
    return 0


def daily(d: "Desk", customers: dict, docs: dict, seed: int) -> None:
    """One branch day: yesterday's sanctioned files disbursed, forwarded files decided (now and then one referred to the Branch Manager),
    and 4-5 new enquiries, 2-3 of them taken through intake, documents and assessment to forwarded."""
    import psycopg
    from psycopg.rows import dict_row

    from app.config import settings

    rnd = random.Random(f"{seed}-{time.strftime('%Y-%m-%d')}")
    with psycopg.connect(settings.supabase_db_url, row_factory=dict_row) as c:
        apps = c.execute("SELECT ap.id, ap.status, ap.referred_to_role, ap.assigned_to IS NULL AS free FROM applications ap "
                         "WHERE ap.status IN ('FORWARDED', 'SANCTIONED') ORDER BY ap.updated_at").fetchall()
        used = {r["customer_id"] for r in c.execute("SELECT DISTINCT customer_id FROM applicants WHERE customer_id IS NOT NULL").fetchall()}
        used_pans = {r["pan"] for r in c.execute("SELECT DISTINCT pan FROM applicants WHERE pan IS NOT NULL").fetchall()}
    def attempt(label, fn, case):  # one file's problem is reported and the day goes on
        try:
            fn()
        except Exception as err:  # noqa: BLE001
            case["error"] = str(err)[:300]
        print(f"  {label}", case.get("app_id") or case.get("customer_id"), case.get("reached"), f"(stopped: {case['error']})" if case.get("error") else "")

    for a in [x for x in apps if x["status"] == "SANCTIONED"][:3]:
        case = {"app_id": str(a["id"]), "target": "DISBURSED", "processor": "LO", "reviewer": "AM", "done": False}
        attempt("disbursed", lambda: disburse(d, case), case)
    forwarded = [x for x in apps if x["status"] == "FORWARDED" and x["free"]]
    for a in forwarded[:3]:
        referred = a["referred_to_role"] == "BRANCH_MANAGER"
        refer = not referred and rnd.random() < 0.2
        case = {"app_id": str(a["id"]), "target": "REFERRED" if refer else ("REFERRED_SANCTIONED" if referred else "SANCTIONED"), "processor": "LO",
                "reviewer": "AM", "adjust": referred, "note": rnd.choice(NOTES), "refer_note": rnd.choice(REFER_NOTES), "done": False}
        attempt("reviewed", lambda: review_and_decide(d, case, already_referred=referred), case)
    fresh = [cid for cid in sorted(docs) if cid not in used and customers[cid]["pan"] not in used_pans]
    rnd.shuffle(fresh)
    n_apps, n_enq = rnd.choice([2, 3]), rnd.choice([4, 5])
    others = [cid for cid in sorted(customers) if cid not in used and cid not in docs and customers[cid]["bank_relationship"] == "ETB"]
    rnd.shuffle(others)
    for cid in fresh[:n_apps]:
        case = {"kind": "APPLICATION", "customer_id": cid, "target": "FORWARDED", "processor": rnd.choices(["LO", "AM", "BM"], weights=[80, 12, 8])[0],
                "reviewer": "AM", "amount": rnd.choice([200000, 300000, 400000, 500000, 750000]), "tenure": rnd.choice([24, 36, 48, 60]),
                "note": rnd.choice(NOTES), "refer_note": rnd.choice(REFER_NOTES), "adjust": False, "app_id": None, "reached": None, "done": False}
        attempt("new application", lambda: run_application(d, case, customers, docs), case)
    for cid in others[: max(0, n_enq - n_apps)]:
        case = {"kind": "ENQUIRY", "customer_id": cid, "target": rnd.choice(["DRAFT", "DRAFT", "CLOSED"]), "processor": "LO",
                "amount": rnd.choice([100000, 200000, 300000, 500000]), "tenure": rnd.choice([12, 24, 36, 48]), "done": False}
        attempt("new enquiry", lambda: run_enquiry(d, case), case)


def at_least(status: str, target: str) -> bool:
    return status in ORDER and target in ORDER and ORDER.index(status) >= ORDER.index(target)


def run_enquiry(d: Desk, c: dict) -> None:
    e = d.call(c["processor"], "POST", "/enquiries", json={"customer_id": c["customer_id"], "declared_credit_history": "ESTABLISHED",
                                                             "requested_amount": str(c["amount"]), "requested_tenure_months": c["tenure"]})
    if c["target"] == "CLOSED":
        d.call(c["processor"], "POST", f"/enquiries/{e['id']}/close", json={"expected_version": e["version"], "reason": "Customer chose not to proceed for now"})
    c["reached"], c["done"] = c["target"], True


def run_application(d: Desk, c: dict, customers: dict, docs: dict) -> None:
    who, cust = c["processor"], customers[c["customer_id"]]
    if not c["app_id"]:  # resuming after an interruption: pick up this customer's application if one was already started
        found = d.call("LO", "GET", "/applications", params={"q": c["customer_id"] if cust["bank_relationship"] == "ETB" else cust["mobile"]})["applications"]
        if found:
            c["app_id"] = found[0]["id"]
    if not c["app_id"]:
        body = {"declared_credit_history": "ESTABLISHED", "requested_amount": str(c["amount"]), "requested_tenure_months": c["tenure"]}
        if cust["bank_relationship"] == "ETB":
            body["customer_id"] = c["customer_id"]
        else:
            emp = "SALARIED_PRIVATE" if any(x["doc_type"] == "SALARY_SLIP" for x in docs[c["customer_id"]]) else "SELF_EMPLOYED_PROFESSIONAL"
            body["applicant"] = {"full_name": cust["full_name"], "date_of_birth": cust["date_of_birth"], "gender": cust["gender"] or None, "mobile": cust["mobile"],
                                 "email": cust["email"] or None, "pan": cust["pan"] or None, "employment_type": emp,
                                 "declared_net_income": str(declared_income(docs[c["customer_id"]]))}
        e = d.call(who, "POST", "/enquiries", json=body)
        conv = d.call(who, "POST", f"/enquiries/{e['id']}/convert", json={"expected_version": e["version"], "confirm": True})
        c["app_id"] = conv["application_id"]
    aid = c["app_id"]
    a = d.app(aid)
    if a["status"] == "INTAKE":
        missing = a["intake"]["missing_details"]
        if missing:
            fill = {k: cust.get(k) for k in missing if cust.get(k)}
            if fill:
                d.call(who, "PATCH", f"/applications/{aid}/applicant", json=fill)
        a = d.app(aid)
        a = d.call(who, "POST", f"/applications/{aid}/intake/complete", json={"expected_version": a["version"]}) and d.app(aid)
    c["reached"] = a["status"]
    if a["status"] in ("DOCS_PENDING", "DISCREPANCY"):
        upload_and_verify(d, c, a, cust, docs)
        a = d.app(aid)
        c["reached"] = a["status"]
    if a["status"] == "DOCS_VERIFIED":
        d.drain_until(lambda: d.app(aid)["status"] != "DOCS_VERIFIED", tries=80)
        a = d.app(aid)
        c["reached"] = a["status"]
    if c["target"] == "AI_ASSESSED" or a["status"] != "AI_ASSESSED" and a["status"] not in ("FORWARDED", "UNDER_REVIEW", "APPROVED", "SANCTIONED"):
        c["done"] = c["target"] == "AI_ASSESSED" or a["status"] in ("DOCS_PENDING", "DISCREPANCY", "DECLINED", "DISBURSED")
        return
    review_and_decide(d, c)


def upload_and_verify(d: Desk, c: dict, a: dict, cust: dict, docs: dict) -> None:
    aid, who = c["app_id"], c["processor"]
    wanted = {i["code"] for i in a["checklist"]["items"]}
    have = {x["requirement"] for x in a["documents"] if x["status"] != "WITHDRAWN"}
    files = []
    for x in sorted(docs[c["customer_id"]], key=lambda r: r["doc_id"]):
        req = REQUIREMENT.get(x["doc_type"])
        if req in wanted and req not in have:
            files.append((req, x["doc_type"], x["file_name"], read_doc(x["file_name"]), json.loads(x["expected_fields"])))
            have.add(req)
    name = cust["full_name"]
    if "APPLICATION_FORM" in wanted and "APPLICATION_FORM" not in have:
        files.append(("APPLICATION_FORM", "APPLICATION_FORM", "application_form.pdf", application_form(name, c["amount"], c["tenure"]),
                      {"applicant_name": name, "requested_amount": str(c["amount"]), "signed": "yes"}))
    if "PHOTOGRAPH" in wanted and "PHOTOGRAPH" not in have:
        files.append(("PHOTOGRAPH", "PHOTOGRAPH", "photograph.png", photograph(name), {}))
    files += extra_documents(cust, c["customer_id"], wanted, have)
    if c["target"] == "DOCS_PENDING":
        files = files[: max(1, len(files) // 2)]  # some documents still to come
    truth = {}
    for req, dt, fname, data, fields in files:
        r = d.call(who, "PUT", f"/applications/{aid}/documents", headers={"Content-Type": MIME[fname.rsplit('.', 1)[1]]}, content=data,
                   params={"requirement": req, "doc_type": dt, "file_name": fname})
        truth[r["id"]] = {k: v for k, v in fields.items() if k != "synthetic_notice" and v not in (None, "")}
    if c["target"] == "DOCS_PENDING":
        c["done"] = True
        return
    pending = lambda: [x for x in d.app(aid)["documents"] if x["status"] in ("UPLOADED", "PROCESSING")]  # noqa: E731
    d.drain_until(lambda: not pending(), tries=120)
    for x in d.app(aid)["documents"]:
        if x["status"] in ("VERIFIED", "WITHDRAWN", "REJECTED"):
            continue
        rv = d.call(who, "GET", f"/applications/{aid}/documents/{x['id']}/review")
        shown = {f["field"]: f for f in rv["fields"]}
        expected = truth.get(x["id"], {})
        send = {}
        for k in rv["expected_fields"]:
            f = shown.get(k)
            if f and f["source"] == "AI_EXTRACTED" and not f["check_note"]:
                continue  # read cleanly; the person agrees with it
            if expected.get(k) not in (None, ""):
                send[k] = str(expected[k])  # keyed or confirmed from the document image
            elif f and f["value"]:
                send[k] = f["value"]
        if send:
            try:
                d.call(who, "PATCH", f"/applications/{aid}/documents/{x['id']}/fields", json={"values": send})
            except RuntimeError as err:
                print("      field entry refused:", err)
        d.call(who, "POST", f"/applications/{aid}/documents/{x['id']}/verify")
    for disc in d.call(who, "GET", f"/applications/{aid}/discrepancies")["discrepancies"]:
        if disc["status"] == "OPEN":
            d.call(who, "POST", f"/applications/{aid}/discrepancies/{disc['id']}/resolve",
                   json={"resolution": "ACCEPT", "note": "Checked against the original at the branch; the difference is in formatting only."})
    a = d.app(aid)
    if a["status"] == "DOCS_PENDING" and a["checklist"]["all_uploaded"]:
        d.call(who, "POST", f"/applications/{aid}/documents/attest", json={"expected_version": a["version"], "attestation": ATTESTATION})


def review_and_decide(d: Desk, c: dict, already_referred: bool = False) -> None:
    aid, who, rev = c["app_id"], c["processor"], c["reviewer"]
    view = lambda r="LO": d.call(r, "GET", f"/applications/{aid}/review")  # noqa: E731
    v = view(who)
    if v["application"]["status"] == "AI_ASSESSED":
        d.call(who, "POST", f"/applications/{aid}/forward", json={"expected_version": v["application"]["version"], "note": c["note"]})
    c["reached"] = "FORWARDED"
    if c["target"] == "FORWARDED":
        c["done"] = True
        return
    if not already_referred:
        v = view(rev)
        if not v["actions"]["claim"] and rev == "AM":  # four-eyes: the Assistant Manager processed it, so the Branch Manager reviews it
            rev = c["reviewer"] = "BM"
            v = view(rev)
        if v["actions"]["claim"]:
            d.call(rev, "POST", f"/applications/{aid}/claim", json={"expected_version": v["application"]["version"]})
        c["reached"] = "UNDER_REVIEW"
        if c["target"] == "UNDER_REVIEW":
            c["done"] = True
            return
    decider = rev
    if c["target"].startswith("REFERRED") and rev == "AM" or already_referred:
        v = view("AM")
        if already_referred or v["actions"]["refer_to_branch_manager"]:  # not referable when the Branch Manager processed it (four-eyes)
            if not already_referred:
                d.call("AM", "POST", f"/applications/{aid}/refer-to-branch-manager", json={"expected_version": v["application"]["version"], "reason": c["refer_note"]})
            c["reached"] = "REFERRED"
            if c["target"] == "REFERRED":
                c["done"] = True
                return
            decider = next((r for r in ("BM", "AM") if view(r)["actions"]["claim"]), "BM")  # the Assistant Manager only if no Branch Manager is free
            v = view(decider)
            if v["actions"]["claim"]:
                d.call(decider, "POST", f"/applications/{aid}/claim", json={"expected_version": v["application"]["version"]})
    v = view(decider)
    rec, cur = v["ai"]["recommendation"], v["terms"]["current"]
    if c["target"] == "DECLINED" or rec == "POTENTIALLY_INELIGIBLE" or cur is None:
        reason = ("The AI found the applicant potentially ineligible and the documents confirm it." if rec == "POTENTIALLY_INELIGIBLE"
                  else "Repayment capacity is too thin for the amount asked; customer advised to reapply later.")
        d.call(decider, "POST", f"/applications/{aid}/decision", json={"expected_version": v["application"]["version"], "outcome": "DECLINED", "reason": reason})
        c["reached"], c["done"] = "DECLINED", True
        return
    if c.get("adjust") and decider == "BM" and v["actions"]["adjust_terms"]:
        amount = (Decimal(cur["amount"]) * Decimal("0.9") / 1000).to_integral_value() * 1000
        try:
            d.call("BM", "POST", f"/applications/{aid}/terms", json={"expected_version": v["application"]["version"], "amount": str(amount),
                                                                       "rate_bp": cur["rate_bp"], "tenure_months": cur["tenure_months"], "reason": c["refer_note"]})
        except RuntimeError as err:
            print("      terms adjustment refused:", err)
        v = view(decider)
        cur = v["terms"]["current"]
    d.call(decider, "POST", f"/applications/{aid}/decision", json={"expected_version": v["application"]["version"], "outcome": "APPROVED",
                                                                   "terms": {k: cur[k] for k in ("amount", "rate_bp", "tenure_months")}})
    d.drain_until(lambda: d.app(aid)["status"] == "SANCTIONED", tries=80)
    c["reached"] = d.app(aid)["status"]
    if not c["target"].endswith("DISBURSED") or c["reached"] != "SANCTIONED":
        c["done"] = c["reached"] == "SANCTIONED"
        return
    disburse(d, c)


def disburse(d: "Desk", c: dict) -> None:
    """The Loan Officer records acceptance and the checklist; a manager who did not process the file releases the simulated disbursement."""
    aid = c["app_id"]
    ver = lambda: d.app(aid)["version"]  # noqa: E731
    try:
        d.call("LO", "POST", f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver()})
    except RuntimeError as err:
        if "KFS was valid until" not in str(err):
            raise
        # the Key Facts Statement's validity has passed: the bank re-confirms the terms with the customer and records how
        d.call("LO", "POST", f"/applications/{aid}/disbursement/acceptance",
               json={"expected_version": ver(), "late_reason": "Terms re-confirmed with the customer at the branch; KFS figures unchanged."})
    d.call("LO", "POST", f"/applications/{aid}/disbursement/checklist", json={"expected_version": ver(), "items": ALL_ITEMS})
    for r in ("AM", "BM"):
        if d.call(r, "GET", f"/applications/{aid}/disbursement")["actions"]["release"]:
            d.call(r, "POST", f"/applications/{aid}/disbursement/release", json={"expected_version": ver(), "confirm": True})
            break
    d.drain_until(lambda: True, tries=3)
    c["reached"] = d.app(aid)["status"]
    c["done"] = c["reached"] == "DISBURSED"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--applications", type=int, default=60)
    ap.add_argument("--enquiries", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20261127)
    ap.add_argument("--cases", type=int, help="process at most this many cases this run")
    ap.add_argument("--target", help="override the target of the cases processed this run (e.g. FORWARDED)")
    ap.add_argument("--gemini-cap", type=int)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--workers", type=int, default=1, help="cases processed side by side (each is still one person's work, step by step)")
    ap.add_argument("--upload-source", action="store_true")
    a = ap.parse_args()
    if a.upload_source or a.daily:
        sys.path.insert(0, str(ROOT / "backend"))
    if a.upload_source:
        return upload_source()
    if a.daily:
        d = Desk(a.gemini_cap)
        customers, docs = load_inputs()
        daily(d, customers, docs, a.seed)
        return 0
    customers, docs = load_inputs()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"cases": plan(a.applications, a.enquiries, a.seed, customers, docs)}
    cases = state["cases"]
    if a.plan:
        from collections import Counter
        print(Counter((c["kind"], c["target"]) for c in cases), Counter((c["kind"], c["processor"]) for c in cases))
        return 0
    import threading
    from concurrent.futures import ThreadPoolExecutor

    todo = [c for c in cases if not c["done"] and (not a.target or (c["kind"] == "APPLICATION" and c["processor"] == "LO"))][: a.cases or None]
    lock, local = threading.Lock(), threading.local()
    first = Desk(a.gemini_cap)  # imports the app once, on this thread

    def work(item):
        i, c = item
        if not hasattr(local, "d"):
            local.d = first if threading.current_thread() is threading.main_thread() else Desk(a.gemini_cap)
        if a.target:
            c["target"] = a.target
        try:
            (run_enquiry(local.d, c) if c["kind"] == "ENQUIRY" else run_application(local.d, c, customers, docs))
            note = ""
        except Exception as err:  # noqa: BLE001  one case's problem is reported and the run goes on
            note = f"\n      stopped: {str(err)[:400]}"
        with lock:
            print(f"[{i}/{len(todo)}] {c['kind']} {c['customer_id']} -> {c['target']} (by {c['processor']}"
                  f"{', review ' + c['reviewer'] if c['kind'] == 'APPLICATION' else ''}){note}\n      reached {c.get('reached')}{' (done)' if c['done'] else ''}",
                  flush=True)
            STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(work, enumerate(todo, 1)))
    left = sum(not c["done"] for c in cases)
    print(f"\n{len(cases) - left}/{len(cases)} cases done; state in {STATE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
