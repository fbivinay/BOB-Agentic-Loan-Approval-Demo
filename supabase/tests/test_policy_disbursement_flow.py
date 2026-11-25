"""Policy activation, sanction pack, disbursement, the full journey, the backend loader and the migration runner."""

import json
import shutil
import uuid
from pathlib import Path

import psycopg
import pytest
from helpers import (CHECK_VIOLATION, INSUFFICIENT_PRIVILEGE, application, applicant, audit, decide, expect_error, force, rule_set, staff, status, under_review, version)

LO, AM, BM = "LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"
PASS = json.dumps({"passed": True, "cases": 6, "failed": 0})


def activate(conn, rs, by, reason="Reviewed the change report and golden-case results", test=PASS):
    return conn.execute("UPDATE policy_rule_sets SET status = 'ACTIVE', approved_by = %s, activation_reason = %s, test_result = %s WHERE id = %s RETURNING *", (by, reason, test, rs)).fetchone()


# ---------------------------------------------------------------- policy rule sets
def test_a_rule_set_cannot_be_created_active(conn):
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "DRAFT"):
        conn.execute("INSERT INTO policy_rule_sets (scheme, version, status, content, content_sha256) VALUES ('s', 1, 'ACTIVE', '{}', 'x')")


def test_only_an_authorised_human_with_a_reason_and_passing_tests_activates_policy(conn):
    lo, am, bm = staff(conn, LO), staff(conn, AM), staff(conn, BM)
    rs = rule_set(conn, bm)
    for who in (lo, am, None, uuid.uuid4()):
        with expect_error(conn, INSUFFICIENT_PRIVILEGE, "policy.activate"):
            activate(conn, rs, who)
    for bad in (None, "", "ok"):
        with expect_error(conn, CHECK_VIOLATION, "reason"):
            activate(conn, rs, bm, reason=bad)
    for tests in (None, json.dumps({"passed": False}), json.dumps({"cases": 6}), json.dumps({"passed": "yes"})):
        with expect_error(conn, CHECK_VIOLATION, "tests must pass"):
            activate(conn, rs, bm, test=tests)
    r = activate(conn, rs, bm)
    assert r["status"] == "ACTIVE" and r["approved_at"] is not None and r["effective_from"] is not None
    log = conn.execute("SELECT * FROM policy_activation_log WHERE rule_set_id = %s", (rs,)).fetchall()
    assert len(log) == 1 and log[0]["activated_by_role"] == BM and log[0]["test_result"]["passed"] is True
    a = conn.execute("SELECT * FROM audit_events WHERE event_type = 'POLICY_ACTIVATED' AND entity_id = %s", (str(rs),)).fetchall()
    assert len(a) == 1 and a[0]["actor_type"] == "STAFF" and str(a[0]["actor_id"]) == str(bm) and a[0]["reason"]


def test_activation_permission_is_configurable_and_the_guard_follows_the_configuration(conn):
    bm, am = staff(conn, BM), staff(conn, AM)
    conn.execute("DELETE FROM role_permissions WHERE role = 'BRANCH_MANAGER' AND permission = 'policy.activate'")
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        activate(conn, rule_set(conn, bm), bm)
    conn.execute("INSERT INTO role_permissions (role, permission, kind, description) VALUES ('BRANCH_MANAGER', 'policy.activate', 'CONFIGURABLE', 'restored')")
    conn.execute("INSERT INTO role_permissions (role, permission, kind, description) VALUES ('ASSISTANT_MANAGER', 'policy.activate', 'CONFIGURABLE', 'demo setting')")
    assert activate(conn, rule_set(conn, bm), am)["status"] == "ACTIVE"  # the demo can grant it to an Assistant Manager by configuration


