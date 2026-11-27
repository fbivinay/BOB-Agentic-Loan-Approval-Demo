"""Phase 9 against real Postgres with the real migrations: forward, review queue, claim, take-over, term adjustment and human decisions.
Applications reach AI_ASSESSED through the real attestation and assessment job (fake BigQuery, knowledge base and Gemini)."""

import uuid
from decimal import Decimal

import pytest

from app.auth import Staff
from test_applications_api import PNG, new_app, upload  # noqa: F401
from test_assessment_flow import aw, verified_etb  # noqa: F401  (fixtures)
from test_documents_flow import run_all, world  # noqa: F401  (fixtures)
from test_enquiries_api import api, env  # noqa: F401  (fixtures)

LO, AM, BM = "LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"
NOTE = "Salary credits at our bank match the slip; customer prefers 36 months."


def second(env, role, key):  # noqa: F811
    """Another member of staff with the same role (e.g. a second Assistant Manager)."""
    if key not in env:
        uid = uuid.uuid4()
        env["admin"].execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (uid, f"{uid}@example.invalid"))
        env["admin"].execute("INSERT INTO staff_profiles (user_id, full_name, email, role) VALUES (%s, %s, %s, %s)", (uid, f"Second {role}", f"{uid}@example.invalid", role))
        perms = [r["permission"] for r in env["admin"].execute("SELECT permission FROM role_permissions WHERE role = %s", (role,)).fetchall()]
        env[key] = Staff(uid, f"Second {role}", f"{uid}@example.invalid", role, frozenset(perms))
    return key


def assessed(api, aw):  # noqa: F811
    aid = verified_etb(api, aw)
    run_all(aw)
    return aid


def ver(api, aid):  # noqa: F811
    return api.get(f"/applications/{aid}/review").json()["application"]["version"]


def forwarded(api, aw):  # noqa: F811
    aid = assessed(api, aw)
    r = api.as_(LO).post(f"/applications/{aid}/forward", json={"expected_version": ver(api, aid), "note": NOTE})
    assert r.status_code == 200 and r.json()["status"] == "FORWARDED", r.text
    return aid


def claimed(api, aw, role=AM):  # noqa: F811
    aid = forwarded(api, aw)
    c = api.as_(role)
    r = c.post(f"/applications/{aid}/claim", json={"expected_version": ver(api, aid)})
    assert r.status_code == 200 and r.json()["status"] == "UNDER_REVIEW", r.text
    return aid


def current(api, aid):  # noqa: F811
    t = api.get(f"/applications/{aid}/review").json()["terms"]["current"]
    return {"amount": t["amount"], "rate_bp": t["rate_bp"], "tenure_months": t["tenure_months"]}


def approve(c, aid, reason=None, terms=None):
    body = {"expected_version": ver(c, aid), "outcome": "APPROVED", "terms": terms or current(c, aid)}
    return c.post(f"/applications/{aid}/decision", json=body | ({"reason": reason} if reason else {}))


