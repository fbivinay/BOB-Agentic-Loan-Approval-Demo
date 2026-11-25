"""Build the curated and gold layers in BigQuery with CREATE OR REPLACE TABLE ... AS SELECT (DDL, free in the sandbox).

    backend/.venv/Scripts/python scripts/build_layers.py

SQL lives in bigquery/curated and bigquery/gold and runs in file-name order. It may read lh_raw and lh_curated only, never lh_truth
(checked before running). Each statement carries a bytes-billed cap, so nothing can cost money even if billing were ever linked.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "data"))
from app.config import settings  # noqa: E402
from generator.build import AS_OF  # noqa: E402

CAP = 500 * 1024 * 1024
SUBST = {"P": settings.gcp_project, "AS_OF": AS_OF.isoformat(), "INCOME_FROM": "2026-05-01"}
FILES = sorted((ROOT / "bigquery" / "curated").glob("*.sql")) + sorted((ROOT / "bigquery" / "gold").glob("*.sql"))


def main() -> int:
    for f in FILES:
        if "lh_truth" in f.read_text(encoding="utf-8"):
            print(f"REFUSED: {f.name} references lh_truth")
            return 1
    for f in FILES:
        sql = f.read_text(encoding="utf-8")
        for k, v in SUBST.items():
            sql = sql.replace("{" + k + "}", v)
        r = subprocess.run([shutil.which("bq"), f"--project_id={settings.gcp_project}", f"--location={settings.bq_location}", "query", "--nouse_legacy_sql",
                            f"--maximum_bytes_billed={CAP}"], input=sql, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            print(f"FAILED {f.name}:\n{(r.stdout + r.stderr).strip()[:1200]}")
            return 1
        print("built", f.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
