"""Phase 11 against real Postgres with the real migrations: acceptance, the pre-disbursement checklist, maker-checker release of a
simulated disbursement, and the separate confirmation email. Applications reach SANCTIONED through the real approval and sanction job."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app import outbox
from app.main import app
from app.policy import finance
from app.routers.enquiries import get_today
from test_applications_api import PNG, new_app, upload  # noqa: F401
from test_assessment_flow import aw, verified_etb  # noqa: F401  (fixtures)
from test_documents_flow import run_all, world  # noqa: F401  (fixtures)
from test_enquiries_api import TODAY, api, env  # noqa: F401  (fixtures)
from test_review_flow import approve, claimed, ver
from test_sanction_flow import sw  # noqa: F401  (fixture)

LO, AM, BM = "LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"
ALL = {"kfs_acknowledged": True, "loan_agreement_executed": True, "repayment_mandate_registered": True, "original_kyc_verified": True}


def sanctioned(api, sw):  # noqa: F811
    aid = claimed(api, sw)
    assert approve(api.as_(AM), aid).status_code == 201
    run_all(sw)
    assert api.get(f"/applications/{aid}/disbursement").json()["application"]["status"] == "SANCTIONED"
    return aid


def ready(api, sw):  # noqa: F811
    aid = sanctioned(api, sw)
    lo = api.as_(LO)
    assert lo.post(f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver(api, aid)}).status_code == 200
    assert lo.post(f"/applications/{aid}/disbursement/checklist", json={"expected_version": ver(api, aid), "items": ALL}).json()["status"] == "CHECKLIST_COMPLETE"
    return aid


def test_acceptance_checklist_and_a_managers_release_disburse_and_send_a_separate_confirmation(api, sw):  # noqa: F811
    aid = sanctioned(api, sw)
    lo = api.as_(LO)
    v = lo.get(f"/applications/{aid}/disbursement").json()
    assert "SIMULATED" in v["banner"] and v["disbursement"] is None and v["actions"] == {"record_acceptance": True, "update_checklist": False, "release": False}
    assert v["kfs_expired"] is False and "Demo permission" in v["permission_label"]
    preview = v["figures"]
    assert api.as_(AM).post(f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver(api, aid)}).status_code == 403  # tracking is the officer's
    lo = api.as_(LO)
    assert lo.post(f"/applications/{aid}/disbursement/checklist", json={"expected_version": ver(api, aid), "items": ALL}).status_code == 409  # acceptance first
    assert lo.post(f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver(api, aid)}).status_code == 200
    assert lo.post(f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver(api, aid)}).status_code == 409  # once
    r = lo.post(f"/applications/{aid}/disbursement/checklist", json={"expected_version": ver(api, aid), "items": {"kfs_acknowledged": True, "original_kyc_verified": True}})
    assert r.json()["status"] == "PENDING"
    r = lo.post(f"/applications/{aid}/disbursement/checklist", json={"expected_version": ver(api, aid), "note": "Mandate registered with the customer's bank",
                                                                     "items": {"loan_agreement_executed": True, "repayment_mandate_registered": True}})
    assert r.json()["status"] == "CHECKLIST_COMPLETE"  # earlier ticks are kept
    assert lo.post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": True}).status_code == 403

    am = api.as_(AM)
    assert am.get(f"/applications/{aid}/disbursement").json()["actions"]["release"] is True
    assert am.post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": False}).status_code == 422
    r = am.post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": True})
    assert r.status_code == 201, r.text
    rel = r.json()
    assert rel["status"] == "DISBURSED" and rel["simulated_reference"].startswith("SIM-") and rel["loan_account_number"].startswith("SIMLN")
    assert rel["first_emi_date"] == finance.add_months(TODAY, 1).isoformat() and rel["net_amount"] == preview["net_to_credit"]
    assert am.post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": True}).status_code == 409  # once

    run_all(sw)
    v = lo.get(f"/applications/{aid}/disbursement").json()
    assert v["application"]["status"] == "DISBURSED" and v["disbursement"]["released"]["by"] == "Test ASSISTANT_MANAGER" and not any(v["actions"].values())
    assert v["confirmation_email"]["status"] == "SENT" and v["confirmation_email"]["to_address"] == "delivered@resend.dev"
    payload, _ = sw["resend"].sent[-1]
    assert payload["subject"].startswith("Your personal loan is disbursed (simulated)") and "attachments" in payload and payload["attachments"] == []
    body = payload["text"]
    assert "SIMULATED DISBURSEMENT" in body and rel["loan_account_number"] in body and f"{finance.add_months(TODAY, 1):%d %b %Y}" in body and "Cooling-off" in body
    applicant = sw["admin"].execute("SELECT a.pan, a.mobile FROM applicants a JOIN applications ap ON ap.applicant_id = a.id WHERE ap.id = %s", (aid,)).fetchone()
    assert applicant["pan"] not in body + payload["html"] and applicant["mobile"] not in body + payload["html"]
    assert len(sw["resend"].sent) == 2  # the approval email, then the separate confirmation
    ev = sw["admin"].execute("SELECT actor_type, actor_role, reason FROM audit_events WHERE application_id = %s AND event_type = 'STATUS_TRANSITION' ORDER BY id DESC LIMIT 1",
                             (aid,)).fetchone()
    assert (ev["actor_type"], ev["actor_role"]) == ("STAFF", AM) and ev["reason"].startswith("Simulated disbursement SIM-")
    row = sw["admin"].execute("SELECT is_simulated, terms_id FROM disbursements WHERE application_id = %s", (aid,)).fetchone()
    assert row["is_simulated"] is True and row["terms_id"] is not None


def test_maker_checker_even_when_one_person_holds_both_demo_permissions(api, sw):  # noqa: F811
    adm = sw["admin"]
    adm.execute("INSERT INTO role_permissions (role, permission, kind, description) VALUES ('ASSISTANT_MANAGER', 'disbursement.track', 'CONFIGURABLE', 'test grant') "
                "ON CONFLICT DO NOTHING")
    original = sw["env"][AM]
    sw["env"][AM] = replace(original, permissions=original.permissions | {"disbursement.track"})  # the login now carries the granted permission
    try:
        aid = sanctioned(api, sw)
        am = api.as_(AM)
        am.post(f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver(api, aid)})
        am.post(f"/applications/{aid}/disbursement/checklist", json={"expected_version": ver(api, aid), "items": ALL})
        assert am.get(f"/applications/{aid}/disbursement").json()["actions"]["release"] is False
        assert "Maker-checker" in am.post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": True}).json()["detail"]
        with pytest.raises(Exception, match="maker-checker"):  # the database refuses it too
            with adm.transaction():
                adm.execute("UPDATE disbursements SET status = 'RELEASED', released_by = %s, simulated_reference = 'SIM-SAMEPERSON01', loan_account_number = 'SIMLN0000000001', "
                            "first_emi_date = '2026-12-26', net_amount = 1000 WHERE application_id = %s", (sw["env"][AM].user_id, aid))
        assert api.as_(BM).post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": True}).status_code == 201
    finally:
        sw["env"][AM] = original
        adm.execute("DELETE FROM role_permissions WHERE role = 'ASSISTANT_MANAGER' AND permission = 'disbursement.track'")


def test_after_the_kfs_validity_acceptance_needs_a_recorded_reason(api, sw):  # noqa: F811
    aid = sanctioned(api, sw)
    app.dependency_overrides[get_today] = lambda: TODAY + timedelta(days=10)
    lo = api.as_(LO)
    v = lo.get(f"/applications/{aid}/disbursement").json()
    assert v["kfs_expired"] is True
    assert "valid until" in lo.post(f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver(api, aid)}).json()["detail"]
    r = lo.post(f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver(api, aid),
                                                                      "late_reason": "Terms re-confirmed with the customer in writing; rate and charges unchanged"})
    assert r.status_code == 200 and r.json()["late"] is True
    assert lo.get(f"/applications/{aid}/disbursement").json()["disbursement"]["acceptance"]["late_reason"].startswith("Terms re-confirmed")


def test_unticking_an_item_reopens_the_checklist_and_blocks_release(api, sw):  # noqa: F811
    aid = ready(api, sw)
    lo = api.as_(LO)
    r = lo.post(f"/applications/{aid}/disbursement/checklist", json={"expected_version": ver(api, aid), "items": {"repayment_mandate_registered": False}})
    assert r.json()["status"] == "PENDING"
    assert sw["admin"].execute("SELECT checklist_completed_by FROM disbursements WHERE application_id = %s", (aid,)).fetchone()["checklist_completed_by"] is None
    assert api.as_(AM).post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": True}).status_code == 409


def test_disbursement_steps_need_a_sanctioned_application(api, sw):  # noqa: F811
    aid = claimed(api, sw)
    lo = api.as_(LO)
    assert lo.post(f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver(api, aid)}).status_code == 409
    assert lo.get(f"/applications/{aid}/disbursement").json()["figures"] is None


def test_a_failed_confirmation_email_never_undoes_the_disbursement(api, sw):  # noqa: F811
    aid = ready(api, sw)
    sw["resend"].fail = [outbox.ResendError(422, "validation_error")]
    assert api.as_(AM).post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": True}).status_code == 201
    run_all(sw)
    v = api.get(f"/applications/{aid}/disbursement").json()
    assert v["application"]["status"] == "DISBURSED" and v["confirmation_email"]["status"] == "FAILED"
