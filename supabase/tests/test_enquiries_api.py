"""The enquiry API against a real Postgres with the real migrations. Auth and the BigQuery lookup are replaced by fakes; the database is real."""

import json
import uuid
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

import app.routers.enquiries as enq
from app import jobs
from app.auth import Staff, current_staff
from app.db import get_conn
from app.lakehouse import get_lakehouse
from app.main import app
from app.policy import ruleset

TODAY = date(2026, 11, 26)
ETB_SALARIED = {
    "customer_id": "BOB-C0027", "full_name": "Kavya Sharma", "date_of_birth": date(1991, 2, 9), "gender": "F", "mobile": "9845012376", "email": "kavya.sharma0027@example.invalid",
    "pan": "SYNP0213813F", "address_line": "14, Paud Road", "city": "Pune", "state": "Maharashtra", "pincode": "411038", "residency_status": "RESIDENT", "is_staff": False, "bank_relationship": "ETB",
    "customer_since": date(2016, 7, 1), "home_branch_id": "BRN-010", "home_branch_name": "Pune Kothrud", "home_branch_tier": "METRO", "kyc_status": "VERIFIED",
    "kyc_last_verified_on": date(2025, 9, 3), "employment_type": "SALARIED_PRIVATE", "employer_name": "Maple IT Services Pvt Ltd", "employer_category": "PRIVATE_LTD",
    "designation": "Analyst", "employment_start_date": date(2019, 4, 1), "current_job_tenure_months": 91, "declared_net_income": Decimal("62000.00"),
    "has_bob_salary_account": True, "bob_account_age_months": 112, "avg_balance_6m": Decimal("84500.00"), "avg_salary_credit_6m": Decimal("62000.00"),
    "salary_credit_months_6m": 6, "salary_regularity": 0.0, "avg_business_receipts_6m": Decimal("0"), "business_receipt_months_6m": 0,
    "income_credit_vs_declared_pct": 100.0, "bob_emi_total": Decimal("5400.00"), "bob_active_loans": 1, "returned_debits_6m": 0,
}
ETB_SELF = ETB_SALARIED | {"customer_id": "BOB-C0118", "full_name": "Suresh Patel", "mobile": "9712345680", "pan": "SYNP0934442Q", "kyc_status": "EXPIRED",
                           "employment_type": "SELF_EMPLOYED_BUSINESS", "has_bob_salary_account": False, "avg_salary_credit_6m": Decimal("0"), "salary_credit_months_6m": 0,
                           "avg_business_receipts_6m": Decimal("71000.00"), "business_receipt_months_6m": 6, "bob_emi_total": Decimal("0"), "declared_net_income": Decimal("70000.00")}
NTB = {"full_name": "Rahul Menon", "date_of_birth": "1994-08-21", "gender": "M", "mobile": "9567012345", "email": "rahul.menon@example.invalid",
       "employment_type": "SALARIED_PRIVATE", "declared_net_income": "55000"}


class FakeLakehouse:
    customers = {c["customer_id"]: c for c in (ETB_SALARIED, ETB_SELF)}

    def search(self, q):
        from app.lakehouse import classify

        kind, v = classify(q)
        return [c for c in self.customers.values() if (kind == "name" and v.lower() in c["full_name"].lower()) or c.get(kind) == v]

    def customer(self, cid):
        return self.customers.get(cid)


@pytest.fixture(scope="module")
def env(db_url):
    admin = psycopg.connect(db_url, autocommit=True, row_factory=dict_row)
    people = {}
    for role in ("LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"):
        uid = uuid.uuid4()
        admin.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (uid, f"{uid}@example.invalid"))
        admin.execute("INSERT INTO staff_profiles (user_id, full_name, email, role) VALUES (%s, %s, %s, %s)", (uid, f"Test {role}", f"{uid}@example.invalid", role))
        perms = [r["permission"] for r in admin.execute("SELECT permission FROM role_permissions WHERE role = %s", (role,)).fetchall()]
        people[role] = Staff(uid, f"Test {role}", f"{uid}@example.invalid", role, frozenset(perms))
    rs = ruleset.load()
    if not admin.execute("SELECT 1 FROM policy_rule_sets WHERE scheme = %s AND status = 'ACTIVE'", (rs.scheme,)).fetchone():
        rid = admin.execute("INSERT INTO policy_rule_sets (scheme, version, content, content_sha256) VALUES (%s, %s, %s, %s) RETURNING id",
                            (rs.scheme, rs.version, json.dumps(rs.content), rs.sha256)).fetchone()["id"]
        admin.execute("UPDATE policy_rule_sets SET status = 'ACTIVE', approved_by = %s, activation_reason = 'test fixture activation', test_result = '{\"passed\": true}' WHERE id = %s",
                      (people["BRANCH_MANAGER"].user_id, rid))
    yield {"url": db_url, "admin": admin, **people}
    admin.close()


