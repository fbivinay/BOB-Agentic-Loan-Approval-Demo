"""Decisions, overrides, terms and term changes: human-only, role-checked, versioned, audited."""

import json
import threading
import uuid

import psycopg
import pytest
from helpers import (eligible, CHECK_VIOLATION, INSUFFICIENT_PRIVILEGE, SERIALIZATION_FAILURE, application, audit, decide, expect_error, force, rule_set, staff, status,
                     under_review, version)

LO, AM, BM = "LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"
REC_OK, REC_REFER, REC_NO = "RECOMMENDED_FOR_APPROVAL", "REFER_FOR_FURTHER_REVIEW", "POTENTIALLY_INELIGIBLE"


# ---------------------------------------------------------------- who may decide
def test_a_loan_officer_cannot_approve_or_decline(conn):
    lo = staff(conn, LO)
    app = under_review(conn)
    for outcome in ("APPROVED", "DECLINED"):
        with expect_error(conn, INSUFFICIENT_PRIVILEGE, "Assistant Manager or Branch Manager"):
            decide(conn, app, lo, outcome, "attempt by a loan officer")
    assert status(conn, app) == "UNDER_REVIEW"


def test_the_system_and_agents_cannot_decide(conn):
    app = under_review(conn)
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        decide(conn, app, uuid.uuid4(), "APPROVED")  # an id that belongs to no member of staff, as an agent would use
    with expect_error(conn):
        decide(conn, app, None, "APPROVED")  # no actor at all
    with expect_error(conn):
        conn.execute("INSERT INTO decisions (application_id, application_version, outcome, decided_by, decided_by_role, ai_recommendation_snapshot, is_override) "
                     "VALUES (%s, %s, 'APPROVED', NULL, 'SYSTEM', '{}', false)", (app, version(conn, app)))
    assert status(conn, app) == "UNDER_REVIEW"


def test_inactive_managers_cannot_decide(conn):
    app = under_review(conn)
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        decide(conn, app, staff(conn, AM, active=False), "APPROVED")


@pytest.mark.parametrize("role", [AM, BM])
def test_assistant_and_branch_manager_can_approve(conn, role):
    who = staff(conn, role)
    app = under_review(conn)
    v = version(conn, app)
    d = decide(conn, app, who, "APPROVED")
    assert d["decided_by_role"] == role and d["is_override"] is False
    assert (status(conn, app), version(conn, app)) == ("APPROVED", v + 1)


def test_every_human_decision_is_in_the_audit_trail_and_the_analytics_outbox(conn):
    am = staff(conn, AM)
    app = under_review(conn, REC_OK)
    d = decide(conn, app, am, "APPROVED")
    rec = audit(conn, app, "DECISION_RECORDED")
    assert len(rec) == 1 and (rec[0]["actor_type"], str(rec[0]["actor_id"]), rec[0]["actor_role"]) == ("STAFF", str(am), AM)
    assert rec[0]["after_state"]["ai"]["recommendation"] == REC_OK
    assert [a["after_state"]["status"] for a in audit(conn, app, "STATUS_TRANSITION")] == ["APPROVED"]
    out = conn.execute("SELECT * FROM analytics_outbox WHERE application_id = %s", (app,)).fetchall()
    assert len(out) == 1 and out[0]["status"] == "PENDING" and out[0]["payload"]["decision_id"] == str(d["id"])


def test_the_caller_cannot_forge_role_snapshot_or_override_flag(conn):
    am = staff(conn, AM)
    app = under_review(conn, REC_NO)
    row = conn.execute("INSERT INTO decisions (application_id, application_version, outcome, decided_by, decided_by_role, reason, ai_recommendation_snapshot, is_override) "
                       "VALUES (%s, %s, 'APPROVED', %s, 'BRANCH_MANAGER', 'approving a potentially ineligible case on documented grounds', '{\"recommendation\": \"fake\"}', false) RETURNING *",
                       (app, version(conn, app), am)).fetchone()
    assert row["decided_by_role"] == AM
    assert row["ai_recommendation_snapshot"]["recommendation"] == REC_NO
    assert row["is_override"] is True


