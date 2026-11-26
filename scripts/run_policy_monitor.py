"""Run the policy monitor once from this machine, exactly as the deployed backend runs it for GitHub Actions: as the app identity
(sa-app-runtime) against the real sources, BigQuery, the private policy-sources bucket and Gemini. It writes change reports and proposals
(that is its job) and never changes a rule.

    backend/.venv/Scripts/python scripts/run_policy_monitor.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import jobs, monitor  # noqa: E402


def main() -> int:
    deps = jobs.get_job_deps()
    conn = deps.connect()
    try:
        result = monitor.run(conn, deps, trigger="LOCAL")
        reports = conn.execute("SELECT * FROM policy_change_reports WHERE run_id = %s ORDER BY created_at", (result["run_id"],)).fetchall()
        print(monitor.summary_markdown(result, reports))
    finally:
        conn.close()
    return 0 if result["status"] in ("SUCCEEDED", "PARTIAL") else 1


if __name__ == "__main__":
    sys.exit(main())