@pytest.fixture
def api(env):
    who = {"staff": env["LOAN_OFFICER"]}

    def conn():
        c = psycopg.connect(env["url"], row_factory=dict_row, autocommit=True)  # same mode as app.db.connect
        try:
            yield c
        finally:
            c.close()

    # safe defaults: nothing in these tests may reach live storage, BigQuery or Gemini; tests that need them install fakes
    no_jobs = jobs.Deps(connect=None, storage=None, store=None, gemini=None, model="none", gemini_cap=0, kick=False)
    app.dependency_overrides.update({get_conn: conn, current_staff: lambda: who["staff"], get_lakehouse: FakeLakehouse, enq.get_today: lambda: TODAY,
                                     jobs.get_job_deps: lambda: no_jobs})
    client = TestClient(app)

    def as_(role):
        who["staff"] = env[role]
        return client

    client.as_ = as_
    yield client
    app.dependency_overrides.clear()


def create(api, **body):
    base = {"declared_credit_history": "ESTABLISHED", "requested_amount": "400000", "requested_tenure_months": 36}
    return api.post("/enquiries", json=base | body)


def counts(env) -> dict:
    return {t: env["admin"].execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in ("agent_runs", "jobs", "decisions", "documents", "discrepancies", "applications")}


# ---------------------------------------------------------------- lookup
def test_customer_search_is_for_staff_who_take_enquiries(api):
    r = api.as_("LOAN_OFFICER").get("/customers/search", params={"q": "BOB-C0027"})
    assert r.status_code == 200 and [x["customer_id"] for x in r.json()["results"]] == ["BOB-C0027"]
    assert api.get("/customers/search", params={"q": "9845012376"}).json()["results"][0]["customer_id"] == "BOB-C0027"
    assert api.get("/customers/search", params={"q": "!!!"}).status_code == 422
    assert api.as_("ASSISTANT_MANAGER").get("/customers/search", params={"q": "BOB-C0027"}).status_code == 200  # migration 0015


# ---------------------------------------------------------------- create
def test_existing_customer_enquiry_is_prefilled_from_bank_records_with_waivers(api, env):
    before = counts(env)
    r = create(api.as_("LOAN_OFFICER"), customer_id="BOB-C0027", documents_available=["APPLICATION_FORM"])
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["status"] == "DRAFT" and e["reference"].startswith("ENQ-") and e["version"] == 1
    assert e["applicant"] == {"customer_id": "BOB-C0027", "relationship": "ETB", "full_name": "Kavya Sharma", "mobile": "9845012376", "employment_type": "SALARIED_PRIVATE"}
    ind = e["indicative"]
    assert "not a loan offer" in ind["disclaimer"].lower() and ind["rate_range_percent"] == ["10.50", "13.00"] and ind["existing_emis"] == "5400"
    assert {d["code"] for d in e["documents"]["waived"]} == {"PAN_CARD", "ADDRESS_PROOF", "BANK_STATEMENT_6M"}
    assert {d["code"] for d in e["documents"]["missing"]} == {"PHOTOGRAPH", "SALARY_SLIPS_3M"}
    assert counts(env) == before  # a draft starts nothing: no application, agent run, job, decision or document
    a = env["admin"].execute("SELECT * FROM audit_events WHERE event_type = 'ENQUIRY_INSERT' AND entity_id = %s", (e["id"],)).fetchone()
    assert (a["actor_type"], str(a["actor_id"]), a["actor_role"]) == ("STAFF", str(env["LOAN_OFFICER"].user_id), "LOAN_OFFICER")


def test_self_employed_customer_with_expired_kyc_must_bring_identity_and_business_proof(api):
    e = create(api.as_("LOAN_OFFICER"), customer_id="BOB-C0118").json()
    req = {d["code"] for d in e["documents"]["required"]}
    assert {"PAN_CARD", "ADDRESS_PROOF", "ITR_1Y", "BUSINESS_PROOF"} <= req and "SALARY_SLIPS_3M" not in req
    assert {d["code"] for d in e["documents"]["waived"]} == {"BANK_STATEMENT_6M"}  # business receipts are credited to the bank account


def test_new_to_bank_enquiry_registers_the_person_and_carries_the_premium(api, env):
    r = create(api.as_("LOAN_OFFICER"), applicant=NTB, declared_credit_history="NONE", other_lender_emi="0")
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["applicant"]["relationship"] == "NTB" and e["applicant"]["customer_id"] is None
    assert e["indicative"]["rate_range_percent"] == ["13.00", "13.00"]  # mid band for no history, plus the new-to-bank premium
    assert {"PAN_CARD", "ADDRESS_PROOF", "BANK_STATEMENT_6M"} <= {d["code"] for d in e["documents"]["required"]}


