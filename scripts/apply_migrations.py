"""Apply supabase/migrations/*.sql in order to a Postgres database. Idempotent: applied files are recorded with their checksum.

    SUPABASE_DB_URL=postgresql://... backend/.venv/Scripts/python scripts/apply_migrations.py

A file that was already applied but has since changed is refused (write a new migration instead).
The connection string is read from the environment or from the local .env; it is never printed.
"""

import hashlib
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"


def db_url() -> str:
    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("SUPABASE_DB_URL="):
                url = line.split("=", 1)[1].strip()
    return url


def apply(url: str, verbose: bool = True) -> list[str]:
    done: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS public.schema_migrations (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
        applied = dict(conn.execute("SELECT name, sha256 FROM public.schema_migrations").fetchall())
        for f in sorted(MIGRATIONS.glob("*.sql")):
            sql = f.read_text(encoding="utf-8")
            digest = hashlib.sha256(sql.encode()).hexdigest()
            if f.name in applied:
                if applied[f.name] != digest:
                    raise SystemExit(f"{f.name} was already applied but has changed. Add a new migration instead of editing it.")
                continue
            with conn.transaction():
                conn.execute(sql)
                conn.execute("INSERT INTO public.schema_migrations (name, sha256) VALUES (%s, %s)", (f.name, digest))
            done.append(f.name)
            if verbose:
                print("applied", f.name)
    return done


if __name__ == "__main__":
    url = db_url()
    if not url:
        raise SystemExit("SUPABASE_DB_URL is not set (the Postgres connection string from your Supabase project settings).")
    if not apply(url):
        print("nothing to apply")
