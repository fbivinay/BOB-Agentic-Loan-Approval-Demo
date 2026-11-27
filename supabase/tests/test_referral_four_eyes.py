"""Migration 0015 against real Postgres: the Assistant Manager's referral to the Branch Manager, and four-eyes (whoever processed an
application cannot review, decide or release it). Applications move through the real API, jobs and guards (fake external services)."""

from decimal import Decimal

from helpers import INSUFFICIENT_PRIVILEGE, application, decide, expect_error, force, staff
from test_applications_api import PNG, new_app, upload  # noqa: F401
from test_assessment_flow import aw, verified_etb  # noqa: F401  (fixtures)
from test_disbursement_flow import ALL
from test_documents_flow import run_all, world  # noqa: F401  (fixtures)
from test_enquiries_api import api, env  # noqa: F401  (fixtures)
from test_review_flow import approve, assessed, claimed, current, ver
from test_sanction_flow import sw  # noqa: F401  (fixture)

LO, AM, BM = "LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"
NOTE = "Customer asked for a smaller amount; please adjust the terms."


def test_assistant_manager_refers_a_file_to_the_branch_manager_who_adjusts_and_decides(api, aw):  # noqa: F811
    aid = claimed(api, aw, role=AM)
    url = f"/applications/{aid}/refer-to-branch-manager"
    assert api.as_(LO).post(url, json={"expected_version": ver(api, aid), "reason": NOTE}).status_code == 403  # not the officer's action
    am = api.as_(AM)
    assert am.post(url, json={"expected_version": ver(api, aid), "reason": "short"}).status_code == 422  # a note is required
    r = am.post(url, json={"expected_version": ver(api, aid), "reason": NOTE})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "FORWARDED" and r.json()["referred_to_role"] == BM

    v = am.get(f"/applications/{aid}/review").json()
    assert v["application"]["referral"]["note"] == NOTE and v["application"]["referral"]["by"] == "Test ASSISTANT_MANAGER"
    assert v["application"]["forwarded_by"] == "Test LOAN_OFFICER"  # the original forwarder stays on record
    assert v["application"]["assigned_to"] is None and v["actions"]["claim"] is False  # it is for the Branch Manager now
    assert any(h["event"] == "REFERRED_TO_BRANCH_MANAGER" and h["reason"] == NOTE for h in v["history"])
    row = next(x for x in am.get("/review/queue").json()["applications"] if x["id"] == aid)
    assert row["referral"] == {"to_role": BM, "note": NOTE, "by": "Test ASSISTANT_MANAGER"}
    assert am.post(f"/applications/{aid}/claim", json={"expected_version": ver(api, aid)}).status_code == 403

    bm = api.as_(BM)
    assert bm.get(f"/applications/{aid}/review").json()["actions"]["claim"] is True
    assert bm.post(f"/applications/{aid}/claim", json={"expected_version": ver(api, aid)}).status_code == 200
    t = current(bm, aid)
    smaller = str((Decimal(t["amount"]) * Decimal("0.9")).quantize(Decimal("1")))
    r = bm.post(f"/applications/{aid}/terms", json={"expected_version": ver(api, aid), "amount": smaller, "rate_bp": t["rate_bp"],
                                                     "tenure_months": t["tenure_months"], "reason": NOTE})
    assert r.status_code == 201, r.text
    r = approve(bm, aid)
    assert r.status_code == 201 and r.json()["status"] == "APPROVED" and Decimal(r.json()["approved_terms"]["amount"]) == Decimal(smaller)


def test_a_manager_who_forwarded_a_file_cannot_review_it_but_another_can(api, aw):  # noqa: F811
    aid = assessed(api, aw)
    am = api.as_(AM)
    assert am.post(f"/applications/{aid}/forward", json={"expected_version": ver(api, aid)}).status_code == 200  # managers can process files too
    v = am.get(f"/applications/{aid}/review").json()
    assert v["actions"]["four_eyes"] is True and v["actions"]["claim"] is False
    assert next(x for x in am.get("/review/queue").json()["applications"] if x["id"] == aid)["processed_by_me"] is True
    r = am.post(f"/applications/{aid}/claim", json={"expected_version": ver(api, aid)})
    assert r.status_code == 403 and "four-eyes" in r.json()["detail"]
    bm = api.as_(BM)
    assert bm.post(f"/applications/{aid}/claim", json={"expected_version": ver(api, aid)}).status_code == 200
    assert approve(bm, aid).status_code == 201


