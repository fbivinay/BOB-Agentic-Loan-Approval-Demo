"""Simulated disbursement: no real banking transaction, ever (the database forces is_simulated).

  GET  /applications/{id}/disbursement             status, KFS validity, acceptance, checklist, release details, confirmation email, actions
  POST /applications/{id}/disbursement/acceptance  Loan Officer (disbursement.track): the customer accepted the sanction and the KFS at the
                                                   branch. After the KFS validity period a reason is required (the bank re-confirmed the terms).
  POST /applications/{id}/disbursement/checklist   Loan Officer: KFS acknowledged, loan agreement executed, repayment mandate registered,
                                                   original KYC verified. All four confirmed -> CHECKLIST_COMPLETE.
  POST /applications/{id}/disbursement/release     Assistant / Branch Manager (disbursement.release, a demo permission): a different person
                                                   from the one who recorded the acceptance or completed the checklist. Records a simulated
                                                   reference, a loan account number, the first EMI date and the net amount credited; the
                                                   application moves to DISBURSED; the confirmation email is queued, all in one transaction.

Release and tracking permissions are CONFIGURABLE demo permissions, not Bank of Baroda's authorization structure.
"""

import json
import secrets
import string
from datetime import date, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app import jobs, outbox, sanction
from app.auth import Staff, require_permission
from app.db import get_conn, staff_tx
from app.policy import finance
from app.routers.applications import load
from app.routers.enquiries import get_today

router = APIRouter(tags=["disbursement"])
BANNER = "SIMULATED DISBURSEMENT - no money moves and no real account is credited. Demo of the workflow only."
PERMISSION_LABEL = "Demo permission, not Bank of Baroda's actual authorization structure."
ITEMS = {"kfs_acknowledged": "Customer signed the KFS acknowledgement", "loan_agreement_executed": "Loan agreement executed, with the KFS as its summary box",
         "repayment_mandate_registered": "Repayment mandate (NACH / standing instruction) registered for the EMI",
         "original_kyc_verified": "Original KYC documents verified at the branch"}


class Acceptance(BaseModel):
    expected_version: int
    late_reason: str | None = Field(default=None, min_length=10, max_length=500, description="required after the KFS validity period")


class Checklist(BaseModel):
    expected_version: int
    items: dict[Literal["kfs_acknowledged", "loan_agreement_executed", "repayment_mandate_registered", "original_kyc_verified"], bool]
    note: str | None = Field(default=None, max_length=500)


class Release(BaseModel):
    expected_version: int
    confirm: Literal[True] = Field(description="the reviewer confirms this is a simulated release")


def _row(conn, app_id) -> dict | None:
    return conn.execute("SELECT * FROM disbursements WHERE application_id = %s", (app_id,)).fetchone()


def _processed(conn, app_id, user_id) -> bool:
    """Four-eyes (migration 0016): the database's one definition of who did processing work on the application."""
    return conn.execute("SELECT processed_by(%s, %s) AS p", (app_id, user_id)).fetchone()["p"]


def _kfs_valid_until(conn, app_id) -> date | None:
    k = conn.execute("SELECT created_at FROM generated_documents WHERE application_id = %s AND kind = 'KFS'", (app_id,)).fetchone()
    return sanction.working_days_after(k["created_at"].astimezone(timezone.utc).date(), sanction.KFS_VALIDITY_WORKING_DAYS) if k else None


def _check(a: dict, expected: int, status: str = "SANCTIONED") -> None:
    if a["version"] != expected:
        raise HTTPException(409, "The application changed since you opened it. Reload it and try again.")
    if a["status"] != status:
        raise HTTPException(409, f"Disbursement steps apply to a {status.lower()} application (status is {a['status']})")


def _reference() -> str:
    return "SIM-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))


def _loan_account() -> str:
    return "SIMLN" + "".join(secrets.choice(string.digits) for _ in range(10))