@pytest.mark.parametrize("body,code", [
    ({"customer_id": "BOB-C0027", "applicant": NTB}, 422),
    ({}, 422),
    ({"customer_id": "BOB-C9999"}, 404),
    ({"customer_id": "CUST-0001"}, 422),
    ({"applicant": NTB | {"pan": "ABCDE1234F"}}, 422),       # a real-format PAN is never accepted
    ({"applicant": NTB | {"mobile": "12345"}}, 422),
    ({"applicant": NTB | {"date_of_birth": "2030-01-01"}}, 422),
    ({"applicant": NTB | {"declared_net_income": "-5"}}, 422),
    ({"customer_id": "BOB-C0027", "documents_available": ["MADE_UP"]}, 422),
    ({"customer_id": "BOB-C0027", "requested_amount": "0"}, 422),
    ({"customer_id": "BOB-C0027", "requested_tenure_months": 0}, 422),
])
def test_bad_input_is_rejected(api, body, code):
    assert create(api.as_("LOAN_OFFICER"), **body).status_code == code


@pytest.mark.parametrize("role", ["ASSISTANT_MANAGER", "BRANCH_MANAGER"])
def test_managers_can_also_create_and_convert_enquiries(api, role):
    """Migration 0015: every role can take a customer from enquiry to application."""
    c = api.as_(role)
    e = create(c, customer_id="BOB-C0027")
    assert e.status_code == 201
    e = e.json()
    assert c.get(f"/enquiries/{e['id']}").status_code == 200 and c.get("/enquiries").status_code == 200
    assert c.post(f"/enquiries/{e['id']}/convert", json={"expected_version": e["version"], "confirm": True}).status_code == 201


# ---------------------------------------------------------------- resume, update, search
def test_draft_is_saved_found_again_and_updated_with_version_checks(api):
    e = create(api.as_("LOAN_OFFICER"), customer_id="BOB-C0027").json()
    found = api.get("/enquiries", params={"q": e["reference"]}).json()["enquiries"]
    assert [x["id"] for x in found] == [e["id"]]
    assert e["id"] in [x["id"] for x in api.get("/enquiries", params={"q": "9845012376", "status": "DRAFT"}).json()["enquiries"]]
    u = api.patch(f"/enquiries/{e['id']}", json={"expected_version": 1, "requested_amount": "250000", "documents_available": ["PHOTOGRAPH", "SALARY_SLIPS_3M"]})
    assert u.status_code == 200 and u.json()["version"] == 2 and u.json()["requested_amount"] == "250000.00"
    assert {d["code"] for d in u.json()["documents"]["missing"]} == {"APPLICATION_FORM"}
    assert api.patch(f"/enquiries/{e['id']}", json={"expected_version": 1, "requested_amount": "999999"}).status_code == 409  # stale screen


# ---------------------------------------------------------------- convert and close
def test_conversion_is_explicit_atomic_and_starts_only_intake(api, env):
    e = create(api.as_("LOAN_OFFICER"), customer_id="BOB-C0027").json()
    assert api.post(f"/enquiries/{e['id']}/convert", json={"expected_version": 1, "confirm": False}).status_code == 422
    assert api.post(f"/enquiries/{e['id']}/convert", json={"expected_version": 0, "confirm": True}).status_code == 409
    before = counts(env)
    r = api.post(f"/enquiries/{e['id']}/convert", json={"expected_version": 1, "confirm": True})
    assert r.status_code == 201, r.text
    app_ = r.json()
    assert app_["status"] == "INTAKE" and app_["reference"].startswith("APP-")
    after = counts(env)
    assert after["applications"] == before["applications"] + 1 and {k: v for k, v in after.items() if k != "applications"} == {k: v for k, v in before.items() if k != "applications"}
    terms = env["admin"].execute("SELECT kind, amount, rate_bp, tenure_months FROM application_terms WHERE application_id = %s", (app_["application_id"],)).fetchall()
    assert terms == [{"kind": "REQUESTED", "amount": Decimal("400000.00"), "rate_bp": 1300, "tenure_months": 36}]
    got = api.get(f"/enquiries/{e['id']}").json()
    assert got["status"] == "CONVERTED" and got["converted_application_id"] == app_["application_id"]
    assert api.post(f"/enquiries/{e['id']}/convert", json={"expected_version": 2, "confirm": True}).status_code == 409
    assert api.patch(f"/enquiries/{e['id']}", json={"expected_version": 2, "requested_amount": "1000"}).status_code == 409
    ins = env["admin"].execute("SELECT actor_type, actor_role FROM audit_events WHERE event_type = 'APPLICATION_INSERT' AND entity_id = %s", (app_["application_id"],)).fetchone()
    assert ins == {"actor_type": "STAFF", "actor_role": "LOAN_OFFICER"}


