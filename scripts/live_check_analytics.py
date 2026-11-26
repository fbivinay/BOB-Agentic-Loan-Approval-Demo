"""Live check of analytics: flush the Supabase analytics outbox into BigQuery as the app identity (the same batch the hourly drain runs),
run it again to show nothing is loaded twice, then read the KPIs and the sync status through the real API with the demo logins.

    backend/.venv/Scripts/python scripts/live_check_analytics.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
from fastapi.testclient import TestClient  # noqa: E402

from app import analytics  # noqa: E402
from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402
from live_check_enquiry import token  # noqa: E402


def main() -> int:
    ok = []

    def check(name, cond, detail=""):
        ok.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))

    conn, sink = connect(), analytics.EventSink()
    pending = conn.execute("SELECT count(*) AS n FROM analytics_outbox WHERE status = 'PENDING'").fetchone()["n"]
    first = analytics.sync(conn, sink)
    print(f"      outbox pending before: {pending}; first sync: {first}")
    check("the outbox loads into lh_raw.events as the app identity", first["failed"] == 0 and first["loaded"] + first["skipped"] == pending, str(first))
    second = analytics.sync(conn, sink)
    check("a second sync loads nothing (each event once)", second == {"loaded": 0, "skipped": 0, "failed": 0}, str(second))
    left = conn.execute("SELECT count(*) AS n FROM analytics_outbox WHERE status <> 'SENT'").fetchone()["n"]
    check("every outbox row is SENT", left == 0, f"{left} not sent")
    conn.close()

    api = TestClient(app)
    am = {"Authorization": "Bearer " + token("assistant.manager@demo.example.com")}
    lo = {"Authorization": "Bearer " + token("loan.officer@demo.example.com")}
    r = api.get("/analytics/overview", headers=am)
    check("the Assistant Manager reads the KPIs", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        o = r.json()
        h = o["headline"]
        print(f"      historical: {h['historical']['decisions']} decisions, approval {h['historical']['approval_rate']:.1%}, avg TAT {h['historical']['avg_tat_days']:.1f} days")
        print(f"      portfolio: {h['portfolio']['loans']} loans, outstanding Rs {h['portfolio']['outstanding']:,.0f}; DPD: {[(d['bucket'], d['loans']) for d in o['dpd']]}")
        print(f"      live funnel: {[(f['status'], f['applications']) for f in o['funnel'] if f['applications']]}; live decisions: {h['live']['decisions']}")
        check("the live funnel counts the demo's applications", any(f["applications"] for f in o["funnel"]))
        check("history and live are labelled synthetic", "synthetic" in o["labels"]["historical"].lower() and o["labels"]["data"] == "All data is synthetic.")
    check("the Loan Officer cannot read analytics", api.get("/analytics/overview", headers=lo).status_code == 403)
    s = api.get("/system/analytics", headers=am).json()
    check("the sync status shows no failed events", not s["failed"], str(s["counts"]))
    print(f"\n{sum(ok)}/{len(ok)} live checks passed")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
