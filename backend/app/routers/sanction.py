"""Sanction pack and email status.

  GET  /applications/{id}/sanction-pack                  the three documents, the sanction status and the approval email's status
  GET  /applications/{id}/sanction-pack/{kind}/url       a 60-second signed URL (audited as SANCTION_DOCUMENT_VIEWED)
  GET  /applications/{id}/emails?refresh=true             outbox rows and their events; refresh polls Resend for delivery (local dev)
  POST /applications/{id}/emails/{email_id}/resend        Loan Officer, after a bounce or permanent failure: optionally correct the
                                                          applicant's email, give a reason, and queue the email again (audited)
  POST /applications/{id}/sanction-pack/retry             Loan Officer: queue pack generation again if it failed (only while APPROVED)
  POST /webhooks/resend                                   Resend delivery events, Svix-signed; off unless RESEND_WEBHOOK_SECRET is set

Documents are produced by the sanction_pack job after a human approval; nothing here creates or changes them.
"""

import json
from typing import Literal
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import jobs, outbox
from app.auth import Staff, require_permission
from app.config import settings
from app.db import get_conn, staff_tx
from app.routers.applications import load
from app.storage import get_generated_storage

router = APIRouter(tags=["sanction"])


class Resend(BaseModel):
    reason: str = Field(min_length=10, max_length=500)
    email: str | None = Field(default=None, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", description="corrected applicant email, if the address was wrong")


class Retry(BaseModel):
    expected_version: int


def _email_view(conn, e: dict) -> dict:
    events = conn.execute("SELECT event_type, occurred_at, raw FROM email_events WHERE email_id = %s ORDER BY id", (e["id"],)).fetchall()
    return {"id": str(e["id"]), "kind": e["kind"], "status": e["status"], "to_address": e["to_address"], "subject": e["subject"], "attempts": e["attempts"],
            "last_error": e["last_error"], "provider_message_id": e["provider_message_id"], "updated_at": e["updated_at"].isoformat(),
            "attachments": len(e["attachment_ids"]), "routing": "Demo routing: sent to a test inbox, never to the synthetic customer's address",
            "events": [{"event": x["event_type"], "at": x["occurred_at"].isoformat()} for x in events],
            "can_resend": e["status"] in ("BOUNCED", "FAILED")}


@router.get("/applications/{app_id}/sanction-pack")
def sanction_pack(app_id: UUID, staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn)):
    a = load(conn, app_id)
    docs = conn.execute("SELECT g.id, g.kind, g.sha256, g.created_at, t.amount, t.rate_bp, t.tenure_months FROM generated_documents g "
                        "JOIN application_terms t ON t.id = g.terms_id WHERE g.application_id = %s ORDER BY g.kind", (app_id,)).fetchall()
    job = conn.execute("SELECT status, attempts, last_error FROM jobs WHERE application_id = %s AND kind = 'sanction_pack' ORDER BY created_at DESC LIMIT 1",
                       (app_id,)).fetchone()
    email = conn.execute("SELECT * FROM email_outbox WHERE application_id = %s AND kind = 'APPROVAL'", (app_id,)).fetchone()
    return {"status": a["status"], "version": a["version"],
            "state": "ISSUED" if len(docs) == 3 else "PENDING" if job and job["status"] in ("QUEUED", "RUNNING") else "FAILED" if job and job["status"] == "FAILED"
            else "NOT_APPLICABLE",
            "job": dict(job) if job else None,
            "documents": [{"id": str(d["id"]), "kind": d["kind"], "sha256": d["sha256"], "created_at": d["created_at"].isoformat(),
                           "terms": {"amount": str(d["amount"]), "rate_bp": d["rate_bp"], "tenure_months": d["tenure_months"]}} for d in docs],
            "approval_email": _email_view(conn, email) if email else None,
            "label": "Demo documents from synthetic data and demo parameters. Not issued by Bank of Baroda and not a loan offer."}


@router.get("/applications/{app_id}/sanction-pack/{kind}/url")
def sanction_document_url(app_id: UUID, kind: Literal["SANCTION_LETTER", "KFS", "EMI_SCHEDULE"], staff: Staff = Depends(require_permission("application.view")),
                          conn=Depends(get_conn), storage=Depends(get_generated_storage)):
    d = conn.execute("SELECT id, storage_path FROM generated_documents WHERE application_id = %s AND kind = %s", (app_id, kind)).fetchone()
    if d is None:
        raise HTTPException(404, "This document has not been generated")
    url = storage.signed_url(d["storage_path"])
    with staff_tx(conn, staff.user_id, staff.role):
        conn.execute("SELECT audit_write('STAFF', %s, %s, 'SANCTION_DOCUMENT_VIEWED', 'generated_documents', %s, %s, NULL, NULL, NULL)",
                     (staff.user_id, staff.role, str(d["id"]), app_id))
    return {"url": url, "expires_in_seconds": 60, "kind": kind}