def test_a_new_active_rule_set_retires_the_previous_one_and_active_ones_are_immutable(conn):
    bm = staff(conn, BM)
    scheme = f"s-{uuid.uuid4().hex[:6]}"
    ids = [conn.execute("INSERT INTO policy_rule_sets (scheme, version, content, content_sha256) VALUES (%s, %s, %s, 'x') RETURNING id", (scheme, v, json.dumps({"v": v}))).fetchone()["id"] for v in (1, 2)]
    activate(conn, ids[0], bm)
    activate(conn, ids[1], bm)
    assert [r["status"] for r in conn.execute("SELECT status FROM policy_rule_sets WHERE scheme = %s ORDER BY version", (scheme,)).fetchall()] == ["RETIRED", "ACTIVE"]
    for sql in ("UPDATE policy_rule_sets SET content = '{\"v\": 99}' WHERE id = %s", "UPDATE policy_rule_sets SET version = 9 WHERE id = %s"):
        with expect_error(conn, INSUFFICIENT_PRIVILEGE, "immutable"):
            conn.execute(sql, (ids[1],))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("UPDATE policy_rule_sets SET content = '{}' WHERE id = %s", (ids[0],))  # retired ones too
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("UPDATE policy_rule_sets SET status = 'DRAFT' WHERE id = %s", (ids[1],))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("DELETE FROM policy_rule_sets WHERE id = %s", (ids[1],))


def test_unverified_candidate_values_can_never_be_inside_a_rule_set(conn):
    bm = staff(conn, BM)
    for content in ({"max_age": {"value": 60, "confidence": "UNVERIFIED_CANDIDATE"}}, {"nested": [{"x": "UNVERIFIED_CANDIDATE"}]}):
        with expect_error(conn, CHECK_VIOLATION):
            rule_set(conn, bm, content=content)
    rule_set(conn, bm, content={"max_age": {"value": 60, "confidence": "DEMO_ASSUMPTION"}, "min_age": {"value": 21, "confidence": "VERIFIED"}})


def test_policy_activation_log_cannot_be_rewritten(conn):
    bm = staff(conn, BM)
    activate(conn, rule_set(conn, bm), bm)
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("UPDATE policy_activation_log SET reason = 'edited'")
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("DELETE FROM policy_activation_log")


# ---------------------------------------------------------------- sanction pack and disbursement
def approved_application(conn, with_terms=True):
    am = staff(conn, AM)
    app = under_review(conn, "RECOMMENDED_FOR_APPROVAL")
    rs = rule_set(conn, am)
    conn.execute("INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months, policy_rule_set_id) VALUES (%s, 'ELIGIBLE', 500000, 1250, 36, %s)", (app, rs))
    d = decide(conn, app, am, "APPROVED")["id"]
    terms = None
    if with_terms:
        terms = conn.execute("INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months, decision_id, created_by) VALUES (%s, 'APPROVED', 450000, 1250, 36, %s, %s) RETURNING id",
                             (app, d, am)).fetchone()["id"]
    return app, am, terms


def gen_doc(conn, app, kind, terms):
    return conn.execute("INSERT INTO generated_documents (application_id, kind, storage_path, sha256, terms_id) VALUES (%s, %s, %s, %s, %s)",
                        (app, kind, f"gen/{uuid.uuid4()}.pdf", "c" * 64, terms))


def test_sanction_documents_need_a_human_approval_and_the_human_approved_terms(conn):
    lo = staff(conn, LO)
    app = under_review(conn)
    other_terms = approved_application(conn)[2]
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "APPROVED decision"):
        gen_doc(conn, app, "SANCTION_LETTER", other_terms)  # no approval on this application
    app2, am, _ = approved_application(conn, with_terms=False)
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "human-approved terms"):
        gen_doc(conn, app2, "SANCTION_LETTER", other_terms)  # terms belong to another application
    declined = under_review(conn, "POTENTIALLY_INELIGIBLE")
    decide(conn, declined, staff(conn, AM), "DECLINED", "Existing obligations exceed the demo limit")
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        gen_doc(conn, declined, "KFS", other_terms)


