"""Phase 0 checks. Run from the repo root:

    backend/.venv/Scripts/python scripts/check_connectivity.py [--billing-canary] [--send-test-email]

Rules: nothing is purchased or enabled; checks whose environment variables are missing are skipped;
secrets are never printed. Only the two flags do anything beyond reading.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import settings  # noqa: E402

results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    print(f"[{status:<4}] {name}" + (f": {detail}" if detail else ""))


def check_supabase() -> None:
    if not settings.supabase_url:
        return record("supabase", "SKIP", "SUPABASE_URL not set")
    base = settings.supabase_url.rstrip("/")
    try:
        if settings.supabase_anon_key:
            r = httpx.get(f"{base}/auth/v1/health", headers={"apikey": settings.supabase_anon_key}, timeout=20)
            record("supabase auth (anon key)", "PASS" if r.status_code == 200 else "FAIL", f"HTTP {r.status_code}")
        else:
            r = httpx.get(f"{base}/auth/v1/health", timeout=20)
            record("supabase reachable", "PASS" if r.status_code in (200, 401) else "FAIL", f"HTTP {r.status_code} (no anon key set)")
        if settings.supabase_service_role_key:
            key = settings.supabase_service_role_key
            r = httpx.get(f"{base}/rest/v1/", headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=20)
            record("supabase service role", "PASS" if r.status_code == 200 else "FAIL", f"HTTP {r.status_code}")
    except httpx.HTTPError as e:
        record("supabase", "FAIL", type(e).__name__)


def check_gemini() -> None:
    if not settings.gemini_api_key:
        return record("gemini", "SKIP", "GEMINI_API_KEY not set")
    try:
        # Listing models is a metadata call; no generation, so nothing counts against free-tier quota.
        r = httpx.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": settings.gemini_api_key},
            params={"pageSize": 200},
            timeout=20,
        )
        if r.status_code != 200:
            return record("gemini key", "FAIL", f"HTTP {r.status_code}")
        names = [m["name"].removeprefix("models/") for m in r.json().get("models", [])]
        flash = sorted(n for n in names if "flash" in n and "image" not in n and "tts" not in n)
        record("gemini key", "PASS", f"{len(names)} models visible; flash-class: {', '.join(flash[:6]) or 'none'}")
        if not settings.gemini_model:
            print("       set GEMINI_MODEL in .env to a model that is free of charge on your key's project (check AI Studio)")
    except httpx.HTTPError as e:
        record("gemini", "FAIL", type(e).__name__)


def send_test_email() -> None:
    if not settings.resend_api_key:
        return record("resend test email", "SKIP", "RESEND_API_KEY not set")
    try:
        # Resend's own test address: counts as 1 of the 100 free emails per day, reaches no real inbox.
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.email_from,
                "to": ["delivered@resend.dev"],
                "subject": "Phase 0 connectivity test",
                "text": "Synthetic test message.",
            },
            timeout=20,
        )
        record("resend test email", "PASS" if r.status_code in (200, 201) else "FAIL", f"HTTP {r.status_code}")
    except httpx.HTTPError as e:
        record("resend", "FAIL", type(e).__name__)


def bq(*args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("bq")
    if not exe:
        raise FileNotFoundError("bq CLI not found")
    return subprocess.run([exe, *args], capture_output=True, text=True, timeout=180)


def billing_canary() -> int:
    """Proves the BigQuery project is an unbilled sandbox. The sandbox rejects DML; if a DML statement
    succeeds, billing is linked: stop. Uses the account from `gcloud auth login`."""
    project = settings.gcp_project
    if not project:
        record("billing canary", "SKIP", "GCP_PROJECT not set")
        return 0
    ds = "zz_billing_canary"
    table = f"{project}.{ds}.t"
    common = [f"--project_id={project}", f"--location={settings.bq_location}"]
    try:
        mk = bq(*common, "mk", "--dataset", f"{project}:{ds}")
        text = (mk.stdout + mk.stderr).lower()
        if mk.returncode != 0 and "already exists" not in text:
            hint = "run `gcloud auth login` first" if "auth" in text or "credential" in text else text.strip()[:200]
            record("billing canary", "FAIL", f"could not create canary dataset ({hint})")
            return 1
        ddl = bq(*common, "query", "--nouse_legacy_sql", f"CREATE OR REPLACE TABLE `{table}` (x INT64)")
        if ddl.returncode != 0:
            record("billing canary", "FAIL", f"DDL failed: {(ddl.stdout + ddl.stderr).strip()[:200]}")
            return 1
        dml = bq(*common, "query", "--nouse_legacy_sql", f"INSERT INTO `{table}` (x) VALUES (1)")
        out = (dml.stdout + dml.stderr).lower()
        if dml.returncode == 0:
            record("billing canary", "STOP", "DML succeeded, so this project has billing linked. Do not continue.")
            return 2
        if "billing" in out or "free tier" in out or "dml" in out:
            record("billing canary", "PASS", "DML rejected as expected: project is an unbilled sandbox")
            return 0
        record("billing canary", "FAIL", f"unexpected error: {out.strip()[:200]}")
        return 1
    except FileNotFoundError as e:
        record("billing canary", "FAIL", str(e))
        return 1
    finally:
        try:
            bq(*common, "rm", "-r", "-f", "-d", f"{project}:{ds}")
        except Exception:  # cleanup is best effort
            pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--billing-canary", action="store_true", help="run the BigQuery billing canary (needs gcloud login)")
    p.add_argument("--send-test-email", action="store_true", help="send one test email to delivered@resend.dev")
    args = p.parse_args()

    check_supabase()
    check_gemini()
    if args.send_test_email:
        send_test_email()
    code = billing_canary() if args.billing_canary else 0
    if not args.billing_canary:
        record("billing canary", "SKIP", "run with --billing-canary after `gcloud auth login`")
    print(json.dumps({n: s for n, s, _ in results}))
    return code or (1 if any(s == "FAIL" for _, s, _ in results) else 0)


if __name__ == "__main__":
    sys.exit(main())
