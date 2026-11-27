"""Live dry run of the assessment graph against the real services, writing nothing to Supabase.

For one synthetic customer in each relationship x history segment, a prepared application (declared income from the bank record,
Rs 3,00,000 over 36 months, no documents) runs from load_credit_record onward: customer 360 from BigQuery as the app identity, the
deterministic engine under the ACTIVE rule set, retrieval from lh_curated.policy_chunks, and a Gemini explanation (one call each, inside
the daily budget). No name, PAN or ID is sent to Gemini. Nothing is recorded: no agent run, no recommendation, no terms. (The one row it can
add is each customer's DEMO / SIMULATED credit profile for the month, which any assessment of them would create and reuse anyway.)

    backend/.venv/Scripts/python scripts/live_check_assessment.py
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import jobs  # noqa: E402
from app.assessment import build_graph  # noqa: E402
from app.config import settings  # noqa: E402
from app.lakehouse import _run  # noqa: E402
from app.policy import ruleset  # noqa: E402

SEGMENT_SQL = (f"SELECT customer_id, bank_relationship, bureau_history_status, pan, date_of_birth, employment_type, declared_net_income, residency_status, is_staff "
               f"FROM `{settings.gcp_project}.lh_gold.customer_360` WHERE residency_status = 'RESIDENT' AND NOT is_staff AND employment_type LIKE 'SALARIED%' "
               "QUALIFY ROW_NUMBER() OVER (PARTITION BY bank_relationship, bureau_history_status ORDER BY customer_id) = 1 ORDER BY bank_relationship, bureau_history_status")


def main() -> int:
    deps = jobs.get_job_deps()
    conn = deps.connect()
    rs_row = conn.execute("SELECT id, content FROM policy_rule_sets WHERE status = 'ACTIVE' AND scheme = 'baroda_personal_loan'").fetchone()
    rs = ruleset.validate(rs_row["content"])
    print(f"rule set {rs.identity} | model {deps.model} | knowledge base {len(deps.kb.index().rows)} chunks")
    ok = True
    for c in _run(SEGMENT_SQL, {}):
        app = {"id": None, "reference": f"DRY-RUN-{c['customer_id']}", "status": "DOCS_VERIFIED", "version": 0, "bank_relationship": c["bank_relationship"],
               "customer_id": c["customer_id"] if c["bank_relationship"] == "ETB" else None, "pan": c["pan"], "date_of_birth": c["date_of_birth"],
               "employment_type": c["employment_type"], "declared_monthly_net": Decimal(str(c["declared_net_income"])), "existing_monthly_emi": Decimal(0),
               "residency_status": c["residency_status"], "is_staff": c["is_staff"], "requested_amount": Decimal(300000), "requested_tenure_months": 36}
        state = build_graph(conn, deps, start="load_credit_record").invoke(
            {"application_id": "dry-run", "today": date.today(), "app": app, "documents": [], "open_discrepancies": 0, "rule_set": rs,
             "rule_set_id": str(rs_row["id"]), "steps": []})
        r, cites, summ = state["result"], state["citations"], state["summary"]
        statuses = " ".join(f"{x['id']}:{x['status'][0]}" for x in r["rules"])
        cited = {rid: (h[0]["source_id"], (h[0]["section"] or "")[:40]) if h else None for rid, h in cites["by_rule"].items()}
        print(f"\n{c['customer_id']} {r['segment']:<9} {r['recommendation']:<25} income: {state['evidence']['income']['source'][:45]}")
        print(f"  rules  {statuses}")
        print(f"  cited  {cited}")
        print(f"  terms  {[t for t, h in cites['terms'].items() if h]}")
        print(f"  summary {summ['source']} {summ['note'] or ''} | {summ['text'][:160]}")
        ok &= state["credit_lookup"]["found"] and len(r["rules"]) == 15 and all(cites["terms"].values())
        if r["segment"].endswith("NTC"):
            ok &= next(x for x in r["rules"] if x["id"] == "R07")["status"] == "SKIPPED"  # no history: skipped, never failed
    conn.close()
    print("\nLIVE CHECK", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
