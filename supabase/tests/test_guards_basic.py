"""Permissions, deny-by-default access, append-only audit, enquiries, workflow transitions, human work records."""

import json
import uuid

import psycopg
import pytest
from helpers import (CHECK_VIOLATION, INSUFFICIENT_PRIVILEGE, SERIALIZATION_FAILURE, application, applicant, assessed_run, audit, expect_error, force, staff, status,
                     under_review, version)

LO, AM, BM = "LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"


def as_role(conn, role: str, sql: str) -> None:
    """Run one statement as a database role. Use inside expect_error: the savepoint rollback also restores the role."""
    conn.execute(f"SET LOCAL ROLE {role}")
    conn.execute(sql)

# The agreed permission matrix (role x permission). Anything not listed must be denied.
# Migration 0015: the managers can also run an application from enquiry to forward; the Assistant Manager can refer a file to the Branch Manager.
SHARED_INTAKE = {"enquiry.manage", "application.intake", "document.verify", "application.forward"}
MATRIX = {
    LO: {"enquiry.manage", "application.intake", "document.verify", "application.forward", "assessment.view", "audit.view", "disbursement.track", "disbursement.view", "policy.view"},
    AM: {"enquiry.view", "assessment.view", "audit.view", "decision.make", "disbursement.release", "disbursement.view", "analytics.view", "system.view", "policy.view",
         *SHARED_INTAKE, "application.escalate"},
    BM: {"enquiry.view", "assessment.view", "audit.view", "decision.make", "application.takeover", "application.refer", "terms.adjust", "policy.activate", "disbursement.release",
         "disbursement.view", "analytics.view", "system.view", "policy.view", *SHARED_INTAKE},
}
EVERY = set().union(*MATRIX.values())


@pytest.mark.parametrize("role", [LO, AM, BM])
def test_permission_matrix_is_exact(conn, role):
    uid = staff(conn, role)
    for perm in EVERY:
        got = conn.execute("SELECT has_permission(%s, %s) AS p", (uid, perm)).fetchone()["p"]
        assert got == (perm in MATRIX[role]), f"{role} / {perm}"


def test_only_the_three_banking_roles_exist(conn):
    uid = uuid.uuid4()
    conn.execute("INSERT INTO auth.users (id) VALUES (%s)", (uid,))
    for bad in ("ADMIN", "OWNER", "AGENT", "SYSTEM", "loan_officer"):
        with expect_error(conn, CHECK_VIOLATION):
            conn.execute("INSERT INTO staff_profiles (user_id, full_name, email, role) VALUES (%s, 'x', 'x@example.invalid', %s)", (uid, bad))


def test_inactive_staff_hold_no_permissions(conn):
    uid = staff(conn, BM, active=False)
    assert conn.execute("SELECT has_permission(%s, 'decision.make') AS p, staff_role(%s) AS r", (uid, uid)).fetchone() == {"p": False, "r": None}


@pytest.mark.parametrize("role,perm", [(LO, "decision.make"), (LO, "application.takeover"), (AM, "application.takeover"), (AM, "application.refer"), (AM, "terms.adjust"),
                                        (LO, "terms.adjust"), (LO, "application.refer")])
def test_configuration_can_never_grant_fixed_authority_to_the_wrong_role(conn, role, perm):
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("INSERT INTO role_permissions (role, permission, kind, description) VALUES (%s, %s, 'CONFIGURABLE', 'attempt')", (role, perm))


def test_configurable_permissions_are_the_demo_ones_and_labelled(conn):
    rows = conn.execute("SELECT permission FROM role_permissions WHERE kind = 'CONFIGURABLE'").fetchall()
    assert {r["permission"] for r in rows} == {"disbursement.track", "disbursement.release", "policy.activate", *SHARED_INTAKE, "application.escalate"}
    d = conn.execute("SELECT description FROM role_permissions WHERE permission = 'policy.activate'").fetchone()["description"]
    assert "not Bank of Baroda" in d


def test_demo_limits_are_labelled_as_assumptions(conn):
    rows = conn.execute("SELECT confidence FROM demo_config").fetchall()
    assert rows and {r["confidence"] for r in rows} == {"DEMO_ASSUMPTION"}
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("INSERT INTO demo_config (key, value, unit, confidence, description) VALUES ('x', 1, 'u', 'UNVERIFIED_CANDIDATE', 'd')")


# ---------------------------------------------------------------- deny by default for the browser roles
TABLES = ["staff_profiles", "role_permissions", "applicants", "enquiries", "applications", "decisions", "audit_events", "policy_rule_sets", "disbursements", "jobs", "email_outbox"]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_browser_roles_cannot_touch_any_table(conn, role):
    for t in TABLES:
        with expect_error(conn, INSUFFICIENT_PRIVILEGE):
            as_role(conn, role, f"SELECT 1 FROM {t} LIMIT 1")
        with expect_error(conn, INSUFFICIENT_PRIVILEGE):
            as_role(conn, role, f"DELETE FROM {t}")


