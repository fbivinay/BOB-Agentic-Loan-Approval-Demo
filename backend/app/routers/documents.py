"""Document review: the Loan Officer checks each document's text and fields, corrects or keys values, verifies or rejects the document,
resolves discrepancies, and attests that the documents are complete. Every human change is recorded (extraction_corrections and the audit
trail). AI output is a suggestion here: nothing becomes evidence until a person verifies it, and prepared demo data never does.
"""

import hmac
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import discrepancy, docproc, jobs, kyc
from app.auth import Staff, require_permission
from app.config import settings
from app.db import get_conn, staff_tx
from app.ocr_store import get_ocr_store
from app.routers.applications import doc_view, load, load_doc
from app.routers.enquiries import get_today

router = APIRouter(tags=["documents"])
METHOD_LABEL = {"EMBEDDED_TEXT": "Embedded PDF text (not OCR)", "UNLIMITED_OCR": "Unlimited-OCR", "PRECOMPUTED_UNLIMITED_OCR": "Unlimited-OCR (precomputed batch)",
                "GEMINI_VISION": "Read by Gemini (AI, not OCR)", "MANUAL": "Keyed in by staff", "PREPARED_DEMO_TEXT": "Prepared demo data — not OCR"}


class FieldValues(BaseModel):
    values: dict[str, str] = Field(min_length=1, max_length=20, description="field -> value as read from the document; re-sending the shown value confirms it")


class Reject(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


class Resolve(BaseModel):
    resolution: Literal["ACCEPT", "CORRECT", "REQUEST_NEW_DOCUMENT"]
    note: str = Field(min_length=10, max_length=1000)


class Attest(BaseModel):
    expected_version: int
    attestation: str = Field(min_length=20, max_length=1000)


def fields_of(conn, doc_id) -> list[dict]:
    return conn.execute("SELECT field, value, source, text_method, page_no, snippet, check_note, model, updated_at FROM document_fields WHERE document_id = %s "
                        "ORDER BY field", (doc_id,)).fetchall()


def open_items(conn, app_id) -> list[dict]:
    return conn.execute("SELECT * FROM discrepancies WHERE application_id = %s ORDER BY created_at", (app_id,)).fetchall()


def disc_view(d: dict) -> dict:
    return {k: (str(d[k]) if k in ("id", "document_id", "resolved_by") and d[k] else d[k].isoformat() if k in ("created_at", "resolved_at") and d[k] else d[k])
            for k in ("id", "document_id", "rule", "field", "form_value", "document_value", "detail", "status", "resolution", "resolution_note", "resolved_by",
                      "resolved_at", "created_at")}


@router.get("/applications/{app_id}/documents/{doc_id}/review")
def review(app_id: UUID, doc_id: UUID, staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn), store=Depends(get_ocr_store)):
    d = load_doc(conn, app_id, doc_id)
    pages = store.document_pages(str(doc_id)) if d["text_method"] in docproc.PAGE_TEXT_METHODS else []
    fields = fields_of(conn, doc_id)
    corrections = conn.execute("SELECT field, value_before, value_after, corrected_by, corrected_at FROM extraction_corrections WHERE document_id = %s "
                               "ORDER BY corrected_at", (doc_id,)).fetchall()
    return {"document": doc_view(d) | {"text_method": d["text_method"], "text_method_label": METHOD_LABEL.get(d["text_method"]), "page_count": d["page_count"],
                                       "processing_note": d["processing_note"], "review_note": d["review_note"]},
            "expected_fields": {k: {"kind": kind, "description": desc} for k, (kind, desc) in docproc.FIELDS.get(d["doc_type"], {}).items()},
            "fields": [f | {"updated_at": f["updated_at"].isoformat(), "source_label": "Prepared demo data — not OCR" if f["source"] == "PREPARED_DEMO_TEXT" else
                            "AI suggestion, not yet verified" if f["source"] == "AI_EXTRACTED" else "Entered or confirmed by staff"} for f in fields],
            "pages": [{"page_no": p["page_no"], "text": p["text"], "method": p["extraction_method"], "engine": p["engine"]} for p in pages],
            "corrections": [c | {"corrected_by": str(c["corrected_by"]), "corrected_at": c["corrected_at"].isoformat()} for c in corrections]}