def test_forward_queue_claim_and_approve_the_engines_terms(api, aw):  # noqa: F811
    aid = assessed(api, aw)
    lo = api.as_(LO)
    assert lo.get(f"/applications/{aid}/review").json()["actions"] == {"forward": True, "claim": False, "take_over": False, "adjust_terms": False,
                                                                       "approve": False, "decline": False, "refer": False,
                                                                       "refer_to_branch_manager": False, "four_eyes": True}  # the officer ran its intake
    assert lo.post(f"/applications/{aid}/forward", json={"expected_version": ver(api, aid) - 1}).status_code == 409
    assert lo.post(f"/applications/{aid}/forward", json={"expected_version": ver(api, aid), "note": NOTE}).status_code == 200
    assert lo.post(f"/applications/{aid}/forward", json={"expected_version": ver(api, aid)}).status_code == 409  # once only
    assert lo.get("/review/queue").status_code == 403  # the queue is for reviewers

    am = api.as_(AM)
    q = am.get("/review/queue", params={"scope": "unassigned"}).json()["applications"]
    row = next(x for x in q if x["id"] == aid)
    assert row["status"] == "FORWARDED" and row["ai_recommendation"] == "RECOMMENDED_FOR_APPROVAL" and row["eligible_amount"] is not None
    assert am.post(f"/applications/{aid}/claim", json={"expected_version": ver(api, aid)}).status_code == 200
    assert aid in [x["id"] for x in am.get("/review/queue", params={"scope": "mine"}).json()["applications"]]
    v = am.get(f"/applications/{aid}/review").json()
    assert v["actions"] == {"forward": False, "claim": False, "take_over": False, "adjust_terms": False, "approve": True, "decline": True, "refer": False,
                            "refer_to_branch_manager": True, "four_eyes": False}
    assert v["application"]["assigned_name"] == "Test ASSISTANT_MANAGER" and v["ai"]["label"] == "AI recommendation, human decides."
    assert any(h["to"] == "FORWARDED" and h["reason"] == NOTE for h in v["history"])  # the Loan Officer's note reaches the reviewer
    assert v["terms"]["current"]["source"] == "ELIGIBLE" and Decimal(v["terms"]["current"]["emi"]) > 0 and "DEMO_ASSUMPTION" in v["terms"]["label"]

    r = approve(am, aid)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "APPROVED" and d["is_override"] is False and d["ai_recommendation"] == "RECOMMENDED_FOR_APPROVAL"
    assert d["approved_terms"]["amount"] == v["terms"]["eligible"]["amount"]
    v = lo.get(f"/applications/{aid}/review").json()
    assert v["terms"]["approved"]["amount"] == d["approved_terms"]["amount"] and v["decisions"][0]["by"] == "Test ASSISTANT_MANAGER"
    assert not any(v["actions"].values())
    out = aw["admin"].execute("SELECT payload FROM analytics_outbox WHERE application_id = %s AND event_type = 'DECISION'", (aid,)).fetchone()
    assert out["payload"]["outcome"] == "APPROVED"  # the decision is queued for the lakehouse in the same transaction


def test_roles_the_loan_officer_never_decides_and_the_assistant_manager_cannot_refer_adjust_or_take_over(api, aw):  # noqa: F811
    aid = forwarded(api, aw)
    lo = api.as_(LO)
    assert lo.post(f"/applications/{aid}/claim", json={"expected_version": ver(api, aid)}).status_code == 403
    assert lo.post(f"/applications/{aid}/decision", json={"expected_version": ver(api, aid), "outcome": "APPROVED"}).status_code == 403
    am = api.as_(AM)
    assert am.post(f"/applications/{aid}/claim", json={"expected_version": ver(api, aid)}).status_code == 200
    v = ver(api, aid)
    assert am.post(f"/applications/{aid}/decision", json={"expected_version": v, "outcome": "REFERRED", "reason": "needs a second opinion"}).status_code == 403
    assert am.post(f"/applications/{aid}/terms", json={"expected_version": v, "amount": "100000", "rate_bp": 1200, "tenure_months": 36,
                                                        "reason": "smaller loan is safer"}).status_code == 403
    assert am.post(f"/applications/{aid}/take-over", json={"expected_version": v, "reason": "taking this file over"}).status_code == 403
    lo = api.as_(LO)
    assert lo.post(f"/applications/{aid}/decision", json={"expected_version": v, "outcome": "DECLINED", "reason": "no"}).status_code == 403


def test_a_decline_needs_a_reason_and_records_no_approved_terms(api, aw):  # noqa: F811
    aid = claimed(api, aw)
    am = api.as_(AM)
    for bad in ({}, {"reason": "short"}):
        assert am.post(f"/applications/{aid}/decision", json={"expected_version": ver(api, aid), "outcome": "DECLINED"} | bad).status_code == 422
    assert am.post(f"/applications/{aid}/decision", json={"expected_version": ver(api, aid), "outcome": "DECLINED", "reason": "x" * 12,
                                                           "terms": current(api, aid)}).status_code == 422  # terms only when approving
    r = am.post(f"/applications/{aid}/decision", json={"expected_version": ver(api, aid), "outcome": "DECLINED",
                                                       "reason": "Customer withdrew; existing EMIs rising next month"})
    assert r.status_code == 201 and r.json()["status"] == "DECLINED" and r.json()["is_override"] is True  # the AI recommended approval
    assert r.json()["approved_terms"] is None
    assert aw["admin"].execute("SELECT count(*) AS n FROM application_terms WHERE application_id = %s AND kind = 'APPROVED'", (aid,)).fetchone()["n"] == 0
    ov = aw["admin"].execute("SELECT reason FROM audit_events WHERE application_id = %s AND event_type = 'DECISION_OVERRIDE'", (aid,)).fetchone()
    assert ov["reason"].startswith("Customer withdrew")


