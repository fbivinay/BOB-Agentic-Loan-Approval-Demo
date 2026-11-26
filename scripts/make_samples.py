"""Produce the sample outputs for the assignment deliverables (docs/samples/): the sanction letter, the Key Facts Statement and the EMI
schedule, both customer emails, and a policy monitor run report. Synthetic borrower, the rule set in the repository, no database needed
for the documents and emails; the monitor report is read from the live database if --monitor is given.

    backend/.venv/Scripts/python scripts/make_samples.py [--monitor]
"""

import argparse
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import outbox, sanction  # noqa: E402
from app.policy import finance, ruleset  # noqa: E402

OUT = ROOT / "docs" / "samples"
TODAY = date(2026, 11, 26)


def sample_data() -> dict:
    rs = ruleset.load()
    p = lambda n: rs.param(n, "ETB_EST").value  # noqa: E731
    amount, rate, tenure = Decimal(400000), 1150, 36
    first = finance.add_months(TODAY, 1)
    return {"reference": "APP-2026-000042", "sanction_no": "SL-2026-000042", "proposal_no": "KFS-2026-000042", "date": TODAY,
            "kfs_valid_until": sanction.working_days_after(TODAY, sanction.KFS_VALIDITY_WORKING_DAYS),
            "borrower": {"name": "Kavya Sharma", "address": ["14, Paud Road", "Pune, Maharashtra 411038"], "email": "kavya.sharma0027@example.invalid"},
            "approver": {"name": "Demo Assistant Manager", "role": "Assistant Manager", "decided_at": TODAY},
            "terms": {"amount": amount, "rate_bp": rate, "tenure_months": tenure},
            "figures": finance.kfs_figures(amount, rate, tenure, first, fee_percent_bp=p("fees.processing_bp"), fee_min=p("fees.processing_min"),
                                           fee_max=p("fees.processing_max"), gst_percent=p("fees.gst_percent")),
            "schedule": finance.schedule(amount, rate, tenure, first), "policy": rs.identity,
            "charges": {"processing_bp": p("fees.processing_bp"), "processing_min": p("fees.processing_min"), "processing_max": p("fees.processing_max"),
                        "gst_percent": p("fees.gst_percent"), "penal_percent_pa": p("fees.penal_interest_percent_pa"),
                        "prepayment_percent": p("fees.prepayment_charge_percent"), "cooling_off_days": p("kfs.cooling_off_days")}}


def page(title: str, html: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{title}</title></head>"
            f"<body style='background:#f4f4f5;padding:24px'>{html}</body></html>\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", action="store_true", help="also export the latest policy monitor run that produced reports (live database)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    d = sample_data()
    for kind, (pdf, sha) in sanction.render_pack(d).items():
        (OUT / f"{sanction.FILE_NAMES[kind]}-sample.pdf").write_bytes(pdf)
        print(f"{sanction.FILE_NAMES[kind]}-sample.pdf  {len(pdf)} bytes  sha256 {sha[:12]}")
    html, text = outbox.approval_email(d["borrower"]["name"], d["reference"], d["terms"]["amount"], d["terms"]["rate_bp"], d["terms"]["tenure_months"],
                                       d["kfs_valid_until"], d["borrower"]["email"])
    (OUT / "approval-email.html").write_text(page("Approval email (sample)", html), encoding="utf-8")
    (OUT / "approval-email.txt").write_text(text + "\n", encoding="utf-8")
    released = date(2026, 11, 30)
    rd = sample_data() | {"date": released}
    rd["figures"] = finance.kfs_figures(d["terms"]["amount"], d["terms"]["rate_bp"], d["terms"]["tenure_months"], finance.add_months(released, 1),
                                        fee_percent_bp=d["charges"]["processing_bp"], fee_min=d["charges"]["processing_min"], fee_max=d["charges"]["processing_max"],
                                        gst_percent=d["charges"]["gst_percent"])
    x = {"loan_account_number": "SIMLN4827150936", "simulated_reference": "SIM-7QK2M9XW4D1P", "net_amount": rd["figures"]["net_disbursed"],
         "first_emi_date": finance.add_months(released, 1)}
    html, text = outbox.disbursement_email(d["borrower"]["name"], d["reference"], x, rd, released, d["borrower"]["email"])
    (OUT / "disbursement-email.html").write_text(page("Disbursement confirmation (sample)", html), encoding="utf-8")
    (OUT / "disbursement-email.txt").write_text(text + "\n", encoding="utf-8")
    print("approval-email.html/.txt, disbursement-email.html/.txt")
    if a.monitor:
        import psycopg
        from psycopg.rows import dict_row

        from app import monitor
        from app.config import settings

        with psycopg.connect(settings.supabase_db_url, row_factory=dict_row) as c:
            run = c.execute("SELECT * FROM monitor_runs WHERE reports > 0 AND status IN ('SUCCEEDED', 'PARTIAL') ORDER BY started_at DESC LIMIT 1").fetchone()
            reports = c.execute("SELECT * FROM policy_change_reports WHERE run_id = %s ORDER BY created_at", (run["id"],)).fetchall()
        result = {"run_id": str(run["id"]), "status": run["status"], "reports": run["reports"], "proposals": run["proposals"],
                  "sources": [s | {"note": s["note"] if s["status"] != "error" else "error"} for s in run["sources"]]}
        md = (f"# Policy monitor run (sample)\n\nRun of {run['started_at']:%d %b %Y %H:%M} UTC against the live official sources. Short quotes, URLs and "
              "hashes only; full documents stay in the private bucket and BigQuery.\n\n" + monitor.summary_markdown(result, reports) + "\n")
        (OUT / "policy-monitor-run.md").write_text(md, encoding="utf-8")
        print("policy-monitor-run.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