@router.patch("/applications/{app_id}/documents/{doc_id}/fields")
def set_fields(app_id: UUID, doc_id: UUID, body: FieldValues, staff: Staff = Depends(require_permission("document.verify")), conn=Depends(get_conn),
               store=Depends(get_ocr_store)):
    d = load_doc(conn, app_id, doc_id)
    spec = docproc.FIELDS.get(d["doc_type"], {})
    unknown = set(body.values) - set(spec)
    if unknown:
        raise HTTPException(422, f"{d['doc_type']} has no fields {sorted(unknown)}")
    current = {f["field"]: f for f in fields_of(conn, doc_id)}
    clean = {}
    for k, raw in body.values.items():
        v, problem = docproc.normalise(spec[k][0], raw)
        if v is None:
            raise HTTPException(422, f"{k}: a value is required")
        if problem:
            raise HTTPException(422, f"{k}: {problem}")
        clean[k] = v
    with staff_tx(conn, staff.user_id, staff.role):
        if d["status"] == "AWAITING_OCR":  # the person takes over from the waiting OCR
            conn.execute("UPDATE documents SET status = 'NEEDS_MANUAL_REVIEW', processing_note = 'Taken over for manual entry by staff' WHERE id = %s", (doc_id,))
        for k, v in clean.items():
            before = current.get(k)
            conn.execute("INSERT INTO extraction_corrections (document_id, field, page_no, value_before, value_after, corrected_by) VALUES (%s, %s, %s, %s, %s, %s)",
                         (doc_id, k, before["page_no"] if before else None, before["value"] if before else None, v, staff.user_id))
            conn.execute("INSERT INTO document_fields (document_id, field, value, source, text_method, page_no, snippet, check_note) "
                         "VALUES (%s, %s, %s, 'MANUAL', %s, %s, %s, NULL) ON CONFLICT (document_id, field) DO UPDATE SET value = EXCLUDED.value, source = 'MANUAL', "
                         "check_note = NULL", (doc_id, k, v, before["text_method"] if before and before["source"] == "AI_EXTRACTED" else "MANUAL",
                                               before["page_no"] if before else None, before["snippet"] if before else None))
    return review(app_id, doc_id, staff, conn, store)


@router.post("/applications/{app_id}/documents/{doc_id}/verify")
def verify(app_id: UUID, doc_id: UUID, staff: Staff = Depends(require_permission("document.verify")), conn=Depends(get_conn),
           store=Depends(get_ocr_store)):
    d = load_doc(conn, app_id, doc_id)
    fields = fields_of(conn, doc_id)
    unconfirmed = [f["field"] for f in fields if f["source"] == "AI_EXTRACTED" and f["check_note"]]
    if unconfirmed:
        raise HTTPException(422, f"Confirm or correct the flagged fields first: {', '.join(unconfirmed)}")
    shown = " ".join(f["value"] or "" for f in fields if f["field"] in ("aadhaar_masked", "aadhaar_number"))
    pages = [p["text"] for p in store.document_pages(str(doc_id))] if d["text_method"] in docproc.PAGE_TEXT_METHODS else []
    if (d["doc_type"] == "AADHAAR_MASKED" and docproc.UNMASKED_AADHAAR.search(shown)) or docproc.full_aadhaar_in(pages + [f["value"] or "" for f in fields]):
        raise HTTPException(422, "This document shows a full 12-digit Aadhaar number. Only a masked Aadhaar (XXXX XXXX and the last 4 digits) can be "
                                 "accepted: reject this document and ask for a copy with the Aadhaar masked.")
    with staff_tx(conn, staff.user_id, staff.role):
        conn.execute("UPDATE documents SET status = 'VERIFIED', reviewed_by = %s, reviewed_at = now() WHERE id = %s", (staff.user_id, doc_id))
        app = load(conn, app_id)
        found = discrepancy.find(app, app["declared_monthly_net"],
                                 [{"id": d["id"], "doc_type": d["doc_type"], "fields": {f["field"]: f["value"] for f in fields}}])
        for x in found:
            conn.execute("INSERT INTO discrepancies (application_id, document_id, rule, field, form_value, document_value, detail) "
                         "SELECT %s, %s, %s, %s, %s, %s, %s WHERE NOT EXISTS (SELECT 1 FROM discrepancies WHERE application_id = %s AND rule = %s AND document_id = %s "
                         "AND status = 'OPEN')", (app_id, x["document_id"], x["rule"], x["field"], x["form_value"], x["document_value"], x["detail"],
                                                   app_id, x["rule"], x["document_id"]))
        if found and app["status"] == "DOCS_PENDING":
            conn.execute("SELECT app_transition(%s, 'DISCREPANCY', %s, 'STAFF', %s, NULL)", (app_id, app["version"], staff.user_id))
    return {"document": doc_view(load_doc(conn, app_id, doc_id)), "discrepancies_opened": len(found), "application_status": load(conn, app_id)["status"]}


@router.post("/applications/{app_id}/documents/{doc_id}/reject")
def reject(app_id: UUID, doc_id: UUID, body: Reject, staff: Staff = Depends(require_permission("document.verify")), conn=Depends(get_conn)):
    load_doc(conn, app_id, doc_id)
    with staff_tx(conn, staff.user_id, staff.role):
        conn.execute("UPDATE documents SET status = 'REJECTED', reviewed_by = %s, reviewed_at = now(), review_note = %s WHERE id = %s",
                     (staff.user_id, body.reason.strip(), doc_id))
    return doc_view(load_doc(conn, app_id, doc_id))