@router.get("/applications/{app_id}/disbursement")
def view(app_id: UUID, staff: Staff = Depends(require_permission("disbursement.view")), conn=Depends(get_conn), today: date = Depends(get_today)):
    a = load(conn, app_id)
    d = _row(conn, app_id)
    people = {r["user_id"]: r["full_name"] for r in conn.execute("SELECT user_id, full_name FROM staff_profiles").fetchall()}
    valid = _kfs_valid_until(conn, app_id)
    preview = None
    if a["status"] in ("SANCTIONED", "DISBURSED"):
        pd = sanction.pack_data(conn, app_id, (d["released_at"].date() if d and d["released_at"] else today))
        f = pd["figures"]
        preview = {"approved_amount": str(pd["terms"]["amount"]), "upfront_charges": str(f["upfront_charges"]), "net_to_credit": str(f["net_disbursed"]),
                   "emi": str(f["emi"]), "tenure_months": f["tenure_months"], "apr_percent": str(f["apr_percent"]),
                   "first_emi_date": (d["first_emi_date"] if d and d["first_emi_date"] else f["first_instalment_date"]).isoformat()}
    email = conn.execute("SELECT status, to_address, last_error, updated_at FROM email_outbox WHERE application_id = %s AND kind = 'DISBURSEMENT_CONFIRMATION'",
                         (app_id,)).fetchone()
    p, mine = staff.permissions, staff.user_id
    status = d["status"] if d else None
    return {
        "banner": BANNER, "application": {"id": str(a["id"]), "reference": a["reference"], "status": a["status"], "version": a["version"]},
        "kfs_valid_until": valid.isoformat() if valid else None, "kfs_expired": bool(valid and today > valid), "figures": preview,
        "disbursement": None if d is None else {
            "status": d["status"], "is_simulated": d["is_simulated"],
            "acceptance": {"recorded_at": d["acceptance_recorded_at"].isoformat() if d["acceptance_recorded_at"] else None,
                           "by": people.get(d["acceptance_recorded_by"]), "late_reason": d["late_acceptance_reason"]},
            "checklist": [{"item": k, "label": v, "done": bool((d["checklist"] or {}).get(k))} for k, v in ITEMS.items()],
            "checklist_note": (d["checklist"] or {}).get("note"), "checklist_completed_by": people.get(d["checklist_completed_by"]),
            "released": None if d["status"] != "RELEASED" else {
                "simulated_reference": d["simulated_reference"], "loan_account_number": d["loan_account_number"], "first_emi_date": d["first_emi_date"].isoformat(),
                "net_amount": str(d["net_amount"]), "by": people.get(d["released_by"]), "role": d["released_by_role"], "at": d["released_at"].isoformat()}},
        "confirmation_email": None if email is None else {"status": email["status"], "to_address": email["to_address"], "last_error": email["last_error"]},
        "actions": {
            "record_acceptance": "disbursement.track" in p and a["status"] == "SANCTIONED" and not (d and d["acceptance_recorded_at"]),
            "update_checklist": "disbursement.track" in p and a["status"] == "SANCTIONED" and bool(d and d["acceptance_recorded_at"]) and status != "RELEASED",
            "release": "disbursement.release" in p and status == "CHECKLIST_COMPLETE"
                       and mine not in (d["acceptance_recorded_by"], d["checklist_completed_by"], *d["checklist_contributors"])
                       and not _processed(conn, app_id, mine)},  # four-eyes: not anyone who processed it
        "permission_label": PERMISSION_LABEL,
    }


@router.post("/applications/{app_id}/disbursement/acceptance")
def record_acceptance(app_id: UUID, body: Acceptance, staff: Staff = Depends(require_permission("disbursement.track")), conn=Depends(get_conn),
                      today: date = Depends(get_today)):
    a = load(conn, app_id)
    _check(a, body.expected_version)
    d = _row(conn, app_id)
    if d and d["acceptance_recorded_at"]:
        raise HTTPException(409, "The customer's acceptance is already recorded")
    valid = _kfs_valid_until(conn, app_id)
    late = bool(valid and today > valid)
    if late and not body.late_reason:
        raise HTTPException(422, f"The KFS was valid until {valid:%d %b %Y}. Record how the bank re-confirmed the terms with the customer (a reason) to accept now.")
    with staff_tx(conn, staff.user_id, staff.role):
        if d is None:
            d = conn.execute("INSERT INTO disbursements (application_id) VALUES (%s) RETURNING *", (app_id,)).fetchone()
        conn.execute("UPDATE disbursements SET acceptance_recorded_at = now(), acceptance_recorded_by = %s, late_acceptance_reason = %s WHERE id = %s",
                     (staff.user_id, body.late_reason.strip() if late and body.late_reason else None, d["id"]))
    return {"recorded": True, "late": late}


