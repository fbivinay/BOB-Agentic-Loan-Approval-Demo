"""Load PREPARED_DEMO_TEXT values for the degraded-looking demo documents into lh_raw.prepared_demo_values (owner credentials).

These are generated demonstration values (the values the generator printed on each degraded sample), used only to show the downstream
flow for those scans. They are always shown as "Prepared demo data - not OCR", are never evidence, and a
document carrying them cannot be verified until a person confirms or re-keys each value from the image.

This owner tool reads the generator's local output; the application and agent identities never read lh_truth. Clean (born-digital)
documents are excluded: their text comes from the PDF's own text layer. Generator labels such as injected discrepancies are not copied.

    backend/.venv/Scripts/python scripts/load_prepared_demo_text.py
"""

import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings  # noqa: E402

OUT = ROOT / "data" / "out"
LABEL = "Prepared demo data - not OCR"


def main() -> int:
    manifest = json.loads((OUT / "documents_manifest.json").read_text(encoding="utf-8"))["files"]
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for d in csv.DictReader(open(OUT / "lh_truth" / "documents_truth.csv", encoding="utf-8")):
        if d["quality"] == "CLEAN":
            continue
        fields = {k: v for k, v in json.loads(d["expected_fields"]).items() if k != "synthetic_notice"}
        rows.append({"source_sha256": manifest[d["file_name"]]["sha256"], "source_file": d["file_name"], "doc_type": d["doc_type"],
                     "fields_json": json.dumps(fields, sort_keys=True), "label": LABEL, "created_at": now})
    tmp = OUT / "_prepared_demo_values.jsonl"
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    r = subprocess.run([shutil.which("bq"), f"--project_id={settings.gcp_project}", f"--location={settings.bq_location}", "load", "--replace",
                        "--source_format=NEWLINE_DELIMITED_JSON", f"{settings.gcp_project}:lh_raw.prepared_demo_values", str(tmp)],
                       capture_output=True, text=True, timeout=600)
    tmp.unlink()
    if r.returncode != 0:
        print((r.stdout + r.stderr).strip()[:600])
        return 1
    print(f"loaded {len(rows)} prepared demo values (degraded documents only), labelled '{LABEL}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