def test_sanctioned_status_needs_a_human_approval_and_all_three_documents(conn):
    app, am, terms = approved_application(conn)
    with expect_error(conn, CHECK_VIOLATION, "sanction letter, KFS and EMI schedule"):
        conn.execute("SELECT app_transition(%s, 'SANCTIONED', %s, 'SYSTEM', NULL, NULL)", (app, version(conn, app)))
    gen_doc(conn, app, "SANCTION_LETTER", terms)
    gen_doc(conn, app, "KFS", terms)
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("SELECT app_transition(%s, 'SANCTIONED', %s, 'SYSTEM', NULL, NULL)", (app, version(conn, app)))
    with expect_error(conn):
        gen_doc(conn, app, "KFS", terms)  # one of each kind
    gen_doc(conn, app, "EMI_SCHEDULE", terms)
    conn.execute("SELECT app_transition(%s, 'SANCTIONED', %s, 'SYSTEM', NULL, NULL)", (app, version(conn, app)))
    assert status(conn, app) == "SANCTIONED"


def test_a_declined_application_can_never_be_sanctioned(conn):
    app = under_review(conn, "POTENTIALLY_INELIGIBLE")
    decide(conn, app, staff(conn, AM), "DECLINED", "Existing obligations exceed the demo limit")
    for actor_type, actor in (("SYSTEM", None), ("STAFF", staff(conn, BM))):
        with expect_error(conn, CHECK_VIOLATION, "not allowed"):
            conn.execute("SELECT app_transition(%s, 'SANCTIONED', %s, %s, %s, NULL)", (app, version(conn, app), actor_type, actor))


def sanctioned(conn):
    app, am, terms = approved_application(conn)
    for k in ("SANCTION_LETTER", "KFS", "EMI_SCHEDULE"):
        gen_doc(conn, app, k, terms)
    conn.execute("SELECT app_transition(%s, 'SANCTIONED', %s, 'SYSTEM', NULL, NULL)", (app, version(conn, app)))
    return app


def test_disbursement_is_simulated_tracked_and_released_only_by_an_authorised_human(conn):
    app = under_review(conn)
    with expect_error(conn, CHECK_VIOLATION, "SANCTIONED"):
        conn.execute("INSERT INTO disbursements (application_id) VALUES (%s)", (app,))
    app = sanctioned(conn)
    lo, am, bm = staff(conn, LO), staff(conn, AM), staff(conn, BM)
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("INSERT INTO disbursements (application_id, is_simulated) VALUES (%s, false)", (app,))  # never a real transaction
    d = conn.execute("INSERT INTO disbursements (application_id) VALUES (%s) RETURNING id", (app,)).fetchone()["id"]
    release = ("UPDATE disbursements SET status = 'RELEASED', released_by = %s, simulated_reference = %s, loan_account_number = %s, first_emi_date = '2026-12-05' WHERE id = %s")
    ref = lambda: ("SIM-" + uuid.uuid4().hex[:10].upper(), "LN" + uuid.uuid4().hex[:12].upper())  # noqa: E731
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute(release, (am, *ref(), d))  # no acceptance, checklist not complete
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "disbursement.track"):
        conn.execute("UPDATE disbursements SET acceptance_recorded_at = now(), acceptance_recorded_by = %s WHERE id = %s", (am, d))
    conn.execute("UPDATE disbursements SET acceptance_recorded_at = now(), acceptance_recorded_by = %s WHERE id = %s", (lo, d))
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute(release, (am, *ref(), d))  # checklist still incomplete
    conn.execute("UPDATE disbursements SET status = 'CHECKLIST_COMPLETE', checklist = %s WHERE id = %s", (json.dumps({"kfs_signed": True, "kyc_ok": True}), d))
    for who in (lo, None, uuid.uuid4()):
        with expect_error(conn, INSUFFICIENT_PRIVILEGE, "disbursement.release"):
            conn.execute(release, (who, *ref(), d))
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("UPDATE disbursements SET status = 'RELEASED', released_by = %s, simulated_reference = 'REAL-BANK-TXN-1', loan_account_number = 'LN1', first_emi_date = '2026-12-05' WHERE id = %s", (am, d))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "human release"):
        conn.execute("SELECT app_transition(%s, 'DISBURSED', %s, 'STAFF', %s, NULL)", (app, version(conn, app), am))  # not released yet
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("SELECT app_transition(%s, 'DISBURSED', %s, 'SYSTEM', NULL, NULL)", (app, version(conn, app)))
    row = conn.execute(release + " RETURNING *", (bm, *ref(), d)).fetchone()
    assert row["released_by_role"] == BM and row["released_at"] is not None and row["is_simulated"] is True
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "cannot change"):
        conn.execute("UPDATE disbursements SET first_emi_date = '2027-01-05' WHERE id = %s", (d,))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("SELECT app_transition(%s, 'DISBURSED', %s, 'SYSTEM', NULL, NULL)", (app, version(conn, app)))
    conn.execute("SELECT app_transition(%s, 'DISBURSED', %s, 'STAFF', %s, NULL)", (app, version(conn, app), am))
    assert status(conn, app) == "DISBURSED"
    rel = [a for a in audit(conn, app, "DISBURSEMENT_UPDATE") if a["after_state"]["status"] == "RELEASED"]
    assert len(rel) == 1


