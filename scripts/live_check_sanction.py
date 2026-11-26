"""Live check of the sanction pack and email path against the real services. It changes no application.

1. Creates the private `generated-documents` bucket if missing (refuses a public one).
2. Renders a sanction pack for a synthetic borrower with the ACTIVE rule set's fee and charge parameters, and checks the KFS APR.
3. Stores one PDF in the private bucket, fetches it back through a 60-second signed URL, and removes it.
4. Sends one real approval-style email with the three PDFs through Resend to the demo inbox (EMAIL_REDIRECT_TO, else Resend's test address
   delivered@resend.dev), counted against the daily email cap, then repeats the call with the same Idempotency-Key and checks that Resend
   returns the same message instead of sending twice, and polls the delivery status.

    backend/.venv/Scripts/python scripts/live_check_sanction.py
"""

import hashlib
import sys
import time
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import outbox, sanction  # noqa: E402
from app.config import settings  # noqa: E402
from app.policy import finance, ruleset  # noqa: E402
from app.storage import get_generated_storage  # noqa: E402


def main() -> int:
    ok = []

    def check(name, cond, detail=""):
        ok.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))

    store = get_generated_storage()
    print("bucket generated-documents:", store.ensure_bucket())
    conn = psycopg.connect(settings.supabase_db_url, row_factory=dict_row, autocommit=True)
    rs = ruleset.validate(conn.execute("SELECT content FROM policy_rule_sets WHERE status = 'ACTIVE' AND scheme = 'baroda_personal_loan'").fetchone()["content"])
    p = lambda n: rs.param(n, "ETB_EST")  # noqa: E731
    today = date.today()
    first, amount, rate, tenure = finance.add_months(today, 1), Decimal(400000), 1150, 36
    d = {"reference": "APP-LIVE-CHECK", "sanction_no": "SL-LIVE-CHECK", "proposal_no": "KFS-LIVE-CHECK", "date": today,
         "kfs_valid_until": sanction.working_days_after(today, 3),
         "borrower": {"name": "Synthetic Borrower", "address": ["1, Demo Street", "Pune, Maharashtra 411001"], "email": "borrower@example.invalid"},
         "approver": {"name": "Demo Assistant Manager", "role": "Assistant Manager", "decided_at": today},
         "terms": {"amount": amount, "rate_bp": rate, "tenure_months": tenure},
         "figures": finance.kfs_figures(amount, rate, tenure, first, fee_percent_bp=p("fees.processing_bp").value, fee_min=p("fees.processing_min").value,
                                        fee_max=p("fees.processing_max").value, gst_percent=p("fees.gst_percent").value),
         "schedule": finance.schedule(amount, rate, tenure, first), "policy": rs.identity,
         "charges": {"processing_bp": p("fees.processing_bp").value, "processing_min": p("fees.processing_min").value, "processing_max": p("fees.processing_max").value,
                     "gst_percent": p("fees.gst_percent").value, "penal_percent_pa": p("fees.penal_interest_percent_pa").value,
                     "prepayment_percent": p("fees.prepayment_charge_percent").value, "cooling_off_days": p("kfs.cooling_off_days").value}}
    pack = sanction.render_pack(d)
    f = d["figures"]
    print(f"      pack {rs.identity}: EMI {f['emi']}, fee+GST {f['upfront_charges']}, net {f['net_disbursed']}, APR {f['apr_percent']}%, "
          f"sizes {[len(v[0]) for v in pack.values()]}")
    check("pack renders three PDFs with APR above the nominal rate", len(pack) == 3 and f["apr_percent"] > Decimal(rate) / 100)

    path = f"live-check/{uuid.uuid4()}.pdf"
    pdf, sha = pack["KFS"]
    store.put(path, pdf, "application/pdf")
    try:
        got = httpx.get(store.signed_url(path), timeout=30)
        check("private bucket: a signed URL returns the same PDF", got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == sha)
        check("private bucket: the object is not public", httpx.get(f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/generated-documents/{path}",
                                                                    timeout=30).status_code != 200)
    finally:
        store.remove(path)

    if not settings.resend_api_key:
        print("RESEND_API_KEY not set: email part skipped")
        return 1
    if not conn.execute("SELECT take_budget('resend', %s) AS ok", (settings.resend_daily_cap,)).fetchone()["ok"]:
        print("daily email cap reached: email part skipped")
        return 1
    import base64

    client, key, to = outbox.Resend(settings.resend_api_key), f"live-check-{uuid.uuid4()}", outbox.route(settings.email_redirect_to)
    payload = {"from": settings.email_from, "to": [to], "subject": "Your personal loan is sanctioned - APP-LIVE-CHECK (demo)",
               "text": "Live check of the demo approval email. Synthetic data; not from Bank of Baroda; not a loan offer.",
               "html": "<p>Live check of the demo approval email. Synthetic data; not from Bank of Baroda; not a loan offer.</p>",
               "attachments": [{"filename": f"{sanction.FILE_NAMES[k]}-APP-LIVE-CHECK.pdf", "content": base64.b64encode(v[0]).decode()} for k, v in pack.items()]}
    try:
        first_id = client.send(payload, key)
        check(f"Resend accepted the email with 3 PDFs to {to}", bool(first_id))
        again = client.send(payload, key)
        check("the same Idempotency-Key returns the same message (no second send)", again == first_id, f"{first_id} vs {again}")
    except outbox.ResendError as e:
        check("Resend accepted the email", False, str(e))
        first_id = None
    if first_id:
        try:
            last = None
            for _ in range(10):
                time.sleep(3)
                last = client.last_event(first_id)
                if last in ("delivered", "bounced", "complained"):
                    break
            print(f"      Resend message {first_id}: last event '{last}'")
            check("delivery status can be polled", last is not None)
        except outbox.ResendError as e:  # not a failure of the send: a send-only key cannot read status
            print(f"[SKIP] delivery polling: {e}. Use the signed webhook (deployed) or a full-access key for status.")
    print(f"\n{sum(ok)}/{len(ok)} live checks passed")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
