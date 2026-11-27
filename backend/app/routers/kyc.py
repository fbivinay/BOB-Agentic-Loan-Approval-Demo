"""KYC records and the simulated credit profile (migration 0018).

  GET  /kyc                                    search KYC records (name, customer ID, PAN), newest first
  GET  /kyc/{id}                               one record: details, documents (current and superseded), version history, this month's credit profile
  GET  /kyc/{id}/documents/{doc_id}/url        a 60-second signed link to view or download one document (recorded in the audit trail)
  PUT  /kyc/{id}/documents                     staff add an updated KYC document (raw body: PDF, PNG or JPEG); the previous version is superseded
  POST /kyc/{id}/update                        staff record changed KYC details with a reason: a new version, verified today
  POST /credit-profile                         this month's DEMO / SIMULATED credit profile for a PAN, created on first use and the same afterwards
"""

import hashlib
from datetime import date
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app import credit_profile, kyc
from app.auth import Staff, require_permission
from app.db import get_conn, staff_tx
from app.lakehouse import get_lakehouse
from app.routers.enquiries import get_today
from app.storage import KYC_BUCKET, MAX_BYTES, get_kyc_storage, sniff, storage_for

router = APIRouter(tags=["kyc"])
PAN = r"^(SYNP\d{7}[A-Z]|[A-Z]{5}\d{4}[A-Z])$"


class KycUpdate(BaseModel):
    expected_version: int
    reason: str = Field(min_length=10, max_length=500)
    full_name: str | None = Field(default=None, min_length=2, max_length=120, pattern=r"^[A-Za-z][A-Za-z .'-]+$")
    date_of_birth: date | None = None
    gender: Literal["M", "F", "O"] | None = None
    aadhaar_last4: str | None = Field(default=None, pattern=r"^\d{4}$", description="last four digits only: Aadhaar is never stored in full")
    address_line: str | None = Field(default=None, min_length=5, max_length=200)
    city: str | None = Field(default=None, min_length=2, max_length=60)
    state: str | None = Field(default=None, min_length=2, max_length=60)
    pincode: str | None = Field(default=None, pattern=r"^[1-9]\d{5}$")
    verified_original: Literal[True] = Field(description="the staff member saw the original document")


class CreditCheck(BaseModel):
    pan: str = Field(pattern=PAN)


def _load(conn, profile_id: UUID) -> dict:
    p = kyc.profile(conn, profile_id=profile_id)
    if p is None:
        raise HTTPException(404, "KYC record not found")
    return p


@router.get("/kyc")
def search(q: str | None = Query(default=None, max_length=60), limit: int = Query(default=50, ge=1, le=200),
           staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn)):
    sql, params = "SELECT * FROM kyc_profiles", []
    if q and q.strip():
        s = q.strip()
        sql += " WHERE full_name ILIKE %s OR customer_id = %s OR pan = %s"
        params = [f"%{s}%", s.upper(), s.upper()]
    rows = conn.execute(sql + " ORDER BY updated_at DESC, customer_id LIMIT %s", (*params, limit)).fetchall()
    total = conn.execute("SELECT count(*) AS n FROM kyc_profiles").fetchone()["n"]
    return {"total": total, "profiles": [{"id": str(r["id"]), "customer_id": r["customer_id"], "pan": r["pan"], "full_name": r["full_name"], "city": r["city"],
                                          "kyc_status": r["kyc_status"], "verified_on": r["verified_on"].isoformat() if r["verified_on"] else None,
                                          "source": r["source"], "version": r["version"]} for r in rows]}


@router.get("/kyc/{profile_id}")
def get_profile(profile_id: UUID, staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn), today: date = Depends(get_today)):
    p = _load(conn, profile_id)
    return kyc.view(conn, p) | {"credit_profile": credit_profile.current(conn, p["pan"], today), "credit_label": credit_profile.LABEL}


@router.get("/kyc/{profile_id}/documents/{doc_id}/url")
def document_url(profile_id: UUID, doc_id: UUID, staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn)):
    d = conn.execute("SELECT * FROM kyc_documents WHERE id = %s AND profile_id = %s", (doc_id, profile_id)).fetchone()
    if d is None:
        raise HTTPException(404, "Document not found")
    url = storage_for(d["bucket"]).signed_url(d["storage_path"])
    with staff_tx(conn, staff.user_id, staff.role):
        conn.execute("SELECT audit_write('STAFF', %s, %s, 'KYC_DOCUMENT_VIEWED', 'kyc_documents', %s, NULL, NULL, NULL, NULL)", (staff.user_id, staff.role, str(doc_id)))
    return {"url": url, "expires_in_seconds": 60, "mime_type": d["mime_type"], "original_name": d["original_name"]}


@router.put("/kyc/{profile_id}/documents", status_code=201)
async def add_document(profile_id: UUID, request: Request, doc_type: Literal["PAN_CARD", "AADHAAR_MASKED", "ADDRESS_PROOF", "PASSPORT", "VOTER_ID"],
                       file_name: str = Query(min_length=1, max_length=200), staff: Staff = Depends(require_permission("application.intake")),
                       conn=Depends(get_conn), storage=Depends(get_kyc_storage)):
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"Files are limited to {MAX_BYTES // (1024 * 1024)} MB")
    kind = sniff(bytes(data[:16])) if data else None
    if kind is None:
        raise HTTPException(415, "Only PDF, PNG and JPEG files are accepted")
    mime, ext = kind

    def save():
        p = _load(conn, profile_id)
        path = f"{p['id']}/{uuid4()}.{ext}"
        with staff_tx(conn, staff.user_id, staff.role):
            storage.put(path, bytes(data), mime)
            d = kyc.add_document(conn, p["id"], doc_type, bucket=KYC_BUCKET, path=path, name=file_name.replace("\\", "/").rsplit("/", 1)[-1], mime=mime,
                                 sha256=hashlib.sha256(data).hexdigest(), source="KYC_UPDATE", staff_id=staff.user_id)
            conn.execute("SELECT audit_write('STAFF', %s, %s, 'KYC_DOCUMENT_ADDED', 'kyc_documents', %s, NULL, NULL, %s, NULL)",
                         (staff.user_id, staff.role, str(d["id"]), f'{{"doc_type": "{doc_type}", "version": {d["version"]}}}'))
        return kyc.doc_view(d)

    return await run_in_threadpool(save)


@router.post("/kyc/{profile_id}/update")
def update_profile(profile_id: UUID, body: KycUpdate, staff: Staff = Depends(require_permission("application.intake")), conn=Depends(get_conn),
                   today: date = Depends(get_today)):
    p = _load(conn, profile_id)
    if p["version"] != body.expected_version:
        raise HTTPException(409, "The KYC record changed since you opened it. Reload it and try again.")
    changes = body.model_dump(exclude={"expected_version", "reason", "verified_original"}, exclude_none=True)
    try:
        p2 = kyc.record_update(conn, p, changes, body.reason.strip(), staff, today)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return kyc.view(conn, p2)


@router.post("/credit-profile")
def check_credit(body: CreditCheck, staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn), lakehouse=Depends(get_lakehouse),
                 today: date = Depends(get_today)):
    """Recheck a PAN: the same profile all month. A synthetic customer's profile reflects their synthetic bureau record."""
    existing = credit_profile.current(conn, body.pan, today)
    if existing:
        return existing
    bureau = lakehouse.credit_record(pan=body.pan)
    return credit_profile.get_or_create(conn, body.pan, today, bureau)
