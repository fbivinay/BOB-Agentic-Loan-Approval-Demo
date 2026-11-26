"""Phase 8 against real Postgres with the real migrations: the assessment graph from attestation to AI_ASSESSED, policy citations, the
checked explanation, eligible terms, and the AI boundaries. BigQuery, the knowledge base and Gemini are fakes; the database is real."""

import json
from decimal import Decimal

import pytest

from app import jobs, policy_kb
from app.assessment import Summary
from test_applications_api import PNG, ntb_ready, new_app, upload  # noqa: F401
from test_documents_flow import FORM_PDF, SLIP_PDF, doc_row, run_all, world  # noqa: F401  (fixtures)
from test_enquiries_api import ETB_SALARIED, TODAY, api, counts, env  # noqa: F401  (fixtures)

REC_OK, REC_REFER, REC_NO = "RECOMMENDED_FOR_APPROVAL", "REFER_FOR_FURTHER_REVIEW", "POTENTIALLY_INELIGIBLE"
BUREAU_CLEAN = {"bureau_history_status": "ESTABLISHED", "bureau_score": 768, "tradeline_count": 2, "active_tradeline_count": 1, "oldest_tradeline_age_months": 64,
                "max_dpd_12m": 0, "late_payment_months_24m": 0, "has_written_off": False, "has_settled": False, "enquiries_last_6m": 1,
                "total_monthly_obligations": Decimal("5400.00"), "existing_obligation_pct": 8.7, "pan_status": "VALID", "name_match_score": Decimal("0.98"),
                "aadhaar_check": "MATCH", "address_match": True}


class CreditLakehouse:
    def __init__(self):
        self.records = {"BOB-C0027": ETB_SALARIED | BUREAU_CLEAN}
        self.calls, self.fail = [], None

    def credit_record(self, *, customer_id=None, pan=None):
        self.calls.append(customer_id or pan)
        if self.fail:
            raise self.fail
        if customer_id:
            return self.records.get(customer_id)
        return next((r for r in self.records.values() if r["pan"] == pan), None)


def chunk(cid, source, text, section):
    return {"chunk_id": cid, "source_id": source, "version": 1, "publisher": "Reserve Bank of India" if source.startswith("rbi") else "Bank of Baroda",
            "title": source, "url": f"https://example.invalid/{source}", "fetched_at": "2026-11-26 06:00:00+00:00", "section": section, "page_no": None,
            "text": text, "extraction_method": "HTML_TEXT"}


class FakeKB:
    def __init__(self):
        rows = [chunk("c1", "rbi_cb_responsible_business_conduct", "The Key Facts Statement shall include the annual percentage rate (APR) computation sheet and "
                      "the amortisation schedule of the loan; the KFS is given to the borrower before the loan agreement.", "B. Key Facts Statement (KFS)"),
                chunk("c2", "rbi_cb_responsible_business_conduct", "Penal charges in loan accounts for non-compliance with material terms shall not be capitalised "
                      "and no further interest is computed on penal charges.", "D. Penal Charges in Loan Accounts"),
                chunk("c3", "bob_personal_loan", "Interest rates and charges: processing charges and penal interest for the personal loan are as notified.",
                      "Baroda Personal Loan : Interest rates & charges"),
                chunk("c4", "rbi_cb_kyc", "Customer due diligence for individuals: the PAN or Form 60 and an officially valid document, with Aadhaar e-KYC "
                      "or offline verification where applicable.", "A. CDD Procedure in case of Individuals"),
                chunk("c5", "bob_personal_loan", "Income proof: salary slips for the last 3 months, income tax returns and bank statement for 6 months.",
                      "Documents Required")]
        self._index = policy_kb.Index(rows, min_score=0.5)  # a five-chunk corpus scores far lower than the real one

    def index(self):
        return self._index


class SummaryGemini:
    def __init__(self, base):
        self.base, self.prompts, self.reply = base, [], None

    def extract(self, doc_type, pages):
        return self.base.extract(doc_type, pages)

    def generate(self, text, schema):
        self.prompts.append(text)
        if isinstance(self.reply, Exception):
            raise self.reply
        facts = json.loads(text.split("Facts (JSON):\n", 1)[1])
        return self.reply or Summary(summary=f"The engine's result is {facts['recommendation']} on the evidence on file.", points=facts["reasons"][:2])


