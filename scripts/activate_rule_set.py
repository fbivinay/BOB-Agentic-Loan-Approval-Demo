"""Put the scheme's rule set into Supabase as DRAFT, run the policy test suite against it, and activate it with a named human approver.

    backend/.venv/Scripts/python scripts/activate_rule_set.py --approver branch.manager@demo.example.com --reason "..."

The database decides whether activation is allowed (policy.activate permission, a passing test result, a reason of 10+ characters),
writes the activation log and the audit entry, and retires any previous active version of the scheme.
A version already in the database with different content is refused: change the version number instead.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings  # noqa: E402
from app.db import staff_tx  # noqa: E402
from app.policy import ruleset  # noqa: E402

SUITES = ["tests/test_policy_engine.py", "tests/test_finance.py", "tests/test_policy_dataset.py"]


def run_tests(rs) -> dict:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *SUITES], cwd=ROOT / "backend", capture_output=True, text=True, timeout=900)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|errors?|skipped)", tail)}
    return {"passed": r.returncode == 0 and counts.get("failed", 0) == 0 and counts.get("skipped", 0) == 0, "summary": tail, "counts": counts,
            "suites": SUITES, "rule_set_sha256": rs.sha256, "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approver", required=True, help="email of the approving member of staff")
    ap.add_argument("--reason", required=True)
    a = ap.parse_args()
    rs = ruleset.load()
    print("rule set:", rs.identity)
    result = run_tests(rs)
    print("tests:", result["summary"], "->", "PASSED" if result["passed"] else "FAILED")
    with psycopg.connect(settings.supabase_db_url, row_factory=dict_row) as conn:
        who = conn.execute("SELECT user_id, role FROM staff_profiles WHERE email = %s AND is_active", (a.approver,)).fetchone()
        if not who:
            print("no active staff member with that email")
            return 1
        with staff_tx(conn, who["user_id"], who["role"]):
            row = conn.execute("SELECT id, status, content_sha256 FROM policy_rule_sets WHERE scheme = %s AND version = %s", (rs.scheme, rs.version)).fetchone()
            if row and row["content_sha256"] != rs.sha256:
                print(f"{rs.scheme} v{rs.version} already exists with different content; bump the version in the YAML")
                return 1
            if row is None:
                row = conn.execute("INSERT INTO policy_rule_sets (scheme, version, content, content_sha256, source_references, created_by) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, status",
                                   (rs.scheme, rs.version, json.dumps(rs.content), rs.sha256, json.dumps(["docs/GATE_B_PARAMETER_SHEET.md"]), who["user_id"])).fetchone()
                print("inserted as DRAFT")
            if row["status"] == "ACTIVE":
                print("already ACTIVE")
                return 0
            conn.execute("UPDATE policy_rule_sets SET status = 'ACTIVE', approved_by = %s, activation_reason = %s, test_result = %s WHERE id = %s",
                         (who["user_id"], a.reason, json.dumps(result), row["id"]))
        done = conn.execute("SELECT status, approved_at, effective_from FROM policy_rule_sets WHERE id = %s", (row["id"],)).fetchone()
        print(f"status: {done['status']}  approved_at: {done['approved_at']:%Y-%m-%d %H:%M} UTC")
    return 0


if __name__ == "__main__":
    sys.exit(main())
