"""Formal application: intake, new-to-bank registration details, the document checklist, and document upload.

Intake (Loan Officer, application in INTAKE):
  * Complete the applicant's contact and KYC details. A new-to-bank applicant's identity details are entered here; an existing customer's
    identity comes from the bank's records and is not edited from a loan application.
  * Complete intake: the document checklist is worked out once from the active rule set and the bank's current records, frozen on the
    application, and the application moves to DOCS_PENDING.
Documents (Loan Officer, INTAKE / DOCS_PENDING / DISCREPANCY): uploaded through the backend into a private bucket, type checked from the
file's own bytes, hashed, never overwritten or deleted. A wrong file is withdrawn with a reason. Viewing uses a 60-second signed URL and
is recorded in the audit trail.

Nothing here verifies documents, reads them with OCR, or assesses the application: that is Phase 7 onward.
"""

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app import jobs, kyc
from app.auth import Staff, require_permission
from app.config import settings
from app.db import get_conn, staff_tx
from app.lakehouse import get_lakehouse
from app.policy import indicative
from app.routers.enquiries import active_rule_set, bank_facts, get_today
from app.storage import MAX_BYTES, get_storage, sniff

router = APIRouter(tags=["applications"])

# which physical documents satisfy each checklist requirement
ACCEPTS = {
    "APPLICATION_FORM": {"APPLICATION_FORM"}, "PHOTOGRAPH": {"PHOTOGRAPH"}, "PAN_CARD": {"PAN_CARD"},
    "ADDRESS_PROOF": {"AADHAAR_MASKED", "PASSPORT", "VOTER_ID"}, "SALARY_SLIPS_3M": {"SALARY_SLIP"}, "ITR_1Y": {"ITR"},
    "BUSINESS_PROOF": {"BUSINESS_PROOF"}, "PENSION_PROOF": {"PENSION_ORDER"}, "BANK_STATEMENT_6M": {"BANK_STATEMENT"},
    "SUPPORTING": {"EMPLOYMENT_PROOF", "OTHER"},  # optional extra evidence, never on the checklist
}
UPLOAD_STATUSES = ("INTAKE", "DOCS_PENDING", "DISCREPANCY")
EXTENSIONS = {"pdf": {"pdf"}, "png": {"png"}, "jpg": {"jpg", "jpeg"}}
APP_STATUS = Literal["INTAKE", "DOCS_PENDING", "DISCREPANCY", "DOCS_VERIFIED", "AI_ASSESSED", "FORWARDED", "UNDER_REVIEW", "APPROVED", "DECLINED",
                     "RECONSIDERATION", "SANCTIONED", "DISBURSED"]