def test_closing_needs_a_reason_and_a_closed_enquiry_cannot_be_converted(api):
    e = create(api.as_("LOAN_OFFICER"), customer_id="BOB-C0027").json()
    assert api.post(f"/enquiries/{e['id']}/close", json={"expected_version": 1, "reason": "no"}).status_code == 422
    c = api.post(f"/enquiries/{e['id']}/close", json={"expected_version": 1, "reason": "Customer decided not to borrow now"})
    assert c.status_code == 200 and c.json()["status"] == "CLOSED" and c.json()["indicative"]["closed_reason"]
    assert api.post(f"/enquiries/{e['id']}/convert", json={"expected_version": 2, "confirm": True}).status_code == 409


def test_without_an_active_rule_set_no_indicative_figures_are_given(api, monkeypatch):
    monkeypatch.setattr(enq, "SCHEME", "scheme_that_was_never_activated")
    r = create(api.as_("LOAN_OFFICER"), customer_id="BOB-C0027")
    assert r.status_code == 503 and "activate" in r.json()["detail"]


def test_the_production_connection_commits_writes_made_after_a_read(env, monkeypatch):
    """Regression: with a non-autocommit connection a read-then-write request lost its write. Uses the real app.db.connect()."""
    import app.db as db

    monkeypatch.setattr(db.settings, "supabase_db_url", env["url"])
    c = db.connect()
    c.execute("SELECT 1")  # a read first, as every endpoint does
    lo = env["LOAN_OFFICER"]
    with db.staff_tx(c, lo.user_id, lo.role):
        aid = c.execute("INSERT INTO applicants (bank_relationship, full_name, date_of_birth, mobile) VALUES ('NTB', 'Commit Check', '1990-01-01', '9876500001') RETURNING id").fetchone()["id"]
    c.close()
    assert env["admin"].execute("SELECT count(*) AS n FROM applicants WHERE id = %s", (aid,)).fetchone()["n"] == 1


def test_a_new_or_changed_draft_never_alters_an_in_flight_application_or_the_shared_applicant(api, env):
    lo = api.as_("LOAN_OFFICER")
    first = create(lo, customer_id="BOB-C0027", declared_net_income="62000", other_lender_emi="3000", declared_credit_history="ESTABLISHED").json()
    conv = lo.post(f"/enquiries/{first['id']}/convert", json={"expected_version": 1, "confirm": True}).json()
    q = "SELECT declared_monthly_net, existing_monthly_emi, declared_credit_history FROM applications WHERE id = %s"
    frozen = env["admin"].execute(q, (conv["application_id"],)).fetchone()
    assert frozen == {"declared_monthly_net": Decimal("62000.00"), "existing_monthly_emi": Decimal("3000.00"), "declared_credit_history": "ESTABLISHED"}
    applicant_before = env["admin"].execute("SELECT * FROM applicants WHERE customer_id = 'BOB-C0027'").fetchone()
    # a second draft for the same customer with different figures, then an edit
    second = create(lo, customer_id="BOB-C0027", declared_net_income="90000", other_lender_emi="0", declared_credit_history="NONE").json()
    lo.patch(f"/enquiries/{second['id']}", json={"expected_version": 1, "declared_net_income": "120000", "other_lender_emi": "1"})
    assert env["admin"].execute(q, (conv["application_id"],)).fetchone() == frozen
    assert env["admin"].execute("SELECT * FROM applicants WHERE customer_id = 'BOB-C0027'").fetchone() == applicant_before
    with pytest.raises(psycopg.Error, match="frozen"):
        env["admin"].execute("UPDATE applications SET declared_monthly_net = 1 WHERE id = %s", (conv["application_id"],))


def test_applicant_records_are_audited(api, env):
    e = create(api.as_("LOAN_OFFICER"), applicant=NTB | {"mobile": "9567099999"}).json()
    aid = env["admin"].execute("SELECT applicant_id FROM enquiries WHERE id = %s", (e["id"],)).fetchone()["applicant_id"]
    row = env["admin"].execute("SELECT actor_type, actor_role FROM audit_events WHERE event_type = 'APPLICANT_INSERT' AND entity_id = %s", (str(aid),)).fetchone()
    assert row == {"actor_type": "STAFF", "actor_role": "LOAN_OFFICER"}
