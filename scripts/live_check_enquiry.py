"""End-to-end check of the draft enquiry against the live services: Supabase Auth sign-in, the real API code, BigQuery as the app identity,
and the Supabase database. It creates one realistic draft enquiry for an existing synthetic customer and then closes it with a reason,
so it leaves a normal, audited record behind (the audit trail is append-only by design). It never converts the enquiry.

    backend/.venv/Scripts/python scripts/live_check_enquiry.py [--customer BOB-C0027]
"""

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
from fastapi.testclient import TestClient  # noqa: E402

import seed_staff  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


def token(email: str) -> str:
    r = httpx.post(settings.supabase_url.rstrip("/") + "/auth/v1/token?grant_type=password", headers={"apikey": settings.supabase_anon_key},
                   json={"email": email, "password": seed_staff.demo_password()}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer", default="BOB-C0027")
    cid = ap.parse_args().customer
    api = TestClient(app)
    lo = {"Authorization": "Bearer " + token("loan.officer@demo.example.com")}
    am = {"Authorization": "Bearer " + token("assistant.manager@demo.example.com")}
    ok = []

    def check(name, cond, detail=""):
        ok.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))

    s = api.get("/customers/search", params={"q": cid}, headers=lo)
    check("Loan Officer finds the existing customer in BigQuery", s.status_code == 200 and [r["customer_id"] for r in s.json()["results"]] == [cid], s.text[:200])
    check("Assistant Manager may not run the customer search", api.get("/customers/search", params={"q": cid}, headers=am).status_code == 403)
    r = api.post("/enquiries", headers=lo, json={"customer_id": cid, "declared_credit_history": "ESTABLISHED", "requested_amount": "300000",
                                                 "requested_tenure_months": 36, "documents_available": ["APPLICATION_FORM"]})
    check("draft enquiry is created with indicative figures", r.status_code == 201, r.text[:300])
    if r.status_code != 201:
        return 1
    e = r.json()
    ind = e["indicative"]
    print(f"      {e['reference']}  rate {ind['rate_range_percent'][0]}-{ind['rate_range_percent'][1]}%  indicative for request {ind['indicative_for_request']['amount']} "
          f"over {ind['indicative_for_request']['tenure_months']} months  missing: {[d['code'] for d in e['documents']['missing']]}")
    check("it is labelled as not a loan offer", "not a loan offer" in ind["disclaimer"].lower())
    check("the Assistant Manager can view it", api.get(f"/enquiries/{e['id']}", headers=am).status_code == 200)
    c = api.post(f"/enquiries/{e['id']}/close", headers=lo, json={"expected_version": e["version"], "reason": "Live system check after deployment of the enquiry API"})
    check("the draft is closed with a recorded reason", c.status_code == 200 and c.json()["status"] == "CLOSED", c.text[:200])
    print(f"\n{sum(ok)}/{len(ok)} live checks passed")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
