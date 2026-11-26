"""BigQuery set-up for analytics (owner credentials through bq; free, no billing). Idempotent.

  * lh_raw.events: the demo's workflow events, loaded from the Supabase analytics outbox
  * the KPI views in bigquery/gold/03_analytics.sql (lh_curated.events, lh_gold.kpi_*)
  * sa-app-runtime may append to lh_raw.events only (table-level grant); it reads the views through its READER access

    backend/.venv/Scripts/python scripts/setup_analytics.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings  # noqa: E402

P, LOC = settings.gcp_project, settings.bq_location
EVENTS = [("event_id", "STRING", "REQUIRED"), ("event_type", "STRING", "REQUIRED"), ("application_id", "STRING", "NULLABLE"),
          ("occurred_at", "TIMESTAMP", "REQUIRED"), ("payload", "STRING", "REQUIRED"), ("loaded_at", "TIMESTAMP", "REQUIRED")]


def bq(*args: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run([shutil.which("bq"), f"--project_id={P}", f"--location={LOC}", *args], input=stdin, capture_output=True, text=True, timeout=300)
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED: bq {' '.join(args)[:160]}\n{(r.stdout + r.stderr).strip()[:600]}")
    return r


def main() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([{"name": n, "type": t, "mode": m} for n, t, m in EVENTS], f)
    r = bq("mk", "--table", "--description=SYNTHETIC DATA ONLY. Demo workflow events (decisions, status changes, simulated disbursements) from the analytics outbox.",
           f"{P}:lh_raw.events", f.name, check=False)
    Path(f.name).unlink()
    print("lh_raw.events:", "created" if r.returncode == 0 else "exists")
    bq("query", "--nouse_legacy_sql", "--maximum_bytes_billed=50000000",
       stdin=(ROOT / "bigquery" / "gold" / "03_analytics.sql").read_text(encoding="utf-8").replace("{P}", P))
    print("views: lh_curated.events, lh_gold.kpi_decisions_monthly, kpi_ai_vs_human, kpi_funnel_live, kpi_disbursements_live, kpi_portfolio, kpi_portfolio_dpd")
    bq("add-iam-policy-binding", f"--member=serviceAccount:sa-app-runtime@{P}.iam.gserviceaccount.com", "--role=roles/bigquery.dataEditor", f"{P}:lh_raw.events")
    print("sa-app-runtime may append to lh_raw.events")


if __name__ == "__main__":
    main()
