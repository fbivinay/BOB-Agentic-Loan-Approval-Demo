"""Create the three demo staff accounts in Supabase Auth and their staff_profiles rows. Idempotent.

    DEMO_STAFF_PASSWORD='...' backend/.venv/Scripts/python scripts/seed_staff.py

Needs SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_DB_URL (from your local .env) and apply_migrations.py to have been run.
The password is read from the environment, never stored in the repository. The accounts are synthetic demo staff, not real people.
Roles are written to staff_profiles (the source of truth), never to user-editable auth metadata.
"""

import os
import sys
from pathlib import Path

import httpx
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import settings  # noqa: E402

STAFF = [
    ("loan.officer@demo.example.com", "Meera Iyer", "LOAN_OFFICER"),
    ("assistant.manager@demo.example.com", "Rohan Deshmukh", "ASSISTANT_MANAGER"),
    ("branch.manager@demo.example.com", "Sunita Rao", "BRANCH_MANAGER"),
]


def main() -> int:
    pw = os.environ.get("DEMO_STAFF_PASSWORD", "")
    missing = [k for k, v in (("SUPABASE_URL", settings.supabase_url), ("SUPABASE_SERVICE_ROLE_KEY", settings.supabase_service_role_key), ("SUPABASE_DB_URL", settings.supabase_db_url)) if not v]
    if missing or len(pw) < 12:
        print("Missing:", ", ".join(missing + (["DEMO_STAFF_PASSWORD (12+ characters)"] if len(pw) < 12 else [])))
        return 1
    base = settings.supabase_url.rstrip("/") + "/auth/v1/admin"
    hdr = {"apikey": settings.supabase_service_role_key, "Authorization": f"Bearer {settings.supabase_service_role_key}"}
    with httpx.Client(timeout=30, headers=hdr) as http, psycopg.connect(settings.supabase_db_url, autocommit=True) as db:
        existing = {u["email"]: u["id"] for u in http.get(f"{base}/users", params={"per_page": 200}).json().get("users", [])}
        for email, name, role in STAFF:
            uid = existing.get(email)
            if uid is None:
                r = http.post(f"{base}/users", json={"email": email, "password": pw, "email_confirm": True, "user_metadata": {"full_name": name}})
                r.raise_for_status()
                uid = r.json()["id"]
            db.execute("INSERT INTO staff_profiles (user_id, full_name, email, role) VALUES (%s, %s, %s, %s) "
                       "ON CONFLICT (user_id) DO UPDATE SET full_name = EXCLUDED.full_name, role = EXCLUDED.role, is_active = true", (uid, name, email, role))
            print(f"{role:<18} {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
