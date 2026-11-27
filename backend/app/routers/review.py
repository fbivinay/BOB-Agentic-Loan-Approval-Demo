"""Review and decision: from the Loan Officer's forward to a human decision.

  POST /applications/{id}/forward     Loan Officer: AI_ASSESSED -> FORWARDED, with an optional note for the reviewer
  GET  /review/queue                  Assistant / Branch Manager: FORWARDED, UNDER_REVIEW and RECONSIDERATION files (all, mine, unassigned)
  POST /applications/{id}/claim       Assistant / Branch Manager: FORWARDED or RECONSIDERATION -> UNDER_REVIEW, assigned to the caller
  POST /applications/{id}/refer-to-branch-manager
                                      Assistant Manager, on their own file under review: back to FORWARDED, for a Branch Manager only, with a note
  POST /applications/{id}/take-over   Branch Manager: take another reviewer's file, with a recorded reason
Four-eyes: whoever did processing work on a file (intake, uploads, corrections, discrepancies, attestation, forward) cannot claim, take over,
decide or release it (migrations 0015 and 0016, function processed_by).
  POST /applications/{id}/terms       Branch Manager, on their own file under review: adjust amount, rate or tenure inside the demo limits,
                                      with a reason (term_changes; the database checks the limits and the cap)
  POST /applications/{id}/decision    Assistant / Branch Manager, on their own file under review: APPROVED, DECLINED, or (Branch Manager)
                                      REFERRED for reconsideration. Approving records the approved terms the reviewer saw, in the same
                                      transaction. A decline, a referral or an override of the AI needs a written reason.
  GET  /applications/{id}/review      everything a reviewer needs, and which of these actions the caller may take now

The AI recommends; only these human actions decide. The database repeats every rule here (decisions_guard, term_changes_guard,
application_terms_guard, app_transition), records the AI snapshot and override flag itself, and writes the audit trail.
"""

import json
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import jobs
from app.auth import Staff, require_permission
from app.db import get_conn, staff_tx
from app.policy import finance
from app.routers.applications import load

router = APIRouter(tags=["review"])
REVIEW_STATUSES = ("FORWARDED", "UNDER_REVIEW", "RECONSIDERATION")
DEMO_LABEL = "Demo limits (DEMO_ASSUMPTION), not Bank of Baroda's authorization structure or policy."


class Versioned(BaseModel):
    expected_version: int


class Forward(Versioned):
    note: str | None = Field(default=None, max_length=1000, description="for the reviewer; recorded in the audit trail")


class Reasoned(Versioned):
    reason: str = Field(min_length=10, max_length=1000)