# ---------------------------------------------------------------- reasons and overrides
def test_overriding_the_ai_recommendation_needs_a_written_reason(conn):
    am = staff(conn, AM)
    for rec, outcome in ((REC_NO, "APPROVED"), (REC_OK, "DECLINED")):
        app = under_review(conn, rec)
        for bad in (None, "", "short", "         "):
            with expect_error(conn, CHECK_VIOLATION, "reason"):
                decide(conn, app, am, outcome, bad)
        d = decide(conn, app, am, outcome, "Documented grounds: income verified on a second statement")
        assert d["is_override"] is True
        ov = audit(conn, app, "DECISION_OVERRIDE")
        assert len(ov) == 1 and ov[0]["before_state"]["recommendation"] == rec and ov[0]["after_state"]["outcome"] == outcome and ov[0]["reason"]
        assert conn.execute("SELECT payload FROM analytics_outbox WHERE application_id = %s", (app,)).fetchone()["payload"]["is_override"] is True


def test_a_decline_always_needs_a_reason_even_when_it_matches_the_ai(conn):
    am = staff(conn, AM)
    app = under_review(conn, REC_NO)
    with expect_error(conn, CHECK_VIOLATION, "reason"):
        decide(conn, app, am, "DECLINED")
    d = decide(conn, app, am, "DECLINED", "Existing obligations exceed the demo limit")
    assert d["is_override"] is False and not audit(conn, app, "DECISION_OVERRIDE")
    assert status(conn, app) == "DECLINED"


def test_following_the_ai_or_approving_after_a_referral_is_not_an_override(conn):
    am = staff(conn, AM)
    for rec in (REC_OK, REC_REFER):
        app = under_review(conn, rec)
        assert decide(conn, app, am, "APPROVED")["is_override"] is False


# ---------------------------------------------------------------- referral and takeover
def test_only_a_branch_manager_can_refer_for_reconsideration(conn):
    am, bm = staff(conn, AM), staff(conn, BM)
    app = under_review(conn)
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "refer"):
        decide(conn, app, am, "REFERRED", "needs a second opinion on income")
    with expect_error(conn, CHECK_VIOLATION, "reason"):
        decide(conn, app, bm, "REFERRED")
    decide(conn, app, bm, "REFERRED", "needs a second opinion on income")
    assert status(conn, app) == "RECONSIDERATION"
    r = conn.execute("SELECT * FROM app_claim(%s, %s, %s)", (app, am, version(conn, app))).fetchone()
    assert r["status"] == "UNDER_REVIEW"  # back in the review queue


def test_an_application_assigned_to_one_reviewer_cannot_be_decided_by_another_assistant_manager(conn):
    am1, am2, bm = staff(conn, AM), staff(conn, AM), staff(conn, BM)
    app = under_review(conn, assigned=am1)
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "assigned to another reviewer"):
        decide(conn, app, am2, "APPROVED")
    d = decide(conn, app, bm, "APPROVED")  # a branch manager may take over
    assert d["decided_by_role"] == BM


def test_takeover_is_branch_manager_only_needs_a_reason_and_is_audited(conn):
    am1, am2, bm = staff(conn, AM), staff(conn, AM), staff(conn, BM)
    app = under_review(conn, assigned=am1)
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("SELECT app_take_over(%s, %s, %s, 'taking over the file')", (app, am2, version(conn, app)))
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("SELECT app_take_over(%s, %s, %s, 'no')", (app, bm, version(conn, app)))
    a = conn.execute("SELECT * FROM app_take_over(%s, %s, %s, 'Reviewer on leave, branch manager taking the file')", (app, bm, version(conn, app))).fetchone()
    assert a["assigned_to"] == bm
    t = audit(conn, app, "APPLICATION_TAKEOVER")[0]
    assert str(t["actor_id"]) == str(bm) and t["before_state"]["assigned_to"] == str(am1) and t["reason"]
    decide(conn, app, bm, "APPROVED")


