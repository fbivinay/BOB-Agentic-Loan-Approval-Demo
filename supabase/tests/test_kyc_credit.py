"""Migration 0018 against real Postgres: KYC records (reuse, expiry, versioned updates, onboarding from verified loan documents), the
masked-Aadhaar rule, and the persistent DEMO / SIMULATED credit profile (one per PAN per month, also under simultaneous requests)."""

import threading
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from app import credit_profile, kyc
from app.docproc import Extraction
from app.main import app
from app.routers.applications import load
from app.lakehouse import get_lakehouse
from app.storage import get_kyc_storage
from test_assessment_flow import CreditLakehouse
from test_applications_api import PNG, new_app, upload  # noqa: F401
from test_documents_flow import PAN_PDF, Storage, ef, make_pdf, ntb_docs_pending, run_all, world  # noqa: F401  (fixtures)
from test_enquiries_api import TODAY, FakeLakehouse, api, env  # noqa: F401  (fixtures)

LO = "LOAN_OFFICER"
AADHAAR_PDF = make_pdf(["AADHAAR-STYLE CARD (synthetic sample)", "Name: RAHUL MENON", "DOB: 21/08/1994", "Gender: Male", "Aadhaar: XXXX XXXX 4321",
                        "Address: Flat 12B, Lakeview Residency, MG Road, Kochi"])
UNMASKED_PDF = make_pdf(["AADHAAR-STYLE CARD (synthetic sample)", "Name: RAHUL MENON", "DOB: 21/08/1994", "Gender: Male", "Aadhaar: 2345 6789 0123",
                         "Address: Flat 12B, Lakeview Residency, MG Road, Kochi"])
COMMON = [ef("dob", "21/08/1994", "DOB: 21/08/1994"), ef("gender", "Male", "Gender: Male"),
          ef("address", "Flat 12B, Lakeview Residency, MG Road, Kochi", "Address: Flat 12B, Lakeview Residency, MG Road, Kochi")]


@pytest.fixture
def kw(world, env):  # noqa: F811
    env["admin"].execute("TRUNCATE kyc_documents, kyc_profile_history, kyc_profiles, simulated_credit_profiles")
    ks = Storage()
    app.dependency_overrides[get_kyc_storage] = lambda: ks
    lh = FakeLakehouse()
    lh.credit_record = CreditLakehouse().credit_record  # customer lookups as in the enquiry tests, plus the synthetic bureau records
    app.dependency_overrides[get_lakehouse] = lambda: lh
    return world | {"kyc_storage": ks, "env": env}


def seed_profile(admin, customer_id, pan, status):
    p = kyc.create(admin, {"customer_id": customer_id, "pan": pan, "full_name": "Kavya Sharma", "date_of_birth": date(1991, 2, 9), "gender": "F",
                           "aadhaar_last4": "1111", "address_line": "14, Paud Road", "city": "Pune", "state": "Maharashtra", "pincode": "411038",
                           "kyc_status": status, "verified_on": date(2025, 3, 1) if status == "VERIFIED" else None}, "ACCOUNT_OPENING", "Account opening KYC")
    kyc.add_document(admin, p["id"], "PAN_CARD", bucket="kyc-documents", path=f"{p['id']}/pan.pdf", name="pan.pdf", mime="application/pdf", sha256="a" * 64,
                     source="ACCOUNT_OPENING", is_sample=True)
    return p


# ---------------------------------------------------------------- existing customers: reuse or refresh
def test_verified_kyc_is_reused_and_pan_and_address_are_not_asked_for_again(api, kw):  # noqa: F811
    seed_profile(kw["admin"], "BOB-C0027", "SYNP0213813F", "VERIFIED")
    aid = new_app(api, customer_id="BOB-C0027")
    lo = api.as_(LO)
    a = lo.post(f"/applications/{aid}/intake/complete", json={"expected_version": 1}).json()
    required = {i["code"] for i in a["checklist"]["items"]}
    assert "PAN_CARD" not in required and "ADDRESS_PROOF" not in required
    assert a["kyc"]["reused"] is True and [d["doc_type"] for d in a["kyc"]["documents"]] == ["PAN_CARD"] and a["kyc"]["documents"][0]["is_sample"]