@pytest.fixture
def aw(world, env):  # noqa: F811
    lh, gem = CreditLakehouse(), SummaryGemini(world["gemini"])
    d = world["deps"]
    d.lakehouse, d.kb, d.today, d.gemini = lh, FakeKB(), lambda: TODAY, gem
    return world | {"lakehouse": lh, "summary": gem, "env": env}


def verified_etb(api, w):  # noqa: F811
    """BOB-C0027 (existing salaried customer) walked to DOCS_VERIFIED through the API; the attestation queues the assessment."""
    aid = new_app(api, customer_id="BOB-C0027")
    lo = api.as_("LOAN_OFFICER")
    assert lo.post(f"/applications/{aid}/intake/complete", json={"expected_version": 1}).status_code == 200
    docs = [upload(lo, aid, "APPLICATION_FORM", "APPLICATION_FORM", data=FORM_PDF).json(), upload(lo, aid, "PHOTOGRAPH", "PHOTOGRAPH", data=PNG, name="p.png", mime="image/png").json(),
            upload(lo, aid, "SALARY_SLIPS_3M", "SALARY_SLIP", data=SLIP_PDF).json()]
    run_all(w)
    assert lo.patch(f"/applications/{aid}/documents/{docs[0]['id']}/fields", json={"values": {"signed": "yes"}}).status_code == 200
    for d in docs:
        assert lo.post(f"/applications/{aid}/documents/{d['id']}/verify").status_code == 200
    r = lo.post(f"/applications/{aid}/documents/attest", json={"expected_version": 2, "attestation": "I have checked every document against the originals shown."})
    assert r.status_code == 200 and r.json()["status"] == "DOCS_VERIFIED"
    return aid


def app_row(w, aid):
    return w["admin"].execute("SELECT * FROM applications WHERE id = %s", (aid,)).fetchone()


def test_attestation_queues_the_assessment_which_records_a_recommendation_with_citations_and_eligible_terms(api, aw):  # noqa: F811
    aid = verified_etb(api, aw)
    assert aw["admin"].execute("SELECT count(*) AS n FROM jobs WHERE application_id = %s AND kind = 'assess_application' AND status = 'QUEUED'", (aid,)).fetchone()["n"] == 1
    before = counts(aw["env"])
    run_all(aw)
    a = app_row(aw, aid)
    assert a["status"] == "AI_ASSESSED" and a["ai_recommendation"] == REC_OK and a["policy_rule_set_id"] is not None
    assert str(a["policy_rule_set_id"]) == a["documents_checklist"]["rule_set_id"]  # evaluated under the version frozen at intake
    assert counts(aw["env"])["decisions"] == before["decisions"]  # a recommendation, never a decision

    for role in ("LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"):
        assert api.as_(role).get(f"/applications/{aid}/assessment").status_code == 200
    v = api.get(f"/applications/{aid}/assessment").json()
    assert v["state"] == "RECORDED" and v["label"] == "AI recommendation, human decides."
    run, out = v["run"], v["run"]["output"]
    assert run["status"] == "DONE" and run["recommendation"] == REC_OK and str(run["id"]) == str(a["ai_recommendation_run_id"])
    assert [s["step"] for s in run["steps"]] == ["load_application", "load_credit_record", "gather_evidence", "evaluate_policy", "retrieve_policy", "write_summary"]
    assert out["evidence"]["income"]["source"].startswith("Bank of Baroda account credits") and out["evidence"]["obligations"]["monthly"] == "5400.00"
    rules = {r["id"]: r for r in out["assessment"]["rules"]}
    assert len(rules) == 15 and rules["R07"]["status"] == "PASS" and rules["R10"]["status"] == "PASS"
    assert all(out["citations"]["terms"][t] for t in policy_kb.TERMS_QUERIES)  # KFS / APR, penal charges, rates: cited with source and version
    assert out["citations"]["terms"]["Penal charges"][0]["url"].endswith("rbi_cb_responsible_business_conduct")
    assert out["summary"]["source"] == "GEMINI" and out["summary"]["label"] == "AI recommendation, human decides."
    f = out["assessment"]["figures"]
    assert v["eligible_terms"] == {"amount": f"{Decimal(f['recommended_amount']):.2f}", "rate_bp": f["indicative_rate_bp"], "tenure_months": f["recommended_tenure_months"],
                                   "label": v["eligible_terms"]["label"]}
    # nothing identifying reaches the model: no name, PAN, customer ID or mobile
    prompt = aw["summary"].prompts[-1]
    for secret in ("Kavya", "SYNP0213813F", "BOB-C0027", "9845012376"):
        assert secret not in prompt
    ev = aw["admin"].execute("SELECT actor_type FROM audit_events WHERE application_id = %s AND event_type = 'STATUS_TRANSITION' ORDER BY id DESC LIMIT 1", (aid,)).fetchone()
    assert ev["actor_type"] == "SYSTEM"


