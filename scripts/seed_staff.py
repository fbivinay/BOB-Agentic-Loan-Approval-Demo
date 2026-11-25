"""Create the three demo staff accounts in Supabase Auth and their staff_profiles rows. Idempotent.

    DEMO_STAFF_PASSWORD='...' backend/.venv/Scripts/python scripts/seed_staff.py

Needs SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_DB_URL (from your local .env) and apply_migrations.py to have been run.
The password is read from the environment, never stored in the repository. The accounts are synthetic demo staff, not real people.
Roles are written to staff_profiles (the source of truth), never to user-editable auth metadata.

Security: an account that already exists is adopted ONLY if this script created it (it carries app_metadata.seeded_by, which only the
service role can set). Otherwise the script refuses, because someone could have pre-registered that email through public sign-up and
would otherwise be handed a staff role. Also turn public sign-up OFF in the Supabase project (Authentication > Sign In / Providers >
"Allow new users to sign up"); this script uses the admin API and is not affected by that setting.
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


MARKER = "seed_staff"


def decide(existing: dict[str, dict], email: str) -> str:
    """create | adopt | refuse, for one demo email, given the users that already exist in Supabase Auth."""
    user = existing.get(email)
    if user is None:
        return "create"
    return "adopt" if (user.get("app_metadata") or {}).get("seeded_by") == MARKER else "refuse"


def all_users(http: httpx.Client, base: str) -> dict[str, dict]:
    users: dict[str, dict] = {}
    page = 1
    while True:
        batch = http.get(f"{base}/users", params={"page": page, "per_page": 200}).json().get("users", [])
        users.update({u["email"]: u for u in batch})
        if len(batch) < 200:
            return users
        page += 1


def main() -> int:
    pw = os.environ.get("DEMO_STAFF_PASSWORD", "")
    missing = [k for k, v in (("SUPABASE_URL", settings.supabase_url), ("SUPABASE_SERVICE_ROLE_KEY", settings.supabase_service_role_key), ("SUPABASE_DB_URL", settings.supabase_db_url)) if not v]
    if missing or len(pw) < 12:
        print("Missing:", ", ".join(missing + (["DEMO_STAFF_PASSWORD (12+ characters)"] if len(pw) < 12 else [])))
        return 1
    base = settings.supabase_url.rstrip("/") + "/auth/v1/admin"
    hdr = {"apikey": settings.supabase_service_role_key, "Authorization": f"Bearer {settings.supabase_service_role_key}"}
    with httpx.Client(timeout=30, headers=hdr) as http, psycopg.connect(settings.supabase_db_url, autocommit=True) as db:
        existing = all_users(http, base)
        actions = {email: decide(existing, email) for email, _, _ in STAFF}
        refused = [e for e, a in actions.items() if a == "refuse"]
        if refused:  # decide everything first, so nothing is half-seeded
            print("Refusing: these accounts already exist but were not created by this script (possible pre-registration):", ", ".join(refused))
            print("Delete them in the Supabase dashboard (Authentication > Users), turn public sign-up off, and run this again.")
            return 1
        for email, name, role in STAFF:
            if actions[email] == "create":
                r = http.post(f"{base}/users", json={"email": email, "password": pw, "email_confirm": True,
                                                     "user_metadata": {"full_name": name}, "app_metadata": {"seeded_by": MARKER}})
                r.raise_for_status()
                uid = r.json()["id"]
            else:
                uid = existing[email]["id"]
            # is_active is deliberately not touched on conflict: a deactivated account stays deactivated
            db.execute("INSERT INTO staff_profiles (user_id, full_name, email, role) VALUES (%s, %s, %s, %s) "
                       "ON CONFLICT (user_id) DO UPDATE SET full_name = EXCLUDED.full_name, role = EXCLUDED.role", (uid, name, email, role))
            print(f"{role:<18} {email}  ({actions[email]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