def test_every_public_table_has_row_level_security_on(conn):
    off = conn.execute("SELECT tablename FROM pg_tables t JOIN pg_class c ON c.relname = t.tablename WHERE schemaname = 'public' "
                       "AND tablename <> 'schema_migrations' AND NOT c.relrowsecurity").fetchall()
    assert off == []


def test_tables_created_later_do_not_become_readable_by_the_browser_roles(conn):
    conn.execute("CREATE TABLE future_table (id int)")
    for role in ("anon", "authenticated"):
        with expect_error(conn, INSUFFICIENT_PRIVILEGE):
            as_role(conn, role, "SELECT * FROM future_table")


# ---------------------------------------------------------------- audit is append-only
def _one_audit_row(conn):
    return conn.execute("SELECT audit_write('SYSTEM', NULL, NULL, 'TEST', 'test', '1', NULL, NULL, NULL, NULL) AS id").fetchone()["id"]


def test_audit_rows_cannot_be_updated_deleted_or_truncated(conn):
    i = _one_audit_row(conn)
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "append-only"):
        conn.execute("UPDATE audit_events SET event_type = 'EDITED' WHERE id = %s", (i,))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "append-only"):
        conn.execute("DELETE FROM audit_events WHERE id = %s", (i,))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "append-only"):
        conn.execute("TRUNCATE audit_events")