@router.post("/applications/{app_id}/sanction-pack/retry", status_code=202)
def retry_pack(app_id: UUID, body: Retry, background: BackgroundTasks, staff: Staff = Depends(require_permission("application.forward")),
               conn=Depends(get_conn), deps=Depends(jobs.get_job_deps)):
    a = load(conn, app_id)
    if a["version"] != body.expected_version:
        raise HTTPException(409, "The application changed since you opened it. Reload it and try again.")
    if a["status"] != "APPROVED":
        raise HTTPException(409, f"The sanction pack is generated for an approved application (status is {a['status']})")
    if conn.execute("SELECT 1 FROM jobs WHERE application_id = %s AND kind = 'sanction_pack' AND status IN ('QUEUED', 'RUNNING')", (app_id,)).fetchone():
        raise HTTPException(409, "Generation is already queued or running")
    with staff_tx(conn, staff.user_id, staff.role):
        jobs.enqueue(conn, "sanction_pack", {"application_id": str(app_id)}, app_id, f"sanction:{app_id}:{uuid4()}")
    if deps.kick:
        background.add_task(jobs.drain, deps, 3)
    return {"queued": True}


@router.get("/applications/{app_id}/emails")
def emails(app_id: UUID, refresh: bool = False, staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn),
           deps=Depends(jobs.get_job_deps)):
    load(conn, app_id)
    rows = conn.execute("SELECT * FROM email_outbox WHERE application_id = %s ORDER BY created_at", (app_id,)).fetchall()
    note = None
    if refresh and deps.resend is not None:
        for e in rows:
            try:
                outbox.refresh(conn, deps.resend, e["id"])
            except outbox.ResendError as err:  # a failed poll leaves the stored status as it was
                note = ("The Resend API key is send-only, so delivery status cannot be polled; configure the webhook (RESEND_WEBHOOK_SECRET) "
                        "or use a full-access key" if err.status == 401 else f"Delivery status could not be refreshed (Resend {err.status})")
                break
            except httpx.HTTPError as err:
                note = f"Delivery status could not be refreshed ({type(err).__name__})"
                break
        rows = conn.execute("SELECT * FROM email_outbox WHERE application_id = %s ORDER BY created_at", (app_id,)).fetchall()
    return {"emails": [_email_view(conn, e) for e in rows], "note": note}


@router.post("/applications/{app_id}/emails/{email_id}/resend", status_code=202)
def resend(app_id: UUID, email_id: UUID, body: Resend, background: BackgroundTasks, staff: Staff = Depends(require_permission("application.forward")),
           conn=Depends(get_conn), deps=Depends(jobs.get_job_deps)):
    a = load(conn, app_id)
    e = conn.execute("SELECT * FROM email_outbox WHERE id = %s AND application_id = %s", (email_id, app_id)).fetchone()
    if e is None:
        raise HTTPException(404, "Email not found")
    if e["status"] not in ("BOUNCED", "FAILED"):
        raise HTTPException(409, f"Only a bounced or failed email can be sent again (status is {e['status']})"
                                 + ("; a complaint means the recipient marked it as spam, so it is not resent." if e["status"] == "COMPLAINED" else ""))
    n = conn.execute("SELECT count(*) AS n FROM audit_events WHERE entity_id = %s AND event_type = 'EMAIL_RESENT'", (str(email_id),)).fetchone()["n"] + 1
    with staff_tx(conn, staff.user_id, staff.role):
        if body.email:  # the change is audited by the applicants trigger
            conn.execute("UPDATE applicants SET email = %s WHERE id = %s", (body.email.strip(), a["applicant_id"]))
        conn.execute("UPDATE email_outbox SET status = 'QUEUED', attempts = 0, last_error = NULL, provider_message_id = NULL, idempotency_key = %s, "
                     "to_address = %s, updated_at = now() WHERE id = %s", (f"{email_id}:resend:{n}", outbox.route(deps.email_redirect_to), email_id))
        outbox.event(conn, email_id, "RESEND_REQUESTED", {"by": str(staff.user_id), "reason": body.reason.strip(), "address_corrected": bool(body.email)})
        conn.execute("SELECT audit_write('STAFF', %s, %s, 'EMAIL_RESENT', 'email_outbox', %s, %s, %s, %s, %s)",
                     (staff.user_id, staff.role, str(email_id), app_id, json.dumps({"status": e["status"], "last_error": e["last_error"]}),
                      json.dumps({"status": "QUEUED", "address_corrected": bool(body.email)}), body.reason.strip()))
        jobs.enqueue(conn, "send_email", {"email_id": str(email_id)}, app_id, f"email:{email_id}:resend:{n}")
    if deps.kick:
        background.add_task(jobs.drain, deps, 3)
    return {"queued": True}


@router.post("/webhooks/resend")
async def resend_webhook(request: Request, conn=Depends(get_conn)):
    if not settings.resend_webhook_secret:
        raise HTTPException(503, "The Resend webhook is not configured; delivery status is polled instead")
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not outbox.verify_webhook(settings.resend_webhook_secret, headers, body):
        raise HTTPException(401, "Invalid signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Body is not JSON") from None
    return {"result": outbox.apply_webhook(conn, headers["svix-id"], payload)}
