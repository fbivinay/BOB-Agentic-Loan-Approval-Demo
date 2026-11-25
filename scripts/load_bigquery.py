"""Load the canonical dataset files into the BigQuery sandbox with load jobs (no DML, no streaming, no GCS, no billing).

    backend/.venv/Scripts/python scripts/load_bigquery.py [--dir data/out]

Tables are replaced (`--replace`), so a reload is idempotent. Dataset-level access (see setup_bq_access.py) is untouched.
Before loading, the dataset's own validation suite must pass and the manifest must match the files.
Load metadata (when, who) is written to data/out/load_run.json, outside the dataset and its checksums.
Logical types -> BigQuery: MONEY/BP/PCT2 NUMERIC, INT64 INTEGER, BOOL BOOLEAN, ARRAY_INT64 STRING (JSON text; parsed in the curated layer).
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "data"))
from app.config import settings  # noqa: E402
from generator.schema import TABLES  # noqa: E402
from generator.validate import run as validate  # noqa: E402

BQ_TYPE = {"STRING": "STRING", "INT64": "INTEGER", "BOOL": "BOOLEAN", "DATE": "DATE", "TIMESTAMP": "TIMESTAMP", "JSON": "JSON",
           "ARRAY_INT64": "STRING", "MONEY": "NUMERIC", "BP": "NUMERIC", "PCT2": "NUMERIC"}


def bq(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([shutil.which("bq"), f"--project_id={settings.gcp_project}", f"--location={settings.bq_location}", *args], capture_output=True, text=True, timeout=900)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "data" / "out"))
    out = Path(ap.parse_args().dir)
    failed = [n for n, ok, _ in validate(out) if not ok]
    if failed:
        print("Dataset validation failed, not loading:", failed)
        return 1
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    P = settings.gcp_project
    started = datetime.now(timezone.utc)
    loaded = []
    for ds, tables in TABLES.items():
        for name, (_, cols) in tables.items():
            schema = [{"name": c, "type": BQ_TYPE[t], "mode": "NULLABLE"} for c, t in cols]
            sf = out / f"_schema_{name}.json"
            sf.write_text(json.dumps(schema), encoding="utf-8")
            r = bq("load", "--replace", "--source_format=CSV", "--skip_leading_rows=1", "--allow_quoted_newlines=false", f"{P}:{ds}.{name}", str(out / ds / f"{name}.csv"), str(sf))
            sf.unlink()
            if r.returncode != 0:
                print(f"FAILED {ds}.{name}: {(r.stdout + r.stderr).strip()[:500]}")
                return 1
            loaded.append(f"{ds}.{name}")
            print(f"loaded {ds}.{name}  ({manifest['tables'][f'{ds}/{name}']['rows']} rows)")
    (out / "load_run.json").write_text(json.dumps({"loaded_at": started.isoformat(timespec="seconds"), "project": P, "tables": loaded,
                                                   "dataset_version": manifest["dataset_version"], "canonical_sha256": manifest["canonical_sha256"],
                                                   "note": "Load metadata. Not part of the canonical dataset."}, indent=1) + "\n", encoding="utf-8")
    print("done:", len(loaded), "tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