def test_even_the_backend_service_role_cannot_change_the_audit_trail(conn):
    i = _one_audit_row(conn)
    for sql in ("UPDATE audit_events SET reason = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
        with expect_error(conn, INSUFFICIENT_PRIVILEGE):
            as_role(conn, "service_role", sql)
    assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE id = %s", (i,)).fetchone()["n"] == 1


def test_staff_audit_rows_must_name_the_person(conn):
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("SELECT audit_write('STAFF', NULL, NULL, 'X', 'test', '1', NULL, NULL, NULL, NULL)")
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("SELECT audit_write('ROBOT', NULL, NULL, 'X', 'test', '1', NULL, NULL, NULL, NULL)")


def test_policy_log_decisions_terms_and_changes_are_immutable_too(conn):
    for t in ("policy_activation_log", "decisions", "term_changes", "application_terms"):
        with expect_error(conn, INSUFFICIENT_PRIVILEGE, "append-only"):
            conn.execute(f"TRUNCATE {t} CASCADE")


def test_audit_actor_comes_from_the_transaction_settings(conn):
    lo = staff(conn, LO)
    conn.execute("SELECT set_config('app.actor_type', 'STAFF', true), set_config('app.actor_id', %s, true), set_config('app.actor_role', 'LOAN_OFFICER', true)", (str(lo),))
    app = application(conn, lo)
    row = audit(conn, app, "APPLICATION_INSERT")[0]
    assert (row["actor_type"], str(row["actor_id"]), row["actor_role"]) == ("STAFF", str(lo), LO)


def test_missing_actor_settings_never_attribute_work_to_a_person(conn):
    lo = staff(conn, LO)
    app = application(conn, lo)
    assert audit(conn, app, "APPLICATION_INSERT")[0]["actor_type"] == "SYSTEM"


def test_agent_runs_are_always_audited_as_agent_activity(conn):
    lo = staff(conn, LO)
    app = application(conn, lo)
    conn.execute("SELECT set_config('app.actor_type', 'STAFF', true), set_config('app.actor_id', %s, true), set_config('app.actor_role', 'LOAN_OFFICER', true)", (str(lo),))
    conn.execute("INSERT INTO agent_runs (application_id, graph) VALUES (%s, 'DOCUMENT')", (app,))
    assert [a["actor_type"] for a in audit(conn, app, "AGENT_RUN_INSERT")] == ["AGENT"]


# ---------------------------------------------------------------- enquiries (the draft stage)
def test_indicative_figures_must_carry_the_not_an_offer_label(conn):
    lo = staff(conn, LO)
    a = applicant(conn, lo)
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("INSERT INTO enquiries (applicant_id, created_by, indicative) VALUES (%s, %s, %s)", (a, lo, json.dumps({"amount": 400000, "rate_bp": 1250})))
    e = conn.execute("INSERT INTO enquiries (applicant_id, created_by, requested_amount, requested_tenure_months, indicative) VALUES (%s, %s, 400000, 36, %s) RETURNING reference, status",
                     (a, lo, json.dumps({"amount": 400000, "disclaimer": "Indicative estimate, not a loan offer"}))).fetchone()
    assert e["status"] == "DRAFT" and e["reference"].startswith("ENQ-")


def test_a_draft_has_no_path_to_agents_bureau_or_workflow_tables(conn):
    refs = conn.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' AND column_name LIKE '%enquiry%' "
                        "AND table_name NOT IN ('enquiries')").fetchall()
    assert {(r["table_name"], r["column_name"]) for r in refs} == {("applications", "enquiry_id")}
    cols = {r["column_name"] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'enquiries'").fetchall()}
    assert not cols & {"ai_recommendation", "bureau_score", "decision", "policy_rule_set_id", "agent_run_id"}


def test_converted_enquiry_must_point_at_its_application(conn):
    lo = staff(conn, LO)
    a = applicant(conn, lo)
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("INSERT INTO enquiries (applicant_id, created_by, status) VALUES (%s, %s, 'CONVERTED')", (a, lo))


def test_pan_must_be_the_synthetic_form_or_a_real_pan_format(conn):
    for bad in ("ABCD1234F", "SYNP123456", "synp0001234k", "SYNP00012345K", "abcde1234f"):
        with expect_error(conn, CHECK_VIOLATION):
            conn.execute("INSERT INTO applicants (bank_relationship, full_name, date_of_birth, mobile, pan) VALUES ('NTB', 'x', '1990-01-01', '9876543210', %s)", (bad,))


def test_mobile_numbers_must_look_like_indian_mobiles(conn):
    for bad in ("12345", "5876543210", "98765432101", "98765abcde"):
        with expect_error(conn, CHECK_VIOLATION):
            conn.execute("INSERT INTO applicants (bank_relationship, full_name, date_of_birth, mobile) VALUES ('NTB', 'x', '1990-01-01', %s)", (bad,))


# ---------------------------------------------------------------- status transitions
def test_status_cannot_be_edited_directly(conn):
    lo = staff(conn, LO)
    app = application(conn, lo)
    for col, val in (("status", "'APPROVED'"), ("ai_recommendation", "'RECOMMENDED_FOR_APPROVAL'"), ("version", "99"), (f"assigned_to", f"'{lo}'")):
        with expect_error(conn, INSUFFICIENT_PRIVILEGE, "workflow columns"):
            conn.execute(f"UPDATE applications SET {col} = {val} WHERE id = %s", (app,))
    assert status(conn, app) == "INTAKE"


def test_applications_cannot_be_deleted(conn):
    app = application(conn, staff(conn, LO))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("DELETE FROM applications WHERE id = %s", (app,))


def test_intake_to_forwarded_by_the_right_people(conn):
    lo, am = staff(conn, LO), staff(conn, AM, active=False)  # an inactive account holds no permissions
    app = application(conn, lo)
    t = lambda to, actor, who="STAFF", reason="Documents checked against the originals": conn.execute(  # noqa: E731
        "SELECT * FROM app_transition(%s, %s, %s, %s, %s, %s)", (app, to, version(conn, app), who, actor, reason)).fetchone()
    assert t("DOCS_PENDING", lo)["status"] == "DOCS_PENDING"
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        t("DOCS_VERIFIED", am)  # someone without document.verify does not attest documents
    assert t("DOCS_VERIFIED", lo)["status"] == "DOCS_VERIFIED"
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        t("AI_ASSESSED", lo)  # AI_ASSESSED is system-only
    with expect_error(conn, CHECK_VIOLATION):
        t("FORWARDED", lo)  # cannot skip the assessment


def test_attestation_needs_a_human_not_the_system(conn):
    lo = staff(conn, LO)
    app = application(conn, lo)
    conn.execute("SELECT app_transition(%s, 'DOCS_PENDING', %s, 'STAFF', %s, NULL)", (app, version(conn, app), lo))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "human"):
        conn.execute("SELECT app_transition(%s, 'DOCS_VERIFIED', %s, 'SYSTEM', NULL, NULL)", (app, version(conn, app)))


def test_system_actor_may_not_carry_a_user_id_and_unknown_actor_types_fail(conn):
    lo = staff(conn, LO)
    app = application(conn, lo)
    with expect_error(conn, match="must not carry"):
        conn.execute("SELECT app_transition(%s, 'DOCS_PENDING', %s, 'SYSTEM', %s, NULL)", (app, version(conn, app), lo))
    with expect_error(conn, match="unknown actor"):
        conn.execute("SELECT app_transition(%s, 'DOCS_PENDING', %s, 'AGENT', NULL, NULL)", (app, version(conn, app)))


def test_unknown_user_ids_have_no_authority(conn):
    app = application(conn, staff(conn, LO))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("SELECT app_transition(%s, 'DOCS_PENDING', %s, 'STAFF', %s, NULL)", (app, version(conn, app), uuid.uuid4()))


