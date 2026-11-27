"""Spread the seeded demo history over the past weeks (owner tool), so the dashboards show a branch's normal flow instead of one busy hour.

scripts/seed_demo.py runs every case through the real application in about an hour, so every timestamp is today. This tool moves each case
(an enquiry and, if converted, its application) to a start day in the past, about 4-5 enquiries a day, and stretches the case's own events
over a realistic 1-7 days. Order within a case is kept exactly: new = start + (old - first) x stretch. Every table that belongs to the case
moves with it (application, documents and their fields, corrections, discrepancies, AI runs, terms, decisions, sanction documents, emails,
disbursement, jobs, analytics events, the audit trail, KYC versions created by the loan). Dates inside the generated PDFs and emails are not
rewritten.

The audit trail and the decision records are append-only, so this runs as the database owner with those triggers off, inside one transaction,
and records a DEMO_HISTORY_BACKDATED audit entry. Then the BigQuery events table is rebuilt from the shifted analytics outbox.

    backend/.venv/Scripts/python scripts/backdate_demo.py --days 24 --dry-run
    backend/.venv/Scripts/python scripts/backdate_demo.py --days 24 --confirm <supabase-project-ref>
"""

import argparse
import json
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

IST = timezone(timedelta(hours=5, minutes=30))
BY_APP = {"agent_runs": ["started_at", "finished_at"], "analytics_outbox": ["created_at", "occurred_at", "next_attempt_at", "sent_at"],
          "application_terms": ["created_at"], "audit_events": ["occurred_at"], "decisions": ["decided_at"],
          "disbursements": ["created_at", "acceptance_recorded_at", "checklist_completed_at", "released_at"], "discrepancies": ["created_at", "resolved_at"],
          "documents": ["uploaded_at", "reviewed_at", "withdrawn_at"], "email_outbox": ["created_at", "updated_at", "next_attempt_at"],
          "generated_documents": ["created_at"], "jobs": ["created_at", "updated_at", "run_after", "locked_at"], "term_changes": ["changed_at"],
          "kyc_profile_history": ["changed_at"]}
APP_COLS = ["created_at", "updated_at", "intake_completed_at", "forwarded_at", "assessed_at", "referred_at"]
TOUCHED = list(BY_APP) + ["applications", "enquiries", "applicants", "document_fields", "extraction_corrections", "email_events", "kyc_documents", "kyc_profiles"]