def test_the_manager_who_processed_a_file_cannot_release_its_money(api, sw):  # noqa: F811
    aid = assessed(api, sw)
    am = api.as_(AM)
    assert am.post(f"/applications/{aid}/forward", json={"expected_version": ver(api, aid)}).status_code == 200
    bm = api.as_(BM)
    assert bm.post(f"/applications/{aid}/claim", json={"expected_version": ver(api, aid)}).status_code == 200
    assert approve(bm, aid).status_code == 201
    run_all(sw)
    lo = api.as_(LO)
    assert lo.post(f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver(api, aid)}).status_code == 200
    assert lo.post(f"/applications/{aid}/disbursement/checklist", json={"expected_version": ver(api, aid), "items": ALL}).json()["status"] == "CHECKLIST_COMPLETE"
    am = api.as_(AM)
    assert am.get(f"/applications/{aid}/disbursement").json()["actions"]["release"] is False
    r = am.post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": True})
    assert r.status_code == 403 and "four-eyes" in r.json()["detail"]
    assert api.as_(BM).post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": True}).status_code == 201


def test_the_database_refuses_a_decision_by_the_person_who_ran_the_intake(conn):
    am = staff(conn, AM)
    app = application(conn, am)  # intake completed by this Assistant Manager
    force(conn, app, "UNDER_REVIEW", "RECOMMENDED_FOR_APPROVAL", assigned=am)
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "four-eyes"):
        decide(conn, app, am, "APPROVED")


def test_uploading_a_document_counts_as_processing(conn):
    lo, am = staff(conn, LO), staff(conn, AM)
    app = application(conn, lo)
    conn.execute("INSERT INTO documents (application_id, requirement, doc_type, storage_path, original_name, mime_type, size_bytes, sha256, uploaded_by) "
                 "VALUES (%s, 'PAN_CARD', 'PAN_CARD', %s, 'pan.pdf', 'application/pdf', 1000, %s, %s)", (app, f"a/{app}", "a" * 64, am))
    force(conn, app, "FORWARDED", "RECOMMENDED_FOR_APPROVAL")
    v = conn.execute("SELECT version FROM applications WHERE id = %s", (app,)).fetchone()["version"]
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "four-eyes"):
        conn.execute("SELECT app_claim(%s, %s, %s)", (app, am, v))
    assert conn.execute("SELECT processed_by(%s, %s) AS p", (app, staff(conn, AM))).fetchone()["p"] is False  # a different manager is free to review


def test_verifying_a_document_counts_as_processing(conn):
    lo, am = staff(conn, LO), staff(conn, AM)
    app = application(conn, lo)
    doc = conn.execute("INSERT INTO documents (application_id, requirement, doc_type, storage_path, original_name, mime_type, size_bytes, sha256, uploaded_by) "
                       "VALUES (%s, 'PAN_CARD', 'PAN_CARD', %s, 'pan.pdf', 'application/pdf', 1000, %s, %s) RETURNING id", (app, f"a/{app}", "a" * 64, lo)).fetchone()["id"]
    conn.execute("SELECT set_config('app.actor_type', 'STAFF', true), set_config('app.actor_id', %s, true), set_config('app.actor_role', %s, true)", (str(am), AM))
    conn.execute("UPDATE documents SET status = 'REJECTED', reviewed_by = %s, reviewed_at = now(), review_note = 'Unreadable scan; asked for a new copy' "
                 "WHERE id = %s", (am, doc))  # reviewing (here, rejecting) a document is processing work
    conn.execute("SELECT set_config('app.actor_type', '', true), set_config('app.actor_id', '', true), set_config('app.actor_role', '', true)")
    force(conn, app, "FORWARDED", "RECOMMENDED_FOR_APPROVAL")
    with expect_error(conn, INSUFFICIENT_PRIVILEGE, "four-eyes"):
        conn.execute("SELECT app_claim(%s, %s, %s)", (app, am, version_of(conn, app)))


def test_attesting_the_documents_counts_as_processing(conn):
    lo, bm = staff(conn, LO), staff(conn, BM)
    app = application(conn, lo)
    t = lambda to, who: conn.execute("SELECT app_transition(%s, %s, %s, 'STAFF', %s, 'Documents checked against the originals')",  # noqa: E731
                                     (app, to, version_of(conn, app), who))
    t("DOCS_PENDING", lo)
    t("DOCS_VERIFIED", bm)  # the Branch Manager attested the documents
    assert conn.execute("SELECT processed_by(%s, %s) AS p", (app, bm)).fetchone()["p"] is True


def version_of(conn, app) -> int:
    return conn.execute("SELECT version FROM applications WHERE id = %s", (app,)).fetchone()["version"]


def test_only_an_assistant_manager_can_refer_to_the_branch_manager(conn):
    bm = staff(conn, BM)
    app = application(conn, staff(conn, LO))
    force(conn, app, "UNDER_REVIEW", "REFER_FOR_FURTHER_REVIEW", assigned=bm)
    v = conn.execute("SELECT version FROM applications WHERE id = %s", (app,)).fetchone()["version"]
    with expect_error(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("SELECT app_refer_to_branch_manager(%s, %s, %s, %s)", (app, bm, v, NOTE))