def test_an_explanation_with_invented_numbers_or_decision_language_is_replaced_by_the_template(api, aw):  # noqa: F811
    aw["summary"].reply = Summary(summary="The loan is approved for 9,99,999 at a great rate.", points=[])
    aid = verified_etb(api, aw)
    run_all(aw)
    out = api.get(f"/applications/{aid}/assessment").json()["run"]["output"]
    assert out["recommendation"] == REC_OK  # the engine's result stands either way
    assert out["summary"]["source"] == "TEMPLATE" and "discarded" in out["summary"]["note"] and "approved" not in out["summary"]["text"]


def test_no_gemini_or_a_gemini_error_still_gives_an_assessment_with_the_template(api, aw):  # noqa: F811
    aw["summary"].reply = RuntimeError("503 UNAVAILABLE")
    aid = verified_etb(api, aw)
    run_all(aw)
    out = api.get(f"/applications/{aid}/assessment").json()["run"]["output"]
    assert app_row(aw, aid)["status"] == "AI_ASSESSED" and out["summary"]["source"] == "TEMPLATE" and "unavailable" in out["summary"]["note"]


def test_risky_bureau_data_gives_potentially_ineligible_with_the_demo_assumption_note(api, aw):  # noqa: F811
    aw["lakehouse"].records["BOB-C0027"] |= {"has_written_off": True}
    aid = verified_etb(api, aw)
    run_all(aw)
    v = api.get(f"/applications/{aid}/assessment").json()
    r08 = next(r for r in v["run"]["output"]["assessment"]["rules"] if r["id"] == "R08")
    assert v["application"]["ai_recommendation"] == REC_NO and r08["status"] == "FAIL" and "demo assumption" in r08["message"]
    assert "R08" in v["run"]["output"]["citations"]["by_rule"]


def force_verified(w, aid):
    """Test set-up: put an intake-complete application at DOCS_VERIFIED without documents (it bypasses the guard on purpose)."""
    with w["admin"].transaction():
        w["admin"].execute("SET LOCAL session_replication_role = replica")  # set-up only: skips every trigger for this one update
        w["admin"].execute("UPDATE applications SET status = 'DOCS_VERIFIED', version = version + 1 WHERE id = %s", (aid,))
    jobs.enqueue(w["admin"], "assess_application", {"application_id": str(aid)}, aid, f"assess:test:{aid}")


def test_new_to_bank_with_no_bureau_record_is_new_to_credit_never_failed_for_it_and_the_pan_is_referred(api, aw):  # noqa: F811
    aid = ntb_ready(api)
    assert api.as_("LOAN_OFFICER").post(f"/applications/{aid}/intake/complete", json={"expected_version": 1}).status_code == 200
    force_verified(aw, aid)
    run_all(aw)
    out = api.get(f"/applications/{aid}/assessment").json()["run"]["output"]
    rules = {r["id"]: r for r in out["assessment"]["rules"]}
    assert aw["lakehouse"].calls[-1] == "SYNP0457812K"  # found only by PAN, as a bureau pull would be
    assert out["credit_lookup"]["found"] is False and out["assessment"]["segment"] == "NTB_NTC"
    assert rules["R07"]["status"] == "SKIPPED" and rules["R08"]["status"] == "SKIPPED"  # no history is skipped, not failed
    assert rules["R03"]["status"] == "NEEDS_REVIEW"  # no verified income evidence on file
    assert rules["R11"]["status"] == "NEEDS_REVIEW" and "NOT_FOUND" in rules["R11"]["message"]
    assert out["recommendation"] == REC_REFER and app_row(aw, aid)["ai_recommendation"] == REC_REFER
    assert "R11" in out["citations"]["by_rule"] and out["citations"]["by_rule"]["R11"][0]["source_id"] == "rbi_cb_kyc"
    assert api.get(f"/applications/{aid}/assessment").json()["eligible_terms"] is not None or out["assessment"]["figures"]["recommended_amount"] == "0"