def schedule(cases: list[dict], days: int, seed: int, now: datetime) -> list[dict]:
    """A start time for each case: working hours, about the same number of enquiries each day, oldest case first."""
    rnd = random.Random(seed)
    first_day = (now - timedelta(days=days)).astimezone(IST).date()
    per_day = max(1, round(len(cases) / days))
    for i, c in enumerate(sorted(cases, key=lambda c: c["first"])):
        day = first_day + timedelta(days=min(days - 1, i // per_day))
        start = datetime(day.year, day.month, day.day, rnd.randint(10, 15), rnd.randint(0, 59), tzinfo=IST)
        span = max(c["last"] - c["first"], timedelta(seconds=1))
        target = timedelta(hours=rnd.uniform(2, 6)) if c["kind"] == "ENQUIRY" else timedelta(days=rnd.uniform(1, 7))
        target = min(target, now - timedelta(hours=1) - start)  # never into the future
        c.update(start=start, factor=max(target, timedelta(minutes=5)) / span)
    return cases


def backdate_database(conn, days: int, seed: int, now: datetime, apply: bool = True) -> tuple[int, int]:
    """Shift every seeded case; returns (applications, enquiries). Nothing changes unless apply is true."""
    apps = conn.execute("SELECT ap.id, ap.enquiry_id, ap.applicant_id, LEAST(ap.created_at, e.created_at) AS first, "
                        "GREATEST(ap.updated_at, (SELECT max(occurred_at) FROM audit_events WHERE application_id = ap.id)) AS last "
                        "FROM applications ap LEFT JOIN enquiries e ON e.id = ap.enquiry_id").fetchall()
    enqs = conn.execute("SELECT id, applicant_id, created_at AS first, updated_at AS last FROM enquiries WHERE converted_application_id IS NULL").fetchall()
    cases = [{"kind": "APPLICATION", **r} for r in apps] + [{"kind": "ENQUIRY", **r} for r in enqs]
    if not cases:
        print("nothing to backdate")
        return 0, 0
    schedule(cases, days, seed, now)
    print(f"{len(apps)} applications and {len(enqs)} enquiries over {days} days, "
          f"{min(c['start'] for c in cases):%d %b} to {max(c['start'] for c in cases):%d %b}")
    if not apply:
        return len(apps), len(enqs)
    with conn.transaction():
        conn.execute("CREATE TEMP TABLE m (app_id uuid, enquiry_id uuid, applicant_id uuid, t0 timestamptz, s timestamptz, f double precision) ON COMMIT DROP")
        with conn.cursor().copy("COPY m FROM STDIN") as cp:
            for c in cases:
                cp.write_row((c["id"] if c["kind"] == "APPLICATION" else None, c.get("enquiry_id") if c["kind"] == "APPLICATION" else c["id"],
                              c["applicant_id"], c["first"], c["start"], c["factor"]))
        shift = lambda col: f"{col} = CASE WHEN {col} IS NULL THEN NULL ELSE m.s + ({col} - m.t0) * m.f END"  # noqa: E731
        for t in TOUCHED:
            conn.execute(f"ALTER TABLE {t} DISABLE TRIGGER USER")  # noqa: S608
        conn.execute(f"UPDATE applications x SET {', '.join(shift(c) for c in APP_COLS)} FROM m WHERE x.id = m.app_id")  # noqa: S608
        conn.execute(f"UPDATE enquiries x SET {shift('created_at')}, {shift('updated_at')} FROM m WHERE x.id = m.enquiry_id")  # noqa: S608
        conn.execute("UPDATE applicants x SET created_at = m.s FROM m WHERE x.id = m.applicant_id")
        for t, cols in BY_APP.items():
            conn.execute(f"UPDATE {t} x SET {', '.join(shift(c) for c in cols)} FROM m WHERE x.application_id = m.app_id")  # noqa: S608
        conn.execute(f"UPDATE audit_events x SET {shift('occurred_at')} FROM m WHERE x.application_id IS NULL AND x.entity_type = 'enquiries' "  # noqa: S608
                     "AND x.entity_id = m.enquiry_id::text")
        conn.execute(f"UPDATE document_fields x SET {shift('updated_at')} FROM documents d JOIN m ON d.application_id = m.app_id WHERE x.document_id = d.id")  # noqa: S608
        conn.execute(f"UPDATE extraction_corrections x SET {shift('corrected_at')} FROM documents d JOIN m ON d.application_id = m.app_id WHERE x.document_id = d.id")  # noqa: S608
        conn.execute(f"UPDATE email_events x SET {shift('occurred_at')} FROM email_outbox e JOIN m ON e.application_id = m.app_id WHERE x.email_id = e.id")  # noqa: S608
        conn.execute(f"UPDATE kyc_documents x SET {shift('created_at')}, {shift('superseded_at')} FROM documents d JOIN m ON d.application_id = m.app_id "  # noqa: S608
                     "WHERE x.application_document_id = d.id")
        conn.execute("UPDATE disbursements x SET first_emi_date = (released_at + interval '1 month')::date WHERE released_at IS NOT NULL AND first_emi_date IS NOT NULL")
        conn.execute("UPDATE kyc_profiles p SET updated_at = h.changed_at, created_at = LEAST(p.created_at, h.changed_at), verified_on = h.changed_at::date "
                     "FROM (SELECT DISTINCT ON (profile_id) profile_id, changed_at, application_id FROM kyc_profile_history ORDER BY profile_id, version DESC) h "
                     "WHERE h.profile_id = p.id AND h.application_id IS NOT NULL")
        conn.execute("UPDATE analytics_outbox SET status = 'PENDING', sent_at = NULL, attempts = 0, last_error = NULL, next_attempt_at = now()")
        for t in TOUCHED:
            conn.execute(f"ALTER TABLE {t} ENABLE TRIGGER USER")  # noqa: S608
        conn.execute("SELECT audit_write('SYSTEM', NULL, NULL, 'DEMO_HISTORY_BACKDATED', 'database', NULL, NULL, NULL, %s::jsonb, %s)",
                     (json.dumps({"applications": len(apps), "enquiries": len(enqs), "days": days}),
                      "Seeded demo history spread over past days by the project owner (scripts/backdate_demo.py)"))
    return len(apps), len(enqs)


def main() -> int:
    from app.config import settings

    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20261127)
    ap.add_argument("--confirm")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ref = (re.match(r"https://([a-z0-9]+)\.supabase\.co", settings.supabase_url) or [None, None])[1]
    now = datetime.now(timezone.utc)
    with psycopg.connect(settings.supabase_db_url, row_factory=dict_row) as conn:
        apply = not a.dry_run and a.confirm == ref
        n_apps, n_enqs = backdate_database(conn, a.days, a.seed, now, apply)
        if not apply:
            print("dry run" if a.dry_run else f"Refused: pass --confirm {ref} to backdate this project's demo data.")
            return 0
        if not n_apps and not n_enqs:
            return 0
    print("database backdated; rebuilding the BigQuery events table from the shifted analytics outbox")
    from reset_demo import reset_events_table

    print("lh_raw.events:", reset_events_table())
    from app import analytics, jobs

    deps = jobs.get_job_deps()
    c = deps.connect()
    sent = 0
    while True:
        r = analytics.sync(c, deps.events)
        if r.get("failed"):
            print("analytics sync failed:", r)
            break
        if not r["loaded"] and not r["skipped"]:
            break
        sent += r["loaded"]
    print("analytics events re-sent:", sent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