def test_expired_kyc_record_overrides_the_bank_flag_and_documents_are_required(api, kw):  # noqa: F811
    seed_profile(kw["admin"], "BOB-C0027", "SYNP0213813F", "EXPIRED")  # the bank feed still says VERIFIED; the KYC record knows better
    aid = new_app(api, customer_id="BOB-C0027")
    a = api.as_(LO).post(f"/applications/{aid}/intake/complete", json={"expected_version": 1}).json()
    required = {i["code"] for i in a["checklist"]["items"]}
    assert {"PAN_CARD", "ADDRESS_PROOF"} <= required and a["kyc"]["reused"] is False and a["checklist"]["basis"]["kyc_status"] == "EXPIRED"


def test_a_kyc_update_keeps_every_version_and_document(api, kw):  # noqa: F811
    p = seed_profile(kw["admin"], "BOB-C0027", "SYNP0213813F", "EXPIRED")
    lo = api.as_(LO)
    url = f"/kyc/{p['id']}"
    for _ in range(2):
        r = lo.put(f"{url}/documents", params={"doc_type": "ADDRESS_PROOF", "file_name": "new-address.png"}, content=PNG + bytes(_), headers={"Content-Type": "image/png"})
        assert r.status_code == 201, r.text
    assert lo.post(f"{url}/update", json={"expected_version": 1, "reason": "short", "city": "Mangaluru", "verified_original": True}).status_code == 422
    r = lo.post(f"{url}/update", json={"expected_version": 1, "reason": "Customer moved; new address proof seen at the branch", "address_line": "4, Kadri Road",
                                       "city": "Mangaluru", "state": "Karnataka", "pincode": "575002", "verified_original": True})
    assert r.status_code == 200, r.text
    v = lo.get(url).json()
    assert (v["version"], v["kyc_status"], v["city"]) == (2, "VERIFIED", "Mangaluru")
    addr = sorted((d["version"], d["status"]) for d in v["documents"] if d["doc_type"] == "ADDRESS_PROOF")
    assert addr == [(1, "SUPERSEDED"), (2, "CURRENT")]
    assert [h["version"] for h in v["history"]] == [2, 1] and "city" in v["history"][0]["changed_fields"]
    assert lo.post(f"{url}/update", json={"expected_version": 1, "reason": "A stale second update attempt", "city": "Udupi", "verified_original": True}).status_code == 409
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        kw["admin"].execute("DELETE FROM kyc_documents WHERE profile_id = %s", (p["id"],))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        kw["admin"].execute("UPDATE kyc_profile_history SET reason = 'x' WHERE profile_id = %s", (p["id"],))


# ---------------------------------------------------------------- new-to-bank: KYC from the verified loan documents
def _verified(api, w, aid, requirement, doc_type, data, answer):  # noqa: F811
    w["gemini"].answers[doc_type] = answer
    lo = api.as_(LO)
    d = upload(lo, aid, requirement, doc_type, data=data).json()
    run_all(w)
    return d, lo.post(f"/applications/{aid}/documents/{d['id']}/verify")