@router.post("/applications/{app_id}/documents/{doc_id}/reprocess", status_code=202)
def reprocess(app_id: UUID, doc_id: UUID, staff: Staff = Depends(require_permission("document.verify")), conn=Depends(get_conn)):
    """Try automatic processing again, e.g. after an Unlimited-OCR batch was imported. Only before anyone has keyed values in."""
    d = load_doc(conn, app_id, doc_id)
    if d["status"] not in ("AWAITING_OCR", "NEEDS_MANUAL_REVIEW") or conn.execute(
            "SELECT 1 FROM document_fields WHERE document_id = %s AND source = 'MANUAL'", (doc_id,)).fetchone():
        raise HTTPException(409, "Only a document waiting for OCR or in manual review with no values keyed in can be reprocessed")
    with staff_tx(conn, staff.user_id, staff.role):
        conn.execute("UPDATE documents SET status = 'AWAITING_OCR', processing_note = 'Reprocessing requested' WHERE id = %s", (doc_id,))
        n = conn.execute("SELECT count(*) AS n FROM jobs WHERE payload ->> 'document_id' = %s", (str(doc_id),)).fetchone()["n"]
        jobs.enqueue(conn, "process_document", {"document_id": str(doc_id)}, app_id, f"process:{doc_id}:{n + 1}")
    return {"queued": True}


@router.get("/applications/{app_id}/discrepancies")
def list_discrepancies(app_id: UUID, staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn)):
    load(conn, app_id)
    return {"discrepancies": [disc_view(d) for d in open_items(conn, app_id)]}


@router.post("/applications/{app_id}/discrepancies/{disc_id}/resolve")
def resolve(app_id: UUID, disc_id: UUID, body: Resolve, staff: Staff = Depends(require_permission("document.verify")), conn=Depends(get_conn)):
    with staff_tx(conn, staff.user_id, staff.role):
        done = conn.execute("UPDATE discrepancies SET status = 'RESOLVED', resolution = %s, resolution_note = %s, resolved_by = %s, resolved_at = now() "
                            "WHERE id = %s AND application_id = %s AND status = 'OPEN' RETURNING id", (body.resolution, body.note.strip(), staff.user_id, disc_id, app_id)).fetchone()
        if done is None:
            raise HTTPException(409, "No open discrepancy with that id on this application")
        app = load(conn, app_id)
        if app["status"] == "DISCREPANCY" and not conn.execute("SELECT 1 FROM discrepancies WHERE application_id = %s AND status = 'OPEN'", (app_id,)).fetchone():
            conn.execute("SELECT app_transition(%s, 'DOCS_PENDING', %s, 'STAFF', %s, NULL)", (app_id, app["version"], staff.user_id))
    return {"discrepancies": [disc_view(d) for d in open_items(conn, app_id)], "application_status": load(conn, app_id)["status"]}


@router.post("/applications/{app_id}/documents/attest")
def attest(app_id: UUID, body: Attest, background: BackgroundTasks, staff: Staff = Depends(require_permission("document.verify")), conn=Depends(get_conn),
           deps=Depends(jobs.get_job_deps), today=Depends(get_today)):
    """The Loan Officer attests the documents are complete and verified; the database checks it is true before the status moves.
    The assessment is queued in the same transaction, so a verified application is never left without one."""
    with staff_tx(conn, staff.user_id, staff.role):
        moved = conn.execute("SELECT * FROM app_transition(%s, 'DOCS_VERIFIED', %s, 'STAFF', %s, %s)",
                             (app_id, body.expected_version, staff.user_id, body.attestation.strip())).fetchone()
        # KYC before the loan: a new-to-bank applicant's KYC record is created, or an out-of-date one updated, from the verified documents
        kyc.from_application(conn, load(conn, app_id), staff.user_id, today)
        jobs.enqueue(conn, "assess_application", {"application_id": str(app_id)}, app_id, f"assess:{app_id}:{moved['version']}")
    if deps.kick:
        background.add_task(jobs.drain, deps, 3)
    a = load(conn, app_id)
    return {"status": a["status"], "version": a["version"]}


# ---------------------------------------------------------------- internal: scheduled drain (GitHub Actions cron)
@router.post("/internal/jobs/drain")
def drain_jobs(x_internal_token: str = Header(default=""), deps=Depends(jobs.get_job_deps)):
    if not settings.internal_api_token:
        raise HTTPException(503, "Internal endpoints are not configured")
    if not hmac.compare_digest(x_internal_token.encode(), settings.internal_api_token.encode()):
        raise HTTPException(401, "Invalid internal token")
    return {"results": jobs.drain(deps, limit=10)}
