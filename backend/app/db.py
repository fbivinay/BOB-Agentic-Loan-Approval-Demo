"""Postgres access to the operational database (Supabase). The backend is the only database client.

Every write runs inside staff_tx(), which tells the database who is acting (per-transaction settings read by the audit triggers).
The decisive human-only checks use explicit columns validated in the database, not these settings.
"""

from contextlib import contextmanager
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.config import settings


def connect() -> psycopg.Connection:
    if not settings.supabase_db_url:
        raise RuntimeError("SUPABASE_DB_URL is not configured")
    return psycopg.connect(settings.supabase_db_url, row_factory=dict_row)


@contextmanager
def staff_tx(conn: psycopg.Connection, staff_id: UUID | None = None, role: str | None = None, actor_type: str = "STAFF"):
    """One transaction with the actor recorded for the audit triggers. Commits on success, rolls back on error."""
    with conn.transaction():
        conn.execute("SELECT set_config('app.actor_type', %s, true), set_config('app.actor_id', %s, true), set_config('app.actor_role', %s, true)",
                     (actor_type, str(staff_id) if staff_id else "", role or ""))
        yield conn


def load_staff(user_id: UUID, conn: psycopg.Connection | None = None) -> dict | None:
    """The staff profile and permissions for a Supabase user, or None. Role comes from the database only."""
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("SELECT user_id, full_name, email, role, is_active FROM staff_profiles WHERE user_id = %s", (user_id,)).fetchone()
        if row is None:
            return None
        perms = conn.execute("SELECT permission FROM role_permissions WHERE role = %s", (row["role"],)).fetchall()
        return {**row, "permissions": [p["permission"] for p in perms]}
    finally:
        if own:
            conn.close()
