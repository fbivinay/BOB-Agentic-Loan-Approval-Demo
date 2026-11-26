"""Manual seed ingest of the policy knowledge base (owner credentials through bq; free, no billing).

For each source in backend/app/policy/sources.yaml:
  1. fetch it (conditional GET with the stored ETag / Last-Modified), politely, one request at a time;
  2. extract its text (HTML_TEXT from markup, EMBEDDED_TEXT from a born-digital PDF; a scanned PDF is recorded as AWAITING_OCR with no text);
  3. if the cleaned text is unchanged from the latest version, record nothing;
  4. otherwise keep the raw bytes in the private Supabase bucket `policy-sources` and append a new version to lh_raw.policy_sources and
     lh_raw.policy_chunks (load jobs: the sandbox has no DML).
Then lh_curated.policy_chunks is rebuilt (bigquery/curated/04_policy_chunks.sql) and checked as the app identity.

Public documents only. Nothing here touches a rule set: rules change only through a human-approved rule set.

    backend/.venv/Scripts/python scripts/ingest_policy_sources.py
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import policy_kb  # noqa: E402
from app.config import settings  # noqa: E402
from app.storage import Storage  # noqa: E402

P, LOC = settings.gcp_project, settings.bq_location
S, I, T = "STRING", "INTEGER", "TIMESTAMP"
SCHEMAS = {
    "policy_sources": [("source_id", S, "REQUIRED"), ("version", I, "REQUIRED"), ("publisher", S, "REQUIRED"), ("title", S, "REQUIRED"), ("url", S, "REQUIRED"),
                       ("fetched_at", T, "REQUIRED"), ("http_status", I, "REQUIRED"), ("content_type", S, "NULLABLE"), ("etag", S, "NULLABLE"),
                       ("last_modified", S, "NULLABLE"), ("raw_sha256", S, "REQUIRED"), ("text_sha256", S, "REQUIRED"), ("storage_path", S, "REQUIRED"),
                       ("extraction_method", S, "REQUIRED"), ("page_count", I, "NULLABLE"), ("chunk_count", I, "REQUIRED"), ("created_at", T, "REQUIRED")],
    "policy_chunks": [("source_id", S, "REQUIRED"), ("version", I, "REQUIRED"), ("chunk_no", I, "REQUIRED"), ("page_no", I, "NULLABLE"), ("section", S, "NULLABLE"),
                      ("text", S, "REQUIRED"), ("extraction_method", S, "REQUIRED"), ("created_at", T, "REQUIRED")],
}
DESC = {"policy_sources": "PUBLIC DOCUMENTS. One row per fetched version of an official policy source (RBI, Bank of Baroda), with hashes and the snapshot path.",
        "policy_chunks": "PUBLIC DOCUMENTS. Text chunks of each source version, with section, page and extraction_method (HTML_TEXT or EMBEDDED_TEXT, not OCR)."}
BUCKET, MAX_BYTES = "policy-sources", 20 * 1024 * 1024


def bq(*args: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run([shutil.which("bq"), f"--project_id={P}", f"--location={LOC}", *args], input=stdin, capture_output=True, text=True, timeout=600)
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED: bq {' '.join(args)[:200]}\n{(r.stdout + r.stderr).strip()[:800]}")
    return r


def ensure_tables() -> None:
    for name, cols in SCHEMAS.items():
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump([{"name": n, "type": t, "mode": m} for n, t, m in cols], f)
        r = bq("mk", "--table", f"--description={DESC[name]}", f"{P}:lh_raw.{name}", f.name, check=False)
        Path(f.name).unlink()
        if r.returncode != 0 and "already exists" not in " ".join((r.stdout + r.stderr).split()):
            raise SystemExit(f"FAILED creating lh_raw.{name}: {(r.stdout + r.stderr).strip()[:300]}")


def latest_versions() -> dict[str, dict]:
    out = bq("query", "--nouse_legacy_sql", "--format=json", "--maximum_bytes_billed=50000000",
             stdin=f"SELECT source_id, version, etag, last_modified, text_sha256 FROM `{P}.lh_raw.policy_sources` "
                   "QUALIFY ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY version DESC) = 1").stdout
    return {r["source_id"]: r for r in json.loads(out or "[]")}


def load_rows(table: str, rows: list[dict]) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    try:
        bq("load", "--source_format=NEWLINE_DELIMITED_JSON", "--noreplace", f"{P}:lh_raw.{table}", f.name)
    finally:
        Path(f.name).unlink()


def main() -> int:
    ensure_tables()
    storage = Storage(settings.supabase_url, settings.supabase_service_role_key, bucket=BUCKET, max_bytes=MAX_BYTES, mime_types=("text/html", "application/pdf"))
    print("bucket", BUCKET + ":", storage.ensure_bucket())
    latest = latest_versions()
    http = httpx.Client(headers={"User-Agent": policy_kb.USER_AGENT}, timeout=60, follow_redirects=True)
    changed = 0
    for i, src in enumerate(policy_kb.load_sources()):
        if i:
            time.sleep(3)  # one request at a time, spaced out: these are public sites, not an API
        prev = latest.get(src["id"])
        headers = {k: v for k, v in (("If-None-Match", (prev or {}).get("etag")), ("If-Modified-Since", (prev or {}).get("last_modified"))) if v}
        r = http.get(src["url"], headers=headers)
        if r.status_code == 304:
            print(f"{src['id']}: not modified (v{prev['version']})")
            continue
        if r.status_code != 200:
            print(f"{src['id']}: HTTP {r.status_code}, skipped")
            continue
        ctype = r.headers.get("content-type", "").split(";")[0].strip()
        if not (r.content.startswith(b"%PDF-") or ctype == "text/html"):
            print(f"{src['id']}: unexpected content ({ctype}), skipped (a bot check or error page is never indexed)")
            continue
        x = policy_kb.extract(src, r.content, ctype)
        if x["extraction_method"] == "HTML_TEXT" and len(x["chunks"]) < 3:
            print(f"{src['id']}: only {len(x['chunks'])} chunk(s) found in the container; page layout may have changed, skipped")
            continue
        if prev and prev["text_sha256"] == x["text_sha256"]:
            print(f"{src['id']}: text unchanged (v{prev['version']})")
            continue
        version = int(prev["version"]) + 1 if prev else 1
        raw_sha = hashlib.sha256(r.content).hexdigest()
        path = f"{src['id']}/v{version}-{raw_sha[:12]}.{'pdf' if r.content.startswith(b'%PDF-') else 'html'}"
        storage.put(path, r.content, "application/pdf" if path.endswith(".pdf") else "text/html")
        now = datetime.now(timezone.utc).isoformat()
        load_rows("policy_chunks", [{"source_id": src["id"], "version": version, "chunk_no": c.chunk_no, "page_no": c.page_no, "section": c.section, "text": c.text,
                                     "extraction_method": x["extraction_method"], "created_at": now} for c in x["chunks"]])
        load_rows("policy_sources", [{"source_id": src["id"], "version": version, "publisher": src["publisher"], "title": src["title"], "url": src["url"],
                                      "fetched_at": now, "http_status": r.status_code, "content_type": ctype, "etag": r.headers.get("etag"),
                                      "last_modified": r.headers.get("last-modified"), "raw_sha256": raw_sha, "text_sha256": x["text_sha256"], "storage_path": path,
                                      "extraction_method": x["extraction_method"], "page_count": x["page_count"], "chunk_count": len(x["chunks"]), "created_at": now}])
        changed += 1
        print(f"{src['id']}: v{version}, {x['extraction_method']}, {len(x['chunks'])} chunks, snapshot {path}")
    sql = (ROOT / "bigquery" / "curated" / "04_policy_chunks.sql").read_text(encoding="utf-8").replace("{P}", P)
    bq("query", "--nouse_legacy_sql", "--maximum_bytes_billed=200000000", stdin=sql)
    # the assessment reads the knowledge base as the app identity: prove that identity can
    from app.lakehouse import _run

    n = _run(f"SELECT COUNT(*) AS n, COUNT(DISTINCT source_id) AS s FROM `{P}.lh_curated.policy_chunks`", {})[0]
    print(f"lh_curated.policy_chunks rebuilt: {n['n']} chunks from {n['s']} sources ({changed} new version(s)); readable by sa-app-runtime")
    return 0


if __name__ == "__main__":
    sys.exit(main())
