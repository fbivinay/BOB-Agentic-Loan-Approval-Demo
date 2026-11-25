"""Import a Kaggle batch of the official baidu/Unlimited-OCR (results.jsonl from ocr/kaggle/run_unlimited_ocr.py) into lh_raw.ocr_pages
as PRECOMPUTED_UNLIMITED_OCR, keyed by the SHA-256 of each source file so an uploaded copy of the same file finds its text.

Only lines whose page split succeeded are imported; the rest are reported and left for manual review. Owner credentials.

    backend/.venv/Scripts/python scripts/import_ocr_results.py path/to/results.jsonl --docs data/out/documents
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("--docs", type=Path, default=ROOT / "data" / "out" / "documents", help="folder holding the source files that were OCRed")
    a = ap.parse_args()
    now, rows, skipped, missing = datetime.now(timezone.utc).isoformat(), [], 0, set()
    for line in a.results.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        src = a.docs / r["document"]
        if not src.exists():
            missing.add(r["document"])
            continue
        if not r.get("page_split_ok", False) or r.get("extraction_method") != "UNLIMITED_OCR":
            skipped += 1
            continue
        rows.append({"source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(), "document_id": None, "application_id": None, "page_no": int(r["page_no"]),
                     "text": r["text"], "extraction_method": "PRECOMPUTED_UNLIMITED_OCR", "engine": r.get("engine"), "engine_version": None,
                     "source_file": r["document"], "created_at": now})
    if not rows:
        print("nothing to import", f"(skipped {skipped}, missing source files {len(missing)})")
        return 1
    tmp = ROOT / "data" / "out" / "_ocr_import.jsonl"
    tmp.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    p = subprocess.run([shutil.which("bq"), f"--project_id={settings.gcp_project}", f"--location={settings.bq_location}", "load", "--noreplace",
                        "--source_format=NEWLINE_DELIMITED_JSON", f"{settings.gcp_project}:lh_raw.ocr_pages", str(tmp)], capture_output=True, text=True, timeout=600)
    tmp.unlink()
    if p.returncode != 0:
        print((p.stdout + p.stderr).strip()[:600])
        return 1
    print(f"imported {len(rows)} pages as PRECOMPUTED_UNLIMITED_OCR; skipped {skipped} with a failed page split; {len(missing)} source files not found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