def test_branch_manager_adjusts_terms_inside_the_limits_and_the_approval_uses_exactly_what_was_seen(api, aw):  # noqa: F811
    aid = claimed(api, aw, role=BM)
    bm = api.as_(BM)
    eligible = current(api, aid)
    v = ver(api, aid)
    t = lambda **kw: {"expected_version": v, "reason": "Reducing exposure: second income source unverified"} | eligible | kw  # noqa: E731
    over = str(Decimal(eligible["amount"]) + 5000)
    assert "eligible cap" in bm.post(f"/applications/{aid}/terms", json=t(amount=over)).json()["detail"]
    assert "rate" in bm.post(f"/applications/{aid}/terms", json=t(rate_bp=2500)).json()["detail"]
    assert "tenure" in bm.post(f"/applications/{aid}/terms", json=t(tenure_months=84)).json()["detail"]
    assert bm.post(f"/applications/{aid}/terms", json=t(reason="short")).status_code == 422
    assert bm.post(f"/applications/{aid}/terms", json=t()).status_code == 422  # nothing changed
    r = bm.post(f"/applications/{aid}/terms", json=t(amount="250000", tenure_months=48))
    assert r.status_code == 201 and r.json()["terms"]["source"] == "TERM_CHANGE" and r.json()["terms"]["amount"] == "250000.00"
    assert approve(bm, aid, terms=eligible).status_code == 409  # stale terms are never approved
    r = approve(bm, aid)
    assert r.status_code == 201 and r.json()["approved_terms"]["amount"] == "250000.00" and r.json()["approved_terms"]["tenure_months"] == 48
    row = aw["admin"].execute("SELECT term_change_id, decision_id, created_by FROM application_terms WHERE application_id = %s AND kind = 'APPROVED'", (aid,)).fetchone()
    assert row["term_change_id"] is not None and row["decision_id"] is not None and row["created_by"] == aw["env"][BM].user_id
    change = api.get(f"/applications/{aid}/review").json()["term_changes"][0]
    assert change["before"]["source"] == "ELIGIBLE" and change["after"]["amount"] == "250000" and change["reason"].startswith("Reducing exposure")


def test_referral_returns_the_file_to_the_queue_with_the_note(api, aw):  # noqa: F811
    aid = claimed(api, aw, role=BM)
    bm = api.as_(BM)
    assert bm.post(f"/applications/{aid}/decision", json={"expected_version": ver(api, aid), "outcome": "REFERRED", "reason": "short"}).status_code == 422
    r = bm.post(f"/applications/{aid}/decision", json={"expected_version": ver(api, aid), "outcome": "REFERRED",
                                                       "reason": "Ask for the Form 16 to confirm the employer before deciding"})
    assert r.status_code == 201 and r.json()["status"] == "RECONSIDERATION"
    am = api.as_(AM)
    row = next(x for x in am.get("/review/queue", params={"status": "RECONSIDERATION"}).json()["applications"] if x["id"] == aid)
    assert row["assigned_name"] == "Test BRANCH_MANAGER"  # the last reviewer is shown; the file is back in the queue for anyone
    r = am.post(f"/applications/{aid}/claim", json={"expected_version": ver(api, aid)})
    assert r.status_code == 200 and r.json()["assigned_to"] == str(aw["env"][AM].user_id), r.text
    v = am.get(f"/applications/{aid}/review").json()
    assert v["decisions"][0]["outcome"] == "REFERRED" and v["decisions"][0]["reason"].startswith("Ask for the Form 16")
    assert v["actions"]["approve"] and not v["actions"]["refer"]
    assert approve(am, aid).status_code == 201