def test_release_permission_is_configurable(conn):
    app = sanctioned(conn)
    am = staff(conn, AM)
    lo = staff(conn, LO)
    d = conn.execute("INSERT INTO disbursements (application_id, status, acceptance_recorded_at, acceptance_recorded_by) VALUES (%s, 'PENDING', now(), %s) RETURNING id", (app, lo)).fetchone()["id"]
    conn.execute("UPDATE disbursements SET status = 'CHECKLIST_COMPLETE' WHERE id = %s", (d,))
    conn.execute("DELETE FROM role_permissions WHERE role = 'ASSISTANT_MANAGER' AND permission = 'disbursement.release'")
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("UPDATE disbursements SET status = 'RELEASED', released_by = %s, simulated_reference = 'SIM-ABCDEF123456', loan_account_number = 'LN1', first_emi_date = '2026-12-05' WHERE id = %s", (am, d))


# ---------------------------------------------------------------- the whole journey, through the real functions
def test_full_journey_from_intake_to_disbursement(conn):
    lo, am, bm = staff(conn, LO), staff(conn, AM), staff(conn, BM)
    app = application(conn, lo)
    step = lambda to, who, actor_type="STAFF", reason=None: conn.execute("SELECT * FROM app_transition(%s, %s, %s, %s, %s, %s)", (app, to, version(conn, app), actor_type, who, reason)).fetchone()  # noqa: E731
    step("DOCS_PENDING", lo)
    step("DISCREPANCY", None, "SYSTEM")
    step("DOCS_PENDING", lo)
    step("DOCS_VERIFIED", lo)
    run = conn.execute("INSERT INTO agent_runs (application_id, graph, status, recommendation, finished_at) VALUES (%s, 'ASSESSMENT', 'DONE', 'REFER_FOR_FURTHER_REVIEW', now()) RETURNING id", (app,)).fetchone()["id"]
    rs = rule_set(conn, bm)
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("SELECT app_record_assessment(%s, %s, 'RECOMMENDED_FOR_APPROVAL', %s, %s)", (app, run, rs, version(conn, app)))  # does not match the run
    r = conn.execute("SELECT * FROM app_record_assessment(%s, %s, 'REFER_FOR_FURTHER_REVIEW', %s, %s)", (app, run, rs, version(conn, app))).fetchone()
    assert r["status"] == "AI_ASSESSED" and r["ai_recommendation"] == "REFER_FOR_FURTHER_REVIEW"
    step("FORWARDED", lo)
    conn.execute("SELECT app_claim(%s, %s, %s)", (app, bm, version(conn, app)))
    conn.execute("INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months, policy_rule_set_id) VALUES (%s, 'ELIGIBLE', 500000, 1250, 36, %s)", (app, rs))
    d = decide(conn, app, bm, "APPROVED")["id"]
    terms = conn.execute("INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months, decision_id, created_by) VALUES (%s, 'APPROVED', 450000, 1250, 36, %s, %s) RETURNING id", (app, d, bm)).fetchone()["id"]
    for k in ("SANCTION_LETTER", "KFS", "EMI_SCHEDULE"):
        gen_doc(conn, app, k, terms)
    step("SANCTIONED", None, "SYSTEM")
    disb = conn.execute("INSERT INTO disbursements (application_id) VALUES (%s) RETURNING id", (app,)).fetchone()["id"]
    conn.execute("UPDATE disbursements SET acceptance_recorded_at = now(), acceptance_recorded_by = %s, status = 'CHECKLIST_COMPLETE' WHERE id = %s", (lo, disb))
    conn.execute("UPDATE disbursements SET status = 'RELEASED', released_by = %s, simulated_reference = 'SIM-FULLFLOW0001', loan_account_number = 'LNFULLFLOW1', first_emi_date = '2026-12-05' WHERE id = %s", (am, disb))
    step("DISBURSED", am)
    assert status(conn, app) == "DISBURSED"

    trail = audit(conn, app)
    transitions = [(t["before_state"]["status"], t["after_state"]["status"], t["actor_type"]) for t in trail if t["event_type"] == "STATUS_TRANSITION"]
    assert transitions == [("INTAKE", "DOCS_PENDING", "STAFF"), ("DOCS_PENDING", "DISCREPANCY", "SYSTEM"), ("DISCREPANCY", "DOCS_PENDING", "STAFF"), ("DOCS_PENDING", "DOCS_VERIFIED", "STAFF"),
                           ("DOCS_VERIFIED", "AI_ASSESSED", "SYSTEM"), ("AI_ASSESSED", "FORWARDED", "STAFF"), ("FORWARDED", "UNDER_REVIEW", "STAFF"), ("UNDER_REVIEW", "APPROVED", "STAFF"),
                           ("APPROVED", "SANCTIONED", "SYSTEM"), ("SANCTIONED", "DISBURSED", "STAFF")]
    assert all(t["actor_id"] is not None and t["actor_role"] for t in trail if t["event_type"] == "DECISION_RECORDED")
    # nobody and nothing but a human ever decided: the only STAFF-with-decision-authority actors in the trail are the two managers
    assert {str(t["actor_id"]) for t in trail if t["event_type"] == "DECISION_RECORDED"} == {str(bm)}
    assert {t["actor_type"] for t in trail if t["event_type"] in ("DECISION_RECORDED", "DECISION_OVERRIDE", "TERM_CHANGE", "POLICY_ACTIVATED")} == {"STAFF"}
    assert any(t["event_type"] == "AGENT_RUN_INSERT" and t["actor_type"] == "AGENT" for t in trail)
    # the audit ids only ever grow
    ids = [t["id"] for t in trail]
    assert ids == sorted(ids)