class ApplicantDetails(BaseModel):
    # contact and address: any applicant
    email: str | None = Field(default=None, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    address_line: str | None = Field(default=None, min_length=5, max_length=200)
    city: str | None = Field(default=None, min_length=2, max_length=60, pattern=r"^[A-Za-z][A-Za-z .-]+$")
    state: str | None = Field(default=None, min_length=2, max_length=60, pattern=r"^[A-Za-z][A-Za-z .&-]+$")
    pincode: str | None = Field(default=None, pattern=r"^[1-9]\d{5}$")
    # identity: new-to-bank applicants only
    gender: Literal["M", "F", "O"] | None = None
    pan: str | None = Field(default=None, pattern=r"^(SYNP\d{7}[A-Z]|[A-Z]{5}\d{4}[A-Z])$", description="the synthetic SYNP form, or a real PAN when testing with genuine documents")
    residency_status: Literal["RESIDENT", "NRI"] | None = None
    is_staff: bool | None = None


IDENTITY = ("gender", "pan", "residency_status", "is_staff")


class CompleteIntake(BaseModel):
    expected_version: int


class Withdraw(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


# ---------------------------------------------------------------- helpers
def load(conn, app_id: UUID) -> dict:
    row = conn.execute(
        "SELECT ap.*, a.customer_id, a.bank_relationship, a.full_name, a.date_of_birth, a.gender, a.mobile, a.email, a.pan, a.residency_status, a.is_staff, "
        "a.employment_type, a.address_line, a.city, a.state, a.pincode, e.docs_checklist AS enquiry_checklist, e.reference AS enquiry_reference "
        "FROM applications ap JOIN applicants a ON a.id = ap.applicant_id LEFT JOIN enquiries e ON e.id = ap.enquiry_id WHERE ap.id = %s", (app_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Application not found")
    return row


def checklist_status(row: dict, docs: list[dict]) -> dict:
    """The frozen checklist after intake; before that, the provisional one from the enquiry. Each requirement shows its live uploads."""
    frozen = row["documents_checklist"]
    base = frozen or {"required": (row["enquiry_checklist"] or {}).get("required", []), "waived": (row["enquiry_checklist"] or {}).get("waived", [])}
    live = [d for d in docs if d["status"] != "WITHDRAWN"]
    items = [{**r, "accepts": sorted(ACCEPTS[r["code"]]), "uploaded": sum(1 for d in live if d["requirement"] == r["code"])} for r in base["required"]]
    return {"frozen": frozen is not None, "items": items, "waived": base["waived"], "all_uploaded": all(i["uploaded"] for i in items),
            "basis": frozen.get("basis") if frozen else None, "policy": frozen.get("policy") if frozen else None}


def doc_view(d: dict) -> dict:
    return {"id": str(d["id"]), "requirement": d["requirement"], "doc_type": d["doc_type"], "original_name": d["original_name"], "mime_type": d["mime_type"],
            "size_bytes": d["size_bytes"], "sha256": d["sha256"], "status": d["status"], "uploaded_at": d["uploaded_at"].isoformat(),
            "withdrawn_reason": d["withdrawn_reason"]}


def view(conn, row: dict) -> dict:
    docs = conn.execute("SELECT * FROM documents WHERE application_id = %s ORDER BY uploaded_at", (row["id"],)).fetchall()
    terms = conn.execute("SELECT kind, amount, rate_bp, tenure_months, created_at FROM application_terms WHERE application_id = %s ORDER BY created_at", (row["id"],)).fetchall()
    return {
        "id": str(row["id"]), "reference": row["reference"], "status": row["status"], "version": row["version"], "enquiry_reference": row["enquiry_reference"],
        "applicant": {k: (str(row[k]) if isinstance(row[k], date) else row[k]) for k in ("customer_id", "bank_relationship", "full_name", "date_of_birth", "gender",
                      "mobile", "email", "pan", "residency_status", "is_staff", "employment_type", "address_line", "city", "state", "pincode")},
        "requested": {"amount": str(row["requested_amount"]), "tenure_months": row["requested_tenure_months"]},
        "declared": {"monthly_net": str(row["declared_monthly_net"]) if row["declared_monthly_net"] is not None else None,
                     "other_lender_emi": str(row["existing_monthly_emi"]) if row["existing_monthly_emi"] is not None else None,
                     "credit_history": row["declared_credit_history"]},
        "terms": [{"kind": t["kind"], "amount": str(t["amount"]), "rate_percent": f"{Decimal(t['rate_bp']) / 100:.2f}", "tenure_months": t["tenure_months"]} for t in terms],
        "intake": {"completed": row["intake_completed_at"] is not None,
                   "completed_at": row["intake_completed_at"].isoformat() if row["intake_completed_at"] else None, "missing_details": missing_details(row)},
        "checklist": checklist_status(row, docs), "documents": [doc_view(d) for d in docs], "kyc": kyc_on_file(conn, row),
        "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat(),
    }


def kyc_on_file(conn, row: dict) -> dict | None:
    """The customer's KYC record and its current documents: reused when verified and unchanged, so PAN and address proof are not asked for again."""
    p = kyc.profile(conn, customer_id=row.get("customer_id"), pan=row.get("pan"))
    if p is None:
        return None
    return {"profile_id": str(p["id"]), "kyc_status": p["kyc_status"], "verified_on": p["verified_on"].isoformat() if p["verified_on"] else None,
            "version": p["version"], "is_current": kyc.is_current(p), "reused": row["bank_relationship"] == "ETB" and kyc.is_current(p),
            "documents": [kyc.doc_view(d) for d in kyc.documents(conn, p["id"], current_only=True)]}


def missing_details(row: dict) -> list[str]:
    """What must be on file before intake can be completed: identity for KYC, a current address, and an email for the sanction pack."""
    return [k for k in ("pan", "email", "address_line", "city", "state", "pincode") if not row.get(k)]


def require_intake_open(row: dict) -> None:
    if row["status"] != "INTAKE" or row["intake_completed_at"] is not None:
        raise HTTPException(409, f"Intake is already complete (status {row['status']})")


# ---------------------------------------------------------------- read
@router.get("/applications")
def list_applications(status: APP_STATUS | None = None, q: str | None = Query(default=None, max_length=60),
                      staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn)):
    sql = ("SELECT ap.id, ap.reference, ap.status, ap.version, ap.requested_amount, ap.requested_tenure_months, ap.updated_at, ap.intake_completed_at, "
           "a.full_name, a.customer_id, a.bank_relationship, a.mobile FROM applications ap JOIN applicants a ON a.id = ap.applicant_id WHERE true")
    params: list = []
    if status:
        sql += " AND ap.status = %s"
        params.append(status)
    if q:
        s = q.strip()
        if re.fullmatch(r"(?i)APP-\d{4}-\d{6}", s):
            sql += " AND ap.reference = %s"
            params.append(s.upper())
        elif re.fullmatch(r"[6-9]\d{9}", s):
            sql += " AND a.mobile = %s"
            params.append(s)
        else:
            sql += " AND (a.full_name ILIKE %s OR a.customer_id = %s)"
            params += [f"%{s}%", s.upper()]
    rows = conn.execute(sql + " ORDER BY ap.updated_at DESC LIMIT 50", params).fetchall()
    return {"applications": [{"id": str(r["id"]), "reference": r["reference"], "status": r["status"], "version": r["version"], "full_name": r["full_name"],
                              "customer_id": r["customer_id"], "relationship": r["bank_relationship"], "requested_amount": str(r["requested_amount"]),
                              "requested_tenure_months": r["requested_tenure_months"], "intake_completed": r["intake_completed_at"] is not None,
                              "updated_at": r["updated_at"].isoformat()} for r in rows]}


@router.get("/applications/{app_id}")
def get_application(app_id: UUID, staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn)):
    return view(conn, load(conn, app_id))


# ---------------------------------------------------------------- intake
@router.patch("/applications/{app_id}/applicant")
def update_applicant(app_id: UUID, body: ApplicantDetails, staff: Staff = Depends(require_permission("application.intake")), conn=Depends(get_conn)):
    row = load(conn, app_id)
    require_intake_open(row)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(422, "Nothing to change")
    if row["bank_relationship"] == "ETB" and set(changes) & set(IDENTITY):
        raise HTTPException(422, "An existing customer's identity details come from the bank's records and are updated through KYC, not from a loan application")
    with staff_tx(conn, staff.user_id, staff.role):
        # the applicant record is shared: once another application of this person has moved past intake, its details are what that
        # application was processed on, so they are not edited from here
        other = conn.execute("SELECT reference FROM applications WHERE applicant_id = %s AND id <> %s AND status <> 'INTAKE' LIMIT 1",
                             (row["applicant_id"], app_id)).fetchone()
        if other:
            raise HTTPException(409, f"This applicant's details are in use by {other['reference']}, which is past intake; they cannot be changed here")
        sets = ", ".join(f"{k} = %s" for k in changes)
        conn.execute(f"UPDATE applicants SET {sets} WHERE id = %s", [*(v.strip() if isinstance(v, str) else v for v in changes.values()), row["applicant_id"]])  # noqa: S608
    return view(conn, load(conn, app_id))


@router.post("/applications/{app_id}/intake/complete")
def complete_intake(app_id: UUID, body: CompleteIntake, staff: Staff = Depends(require_permission("application.intake")), conn=Depends(get_conn),
                    lakehouse=Depends(get_lakehouse), today: date = Depends(get_today)):
    rs = active_rule_set(conn)
    row = load(conn, app_id)
    require_intake_open(row)
    if row["version"] != body.expected_version:
        raise HTTPException(409, "The application changed since you opened it. Reload it and try again.")
    missing = missing_details(row)
    if missing:
        raise HTTPException(422, f"Complete the applicant's details first: {', '.join(missing)}")
    facts = {}
    if row["bank_relationship"] == "ETB":  # today's bank records decide the waivers, not those seen at enquiry time
        c = lakehouse.customer(row["customer_id"])
        if c is None:
            raise HTTPException(409, "The customer's bank record could not be found")
        facts = bank_facts(c)
        record = kyc.profile(conn, customer_id=row["customer_id"])
        if record is not None:  # the KYC record (with any updates since account opening) is the authority on whether KYC is current
            facts["kyc_status"], facts["kyc_current"] = record["kyc_status"], kyc.is_current(record)
    docs = indicative.documents(rs, indicative.EnquiryInput(
        bank_relationship=row["bank_relationship"], declared_credit_history=row["declared_credit_history"] or "UNKNOWN", date_of_birth=row["date_of_birth"],
        employment_type=row["employment_type"], declared_net_income=row["declared_monthly_net"] or Decimal(1), other_lender_emi=row["existing_monthly_emi"] or Decimal(0),
        requested_amount=row["requested_amount"], requested_tenure_months=row["requested_tenure_months"], as_of=today,
        kyc_current=bool(facts.get("kyc_current")), income_at_bob=bool(facts.get("income_at_bob")), is_staff=bool(row["is_staff"]),
        is_nri=row["residency_status"] == "NRI"))
    rule_set_id = conn.execute("SELECT id FROM policy_rule_sets WHERE scheme = %s AND status = 'ACTIVE'", (rs.scheme,)).fetchone()["id"]
    checklist = {"required": docs["required"], "waived": docs["waived"], "policy": rs.identity, "rule_set_id": str(rule_set_id),
                 "basis": {"bank_relationship": row["bank_relationship"], "employment_type": row["employment_type"], "kyc_status": facts.get("kyc_status"),
                           "kyc_current": bool(facts.get("kyc_current")), "income_at_bob": bool(facts.get("income_at_bob"))},
                 "frozen_at": datetime.now(timezone.utc).isoformat()}
    with staff_tx(conn, staff.user_id, staff.role):
        done = conn.execute("UPDATE applications SET documents_checklist = %s, intake_completed_by = %s, intake_completed_at = now() "
                            "WHERE id = %s AND version = %s AND status = 'INTAKE' AND intake_completed_at IS NULL RETURNING id",
                            (json.dumps(checklist), staff.user_id, app_id, body.expected_version)).fetchone()
        if done is None:
            raise HTTPException(409, "The application changed since you opened it. Reload it and try again.")
        conn.execute("SELECT app_transition(%s, 'DOCS_PENDING', %s, 'STAFF', %s, NULL)", (app_id, body.expected_version, staff.user_id))
    return view(conn, load(conn, app_id))


# ---------------------------------------------------------------- documents
@router.put("/applications/{app_id}/documents", status_code=201)
async def upload_document(app_id: UUID, request: Request, background: BackgroundTasks,
                          requirement: Literal["APPLICATION_FORM", "PHOTOGRAPH", "PAN_CARD", "ADDRESS_PROOF", "SALARY_SLIPS_3M", "ITR_1Y", "BUSINESS_PROOF",
                                               "PENSION_PROOF", "BANK_STATEMENT_6M", "SUPPORTING"],
                          doc_type: Literal["APPLICATION_FORM", "PHOTOGRAPH", "PAN_CARD", "AADHAAR_MASKED", "PASSPORT", "VOTER_ID", "SALARY_SLIP", "ITR",
                                            "BANK_STATEMENT", "BUSINESS_PROOF", "PENSION_ORDER", "EMPLOYMENT_PROOF", "OTHER"],
                          file_name: str = Query(min_length=1, max_length=200),
                          staff: Staff = Depends(require_permission("application.intake")), conn=Depends(get_conn),
                          storage=Depends(get_storage), deps=Depends(jobs.get_job_deps)):
    """The file is the raw request body (Content-Type: the file's type). The browser sends it with a plain fetch(); no multipart parsing."""
    if doc_type not in ACCEPTS[requirement]:
        raise HTTPException(422, f"{requirement} is satisfied by {sorted(ACCEPTS[requirement])}, not {doc_type}")
    if int(request.headers.get("content-length") or 0) > MAX_BYTES:
        raise HTTPException(413, f"Files are limited to {MAX_BYTES // (1024 * 1024)} MB")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"Files are limited to {MAX_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(422, "The file is empty")
    kind = sniff(bytes(data[:16]))
    if kind is None:
        raise HTTPException(415, "Only PDF, PNG and JPEG files are accepted")
    mime, ext = kind
    declared = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    name = file_name.strip().replace("\\", "/").rsplit("/", 1)[-1]
    if declared not in ("", "application/octet-stream", mime) or name.rsplit(".", 1)[-1].lower() not in EXTENSIONS[ext]:
        raise HTTPException(415, f"The file's contents are {mime}, which does not match its name or declared type")
    if requirement == "PHOTOGRAPH" and mime == "application/pdf":
        raise HTTPException(415, "The photograph must be a PNG or JPEG image")
    # the database and storage calls are blocking, so they run off the event loop
    saved = await run_in_threadpool(save_document, conn, storage, staff, app_id, requirement, doc_type, name, mime, ext, bytes(data))
    if deps.kick:  # start processing now; the scheduled drain picks up anything left
        background.add_task(jobs.drain, deps, 5)
    return saved


def save_document(conn, storage, staff: Staff, app_id: UUID, requirement: str, doc_type: str, name: str, mime: str, ext: str, data: bytes) -> dict:
    row = load(conn, app_id)
    if row["status"] not in UPLOAD_STATUSES:
        raise HTTPException(409, f"Documents cannot be added while the application is {row['status']}")
    if row["documents_checklist"] and requirement != "SUPPORTING" and requirement not in {r["code"] for r in row["documents_checklist"]["required"]}:
        raise HTTPException(422, f"{requirement} is not on this application's document checklist")
    doc_id, sha = uuid4(), hashlib.sha256(data).hexdigest()
    path = f"{app_id}/{doc_id}.{ext}"
    uploaded = False
    try:
        with staff_tx(conn, staff.user_id, staff.role):
            # counted only for a file that is really stored: in the same transaction as the insert, after every check, so a refused,
            # duplicate or failed upload uses up nothing. A per-account limit sits under the global one.
            for service, cap in (("upload", settings.upload_daily_cap), (f"upload:staff:{staff.user_id}", settings.upload_daily_cap_per_staff)):
                if not conn.execute("SELECT take_budget(%s, %s) AS ok", (service, cap)).fetchone()["ok"]:
                    raise HTTPException(429, f"The demo's daily upload limit ({cap}{' for this account' if ':' in service else ''}) has been reached; "
                                             "try again tomorrow (IST)")
            conn.execute("INSERT INTO documents (id, application_id, requirement, doc_type, storage_path, original_name, mime_type, size_bytes, sha256, uploaded_by) "
                         "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (doc_id, app_id, requirement, doc_type, path, name, mime, len(data), sha, staff.user_id))
            jobs.enqueue(conn, "process_document", {"document_id": str(doc_id)}, app_id, f"process:{doc_id}:1")
            storage.put(path, data, mime)  # inside the transaction: if storage fails, no record is left pointing at nothing
            uploaded = True
    except Exception:
        if uploaded:  # stored, but the record did not commit: remove the orphan
            storage.remove(path)
        raise
    return doc_view(conn.execute("SELECT * FROM documents WHERE id = %s", (doc_id,)).fetchone())


def load_doc(conn, app_id: UUID, doc_id: UUID) -> dict:
    d = conn.execute("SELECT * FROM documents WHERE id = %s AND application_id = %s", (doc_id, app_id)).fetchone()
    if d is None:
        raise HTTPException(404, "Document not found")
    return d


@router.get("/applications/{app_id}/documents/{doc_id}/url")
def document_url(app_id: UUID, doc_id: UUID, staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn),
                 storage=Depends(get_storage)):
    d = load_doc(conn, app_id, doc_id)
    url = storage.signed_url(d["storage_path"])
    with staff_tx(conn, staff.user_id, staff.role):  # who opened which customer document, and when
        conn.execute("SELECT audit_write('STAFF', %s, %s, 'DOCUMENT_VIEWED', 'documents', %s, %s, NULL, NULL, NULL)",
                     (staff.user_id, staff.role, str(doc_id), app_id))
    return {"url": url, "expires_in_seconds": 60, "mime_type": d["mime_type"], "original_name": d["original_name"]}


@router.post("/applications/{app_id}/documents/{doc_id}/withdraw")
def withdraw_document(app_id: UUID, doc_id: UUID, body: Withdraw, staff: Staff = Depends(require_permission("application.intake")), conn=Depends(get_conn)):
    load_doc(conn, app_id, doc_id)
    with staff_tx(conn, staff.user_id, staff.role):
        conn.execute("UPDATE documents SET status = 'WITHDRAWN', withdrawn_reason = %s, withdrawn_by = %s, withdrawn_at = now() WHERE id = %s",
                     (body.reason.strip(), staff.user_id, doc_id))
    return doc_view(load_doc(conn, app_id, doc_id))
