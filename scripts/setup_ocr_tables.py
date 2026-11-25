"""Create the lh_raw tables for document text and AI extraction output, and let the app identity append to exactly those two.
Idempotent, free (owner credentials through gcloud/bq). The app identity keeps READER on the rest of lh_raw and no access to lh_truth.

    backend/.venv/Scripts/python scripts/setup_ocr_tables.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import settings  # noqa: E402

P, LOC = settings.gcp_project, settings.bq_location
APP_SA = f"sa-app-runtime@{P}.iam.gserviceaccount.com"
S, I, T = "STRING", "INTEGER", "TIMESTAMP"
SCHEMAS = {
    "ocr_pages": [("source_sha256", S, "REQUIRED"), ("document_id", S, "NULLABLE"), ("application_id", S, "NULLABLE"), ("page_no", I, "REQUIRED"),
                  ("text", S, "NULLABLE"), ("extraction_method", S, "REQUIRED"), ("engine", S, "NULLABLE"), ("engine_version", S, "NULLABLE"),
                  ("source_file", S, "NULLABLE"), ("created_at", T, "REQUIRED")],
    "extraction_outputs": [("source_sha256", S, "REQUIRED"), ("document_id", S, "NULLABLE"), ("application_id", S, "NULLABLE"), ("model", S, "REQUIRED"),
                           ("prompt_version", S, "REQUIRED"), ("output_json", S, "REQUIRED"), ("created_at", T, "REQUIRED")],
    "prepared_demo_values": [("source_sha256", S, "REQUIRED"), ("source_file", S, "NULLABLE"), ("doc_type", S, "REQUIRED"), ("fields_json", S, "REQUIRED"),
                             ("label", S, "REQUIRED"), ("created_at", T, "REQUIRED")],
}
DESC = {"ocr_pages": "SYNTHETIC DATA ONLY. Page text per document with its extraction_method (EMBEDDED_TEXT is not OCR).",
        "extraction_outputs": "SYNTHETIC DATA ONLY. Validated AI extraction output (PANs replaced by placeholders), keyed by file hash, model and prompt version.",
        "prepared_demo_values": "SYNTHETIC DATA ONLY. PREPARED_DEMO_TEXT: generated demonstration values, 'Prepared demo data - not OCR'. Never evidence."}
APPENDABLE = ("ocr_pages", "extraction_outputs")


def bq(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run([shutil.which("bq"), f"--project_id={P}", f"--location={LOC}", *args], capture_output=True, text=True, timeout=300)
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED: bq {' '.join(args)}\n{(r.stdout + r.stderr).strip()[:600]}")
    return r


def main() -> None:
    for name, cols in SCHEMAS.items():
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump([{"name": n, "type": t, "mode": m} for n, t, m in cols], f)
        r = bq("mk", "--table", f"--description={DESC[name]}", f"{P}:lh_raw.{name}", f.name, check=False)
        Path(f.name).unlink()
        print(f"lh_raw.{name}:", "created" if r.returncode == 0 else "exists" if "already exists" in (r.stdout + r.stderr) else (r.stdout + r.stderr).strip()[:300])
    for name in APPENDABLE:  # table-level grant: append to these two tables only
        bq("add-iam-policy-binding", f"--member=serviceAccount:{APP_SA}", "--role=roles/bigquery.dataEditor", f"{P}:lh_raw.{name}")
        print(f"sa-app-runtime may append to lh_raw.{name}")


if __name__ == "__main__":
    main()