# ---------------------------------------------------------------- backend loader and migration runner
def test_backend_loader_reads_role_and_permissions_from_the_database(conn):
    import app.db as db

    bm, lo = staff(conn, BM), staff(conn, LO)
    row = db.load_staff(bm, conn)
    assert row["role"] == BM and "policy.activate" in row["permissions"] and "decision.make" in row["permissions"]
    assert "decision.make" not in db.load_staff(lo, conn)["permissions"]
    assert db.load_staff(uuid.uuid4(), conn) is None


def test_staff_tx_records_who_acted_for_the_audit_triggers(conn):
    import app.db as db

    lo = staff(conn, LO)
    with db.staff_tx(conn, lo, LO):
        app = application(conn, lo)
    row = audit(conn, app, "APPLICATION_INSERT")[0]
    assert (row["actor_type"], str(row["actor_id"]), row["actor_role"]) == ("STAFF", str(lo), LO)


def test_migration_runner_is_idempotent_and_refuses_edited_migrations(db_url, tmp_path, monkeypatch):
    import apply_migrations as m

    admin = psycopg.connect(db_url, autocommit=True)
    name = f"t_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {name}")
    url = db_url.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(url, autocommit=True) as c:
        c.execute((Path(__file__).parent / "stub_supabase.sql").read_text(encoding="utf-8"))
    copy = tmp_path / "migrations"
    shutil.copytree(m.MIGRATIONS, copy)
    monkeypatch.setattr(m, "MIGRATIONS", copy)
    assert len(m.apply(url, verbose=False)) == 4
    assert m.apply(url, verbose=False) == []
    f = copy / "0004_security_and_seed.sql"
    f.write_text(f.read_text(encoding="utf-8") + "\n-- edited after being applied\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="already applied but has changed"):
        m.apply(url, verbose=False)
    admin.close()