# ---------------------------------------------------------------- state and concurrency rules
def test_decisions_need_an_under_review_application_with_an_ai_assessment(conn):
    am = staff(conn, AM)
    lo = staff(conn, LO)
    app = application(conn, lo)
    force(conn, app, "FORWARDED", REC_OK)
    with expect_error(conn, CHECK_VIOLATION, "UNDER_REVIEW"):
        decide(conn, app, am, "APPROVED")
    app2 = application(conn, lo)
    force(conn, app2, "UNDER_REVIEW", None)  # no recommendation on file
    with expect_error(conn, CHECK_VIOLATION, "no AI assessment"):
        decide(conn, app2, am, "APPROVED")


def test_a_stale_decision_is_rejected(conn):
    am = staff(conn, AM)
    app = under_review(conn)
    v = version(conn, app)
    decide(conn, app, am, "APPROVED")
    with expect_error(conn, SERIALIZATION_FAILURE):
        decide(conn, app, am, "DECLINED", "second decision from a stale screen", at_version=v)


def test_decisions_cannot_be_edited_or_removed(conn):
    am = staff(conn, AM)
    app = under_review(conn)
    d = decide(conn, app, am, "APPROVED")
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("UPDATE decisions SET outcome = 'DECLINED' WHERE id = %s", (d["id"],))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("DELETE FROM decisions WHERE id = %s", (d["id"],))


def test_approved_and_declined_status_cannot_be_reached_without_a_matching_human_decision(conn):
    am = staff(conn, AM)
    app = under_review(conn)
    for to in ("APPROVED", "DECLINED"):
        for actor_type, actor in (("STAFF", am), ("SYSTEM", None)):
            with expect_error(conn, INSUFFICIENT_PRIVILEGE):
                conn.execute("SELECT app_transition(%s, %s, %s, %s, %s, 'trying without a decision')", (app, to, version(conn, app), actor_type, actor))
    assert status(conn, app) == "UNDER_REVIEW"


def test_two_reviewers_cannot_both_decide_the_same_version_even_concurrently(db_url):
    """Real concurrency on committed data: the loser blocks on the row lock, then fails with a serialization error."""
    setup = psycopg.connect(db_url, row_factory=psycopg.rows.dict_row)
    am1, am2 = staff(setup, AM), staff(setup, AM)
    app = under_review(setup)
    v = version(setup, app)
    setup.commit()
    a = psycopg.connect(db_url, row_factory=psycopg.rows.dict_row)
    b = psycopg.connect(db_url, row_factory=psycopg.rows.dict_row)
    result: dict = {}

    def second():
        try:
            decide(b, app, am2, "DECLINED", "second reviewer, same version, at the same moment", at_version=v)
            b.commit()
            result["ok"] = True
        except psycopg.Error as e:
            result["sqlstate"] = e.sqlstate
            b.rollback()

    decide(a, app, am1, "APPROVED", at_version=v)  # holds the application row lock until commit
    t = threading.Thread(target=second)
    t.start()
    t.join(timeout=1.5)
    assert t.is_alive(), "the second reviewer should be waiting on the row lock"
    a.commit()
    t.join(timeout=15)
    assert result.get("sqlstate") == SERIALIZATION_FAILURE
    setup.rollback()  # end the set-up transaction so the next read sees the committed result
    assert status(setup, app) == "APPROVED"
    assert setup.execute("SELECT count(*) AS n FROM decisions WHERE application_id = %s", (app,)).fetchone()["n"] == 1
    for c in (a, b, setup):
        c.close()


