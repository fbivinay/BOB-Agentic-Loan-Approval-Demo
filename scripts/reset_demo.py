"""Reset the demo's workflow data to a clean state (owner tool). The public demo logins let anyone change the synthetic state; this restores it.

    backend/.venv/Scripts/python scripts/reset_demo.py --dry-run
    backend/.venv/Scripts/python scripts/reset_demo.py --confirm <supabase-project-ref>

What it clears (all synthetic):
  * Supabase: enquiries, applicants, applications and everything hanging off them (documents and their fields, corrections, discrepancies,
    agent runs, terms, term changes, decisions, sanction documents, emails, disbursements), the job queue, the analytics outbox, the daily
    budget counters, and the audit entries about those records. Reference numbers start again at 000001.
  * Storage: every object in the private loan-documents and generated-documents buckets.
  * BigQuery: lh_raw.events (the demo's analytics events) is re-created empty.
What it keeps: staff and permissions, demo limits, the workflow table, policy rule sets and their activation log, policy monitoring runs,
change reports and proposals (and their audit entries), the policy knowledge base, the synthetic bank dataset, and the OCR / extraction
tables in BigQuery (they include imported Unlimited-OCR results keyed by file hash; old rows for deleted uploads are harmless).

The audit trail, decisions and terms are append-only by database triggers. Only the database owner can switch those triggers off, which
this tool does inside one transaction for exactly the tables it clears, then switches them back on; a DEMO_RESET entry is written last.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# children first; the circular links (applications <-> agent_runs, applications <-> enquiries) are cut on the application side first
CLEAR = ["email_events", "email_outbox", "generated_documents", "disbursements", "application_terms", "term_changes", "decisions", "document_fields",
         "extraction_corrections", "discrepancies", "documents", "jobs", "analytics_outbox", "agent_runs", "enquiries", "applications", "applicants",
         "usage_counters"]
AUDIT_ENTITIES = ["applicants", "enquiries", "applications", "documents", "document_fields", "discrepancies", "extraction_corrections", "agent_runs",
                  "application_terms", "term_changes", "decisions", "generated_documents", "email_outbox", "disbursements", "analytics_outbox"]
BUCKETS = ["loan-documents", "generated-documents"]


def counts(conn) -> dict:
    out = {t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in CLEAR}  # noqa: S608  fixed table names
    out["audit_events (workflow)"] = conn.execute("SELECT count(*) AS n FROM audit_events WHERE application_id IS NOT NULL OR entity_type = ANY(%s)",
                                                  (AUDIT_ENTITIES,)).fetchone()["n"]
    return out


def reset_database(conn) -> dict:
    """One transaction: triggers off for the cleared tables only, delete, triggers back on, sequences restarted, a DEMO_RESET entry."""
    before = counts(conn)
    with conn.transaction():
        tables = CLEAR + ["audit_events"]
        for t in tables:
            conn.execute(f"ALTER TABLE {t} DISABLE TRIGGER USER")  # noqa: S608
        conn.execute("UPDATE applications SET ai_recommendation_run_id = NULL, enquiry_id = NULL")
        for t in CLEAR:
            conn.execute(f"DELETE FROM {t}")  # noqa: S608
        conn.execute("DELETE FROM audit_events WHERE application_id IS NOT NULL OR entity_type = ANY(%s)", (AUDIT_ENTITIES,))
        for t in tables:
            conn.execute(f"ALTER TABLE {t} ENABLE TRIGGER USER")  # noqa: S608
        for seq in ("application_seq", "enquiry_seq"):
            conn.execute(f"ALTER SEQUENCE {seq} RESTART WITH 1")  # noqa: S608
        conn.execute("SELECT audit_write('SYSTEM', NULL, NULL, 'DEMO_RESET', 'database', NULL, NULL, %s::jsonb, NULL, %s)",
                     (json.dumps(before), "Demo data reset by the project owner (scripts/reset_demo.py)"))
    return before


def clear_bucket(storage) -> int:
    """Every object, folder by folder (Supabase lists one level at a time)."""
    removed, prefixes = 0, [""]
    while prefixes:
        prefix = prefixes.pop()
        r = storage.http.post(f"{storage.base}/object/list/{storage.bucket}", json={"prefix": prefix, "limit": 1000, "offset": 0})
        r.raise_for_status()
        files = []
        for item in r.json():
            path = f"{prefix}/{item['name']}" if prefix else item["name"]
            (files if item.get("id") else prefixes).append(path)
        if files:
            storage.http.request("DELETE", f"{storage.base}/object/{storage.bucket}", json={"prefixes": files}).raise_for_status()
            removed += len(files)
    return removed


def reset_events_table() -> str:
    from app.config import settings

    bq = shutil.which("bq")
    subprocess.run([bq, f"--project_id={settings.gcp_project}", "rm", "-f", "-t", f"{settings.gcp_project}:lh_raw.events"], capture_output=True, timeout=120)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "setup_analytics.py")], capture_output=True, text=True, timeout=600)
    return "re-created" if r.returncode == 0 else f"FAILED: {(r.stdout + r.stderr)[-300:]}"


def main() -> int:
    from app.config import settings
    from app.storage import Storage

    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", help="the Supabase project reference, typed out, to confirm the reset")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ref = (re.match(r"https://([a-z0-9]+)\.supabase\.co", settings.supabase_url) or [None, None])[1]
    with psycopg.connect(settings.supabase_db_url, row_factory=dict_row, autocommit=True) as conn:
        print("would clear:" if a.dry_run or a.confirm != ref else "clearing:", {k: v for k, v in counts(conn).items() if v})
        if a.dry_run:
            return 0
        if not ref or a.confirm != ref:
            print(f"Refused: pass --confirm {ref or '<project-ref>'} to reset this project's demo data.")
            return 1
        reset_database(conn)
    for b in BUCKETS:
        print(f"bucket {b}: removed {clear_bucket(Storage(settings.supabase_url, settings.supabase_service_role_key, bucket=b))} object(s)")
    print("lh_raw.events:", reset_events_table())
    print("Done. Re-activate nothing: the ACTIVE rule set and policy history are kept. Run scripts/seed_demo.py for demo cases.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
