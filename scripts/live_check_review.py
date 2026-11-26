"""Live check of review and decision against the real Supabase project. It changes nothing.

1. The three demo staff sign in with Supabase Auth and call the Phase 9 endpoints through the real API code: the review queue is for
   reviewers only, and APP-2026-000001 (not yet assessed) can be neither forwarded, claimed nor decided.
2. A probe of the two guards migration 0009 tightened, inside one database transaction that is rolled back: a Branch Manager cannot
   decide another reviewer's file without a recorded takeover, and without eligible terms the term cap is the requested amount.

    backend/.venv/Scripts/python scripts/live_check_review.py
"""

import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from live_check_enquiry import token  # noqa: E402

EMAILS = {"LO": "loan.officer@demo.example.com", "AM": "assistant.manager@demo.example.com", "BM": "branch.manager@demo.example.com"}


def main() -> int:
    api, ok = TestClient(app), []
    h = {k: {"Authorization": "Bearer " + token(e)} for k, e in EMAILS.items()}

    def check(name, cond, detail=""):
        ok.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))

    for role in ("AM", "BM"):
        r = api.get("/review/queue", headers=h[role])
        check(f"{role} can open the review queue", r.status_code == 200, r.text[:200])
    check("LO cannot open the review queue", api.get("/review/queue", headers=h["LO"]).status_code == 403)
    apps = api.get("/applications", headers=h["LO"]).json()["applications"]
    app1 = next((a for a in apps if a["reference"] == "APP-2026-000001"), None)
    check("APP-2026-000001 is visible to the Loan Officer", app1 is not None)
    if app1:
        v = api.get(f"/applications/{app1['id']}/review", headers=h["LO"])
        body = v.json()
        check("review view loads with actions for the caller", v.status_code == 200 and "actions" in body, v.text[:200])
        check("nothing can be done before the assessment", not any(body["actions"].values()), str(body["actions"]))
        ver = body["application"]["version"]
        check("forwarding an unassessed application is refused", api.post(f"/applications/{app1['id']}/forward", headers=h["LO"], json={"expected_version": ver}).status_code == 409)
        check("claiming it is refused", api.post(f"/applications/{app1['id']}/claim", headers=h["AM"], json={"expected_version": ver}).status_code == 409)
        check("the Loan Officer can never decide", api.post(f"/applications/{app1['id']}/decision", headers=h["LO"],
                                                            json={"expected_version": ver, "outcome": "APPROVED"}).status_code == 403)

    # guard probe, rolled back
    with psycopg.connect(settings.supabase_db_url, row_factory=dict_row) as c:
        staff = {r["role"]: r["user_id"] for r in c.execute("SELECT role, user_id FROM staff_profiles WHERE email = ANY(%s)", (list(EMAILS.values()),)).fetchall()}
        try:
            applicant = c.execute("INSERT INTO applicants (bank_relationship, full_name, date_of_birth, mobile) VALUES ('NTB', 'Probe Applicant', '1990-01-01', '9000000001') "
                                  "RETURNING id").fetchone()["id"]
            a = c.execute("INSERT INTO applications (applicant_id, requested_amount, requested_tenure_months, created_by, documents_checklist, intake_completed_by, "
                          "intake_completed_at) VALUES (%s, 300000, 36, %s, '{\"required\": [], \"waived\": []}', %s, now()) RETURNING id",
                          (applicant, staff["LOAN_OFFICER"], staff["LOAN_OFFICER"])).fetchone()["id"]
            run = c.execute("INSERT INTO agent_runs (application_id, graph, status, recommendation, finished_at) VALUES (%s, 'ASSESSMENT', 'DONE', 'REFER_FOR_FURTHER_REVIEW', now()) "
                            "RETURNING id", (a,)).fetchone()["id"]
            c.execute("SELECT set_config('app.in_workflow_fn', 'on', true)")
            c.execute("UPDATE applications SET status = 'UNDER_REVIEW', ai_recommendation = 'REFER_FOR_FURTHER_REVIEW', ai_recommendation_run_id = %s, assigned_to = %s "
                      "WHERE id = %s", (run, staff["ASSISTANT_MANAGER"], a))
            c.execute("SELECT set_config('app.in_workflow_fn', 'off', true)")
            ver = c.execute("SELECT version FROM applications WHERE id = %s", (a,)).fetchone()["version"]

            def refused(sql, params, words):
                try:
                    with c.transaction():
                        c.execute(sql, params)
                    return False
                except psycopg.Error as e:
                    return words in str(e)

            check("live guard: a Branch Manager cannot decide another reviewer's file without a takeover",
                  refused("INSERT INTO decisions (application_id, application_version, outcome, decided_by, decided_by_role, reason, ai_recommendation_snapshot, is_override) "
                          "VALUES (%s, %s, 'APPROVED', %s, 'BRANCH_MANAGER', NULL, '{}', false)", (a, ver, staff["BRANCH_MANAGER"]), "takes it over"))
            c.execute("SELECT app_take_over(%s, %s, %s, 'Live probe: takeover before deciding')", (a, staff["BRANCH_MANAGER"], ver))
            ver += 1
            check("live guard: with no eligible terms, an amount above the request is refused",
                  refused("INSERT INTO term_changes (application_id, application_version, changed_by, changed_by_role, reason, before_terms, after_terms) "
                          "VALUES (%s, %s, %s, 'BRANCH_MANAGER', 'Live probe of the requested-amount cap', '{}', '{\"amount\": 300001, \"rate_bp\": 1200, \"tenure_months\": 36}')",
                          (a, ver, staff["BRANCH_MANAGER"]), "requested cap"))
            check("live guard: an amount within the request is accepted",
                  not refused("INSERT INTO term_changes (application_id, application_version, changed_by, changed_by_role, reason, before_terms, after_terms) "
                              "VALUES (%s, %s, %s, 'BRANCH_MANAGER', 'Live probe of the requested-amount cap', '{}', '{\"amount\": 250000, \"rate_bp\": 1200, \"tenure_months\": 36}')",
                              (a, ver, staff["BRANCH_MANAGER"]), ""))
        finally:
            c.rollback()  # nothing from the probe is kept
    print(f"\n{sum(ok)}/{len(ok)} live checks passed (probe rolled back)")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