# ---------------------------------------------------------------- terms
def _eligible(conn, app, amount=500000, rate=1250, tenure=36):
    eligible(conn, app, rule_set(conn, None), amount, rate, tenure)


def _approved_terms(conn, app, by, decision=None, change=None, amount=450000, rate=1250, tenure=36):
    return conn.execute("INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months, decision_id, term_change_id, created_by) "
                        "VALUES (%s, 'APPROVED', %s, %s, %s, %s, %s, %s) RETURNING id", (app, amount, rate, tenure, decision, change, by)).fetchone()["id"]


def test_approved_terms_need_the_approving_human_and_stay_inside_demo_limits(conn):
    am, lo = staff(conn, AM), staff(conn, LO)
    app = under_review(conn, REC_OK)
    _eligible(conn, app)
    d = decide(conn, app, am, "APPROVED")["id"]
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        _approved_terms(conn, app, lo, decision=d)  # a loan officer cannot set approved terms
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        _approved_terms(conn, app, None, decision=d)  # nor the system
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "reference the approving decision"):
        _approved_terms(conn, app, am)  # no link to a decision
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        _approved_terms(conn, app, staff(conn, AM), decision=d)  # a different reviewer than the one who decided
    for kw, why in (({"amount": 500001}, "eligible cap"), ({"rate": 2500}, "rate"), ({"rate": 900}, "rate"), ({"tenure": 84}, "tenure"), ({"tenure": 6}, "tenure")):
        with expect_error(conn, CHECK_VIOLATION, why):
            _approved_terms(conn, app, am, decision=d, **kw)
    assert _approved_terms(conn, app, am, decision=d)


def test_requested_and_eligible_terms_have_their_own_rules(conn):
    lo, am = staff(conn, LO), staff(conn, AM)
    app = application(conn, lo)
    ins = "INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months, created_by, policy_rule_set_id) VALUES (%s, %s, 500000, 1250, 36, %s, %s)"
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute(ins, (app, "REQUESTED", am, None))
    conn.execute(ins, (app, "REQUESTED", lo, None))
    with expect_error(conn, CHECK_VIOLATION, "policy rule set"):
        conn.execute(ins, (app, "ELIGIBLE", None, None))  # eligible terms must cite the rules they came from
    rs = rule_set(conn, lo)
    for who in (lo, am):  # a person can never write the cap that limits what may be approved
        with expect_error(conn, INSUFFICIENT_PRIVILEGE, "policy engine"):
            conn.execute(ins, (app, "ELIGIBLE", who, rs))
    conn.execute("SELECT set_config('app.actor_type', 'SYSTEM', true)")
    with expect_error(conn, CHECK_VIOLATION, "documents-verified"):
        conn.execute(ins, (app, "ELIGIBLE", None, rs))  # not at the assessment step
    conn.execute("SELECT set_config('app.actor_type', '', true)")
    eligible(conn, app, rs)  # matches a finished assessment run: accepted


def test_eligible_terms_must_match_a_finished_assessment_run(conn):
    lo = staff(conn, LO)
    app, rs = application(conn, lo), rule_set(conn, lo)
    conn.execute("SELECT set_config('app.in_workflow_fn', 'on', true)")
    conn.execute("UPDATE applications SET status = 'DOCS_VERIFIED' WHERE id = %s", (app,))
    conn.execute("SELECT set_config('app.in_workflow_fn', 'off', true)")
    conn.execute("INSERT INTO agent_runs (application_id, graph, status, recommendation, policy_rule_set_id, output, finished_at) VALUES "
                 "(%s, 'ASSESSMENT', 'DONE', 'RECOMMENDED_FOR_APPROVAL', %s, '{\"assessment\": {\"figures\": {\"recommended_amount\": \"300000\", "
                 "\"indicative_rate_bp\": 1200, \"recommended_tenure_months\": 36}}}', now())", (app, rs))
    conn.execute("SELECT set_config('app.actor_type', 'SYSTEM', true)")
    ins = "INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months, policy_rule_set_id) VALUES (%s, 'ELIGIBLE', %s, %s, %s, %s)"
    for amount, rate, tenure, rule in ((900000, 1200, 36, rs), (300000, 1100, 36, rs), (300000, 1200, 60, rs), (300000, 1200, 36, rule_set(conn, lo))):
        with expect_error(conn, CHECK_VIOLATION, "finished assessment run"):
            conn.execute(ins, (app, amount, rate, tenure, rule))  # an inflated or altered cap, or another rule set
    conn.execute(ins, (app, 300000, 1200, 36, rs))