@router.post("/applications/{app_id}/disbursement/checklist")
def update_checklist(app_id: UUID, body: Checklist, staff: Staff = Depends(require_permission("disbursement.track")), conn=Depends(get_conn)):
    a = load(conn, app_id)
    _check(a, body.expected_version)
    d = _row(conn, app_id)
    if d is None or d["acceptance_recorded_at"] is None:
        raise HTTPException(409, "Record the customer's acceptance first")
    items = {**{k: bool((d["checklist"] or {}).get(k)) for k in ITEMS}, **body.items}
    complete = all(items.values())
    # whoever completed it stays the completer while it stays complete (the database enforces this too); every editor is recorded as a
    # contributor, and no contributor may release
    completer = (d["checklist_completed_by"] if d["status"] == "CHECKLIST_COMPLETE" else staff.user_id) if complete else None
    with staff_tx(conn, staff.user_id, staff.role):
        conn.execute("UPDATE disbursements SET checklist = %s, status = %s, checklist_completed_by = %s, "
                     "checklist_contributors = array_append(checklist_contributors, %s) WHERE id = %s",
                     (json.dumps({**items, "note": (body.note or "").strip() or None}), "CHECKLIST_COMPLETE" if complete else "PENDING",
                      completer, staff.user_id, d["id"]))
    return {"status": "CHECKLIST_COMPLETE" if complete else "PENDING", "items": items}


@router.post("/applications/{app_id}/disbursement/release", status_code=201)
def release(app_id: UUID, body: Release, background: BackgroundTasks, staff: Staff = Depends(require_permission("disbursement.release")), conn=Depends(get_conn),
            today: date = Depends(get_today), deps=Depends(jobs.get_job_deps)):
    a = load(conn, app_id)
    _check(a, body.expected_version)
    d = _row(conn, app_id)
    if d is None or d["status"] != "CHECKLIST_COMPLETE":
        raise HTTPException(409, "Complete the customer's acceptance and the pre-disbursement checklist first")
    if staff.user_id in (d["acceptance_recorded_by"], d["checklist_completed_by"], *d["checklist_contributors"]):
        raise HTTPException(409, "Maker-checker: you recorded the acceptance or worked on the checklist, so another authorised person releases it")
    if _processed(conn, app_id, staff.user_id):
        raise HTTPException(403, "four-eyes: you processed this application, so another authorised manager releases it")
    pd = sanction.pack_data(conn, app_id, today)
    first_emi = finance.add_months(today, 1)
    with staff_tx(conn, staff.user_id, staff.role):
        r = conn.execute("UPDATE disbursements SET status = 'RELEASED', released_by = %s, simulated_reference = %s, loan_account_number = %s, first_emi_date = %s, "
                         "net_amount = %s WHERE id = %s RETURNING *",
                         (staff.user_id, _reference(), _loan_account(), first_emi, pd["figures"]["net_disbursed"], d["id"])).fetchone()
        conn.execute("SELECT app_transition(%s, 'DISBURSED', %s, 'STAFF', %s, %s)",
                     (app_id, body.expected_version, staff.user_id, f"Simulated disbursement {r['simulated_reference']} released"))
        outbox.queue(conn, app_id, "DISBURSEMENT_CONFIRMATION", f"Your personal loan is disbursed (simulated) - {a['reference']} (demo)", [], deps.email_redirect_to)
    if deps.kick:
        background.add_task(jobs.drain, deps, 3)
    return {"status": "DISBURSED", "banner": BANNER, "simulated_reference": r["simulated_reference"], "loan_account_number": r["loan_account_number"],
            "first_emi_date": first_emi.isoformat(), "net_amount": str(r["net_amount"]), "released_at": r["released_at"].isoformat()}
