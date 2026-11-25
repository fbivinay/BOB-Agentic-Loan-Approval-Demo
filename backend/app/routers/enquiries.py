"""Draft / Loan Enquiry. The Loan Officer looks up an existing customer (or registers a new one), gets indicative figures and the
missing-documents list, saves the draft, comes back to it, and only on an explicit, confirmed request turns it into an application.

A draft never triggers a bureau check, document verification, AI assessment or the approval workflow: it touches only the applicants and
enquiries tables, plus the application and its requested terms at the moment of conversion.
"""

import json
import re
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.auth import Staff, require_any_permission, require_permission
from app.db import get_conn, staff_tx
from app.lakehouse import get_lakehouse
from app.policy import indicative, ruleset

router = APIRouter(tags=["enquiries"])
SCHEME = "baroda_personal_loan"
EMPLOYMENT = Literal["SALARIED_PRIVATE", "SALARIED_GOVT", "SALARIED_PSU", "SELF_EMPLOYED_PROFESSIONAL", "SELF_EMPLOYED_BUSINESS", "PENSIONER"]
HISTORY = Literal["ESTABLISHED", "LIMITED", "NONE", "UNKNOWN"]


def get_today() -> date:
    return date.today()


class NewApplicant(BaseModel):
    full_name: str = Field(min_length=2, max_length=120, pattern=r"^[A-Za-z][A-Za-z .'-]+$")
    date_of_birth: date
    gender: Literal["M", "F", "O"] | None = None
    mobile: str = Field(pattern=r"^[6-9]\d{9}$")
    email: str | None = Field(default=None, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    pan: str | None = Field(default=None, pattern=r"^SYNP\d{7}[A-Z]$", description="synthetic PAN form only; real PANs are never accepted")
    residency_status: Literal["RESIDENT", "NRI"] = "RESIDENT"
    employment_type: EMPLOYMENT
    declared_net_income: Decimal = Field(gt=0, le=Decimal("100000000"), max_digits=12, decimal_places=2)


class EnquiryCreate(BaseModel):
    customer_id: str | None = Field(default=None, pattern=r"^BOB-C\d{4}$")
    applicant: NewApplicant | None = None
    declared_net_income: Decimal | None = Field(default=None, gt=0, le=Decimal("100000000"), max_digits=12, decimal_places=2)
    declared_credit_history: HISTORY = "UNKNOWN"
    other_lender_emi: Decimal = Field(default=Decimal(0), ge=0, le=Decimal("10000000"), max_digits=12, decimal_places=2)
    requested_amount: Decimal = Field(gt=0, le=Decimal("10000000"), max_digits=12, decimal_places=2)
    requested_tenure_months: int = Field(ge=1, le=360)
    documents_available: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def one_person(self):
        if (self.customer_id is None) == (self.applicant is None):
            raise ValueError("give either customer_id (existing customer) or applicant (new customer), not both")
        return self


class EnquiryUpdate(BaseModel):
    expected_version: int
    declared_net_income: Decimal | None = Field(default=None, gt=0, le=Decimal("100000000"), max_digits=12, decimal_places=2)
    declared_credit_history: HISTORY | None = None
    other_lender_emi: Decimal | None = Field(default=None, ge=0, le=Decimal("10000000"), max_digits=12, decimal_places=2)
    requested_amount: Decimal | None = Field(default=None, gt=0, le=Decimal("10000000"), max_digits=12, decimal_places=2)
    requested_tenure_months: int | None = Field(default=None, ge=1, le=360)
    documents_available: list[str] | None = Field(default=None, max_length=20)


class Convert(BaseModel):
    expected_version: int
    confirm: Literal[True]


class Close(BaseModel):
    expected_version: int
    reason: str = Field(min_length=5, max_length=500)


# ---------------------------------------------------------------- helpers
def active_rule_set(conn) -> ruleset.RuleSet:
    row = conn.execute("SELECT content FROM policy_rule_sets WHERE scheme = %s AND status = 'ACTIVE'", (SCHEME,)).fetchone()
    if row is None:
        raise HTTPException(503, "No active policy rule set. A Branch Manager must activate one (scripts/activate_rule_set.py).")
    return ruleset.validate(row["content"])


def bank_facts(c: dict) -> dict:
    """What the bank's own records say, kept with the draft so it can be recomputed later without another lookup."""
    se = str(c["employment_type"]).startswith("SELF_EMPLOYED")
    observed = c.get("avg_business_receipts_6m") if se else c.get("avg_salary_credit_6m")
    months = c.get("business_receipt_months_6m") if se else c.get("salary_credit_months_6m")
    return {"bob_emi_total": str(c.get("bob_emi_total") or 0), "observed_income": None if observed is None else str(observed),
            "observed_income_months": months, "kyc_current": c.get("kyc_status") == "VERIFIED", "income_at_bob": (months or 0) >= 3,
            "kyc_status": c.get("kyc_status"), "home_branch_name": c.get("home_branch_name"), "customer_since": str(c.get("customer_since"))}


def compute(rs, applicant: dict, inputs: dict, today: date) -> dict:
    f = inputs.get("bank_facts") or {}
    known = {d["code"] for d in rs.param("documents.required", "ETB_EST").value}
    unknown = set(inputs["documents_available"]) - known
    if unknown:
        raise HTTPException(422, f"unknown document codes: {sorted(unknown)}")
    est = indicative.estimate(rs, indicative.EnquiryInput(
        bank_relationship=applicant["bank_relationship"], declared_credit_history=inputs["declared_credit_history"],
        date_of_birth=applicant["date_of_birth"], employment_type=applicant["employment_type"], declared_net_income=Decimal(inputs["declared_net_income"]),
        other_lender_emi=Decimal(inputs["other_lender_emi"]), requested_amount=Decimal(inputs["requested_amount"]), requested_tenure_months=int(inputs["requested_tenure_months"]),
        as_of=today, bob_emi_total=Decimal(f["bob_emi_total"]) if f else None, observed_income=Decimal(f["observed_income"]) if f.get("observed_income") else None,
        observed_income_months=f.get("observed_income_months"), kyc_current=bool(f.get("kyc_current")), income_at_bob=bool(f.get("income_at_bob")),
        is_staff=bool(applicant["is_staff"]), is_nri=applicant["residency_status"] == "NRI", documents_available=frozenset(inputs["documents_available"])))
    est["inputs"] = inputs
    return est


def load(conn, enquiry_id: UUID) -> dict:
    row = conn.execute("SELECT e.*, a.customer_id, a.bank_relationship, a.full_name, a.date_of_birth, a.mobile, a.pan, a.employment_type, a.is_staff, a.residency_status "
                       "FROM enquiries e JOIN applicants a ON a.id = e.applicant_id WHERE e.id = %s", (enquiry_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Enquiry not found")
    return row


def view(row: dict) -> dict:
    return {"id": str(row["id"]), "reference": row["reference"], "status": row["status"], "version": row["version"],
            "applicant": {"customer_id": row["customer_id"], "relationship": row["bank_relationship"], "full_name": row["full_name"], "mobile": row["mobile"],
                          "employment_type": row["employment_type"]},
            "requested_amount": str(row["requested_amount"]), "requested_tenure_months": row["requested_tenure_months"],
            "indicative": row["indicative"], "documents": row["docs_checklist"],
            "converted_application_id": str(row["converted_application_id"]) if row["converted_application_id"] else None,
            "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat()}


# ---------------------------------------------------------------- endpoints
@router.get("/customers/search")
def search_customers(q: str = Query(min_length=3, max_length=60), staff: Staff = Depends(require_permission("enquiry.manage")), lakehouse=Depends(get_lakehouse)):
    try:
        return {"query": q, "results": [{k: (str(v) if isinstance(v, (date, Decimal)) else v) for k, v in r.items()} for r in lakehouse.search(q)]}
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.post("/enquiries", status_code=201)
def create_enquiry(body: EnquiryCreate, staff: Staff = Depends(require_permission("enquiry.manage")), conn=Depends(get_conn), lakehouse=Depends(get_lakehouse),
                   today: date = Depends(get_today)):
    rs = active_rule_set(conn)
    if body.customer_id:
        c = lakehouse.customer(body.customer_id)
        if c is None:
            raise HTTPException(404, "No existing customer with that ID. A new-to-bank person is registered as a new applicant.")
        applicant = {"bank_relationship": "ETB", "date_of_birth": c["date_of_birth"], "employment_type": c["employment_type"], "is_staff": bool(c["is_staff"]),
                     "residency_status": c["residency_status"]}
        income = body.declared_net_income or Decimal(str(c["declared_net_income"]))
        facts = bank_facts(c)
    else:
        a = body.applicant
        applicant = {"bank_relationship": "NTB", "date_of_birth": a.date_of_birth, "employment_type": a.employment_type, "is_staff": False,
                     "residency_status": a.residency_status}
        income, facts = a.declared_net_income, None
    if applicant["date_of_birth"] >= today:
        raise HTTPException(422, "date of birth must be in the past")
    inputs = {"declared_net_income": str(income), "declared_credit_history": body.declared_credit_history, "other_lender_emi": str(body.other_lender_emi),
              "requested_amount": str(body.requested_amount), "requested_tenure_months": body.requested_tenure_months,
              "documents_available": sorted(set(body.documents_available)), "bank_facts": facts}
    est = compute(rs, applicant, inputs, today)
    with staff_tx(conn, staff.user_id, staff.role):
        if body.customer_id:
            aid = conn.execute(
                "INSERT INTO applicants (customer_id, bank_relationship, full_name, date_of_birth, gender, mobile, email, pan, residency_status, is_staff, employment_type, "
                "declared_monthly_net, existing_monthly_emi, declared_credit_history, created_by) VALUES (%s, 'ETB', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (customer_id) DO UPDATE SET full_name = EXCLUDED.full_name, mobile = EXCLUDED.mobile, email = EXCLUDED.email, employment_type = EXCLUDED.employment_type, "
                "declared_monthly_net = EXCLUDED.declared_monthly_net, existing_monthly_emi = EXCLUDED.existing_monthly_emi, declared_credit_history = EXCLUDED.declared_credit_history "
                "RETURNING id",
                (c["customer_id"], c["full_name"], c["date_of_birth"], c["gender"], c["mobile"], c["email"], c["pan"], c["residency_status"], bool(c["is_staff"]),
                 c["employment_type"], income, body.other_lender_emi, body.declared_credit_history, staff.user_id)).fetchone()["id"]
        else:
            a = body.applicant
            aid = conn.execute(
                "INSERT INTO applicants (bank_relationship, full_name, date_of_birth, gender, mobile, email, pan, residency_status, employment_type, declared_monthly_net, "
                "existing_monthly_emi, declared_credit_history, created_by) VALUES ('NTB', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (a.full_name.strip(), a.date_of_birth, a.gender, a.mobile, a.email, a.pan, a.residency_status, a.employment_type, income, body.other_lender_emi,
                 body.declared_credit_history, staff.user_id)).fetchone()["id"]
        eid = conn.execute("INSERT INTO enquiries (applicant_id, requested_amount, requested_tenure_months, indicative, docs_checklist, created_by) "
                           "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                           (aid, body.requested_amount, body.requested_tenure_months, json.dumps(est), json.dumps(est["documents"]), staff.user_id)).fetchone()["id"]
    return view(load(conn, eid))


@router.get("/enquiries")
def list_enquiries(status: Literal["DRAFT", "CONVERTED", "CLOSED"] | None = None, q: str | None = Query(default=None, max_length=60),
                   staff: Staff = Depends(require_any_permission("enquiry.manage", "enquiry.view")), conn=Depends(get_conn)):
    sql = ("SELECT e.*, a.customer_id, a.bank_relationship, a.full_name, a.date_of_birth, a.mobile, a.pan, a.employment_type, a.is_staff, a.residency_status "
           "FROM enquiries e JOIN applicants a ON a.id = e.applicant_id WHERE true")
    params: list = []
    if status:
        sql += " AND e.status = %s"
        params.append(status)
    if q:
        s = q.strip()
        if re.fullmatch(r"(?i)ENQ-\d{4}-\d{6}", s):
            sql += " AND e.reference = %s"
            params.append(s.upper())
        elif re.fullmatch(r"[6-9]\d{9}", s):
            sql += " AND a.mobile = %s"
            params.append(s)
        else:
            sql += " AND (a.full_name ILIKE %s OR a.customer_id = %s)"
            params += [f"%{s}%", s.upper()]
    rows = conn.execute(sql + " ORDER BY e.updated_at DESC LIMIT 50", params).fetchall()
    return {"enquiries": [view(r) for r in rows]}


@router.get("/enquiries/{enquiry_id}")
def get_enquiry(enquiry_id: UUID, staff: Staff = Depends(require_any_permission("enquiry.manage", "enquiry.view")), conn=Depends(get_conn)):
    return view(load(conn, enquiry_id))


@router.patch("/enquiries/{enquiry_id}")
def update_enquiry(enquiry_id: UUID, body: EnquiryUpdate, staff: Staff = Depends(require_permission("enquiry.manage")), conn=Depends(get_conn),
                   today: date = Depends(get_today)):
    rs = active_rule_set(conn)
    row = load(conn, enquiry_id)
    if row["status"] != "DRAFT":
        raise HTTPException(409, f"Only a DRAFT enquiry can be changed (this one is {row['status']})")
    inputs = dict(row["indicative"]["inputs"])
    for k in ("declared_net_income", "declared_credit_history", "other_lender_emi", "requested_amount", "requested_tenure_months"):
        v = getattr(body, k)
        if v is not None:
            inputs[k] = str(v) if isinstance(v, Decimal) else v
    if body.documents_available is not None:
        inputs["documents_available"] = sorted(set(body.documents_available))
    est = compute(rs, row, inputs, today)
    with staff_tx(conn, staff.user_id, staff.role):
        done = conn.execute("UPDATE enquiries SET indicative = %s, docs_checklist = %s, requested_amount = %s, requested_tenure_months = %s, version = version + 1, updated_at = now() "
                            "WHERE id = %s AND version = %s AND status = 'DRAFT' RETURNING id",
                            (json.dumps(est), json.dumps(est["documents"]), Decimal(inputs["requested_amount"]), int(inputs["requested_tenure_months"]), enquiry_id,
                             body.expected_version)).fetchone()
        if done is None:
            raise HTTPException(409, "The enquiry changed since you opened it. Reload it and try again.")
        conn.execute("UPDATE applicants SET declared_monthly_net = %s, existing_monthly_emi = %s, declared_credit_history = %s WHERE id = %s",
                     (Decimal(inputs["declared_net_income"]), Decimal(inputs["other_lender_emi"]), inputs["declared_credit_history"], row["applicant_id"]))
    return view(load(conn, enquiry_id))


@router.post("/enquiries/{enquiry_id}/convert", status_code=201)
def convert_enquiry(enquiry_id: UUID, body: Convert, staff: Staff = Depends(require_permission("application.intake")), conn=Depends(get_conn)):
    """The customer has chosen to apply formally. Creates the application (INTAKE) and its requested terms; nothing else starts."""
    with staff_tx(conn, staff.user_id, staff.role):
        row = conn.execute("SELECT * FROM enquiries WHERE id = %s FOR UPDATE", (enquiry_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Enquiry not found")
        if row["status"] != "DRAFT":
            raise HTTPException(409, f"This enquiry is already {row['status']}")
        if row["version"] != body.expected_version:
            raise HTTPException(409, "The enquiry changed since you opened it. Reload it and try again.")
        ind = row["indicative"]
        rate_bp = int(round(Decimal(ind["rate_range_percent"][1]) * 100))  # requested at the top of the indicative range, confirmed at assessment
        app = conn.execute("INSERT INTO applications (enquiry_id, applicant_id, requested_amount, requested_tenure_months, created_by) VALUES (%s, %s, %s, %s, %s) "
                           "RETURNING id, reference, status, version", (enquiry_id, row["applicant_id"], row["requested_amount"], row["requested_tenure_months"], staff.user_id)).fetchone()
        conn.execute("INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months, created_by) VALUES (%s, 'REQUESTED', %s, %s, %s, %s)",
                     (app["id"], row["requested_amount"], rate_bp, row["requested_tenure_months"], staff.user_id))
        conn.execute("UPDATE enquiries SET status = 'CONVERTED', converted_application_id = %s, version = version + 1, updated_at = now() WHERE id = %s", (app["id"], enquiry_id))
    return {"application_id": str(app["id"]), "reference": app["reference"], "status": app["status"], "version": app["version"],
            "warnings": [] if ind.get("likely_eligible") else ["The indicative check suggested the customer may not be eligible; the formal assessment will decide."]}


@router.post("/enquiries/{enquiry_id}/close")
def close_enquiry(enquiry_id: UUID, body: Close, staff: Staff = Depends(require_permission("enquiry.manage")), conn=Depends(get_conn)):
    with staff_tx(conn, staff.user_id, staff.role):
        done = conn.execute("UPDATE enquiries SET status = 'CLOSED', version = version + 1, updated_at = now(), "
                            "indicative = jsonb_set(indicative, '{closed_reason}', to_jsonb(%s::text)) WHERE id = %s AND version = %s AND status = 'DRAFT' RETURNING id",
                            (body.reason, enquiry_id, body.expected_version)).fetchone()
        if done is None:
            raise HTTPException(409, "Only an unchanged DRAFT enquiry can be closed. Reload it and try again.")
    return view(load(conn, enquiry_id))