class Terms(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    rate_bp: int = Field(ge=1, le=6000)
    tenure_months: int = Field(ge=1, le=360)


class TermChange(Reasoned, Terms):
    pass


class Decision(Versioned):
    outcome: Literal["APPROVED", "DECLINED", "REFERRED"]
    reason: str | None = Field(default=None, max_length=1000)
    terms: Terms | None = Field(default=None, description="APPROVED only: the terms shown to the reviewer; they must still be the current terms")


# ---------------------------------------------------------------- helpers
def current_terms(conn, app_id) -> dict | None:
    """The terms a decision would approve: the latest Branch Manager adjustment, else the engine's eligible terms."""
    change = conn.execute("SELECT id, after_terms, changed_at FROM term_changes WHERE application_id = %s ORDER BY changed_at DESC, id DESC LIMIT 1",
                          (app_id,)).fetchone()
    if change:
        t = change["after_terms"]
        return {"amount": Decimal(str(t["amount"])), "rate_bp": int(t["rate_bp"]), "tenure_months": int(t["tenure_months"]), "source": "TERM_CHANGE",
                "term_change_id": change["id"]}
    e = conn.execute("SELECT amount, rate_bp, tenure_months FROM application_terms WHERE application_id = %s AND kind = 'ELIGIBLE' ORDER BY seq DESC LIMIT 1",
                     (app_id,)).fetchone()
    if e:
        return {"amount": e["amount"], "rate_bp": e["rate_bp"], "tenure_months": e["tenure_months"], "source": "ELIGIBLE", "term_change_id": None}
    return None


def terms_view(t: dict | None) -> dict | None:
    if t is None:
        return None
    return {"amount": f"{Decimal(t['amount']):.2f}", "rate_bp": t["rate_bp"], "rate_percent": f"{Decimal(t['rate_bp']) / 100:.2f}", "tenure_months": t["tenure_months"],
            "emi": str(finance.emi(Decimal(t["amount"]), t["rate_bp"], t["tenure_months"])), "source": t.get("source")}


def same_terms(a: dict, b: Terms) -> bool:
    return Decimal(a["amount"]) == b.amount and a["rate_bp"] == b.rate_bp and a["tenure_months"] == b.tenure_months


def check_version(a: dict, expected: int) -> None:
    if a["version"] != expected:
        raise HTTPException(409, "The application changed since you opened it. Reload it and try again.")


def require_own_review(a: dict, staff: Staff) -> None:
    if a["status"] != "UNDER_REVIEW":
        raise HTTPException(409, f"Only an application under review can be decided or have its terms adjusted (status is {a['status']}). Claim it first.")
    if a["assigned_to"] != staff.user_id:
        who = "another reviewer" if a["assigned_to"] else "nobody"
        raise HTTPException(409, f"This application is assigned to {who}. "
                                 + ("Take it over (with a reason) first." if "application.takeover" in staff.permissions else "Only the assigned reviewer can act on it."))


def processed(conn, app_id, staff: Staff) -> bool:
    """Four-eyes: anyone who did processing work on the file may not review, decide or release it. The database function is the one
    definition (migration 0016); the guards use it too."""
    return conn.execute("SELECT processed_by(%s, %s) AS p", (app_id, staff.user_id)).fetchone()["p"]


def actions_for(a: dict, staff: Staff, own: bool) -> dict:
    p, mine = staff.permissions, a["assigned_to"] == staff.user_id
    under = a["status"] == "UNDER_REVIEW"
    referred_elsewhere = a["status"] == "FORWARDED" and a.get("referred_to_role") not in (None, staff.role)
    return {"forward": "application.forward" in p and a["status"] == "AI_ASSESSED",
            "claim": "decision.make" in p and a["status"] in ("FORWARDED", "RECONSIDERATION") and not own and not referred_elsewhere,
            "take_over": "application.takeover" in p and a["status"] in REVIEW_STATUSES and a["assigned_to"] is not None and not mine and not own,
            "adjust_terms": "terms.adjust" in p and under and mine,
            "approve": "decision.make" in p and under and mine and not own, "decline": "decision.make" in p and under and mine and not own,
            "refer": "application.refer" in p and under and mine and not own,
            "refer_to_branch_manager": "application.escalate" in p and under and mine,
            "four_eyes": own}


# ---------------------------------------------------------------- forward and queue
@router.post("/applications/{app_id}/forward")
def forward(app_id: UUID, body: Forward, staff: Staff = Depends(require_permission("application.forward")), conn=Depends(get_conn)):
    a = load(conn, app_id)
    check_version(a, body.expected_version)
    if a["status"] != "AI_ASSESSED":
        raise HTTPException(409, f"Only an assessed application can be forwarded for review (status is {a['status']})")
    with staff_tx(conn, staff.user_id, staff.role):
        r = conn.execute("SELECT * FROM app_transition(%s, 'FORWARDED', %s, 'STAFF', %s, %s)",
                         (app_id, body.expected_version, staff.user_id, (body.note or "").strip() or None)).fetchone()
    return {"status": r["status"], "version": r["version"]}


@router.get("/review/queue")
def review_queue(scope: Literal["all", "mine", "unassigned"] = "all", status: Literal["FORWARDED", "UNDER_REVIEW", "RECONSIDERATION"] | None = None,
                 limit: int = Query(default=50, ge=1, le=200), staff: Staff = Depends(require_permission("decision.make")), conn=Depends(get_conn)):
    sql = ("SELECT ap.id, ap.reference, ap.status, ap.version, ap.requested_amount, ap.requested_tenure_months, ap.ai_recommendation, ap.assessed_at, "
           "ap.forwarded_at, ap.assigned_to, sp.full_name AS assigned_name, a.full_name, a.bank_relationship, "
           "ap.referred_to_role, ap.referral_note, rb.full_name AS referred_by_name, processed_by(ap.id, %s) AS processed_by_me, "
           "(SELECT amount FROM application_terms t WHERE t.application_id = ap.id AND t.kind = 'ELIGIBLE' ORDER BY seq DESC LIMIT 1) AS eligible_amount "
           "FROM applications ap JOIN applicants a ON a.id = ap.applicant_id LEFT JOIN staff_profiles sp ON sp.user_id = ap.assigned_to "
           "LEFT JOIN staff_profiles rb ON rb.user_id = ap.referred_by "
           "WHERE ap.status = ANY(%s)")
    params: list = [staff.user_id, [status] if status else list(REVIEW_STATUSES)]
    if scope == "mine":
        sql += " AND ap.assigned_to = %s"
        params.append(staff.user_id)
    elif scope == "unassigned":
        sql += " AND ap.assigned_to IS NULL"
    rows = conn.execute(sql + " ORDER BY ap.forwarded_at NULLS LAST, ap.reference LIMIT %s", (*params, limit)).fetchall()
    return {"applications": [{"id": str(r["id"]), "reference": r["reference"], "status": r["status"], "version": r["version"], "applicant": r["full_name"],
                              "relationship": r["bank_relationship"], "requested_amount": str(r["requested_amount"]), "requested_tenure_months": r["requested_tenure_months"],
                              "eligible_amount": str(r["eligible_amount"]) if r["eligible_amount"] is not None else None, "ai_recommendation": r["ai_recommendation"],
                              "forwarded_at": r["forwarded_at"].isoformat() if r["forwarded_at"] else None,
                              "assigned_to": str(r["assigned_to"]) if r["assigned_to"] else None, "assigned_name": r["assigned_name"],
                              "mine": r["assigned_to"] == staff.user_id,
                              "referral": {"to_role": r["referred_to_role"], "note": r["referral_note"], "by": r["referred_by_name"]} if r["referred_to_role"] else None,
                              "processed_by_me": r["processed_by_me"]} for r in rows],
            "label": "AI recommendation, human decides."}


@router.post("/applications/{app_id}/claim")
def claim(app_id: UUID, body: Versioned, staff: Staff = Depends(require_permission("decision.make")), conn=Depends(get_conn)):
    a = load(conn, app_id)
    check_version(a, body.expected_version)
    if a["status"] not in ("FORWARDED", "RECONSIDERATION"):
        raise HTTPException(409, f"Only a forwarded or referred application can be picked up (status is {a['status']})")
    if a["status"] == "FORWARDED" and a["assigned_to"] not in (None, staff.user_id):  # a referral (RECONSIDERATION) is back in the queue for any reviewer
        raise HTTPException(409, "This application is assigned to another reviewer" + (". Take it over (with a reason) instead." if "application.takeover" in staff.permissions else "."))
    with staff_tx(conn, staff.user_id, staff.role):
        r = conn.execute("SELECT * FROM app_claim(%s, %s, %s)", (app_id, staff.user_id, body.expected_version)).fetchone()
    return {"status": r["status"], "version": r["version"], "assigned_to": str(r["assigned_to"])}


@router.post("/applications/{app_id}/refer-to-branch-manager")
def refer_to_branch_manager(app_id: UUID, body: Reasoned, staff: Staff = Depends(require_permission("application.escalate")), conn=Depends(get_conn)):
    """Assistant Manager, on their own file under review: send it to the Branch Manager with a note (e.g. a change of terms the customer asked for)."""
    a = load(conn, app_id)
    check_version(a, body.expected_version)
    if a["status"] != "UNDER_REVIEW" or a["assigned_to"] != staff.user_id:
        raise HTTPException(409, "Only the reviewer of a file under review can refer it to the Branch Manager")
    with staff_tx(conn, staff.user_id, staff.role):
        r = conn.execute("SELECT * FROM app_refer_to_branch_manager(%s, %s, %s, %s)", (app_id, staff.user_id, body.expected_version, body.reason.strip())).fetchone()
    return {"status": r["status"], "version": r["version"], "referred_to_role": r["referred_to_role"]}


@router.post("/applications/{app_id}/take-over")
def take_over(app_id: UUID, body: Reasoned, staff: Staff = Depends(require_permission("application.takeover")), conn=Depends(get_conn)):
    a = load(conn, app_id)
    check_version(a, body.expected_version)
    if a["assigned_to"] == staff.user_id:
        raise HTTPException(409, "This application is already assigned to you")
    with staff_tx(conn, staff.user_id, staff.role):
        r = conn.execute("SELECT * FROM app_take_over(%s, %s, %s, %s)", (app_id, staff.user_id, body.expected_version, body.reason.strip())).fetchone()
    return {"status": r["status"], "version": r["version"], "assigned_to": str(r["assigned_to"])}


# ---------------------------------------------------------------- terms and decision
@router.post("/applications/{app_id}/terms", status_code=201)
def adjust_terms(app_id: UUID, body: TermChange, staff: Staff = Depends(require_permission("terms.adjust")), conn=Depends(get_conn)):
    a = load(conn, app_id)
    check_version(a, body.expected_version)
    require_own_review(a, staff)
    before = current_terms(conn, app_id)
    if before and same_terms(before, body):
        raise HTTPException(422, "These are already the current terms")
    after = {"amount": str(body.amount), "rate_bp": body.rate_bp, "tenure_months": body.tenure_months}
    before_json = {"amount": str(before["amount"]), "rate_bp": before["rate_bp"], "tenure_months": before["tenure_months"], "source": before["source"]} if before else {}
    with staff_tx(conn, staff.user_id, staff.role):
        conn.execute("INSERT INTO term_changes (application_id, application_version, changed_by, changed_by_role, reason, before_terms, after_terms) "
                     "VALUES (%s, %s, %s, %s, %s, %s, %s)", (app_id, body.expected_version, staff.user_id, staff.role, body.reason.strip(),
                                                             json.dumps(before_json), json.dumps(after)))
    return {"terms": terms_view(current_terms(conn, app_id)), "label": DEMO_LABEL}


@router.post("/applications/{app_id}/decision", status_code=201)
def decide(app_id: UUID, body: Decision, background: BackgroundTasks, staff: Staff = Depends(require_permission("decision.make")), conn=Depends(get_conn),
           deps=Depends(jobs.get_job_deps)):
    a = load(conn, app_id)
    check_version(a, body.expected_version)
    require_own_review(a, staff)
    if body.outcome == "REFERRED" and "application.refer" not in staff.permissions:
        raise HTTPException(403, "Only a Branch Manager may refer an application for reconsideration")
    reason = (body.reason or "").strip() or None
    terms = None
    if body.outcome == "APPROVED":
        terms = current_terms(conn, app_id)
        if terms is None:
            raise HTTPException(422, "There are no terms to approve: the engine found no eligible amount. A Branch Manager sets terms (with a reason) first.")
        if body.terms is None or not same_terms(terms, body.terms):
            raise HTTPException(409, "The terms changed since you opened the application. Reload, check the current terms and approve again.")
    elif body.terms is not None:
        raise HTTPException(422, "Terms are given only when approving")
    with staff_tx(conn, staff.user_id, staff.role):
        d = conn.execute("INSERT INTO decisions (application_id, application_version, outcome, decided_by, decided_by_role, reason, ai_recommendation_snapshot, is_override) "
                         "VALUES (%s, %s, %s, %s, %s, %s, '{}', false) RETURNING id, outcome, is_override, decided_by_role, ai_recommendation_snapshot",
                         (app_id, body.expected_version, body.outcome, staff.user_id, staff.role, reason)).fetchone()
        if terms:
            conn.execute("INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months, policy_rule_set_id, decision_id, term_change_id, created_by) "
                         "VALUES (%s, 'APPROVED', %s, %s, %s, %s, %s, %s, %s)",
                         (app_id, terms["amount"], terms["rate_bp"], terms["tenure_months"], a["policy_rule_set_id"], d["id"], terms["term_change_id"], staff.user_id))
            # the sanction pack follows the human approval, in the same transaction, so an approval is never left without one
            jobs.enqueue(conn, "sanction_pack", {"application_id": str(app_id)}, app_id, f"sanction:{app_id}:{d['id']}")
    if terms and deps.kick:
        background.add_task(jobs.drain, deps, 5)
    after = load(conn, app_id)
    return {"decision_id": str(d["id"]), "outcome": d["outcome"], "is_override": d["is_override"], "ai_recommendation": d["ai_recommendation_snapshot"]["recommendation"],
            "status": after["status"], "version": after["version"], "approved_terms": terms_view(terms)}


# ---------------------------------------------------------------- review view
@router.get("/applications/{app_id}/review")
def review_view(app_id: UUID, staff: Staff = Depends(require_permission("application.view")), conn=Depends(get_conn)):
    a = load(conn, app_id)
    run = conn.execute("SELECT output FROM agent_runs WHERE id = %s", (a["ai_recommendation_run_id"],)).fetchone() if a["ai_recommendation_run_id"] else None
    out = (run or {}).get("output") or {}
    people = {r["user_id"]: r["full_name"] for r in conn.execute("SELECT user_id, full_name FROM staff_profiles").fetchall()}
    eligible = conn.execute("SELECT amount, rate_bp, tenure_months FROM application_terms WHERE application_id = %s AND kind = 'ELIGIBLE' ORDER BY seq DESC LIMIT 1",
                            (app_id,)).fetchone()
    approved = conn.execute("SELECT amount, rate_bp, tenure_months FROM application_terms WHERE application_id = %s AND kind = 'APPROVED' ORDER BY created_at DESC LIMIT 1",
                            (app_id,)).fetchone()
    changes = conn.execute("SELECT changed_by, changed_by_role, reason, before_terms, after_terms, changed_at FROM term_changes WHERE application_id = %s ORDER BY changed_at",
                           (app_id,)).fetchall()
    decisions = conn.execute("SELECT id, outcome, decided_by, decided_by_role, reason, ai_recommendation_snapshot, is_override, decided_at FROM decisions "
                             "WHERE application_id = %s ORDER BY decided_at", (app_id,)).fetchall()
    events = conn.execute("SELECT event_type, actor_type, actor_id, actor_role, before_state, after_state, reason, occurred_at FROM audit_events "
                          "WHERE application_id = %s AND event_type IN ('STATUS_TRANSITION', 'APPLICATION_TAKEOVER', 'REFERRED_TO_BRANCH_MANAGER', 'TERM_CHANGE', "
                          "'DECISION_RECORDED', 'DECISION_OVERRIDE') "
                          "ORDER BY id", (app_id,)).fetchall()
    limits = {r["key"]: str(r["value"]) for r in conn.execute("SELECT key, value FROM demo_config WHERE key LIKE 'terms.%'").fetchall()}
    return {
        "application": {"id": str(a["id"]), "reference": a["reference"], "status": a["status"], "version": a["version"], "applicant": a["full_name"],
                        "relationship": a["bank_relationship"], "employment_type": a["employment_type"], "requested_amount": str(a["requested_amount"]),
                        "requested_tenure_months": a["requested_tenure_months"], "assigned_to": str(a["assigned_to"]) if a["assigned_to"] else None,
                        "assigned_name": people.get(a["assigned_to"]), "forwarded_by": people.get(a["forwarded_by"]),
                        "forwarded_at": a["forwarded_at"].isoformat() if a["forwarded_at"] else None,
                        "referral": {"to_role": a["referred_to_role"], "note": a["referral_note"], "by": people.get(a["referred_by"]),
                                     "at": a["referred_at"].isoformat() if a["referred_at"] else None} if a["referred_to_role"] else None},
        "ai": {"recommendation": a["ai_recommendation"], "label": "AI recommendation, human decides.", "summary": (out.get("summary") or {}).get("text"),
               "points": (out.get("summary") or {}).get("points"), "reasons": (out.get("assessment") or {}).get("reasons"), "policy": out.get("policy"),
               "disclaimer": out.get("disclaimer")},
        "terms": {"eligible": terms_view(dict(eligible) | {"source": "ELIGIBLE"}) if eligible else None, "current": terms_view(current_terms(conn, app_id)),
                  "approved": terms_view(dict(approved) | {"source": "APPROVED"}) if approved else None, "limits": limits, "label": DEMO_LABEL},
        "term_changes": [{"by": people.get(c["changed_by"]), "role": c["changed_by_role"], "reason": c["reason"], "before": c["before_terms"], "after": c["after_terms"],
                          "at": c["changed_at"].isoformat()} for c in changes],
        "decisions": [{"id": str(d["id"]), "outcome": d["outcome"], "by": people.get(d["decided_by"]), "role": d["decided_by_role"], "reason": d["reason"],
                       "ai_recommendation": d["ai_recommendation_snapshot"].get("recommendation"), "is_override": d["is_override"], "at": d["decided_at"].isoformat()}
                      for d in decisions],
        "history": [{"event": e["event_type"], "actor_type": e["actor_type"], "by": people.get(e["actor_id"]), "role": e["actor_role"],
                     "from": (e["before_state"] or {}).get("status"), "to": (e["after_state"] or {}).get("status"), "reason": e["reason"],
                     "at": e["occurred_at"].isoformat()} for e in events],
        "actions": actions_for(a, staff, processed(conn, app_id, staff)),
    }