def test_a_pan_record_with_a_different_date_of_birth_is_not_used(api, aw):  # noqa: F811
    aw["lakehouse"].records["OTHER"] = ETB_SALARIED | BUREAU_CLEAN | {"customer_id": "BOB-C0999", "pan": "SYNP0457812K", "date_of_birth": "1970-01-01"}
    aid = ntb_ready(api)
    assert api.as_("LOAN_OFFICER").post(f"/applications/{aid}/intake/complete", json={"expected_version": 1}).status_code == 200
    force_verified(aw, aid)
    run_all(aw)
    out = api.get(f"/applications/{aid}/assessment").json()["run"]["output"]
    rules = {r["id"]: r for r in out["assessment"]["rules"]}
    assert out["credit_lookup"]["found"] is False and "date of birth differs" in out["credit_lookup"]["note"]
    assert rules["R07"]["status"] == "SKIPPED" and "DOB_MISMATCH" in rules["R11"]["message"]  # someone else's bureau file is never used


def test_failures_retry_then_stop_and_the_loan_officer_can_queue_it_again(api, aw):  # noqa: F811
    aw["lakehouse"].fail = RuntimeError("BigQuery unavailable")
    aid = verified_etb(api, aw)
    run_all(aw, rounds=8)
    v = api.get(f"/applications/{aid}/assessment").json()
    assert v["state"] == "FAILED" and app_row(aw, aid)["status"] == "DOCS_VERIFIED" and v["run"]["status"] == "FAILED"
    ver = app_row(aw, aid)["version"]
    assert api.as_("ASSISTANT_MANAGER").post(f"/applications/{aid}/assessment", json={"expected_version": ver}).status_code == 403
    lo = api.as_("LOAN_OFFICER")
    assert lo.post(f"/applications/{aid}/assessment", json={"expected_version": ver - 1}).status_code == 409
    assert lo.post(f"/applications/{aid}/assessment", json={"expected_version": ver}).status_code == 202
    assert lo.post(f"/applications/{aid}/assessment", json={"expected_version": ver}).status_code == 409  # already queued
    aw["lakehouse"].fail = None
    run_all(aw)
    assert app_row(aw, aid)["status"] == "AI_ASSESSED"
    assert lo.post(f"/applications/{aid}/assessment", json={"expected_version": app_row(aw, aid)["version"]}).status_code == 409  # only while DOCS_VERIFIED


def test_the_assessment_cannot_decide_forward_or_set_terms(api, aw):  # noqa: F811
    aid = verified_etb(api, aw)
    run_all(aw)
    a, adm = app_row(aw, aid), aw["admin"]
    for to in ("FORWARDED", "APPROVED", "DECLINED", "SANCTIONED"):  # the system actor the assessment uses can move nothing further
        with pytest.raises(Exception):
            adm.execute("SELECT app_transition(%s, %s, %s, 'SYSTEM', NULL, NULL)", (aid, to, a["version"]))
    with pytest.raises(Exception, match="approved terms"):
        adm.execute("INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months) VALUES (%s, 'APPROVED', 400000, 1150, 36)", (aid,))
    with pytest.raises(Exception, match="documents-verified"), adm.transaction():  # no second eligible cap after the assessment is recorded
        adm.execute("SELECT set_config('app.actor_type', 'SYSTEM', true)")
        adm.execute("INSERT INTO application_terms (application_id, kind, amount, rate_bp, tenure_months, policy_rule_set_id) VALUES (%s, 'ELIGIBLE', 900000, 1150, 36, %s)",
                    (aid, a["policy_rule_set_id"]))
    jobs.enqueue(adm, "assess_application", {"application_id": str(aid)}, aid, f"assess:again:{aid}")
    run_all(aw)  # a stale job finds the application already assessed and does nothing
    assert app_row(aw, aid)["ai_recommendation_run_id"] == a["ai_recommendation_run_id"]
    assert adm.execute("SELECT count(*) AS n FROM decisions WHERE application_id = %s", (aid,)).fetchone()["n"] == 0


def test_policy_knowledge_base_is_readable_by_every_role(api, aw):  # noqa: F811
    from app.main import app

    app.dependency_overrides[policy_kb.get_kb] = lambda: aw["deps"].kb
    for role in ("LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"):
        r = api.as_(role).get("/policy/search", params={"q": "penal charges loan accounts"})
        assert r.status_code == 200 and r.json()["results"][0]["chunk_id"] == "c2" and r.json()["results"][0]["quote"].startswith("Penal charges")
    assert api.get("/policy/search", params={"q": "ab"}).status_code == 422
