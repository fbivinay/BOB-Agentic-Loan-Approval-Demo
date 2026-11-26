"""Live check of the simulated disbursement against the real Supabase project and Resend. It keeps no application data.

1. The demo staff sign in with Supabase Auth: the disbursement view of APP-2026-000001 loads with the SIMULATED banner and allows nothing
   (the application is not sanctioned), and the Loan Officer cannot release.
2. Inside one database transaction that is rolled back at the end, a throwaway synthetic application (fixed reference, so no reference
   number is used up) is approved by the demo Assistant Manager, sanctioned and released, exercising every live guard: checklist
   completeness, permissions, maker-checker and the approved-amount cap.
3. The real confirmation email for that release is rendered from the live data and sent through Resend to the demo inbox.

    backend/.venv/Scripts/python scripts/live_check_disbursement.py
"""

import json
import sys
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
from fastapi.testclient import TestClient  # noqa: E402

from app import outbox, sanction  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.policy import finance  # noqa: E402
from live_check_enquiry import token  # noqa: E402

EMAILS = {"LO": "loan.officer@demo.example.com", "AM": "assistant.manager@demo.example.com", "BM": "branch.manager@demo.example.com"}
ALL = {"kfs_acknowledged": True, "loan_agreement_executed": True, "repayment_mandate_registered": True, "original_kyc_verified": True}