def test_take_over_is_branch_manager_only_with_a_reason_and_then_only_the_new_holder_decides(api, aw):  # noqa: F811
    aid = claimed(api, aw)
    am2 = api.as_(second(aw["env"], AM, "AM2"))
    assert am2.post(f"/applications/{aid}/claim", json={"expected_version": ver(api, aid)}).status_code == 409
    assert approve(am2, aid).status_code == 409  # another assistant manager's file
    bm = api.as_(BM)
    assert "Take it over" in approve(bm, aid).json()["detail"]  # even the branch manager takes it over first
    assert bm.post(f"/applications/{aid}/take-over", json={"expected_version": ver(api, aid), "reason": "busy"}).status_code == 422
    r = bm.post(f"/applications/{aid}/take-over", json={"expected_version": ver(api, aid), "reason": "Assistant Manager on leave this week"})
    assert r.status_code == 200 and r.json()["assigned_to"] == str(aw["env"][BM].user_id)
    assert approve(api.as_(AM), aid).status_code == 409  # the previous holder no longer decides it
    assert approve(api.as_(BM), aid).status_code == 201
    ev = aw["admin"].execute("SELECT reason, before_state FROM audit_events WHERE application_id = %s AND event_type = 'APPLICATION_TAKEOVER'", (aid,)).fetchone()
    assert ev["reason"].startswith("Assistant Manager on leave") and ev["before_state"]["assigned_to"] == str(aw["env"][AM].user_id)


def test_two_reviewers_claiming_at_once_only_one_gets_it(api, aw):  # noqa: F811
    aid = forwarded(api, aw)
    v = ver(api, aid)
    assert api.as_(AM).post(f"/applications/{aid}/claim", json={"expected_version": v}).status_code == 200
    assert api.as_(second(aw["env"], AM, "AM2")).post(f"/applications/{aid}/claim", json={"expected_version": v}).status_code == 409


def test_approving_a_potentially_ineligible_case_is_an_override_that_needs_a_reason(api, aw):  # noqa: F811
    aw["lakehouse"].records["BOB-C0027"] |= {"has_written_off": True}
    aid = claimed(api, aw)
    am = api.as_(AM)
    assert api.get(f"/applications/{aid}/review").json()["ai"]["recommendation"] == "POTENTIALLY_INELIGIBLE"
    assert approve(am, aid).status_code == 422
    r = approve(am, aid, reason="Write-off was a disputed card settled in 2019; NOC on file")
    assert r.status_code == 201 and r.json()["is_override"] is True


def test_with_no_eligible_amount_only_a_branch_manager_can_set_terms_and_never_above_the_request(api, aw):  # noqa: F811
    aw["lakehouse"].records["BOB-C0027"] |= {"total_monthly_obligations": Decimal("60000")}  # no room for any EMI
    aid = claimed(api, aw, role=BM)
    bm = api.as_(BM)
    v = bm.get(f"/applications/{aid}/review").json()
    assert v["terms"]["eligible"] is None and v["terms"]["current"] is None
    assert "no terms to approve" in bm.post(f"/applications/{aid}/decision", json={"expected_version": ver(api, aid), "outcome": "APPROVED",
                                                                                     "reason": "x" * 20}).json()["detail"]
    t = {"expected_version": ver(api, aid), "rate_bp": 1400, "tenure_months": 60, "reason": "Two EMIs close next month; statement shows the closures"}
    assert "requested cap" in bm.post(f"/applications/{aid}/terms", json=t | {"amount": "400001"}).json()["detail"]
    assert bm.post(f"/applications/{aid}/terms", json=t | {"amount": "150000"}).status_code == 201
    r = approve(bm, aid, reason="Approving below the request: obligations end next month, closure letters on file")
    assert r.status_code == 201 and r.json()["approved_terms"]["amount"] == "150000.00" and r.json()["is_override"] is True
