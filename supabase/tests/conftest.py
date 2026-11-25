"""Test harness: a throwaway Postgres (Docker, free) with the real migrations applied on top of a minimal Supabase stand-in.

Set TEST_DATABASE_URL to use an existing empty Postgres instead of starting a container. TEST_PG_IMAGE picks the image
(default postgres:17-alpine, the major version the Supabase project runs).
"""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))


def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120, check=True).stdout.strip()


@pytest.fixture(scope="session")
def db_url():
    url = os.environ.get("TEST_DATABASE_URL")
    name = None
    if not url:
        name = f"bob-pg-test-{uuid.uuid4().hex[:8]}"
        _docker("run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=test", "-p", "127.0.0.1::5432", os.environ.get("TEST_PG_IMAGE", "postgres:17-alpine"))
        port = _docker("port", name, "5432/tcp").splitlines()[0].rsplit(":", 1)[1]
        url = f"postgresql://postgres:test@127.0.0.1:{port}/postgres"
        for _ in range(60):
            try:
                with psycopg.connect(url, connect_timeout=2):
                    break
            except psycopg.OperationalError:
                time.sleep(1)
        time.sleep(1)
    import apply_migrations

    with psycopg.connect(url, autocommit=True) as c:
        c.execute((Path(__file__).parent / "stub_supabase.sql").read_text(encoding="utf-8"))
    apply_migrations.apply(url, verbose=False)
    yield url
    if name:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=120)


@pytest.fixture
def conn(db_url):
    c = psycopg.connect(db_url, row_factory=dict_row)
    yield c
    c.rollback()
    c.close()
