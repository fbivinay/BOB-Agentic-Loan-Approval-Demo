"""Live check of Phase 7 against the real services: queues processing for any uploaded document that has no job yet (documents uploaded
before Phase 7), drains the queue with the real storage, BigQuery and Gemini, and prints what each document got: the text method, the
fields and anything flagged. It stops before any human step: verifying documents and resolving discrepancies are left to the Loan Officer.

    backend/.venv/Scripts/python scripts/live_check_documents.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import jobs  # noqa: E402
from app.db import connect, staff_tx  # noqa: E402


def main() -> int:
    conn = connect()
    with staff_tx(conn, actor_type="SYSTEM"):
        waiting = conn.execute("SELECT d.id, d.application_id FROM documents d WHERE d.status IN ('UPLOADED', 'AWAITING_OCR') "
                               "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.payload ->> 'document_id' = d.id::text AND j.status IN ('QUEUED', 'RUNNING'))").fetchall()
        for d in waiting:
            n = conn.execute("SELECT count(*) AS n FROM jobs WHERE payload ->> 'document_id' = %s", (str(d["id"]),)).fetchone()["n"]
            jobs.enqueue(conn, "process_document", {"document_id": str(d["id"])}, d["application_id"], f"process:{d['id']}:{n + 1}")
    print(f"queued {len(waiting)} document(s)")
    for r in jobs.drain(jobs.get_job_deps(), limit=20):
        print(" ", r)
    rows = conn.execute("SELECT a.reference, d.id, d.original_name, d.doc_type, d.status, d.text_method, d.processing_note FROM documents d "
                        "JOIN applications a ON a.id = d.application_id ORDER BY a.reference, d.uploaded_at").fetchall()
    ok = True
    for d in rows:
        print(f"\n{d['reference']}  {d['original_name']}  {d['doc_type']}  -> {d['status']}  [{d['text_method']}]\n  {d['processing_note']}")
        for f in conn.execute("SELECT field, value, source, check_note FROM document_fields WHERE document_id = %s ORDER BY field", (d["id"],)).fetchall():
            print(f"    {f['field']:<22} {str(f['value']):<36} {f['source']:<18} {f['check_note'] or ''}")
        ok &= d["status"] in ("EXTRACTED", "NEEDS_MANUAL_REVIEW", "AWAITING_OCR", "VERIFIED", "REJECTED", "WITHDRAWN")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