def test_terms_are_immutable(conn):
    am = staff(conn, AM)
    app = under_review(conn, REC_OK)
    d = decide(conn, app, am, "APPROVED")["id"]
    t = _approved_terms(conn, app, am, decision=d)
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("UPDATE application_terms SET amount = 1 WHERE id = %s", (t,))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("DELETE FROM application_terms WHERE id = %s", (t,))


def _change(conn, app, by, amount=400000, rate=1300, tenure=48, reason="Customer income supports a shorter exposure", at_version=None):
    return conn.execute("INSERT INTO term_changes (application_id, application_version, changed_by, changed_by_role, reason, before_terms, after_terms) VALUES (%s, %s, %s, 'BRANCH_MANAGER', %s, %s, %s) RETURNING *",
                        (app, at_version or version(conn, app), by, reason, json.dumps({"amount": 500000, "rate_bp": 1250, "tenure_months": 36}),
                         json.dumps({"amount": amount, "rate_bp": rate, "tenure_months": tenure}))).fetchone()


def test_only_a_branch_manager_adjusts_terms_with_a_reason_inside_limits(conn):
    am, bm, lo = staff(conn, AM), staff(conn, BM), staff(conn, LO)
    app = under_review(conn, REC_OK)
    _eligible(conn, app)
    for who in (am, lo):
        with expect_error(conn, INSUFFICIENT_PRIVILEGE):
            _change(conn, app, who)
    for bad in (None, "", "no"):
        with expect_error(conn, CHECK_VIOLATION, "reason"):
            _change(conn, app, bm, reason=bad)
    for kw, why in (({"amount": 500001}, "eligible cap"), ({"rate": 2100}, "rate"), ({"tenure": 72}, "tenure")):
        with expect_error(conn, CHECK_VIOLATION, why):
            _change(conn, app, bm, **kw)
    with expect_error(conn, SERIALIZATION_FAILURE):
        _change(conn, app, bm, at_version=version(conn, app) - 1)
    row = _change(conn, app, bm)
    assert row["changed_by_role"] == BM
    a = audit(conn, app, "TERM_CHANGE")
    assert len(a) == 1 and a[0]["reason"] and a[0]["before_state"]["amount"] == 500000 and a[0]["after_state"]["amount"] == 400000 and str(a[0]["actor_id"]) == str(bm)
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("UPDATE term_changes SET reason = 'edited later' WHERE id = %s", (row["id"],))


def test_terms_cannot_be_adjusted_outside_review(conn):
    bm, lo = staff(conn, BM), staff(conn, LO)
    app = application(conn, lo)
    force(conn, app, "FORWARDED", REC_OK)
    with expect_error(conn, CHECK_VIOLATION, "during review"):
        _change(conn, app, bm)


def test_approved_terms_can_cite_a_recorded_term_change(conn):
    am, bm = staff(conn, AM), staff(conn, BM)
    app = under_review(conn, REC_OK)
    _eligible(conn, app)
    ch = _change(conn, app, bm)["id"]
    d = decide(conn, app, bm, "APPROVED")["id"]
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        _approved_terms(conn, app, am, change=ch)  # only the manager who made the change may carry it into approved terms
    assert _approved_terms(conn, app, bm, change=ch, amount=400000, rate=1300, tenure=48)