def test_new_to_bank_kyc_is_created_from_verified_documents(api, kw):  # noqa: F811
    aid = ntb_docs_pending(api)
    pan_doc, r = _verified(api, kw, aid, "PAN_CARD", "PAN_CARD", PAN_PDF, kw["gemini"].answers["PAN_CARD"])
    assert r.status_code == 200, r.text
    aad = Extraction(fields=[ef("name", "RAHUL MENON", "Name: RAHUL MENON"), ef("aadhaar_masked", "XXXX XXXX 4321", "Aadhaar: XXXX XXXX 4321"), *COMMON])
    _, r = _verified(api, kw, aid, "ADDRESS_PROOF", "AADHAAR_MASKED", AADHAAR_PDF, aad)
    assert r.status_code == 200, r.text
    admin = kw["admin"]
    lo_id = kw["env"][LO].user_id
    done = kyc.from_application(admin, load(admin, aid), lo_id, TODAY)
    assert done["done"] == "created"
    p = kyc.profile(admin, pan="SYNP0457812K")
    assert (p["source"], p["kyc_status"], p["aadhaar_last4"], p["customer_id"]) == ("LOAN_ONBOARDING", "VERIFIED", "4321", None)
    docs = {d["doc_type"]: d for d in kyc.documents(admin, p["id"])}
    assert set(docs) == {"PAN_CARD", "AADHAAR_MASKED"} and docs["PAN_CARD"]["application_document_id"] == pan_doc["id"] or str(docs["PAN_CARD"]["application_document_id"]) == pan_doc["id"]
    assert admin.execute("SELECT count(*) AS n FROM audit_events WHERE event_type = 'KYC_CREATED' AND application_id = %s", (aid,)).fetchone()["n"] == 1


def test_a_new_to_bank_application_cannot_take_over_someone_elses_kyc(api, kw):  # noqa: F811
    admin = kw["admin"]
    other = seed_profile(admin, "BOB-C0027", "SYNP0457812K", "VERIFIED")  # an existing customer who holds the PAN the NTB applicant typed in
    aid = ntb_docs_pending(api)
    assert api.as_(LO).get(f"/applications/{aid}").json()["kyc"] is None  # an existing customer's record is never reached by a typed PAN
    _, r = _verified(api, kw, aid, "PAN_CARD", "PAN_CARD", PAN_PDF, kw["gemini"].answers["PAN_CARD"])
    assert r.status_code == 200, r.text
    done = kyc.from_application(admin, load(admin, aid), kw["env"][LO].user_id, TODAY)
    assert done["done"] == "skipped" and "manual review" in done["note"]
    after = kyc.profile(admin, profile_id=other["id"])
    assert (after["version"], after["full_name"]) == (1, "Kavya Sharma")


def test_a_full_aadhaar_on_any_document_blocks_verification_and_never_reaches_gemini(api, kw):  # noqa: F811
    from app import docproc

    aid = ntb_docs_pending(api)
    leaky = make_pdf(["INCOME TAX DEPARTMENT (synthetic sample)", "Name: RAHUL MENON", "Father's Name: SURESH MENON", "Date of Birth: 21/08/1994",
                      "Permanent Account Number: SYNP0457812K", "Linked Aadhaar 2345 6789 0123"])
    _, r = _verified(api, kw, aid, "PAN_CARD", "PAN_CARD", leaky, kw["gemini"].answers["PAN_CARD"])
    assert r.status_code == 422 and "12-digit Aadhaar" in r.json()["detail"]
    sent = " ".join(" ".join(p) for _, p in kw["gemini"].calls)
    assert "2345 6789 0123" not in sent and "[AADHAAR-REMOVED]" in sent
    assert docproc.redact(["acct 1234 5678 9012 3456 and XXXX XXXX 4321"])[0] == ["acct 1234 5678 9012 3456 and XXXX XXXX 4321"]  # not an Aadhaar layout


def test_an_unmasked_aadhaar_is_never_accepted(api, kw):  # noqa: F811
    aid = ntb_docs_pending(api)
    full = Extraction(fields=[ef("name", "RAHUL MENON", "Name: RAHUL MENON"), ef("aadhaar_masked", "2345 6789 0123", "Aadhaar: 2345 6789 0123"), *COMMON])
    _, r = _verified(api, kw, aid, "ADDRESS_PROOF", "AADHAAR_MASKED", UNMASKED_PDF, full)
    assert r.status_code == 422 and "12-digit Aadhaar" in r.json()["detail"]