def main() -> int:
    api, ok = TestClient(app), []
    h = {k: {"Authorization": "Bearer " + token(e)} for k, e in EMAILS.items()}

    def check(name, cond, detail=""):
        ok.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))

    apps = api.get("/applications", headers=h["LO"]).json()["applications"]
    a1 = next(a for a in apps if a["reference"] == "APP-2026-000001")
    v = api.get(f"/applications/{a1['id']}/disbursement", headers=h["LO"])
    check("disbursement view loads with the SIMULATED banner", v.status_code == 200 and "SIMULATED" in v.json()["banner"], v.text[:200])
    check("nothing is allowed before sanction", not any(v.json()["actions"].values()))
    check("the Loan Officer cannot release", api.post(f"/applications/{a1['id']}/disbursement/release", headers=h["LO"],
                                                      json={"expected_version": a1["version"], "confirm": True}).status_code == 403)

    with psycopg.connect(settings.supabase_db_url, row_factory=dict_row) as c:
        s = {r["role"]: r["user_id"] for r in c.execute("SELECT role, user_id FROM staff_profiles WHERE email = ANY(%s)", (list(EMAILS.values()),)).fetchall()}
        lo, am, bm = s["LOAN_OFFICER"], s["ASSISTANT_MANAGER"], s["BRANCH_MANAGER"]
        rs = c.execute("SELECT id FROM policy_rule_sets WHERE status = 'ACTIVE' AND scheme = 'baroda_personal_loan'").fetchone()["id"]

        def actor(kind, who=None, role=None):
            c.execute("SELECT set_config('app.actor_type', %s, true), set_config('app.actor_id', %s, true), set_config('app.actor_role', %s, true)",
                      (kind, str(who or ""), role or ""))

        def refused(sql, params, words):
            try:
                with c.transaction():
                    c.execute(sql, params)
                return False
            except psycopg.Error as e:
                return words in str(e)

        try:
            ap = c.execute("INSERT INTO applicants (bank_relationship, full_name, date_of_birth, mobile, email, address_line, city, state, pincode, employment_type) "
                           "VALUES ('NTB', 'Probe Borrower', '1990-01-01', '9000000002', 'probe.borrower@example.invalid', '1, Demo Street', 'Pune', 'Maharashtra', "
                           "'411001', 'SALARIED_PRIVATE') RETURNING id").fetchone()["id"]
            aid = c.execute("INSERT INTO applications (reference, applicant_id, requested_amount, requested_tenure_months, created_by, documents_checklist, intake_completed_by, "
                            "intake_completed_at, policy_rule_set_id) VALUES ('APP-LIVE-PROBE', %s, 300000, 36, %s, '{\"required\": [], \"waived\": []}', %s, now(), %s) "
                            "RETURNING id", (ap, lo, lo, rs)).fetchone()["id"]
            run = c.execute("INSERT INTO agent_runs (application_id, graph, status, recommendation, policy_rule_set_id, output, finished_at) "
                            "VALUES (%s, 'ASSESSMENT', 'DONE', 'RECOMMENDED_FOR_APPROVAL', %s, %s, now()) RETURNING id",
                            (aid, rs, json.dumps({"segment": "NTB_EST"}))).fetchone()["id"]
            c.execute("SELECT set_config('app.in_workflow_fn', 'on', true)")
            c.execute("UPDATE applications SET status = 'UNDER_REVIEW', ai_recommendation = 'RECOMMENDED_FOR_APPROVAL', ai_recommendation_run_id = %s, assigned_to = %s "
                      "WHERE id = %s", (run, am, aid))
            c.execute("SELECT set_config('app.in_workflow_fn', 'off', true)")
            ver = c.execute("SELECT version FROM applications WHERE id = %s", (aid,)).fetchone()["version"]
            actor("STAFF", am, "ASSISTANT_MANAGER")
            dec = c.execute("INSERT INTO decisions (application_id, application_version, outcome, decided_by, decided_by_role, ai_recommendation_snapshot, is_override) "
                            "VALUES (%s, %s, 'APPROVED', %s, 'ASSISTANT_MANAGER', '{}', false) RETURNING id", (aid, ver, am)).fetchone()["id"]
            terms = c.execute("INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months, policy_rule_set_id, decision_id, created_by) "
                              "VALUES (%s, 'APPROVED', 300000, 1250, 36, %s, %s, %s) RETURNING id", (aid, rs, dec, am)).fetchone()["id"]
            for k in sanction.KINDS:
                c.execute("INSERT INTO generated_documents (application_id, kind, storage_path, sha256, terms_id, policy_rule_set_id) VALUES (%s, %s, %s, %s, %s, %s)",
                          (aid, k, f"probe/{aid}/{k}.pdf", "a" * 64, terms, rs))
            actor("SYSTEM")
            c.execute("SELECT app_transition(%s, 'SANCTIONED', %s, 'SYSTEM', NULL, 'live probe')", (aid, ver + 1))
            d = c.execute("INSERT INTO disbursements (application_id) VALUES (%s) RETURNING id", (aid,)).fetchone()["id"]
            c.execute("UPDATE disbursements SET acceptance_recorded_at = now(), acceptance_recorded_by = %s WHERE id = %s", (lo, d))
            check("live guard: an incomplete checklist is refused",
                  refused("UPDATE disbursements SET status = 'CHECKLIST_COMPLETE', checklist = %s, checklist_completed_by = %s WHERE id = %s",
                          (json.dumps({"kfs_acknowledged": True}), lo, d), "checklist item"))
            c.execute("UPDATE disbursements SET status = 'CHECKLIST_COMPLETE', checklist = %s, checklist_completed_by = %s WHERE id = %s", (json.dumps(ALL), lo, d))
            pd = sanction.pack_data(c, aid, date.today())
            net, first = pd["figures"]["net_disbursed"], finance.add_months(date.today(), 1)
            release = ("UPDATE disbursements SET status = 'RELEASED', released_by = %s, simulated_reference = 'SIM-LIVEPROBE001', loan_account_number = 'SIMLN0000000000', "
                       "first_emi_date = %s, net_amount = %s WHERE id = %s")
            check("live guard: the Loan Officer cannot release", refused(release, (lo, first, net, d), "disbursement.release"))
            check("live guard: no more than the approved amount", refused(release, (am, first, 300001, d), "approved amount"))
            c.execute(release, (am, first, net, d))
            actor("STAFF", am, "ASSISTANT_MANAGER")
            c.execute("SELECT app_transition(%s, 'DISBURSED', %s, 'STAFF', %s, 'live probe release')", (aid, ver + 2, am))
            row = c.execute("SELECT is_simulated, terms_id, released_by_role FROM disbursements WHERE id = %s", (d,)).fetchone()
            check("live: release is simulated, tied to the approved terms, by the Assistant Manager",
                  row["is_simulated"] and row["terms_id"] == terms and row["released_by_role"] == "ASSISTANT_MANAGER")
            actor("SYSTEM")
            email_id = outbox.queue(c, aid, "DISBURSEMENT_CONFIRMATION", "Your personal loan is disbursed (simulated) - APP-LIVE-PROBE (demo)", [], settings.email_redirect_to)
            html, text, files = outbox.disbursement_content(c, email_id)
            check("confirmation email renders with the simulated banner and the loan account", "SIMULATED DISBURSEMENT" in text and "SIMLN0000000000" in text)
            if settings.resend_api_key and c.execute("SELECT take_budget('resend', %s) AS ok", (settings.resend_daily_cap,)).fetchone()["ok"]:
                try:
                    mid = outbox.Resend(settings.resend_api_key).send({"from": settings.email_from, "to": [outbox.route(settings.email_redirect_to)],
                                                                       "subject": "Your personal loan is disbursed (simulated) - APP-LIVE-PROBE (demo)",
                                                                       "html": html, "text": text}, f"live-probe-{email_id}")
                    check("Resend accepted the confirmation email for the demo inbox", bool(mid))
                except outbox.ResendError as e:
                    check("Resend accepted the confirmation email", False, str(e))
        finally:
            c.rollback()  # nothing from the probe is kept, including its email budget count
    print(f"\n{sum(ok)}/{len(ok)} live checks passed (probe rolled back)")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
