"""BigQuery and storage set-up for the policy monitor (owner credentials through bq; free, no billing). Idempotent.

  * lh_raw.policy_facts (Layer B facts per source version)
  * lh_curated.policy_chunks becomes a VIEW (it was a table built by the seed ingest), plus lh_curated.policy_facts_current and
    lh_gold.policy_current views
  * sa-app-runtime may append to lh_raw.policy_sources, policy_chunks and policy_facts only (table-level grants), so the deployed backend
    can run the monitor; it still cannot create tables, write elsewhere, or read lh_truth

    backend/.venv/Scripts/python scripts/setup_policy_monitor.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings  # noqa: E402

P, LOC = settings.gcp_project, settings.bq_location
APP_SA = f"sa-app-runtime@{P}.iam.gserviceaccount.com"
FACTS = [("source_id", "STRING", "REQUIRED"), ("version", "INTEGER", "REQUIRED"), ("fact_key", "STRING", "REQUIRED"), ("value", "STRING", "NULLABLE"),
         ("condition", "STRING", "NULLABLE"), ("quote", "STRING", "NULLABLE"), ("numbers", "STRING", "NULLABLE"), ("model", "STRING", "NULLABLE"),
         ("prompt_version", "STRING", "NULLABLE"), ("extracted_at", "TIMESTAMP", "REQUIRED")]
VIEWS = [ROOT / "bigquery" / "curated" / "04_policy_chunks.sql", ROOT / "bigquery" / "curated" / "05_policy_facts_current.sql",
         ROOT / "bigquery" / "gold" / "02_policy_current.sql"]


def bq(*args: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run([shutil.which("bq"), f"--project_id={P}", f"--location={LOC}", *args], input=stdin, capture_output=True, text=True, timeout=300)
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED: bq {' '.join(args)[:160]}\n{(r.stdout + r.stderr).strip()[:600]}")
    return r


def main() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([{"name": n, "type": t, "mode": m} for n, t, m in FACTS], f)
    r = bq("mk", "--table", "--description=PUBLIC DOCUMENTS. Facts extracted from official policy sources, with quotes (policy monitor Layer B).",
           f"{P}:lh_raw.policy_facts", f.name, check=False)
    Path(f.name).unlink()
    print("lh_raw.policy_facts:", "created" if r.returncode == 0 else "exists")
    info = json.loads(bq("show", "--format=json", f"{P}:lh_curated.policy_chunks", check=False).stdout or "{}")
    if info.get("type") == "TABLE":  # the seed ingest built it as a table; the monitor needs a view
        bq("rm", "-f", "-t", f"{P}:lh_curated.policy_chunks")
        print("lh_curated.policy_chunks: table replaced by a view")
    for v in VIEWS:
        bq("query", "--nouse_legacy_sql", "--maximum_bytes_billed=50000000", stdin=v.read_text(encoding="utf-8").replace("{P}", P))
        print("view:", v.relative_to(ROOT))
    for t in ("policy_sources", "policy_chunks", "policy_facts"):
        bq("add-iam-policy-binding", f"--member=serviceAccount:{APP_SA}", "--role=roles/bigquery.dataEditor", f"{P}:lh_raw.{t}")
        print(f"sa-app-runtime may append to lh_raw.{t}")


if __name__ == "__main__":
    main()