def test_real_format_pan_is_accepted_for_a_test_applicant(api, kw):  # noqa: F811
    aid = ntb_docs_pending(api)
    assert api.as_(LO).get(f"/applications/{aid}").status_code == 200
    from test_enquiries_api import NTB, create

    e = create(api.as_(LO), applicant=NTB | {"mobile": "9567033333", "pan": "ABCPM1234Q"}, declared_credit_history="NONE")
    assert e.status_code == 201, e.text


# ---------------------------------------------------------------- credit profile: one per PAN per month
def test_credit_profile_is_the_same_on_every_check_within_the_month(api, kw):  # noqa: F811
    lo = api.as_(LO)
    a = lo.post("/credit-profile", json={"pan": "ABCPM1234Q"}).json()
    b = lo.post("/credit-profile", json={"pan": "ABCPM1234Q"}).json()
    assert a == b and a["source"] == "SIMULATED" and "DEMO / SIMULATED" in a["label"]
    assert a["period_start"] == TODAY.replace(day=1).isoformat() and a["valid_until"] >= TODAY.isoformat()
    assert lo.post("/credit-profile", json={"pan": "not-a-pan"}).status_code == 422
    rows = kw["admin"].execute("SELECT count(*) AS n FROM simulated_credit_profiles WHERE pan = 'ABCPM1234Q'").fetchone()["n"]
    assert rows == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        kw["admin"].execute("UPDATE simulated_credit_profiles SET score = 900 WHERE pan = 'ABCPM1234Q'")


def test_a_synthetic_customers_profile_follows_their_bureau_record(api, kw):  # noqa: F811
    p = api.as_(LO).post("/credit-profile", json={"pan": "SYNP0213813F"}).json()  # BOB-C0027 in the fake lakehouse
    assert p["source"] == "SYNTHETIC_BUREAU"


def test_a_new_month_gives_a_new_profile_and_the_old_one_is_kept(kw):
    with psycopg.connect(kw["env"]["url"], row_factory=dict_row, autocommit=True) as c:
        nov = credit_profile.get_or_create(c, "ABCPM1234Q", date(2026, 11, 27))
        dec = credit_profile.get_or_create(c, "ABCPM1234Q", date(2026, 12, 2))
        assert (nov["period_start"], dec["period_start"]) == ("2026-11-01", "2026-12-01")
        assert credit_profile.get_or_create(c, "ABCPM1234Q", date(2026, 11, 30)) == nov


def test_simultaneous_first_requests_store_exactly_one_profile(kw):
    out, url = [], kw["env"]["url"]

    def ask():
        with psycopg.connect(url, row_factory=dict_row, autocommit=True) as c:
            out.append(credit_profile.get_or_create(c, "PQRST6789Z", TODAY))

    threads = [threading.Thread(target=ask) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(out) == 8 and all(x == out[0] for x in out)
    assert kw["admin"].execute("SELECT count(*) AS n FROM simulated_credit_profiles WHERE pan = 'PQRST6789Z'").fetchone()["n"] == 1


def test_simulation_is_deterministic_and_risk_is_explained():
    start = date(2026, 11, 1)
    assert credit_profile.simulate("ABCPM1234Q", start) == credit_profile.simulate("abcpm1234q", start)
    r = credit_profile.risk_category(640, "ESTABLISHED", "38.0", "RECOMMENDED_FOR_APPROVAL")
    assert r["level"] == "HIGH" and r["reasons"]
    assert credit_profile.risk_category(790, "ESTABLISHED", "62.5", "RECOMMENDED_FOR_APPROVAL")["level"] == "HIGH"
    assert credit_profile.risk_category(790, "ESTABLISHED", "30", "RECOMMENDED_FOR_APPROVAL")["level"] == "LOW"