def test_stale_versions_are_rejected_with_a_serialization_failure(conn):
    lo = staff(conn, LO)
    app = application(conn, lo)
    v = version(conn, app)
    conn.execute("SELECT app_transition(%s, 'DOCS_PENDING', %s, 'STAFF', %s, NULL)", (app, v, lo))
    with expect_error(conn, SERIALIZATION_FAILURE):
        conn.execute("SELECT app_transition(%s, 'DOCS_VERIFIED', %s, 'STAFF', %s, NULL)", (app, v, lo))


def test_forwarding_and_review_pickup_need_the_right_permissions(conn):
    lo, am, nobody = staff(conn, LO), staff(conn, AM), staff(conn, AM, active=False)
    app = application(conn, lo)
    force(conn, app, "AI_ASSESSED", "REFER_FOR_FURTHER_REVIEW")
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("SELECT app_transition(%s, 'FORWARDED', %s, 'STAFF', %s, NULL)", (app, version(conn, app), nobody))
    conn.execute("SELECT app_transition(%s, 'FORWARDED', %s, 'STAFF', %s, NULL)", (app, version(conn, app), lo))
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("SELECT app_claim(%s, %s, %s)", (app, lo, version(conn, app)))  # a Loan Officer cannot pick up a review
    r = conn.execute("SELECT * FROM app_claim(%s, %s, %s)", (app, am, version(conn, app))).fetchone()
    assert (r["status"], r["assigned_to"]) == ("UNDER_REVIEW", am)


def test_every_transition_is_audited_with_actor_and_before_after(conn):
    lo = staff(conn, LO)
    app = application(conn, lo)
    conn.execute("SELECT app_transition(%s, 'DOCS_PENDING', %s, 'STAFF', %s, NULL)", (app, version(conn, app), lo))
    t = audit(conn, app, "STATUS_TRANSITION")[0]
    assert (t["actor_type"], str(t["actor_id"]), t["actor_role"]) == ("STAFF", str(lo), LO)
    assert t["before_state"]["status"] == "INTAKE" and t["after_state"]["status"] == "DOCS_PENDING"


# ---------------------------------------------------------------- human work records
def test_documents_are_uploaded_by_staff_with_intake_permission(conn):
    lo, am = staff(conn, LO), staff(conn, AM, active=False)  # an inactive account holds no permissions
    app = application(conn, lo)
    row = ("INSERT INTO documents (application_id, requirement, doc_type, storage_path, original_name, mime_type, size_bytes, sha256, uploaded_by) "
           "VALUES (%s, 'PAN_CARD', 'PAN_CARD', %s, 'pan.pdf', 'application/pdf', 1000, %s, %s)")
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute(row, (app, f"a/{uuid.uuid4()}", "a" * 64, am))
    conn.execute(row, (app, f"a/{uuid.uuid4()}", "a" * 64, lo))
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute(row, (app, f"a/{uuid.uuid4()}", "not-a-hash", lo))


def test_discrepancies_are_resolved_by_a_loan_officer_with_a_note(conn):
    lo, am = staff(conn, LO), staff(conn, AM, active=False)  # an inactive account holds no permissions
    app = application(conn, lo)
    d = conn.execute("INSERT INTO discrepancies (application_id, field, form_value, document_value) VALUES (%s, 'name', 'Asha Verma', 'A. Verma') RETURNING id", (app,)).fetchone()["id"]
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute("UPDATE discrepancies SET status = 'RESOLVED', resolution = 'ACCEPT', resolved_by = %s WHERE id = %s", (lo, d))  # no note
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("UPDATE discrepancies SET status = 'RESOLVED', resolution = 'ACCEPT', resolution_note = 'initial only', resolved_by = %s WHERE id = %s", (am, d))
    conn.execute("UPDATE discrepancies SET status = 'RESOLVED', resolution = 'ACCEPT', resolution_note = 'initial only on the card', resolved_by = %s WHERE id = %s", (lo, d))
    assert audit(conn, app, "DISCREPANCY_UPDATE")


def test_extraction_corrections_are_human_and_manual_only(conn):
    lo, am = staff(conn, LO), staff(conn, AM, active=False)  # an inactive account holds no permissions
    app = application(conn, lo)
    doc = conn.execute("INSERT INTO documents (application_id, requirement, doc_type, storage_path, original_name, mime_type, size_bytes, sha256, uploaded_by) "
                       "VALUES (%s, 'SALARY_SLIPS_3M', 'SALARY_SLIP', %s, 's.pdf', 'application/pdf', 10, %s, %s) RETURNING id", (app, f"a/{uuid.uuid4()}", "b" * 64, lo)).fetchone()["id"]
    ins = "INSERT INTO extraction_corrections (document_id, field, value_before, value_after, corrected_by, source) VALUES (%s, 'net_pay', '24100', '24,100', %s, %s)"
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute(ins, (doc, am, "MANUAL"))
    with expect_error(conn, CHECK_VIOLATION):
        conn.execute(ins, (doc, lo, "AI"))
    conn.execute(ins, (doc, lo, "MANUAL"))
