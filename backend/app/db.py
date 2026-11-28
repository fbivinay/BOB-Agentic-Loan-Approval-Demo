"""Postgres access to the operational database (Supabase). The backend is the only database client.

Every write runs inside staff_tx(), which tells the database who is acting (per-transaction settings read by the audit triggers).
The decisive human-only checks use explicit columns validated in the database, not these settings.
"""

import atexit
from contextlib import contextmanager
from functools import lru_cache
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings


def connect() -> psycopg.Connection:
    if not settings.supabase_db_url:
        raise RuntimeError("SUPABASE_DB_URL is not configured")
    # autocommit: reads run on their own, and every staff_tx() is a real BEGIN/COMMIT. Without it, a read before the write opens an
    # implicit transaction, staff_tx() becomes a mere savepoint, and the work is silently rolled back when the connection closes.
    return psycopg.connect(settings.supabase_db_url, row_factory=dict_row, autocommit=True)


@lru_cache(maxsize=1)
def pool() -> ConnectionPool:
    """Open connections kept for reuse: a fresh Supabase connection costs a TLS handshake (most of a request's time).
    `check` drops a connection the pooler closed while the free instance slept. Session-mode pooler, so prepared statements are fine."""
    if not settings.supabase_db_url:
        raise RuntimeError("SUPABASE_DB_URL is not configured")
    p = ConnectionPool(settings.supabase_db_url, min_size=1, max_size=8, max_idle=240, check=ConnectionPool.check_connection,
                       kwargs={"row_factory": dict_row, "autocommit": True}, open=True)
    atexit.register(p.close)
    return p


def get_conn():
    """FastAPI dependency: one pooled connection per request, returned afterwards."""
    with pool().connection() as conn:
        yield conn


@contextmanager
def staff_tx(conn: psycopg.Connection, staff_id: UUID | None = None, role: str | None = None, actor_type: str = "STAFF"):
    """One transaction with the actor recorded for the audit triggers. Commits on success, rolls back on error."""
    with conn.transaction():
        conn.execute("SELECT set_config('app.actor_type', %s, true), set_config('app.actor_id', %s, true), set_config('app.actor_role', %s, true)",
                     (actor_type, str(staff_id) if staff_id else "", role or ""))
        yield conn


def load_staff(user_id: UUID, conn: psycopg.Connection | None = None) -> dict | None:
    """The staff profile and permissions for a Supabase user, or None. Role comes from the database only."""
    if conn is None:
        with pool().connection() as own:
            return load_staff(user_id, own)
    # One round trip, not two: this runs on every request.
    row = conn.execute("SELECT s.user_id, s.full_name, s.email, s.role, s.is_active,"
                       " COALESCE((SELECT array_agg(r.permission) FROM role_permissions r WHERE r.role = s.role), '{}') AS permissions"
                       " FROM staff_profiles s WHERE s.user_id = %s", (user_id,)).fetchone()
    return None if row is None else {**row, "permissions": list(row["permissions"])}
