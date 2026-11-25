"""Intake, new-to-bank registration, the frozen document checklist, and document upload, against real Postgres with the real migrations.
Auth, BigQuery and Supabase Storage are replaced by fakes; the database and its guards are real."""

import hashlib

import psycopg
import pytest

from app import jobs
from app.main import app
from app.storage import get_storage
from test_enquiries_api import NTB, api, counts, create, env  # noqa: F401  (fixtures)

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class FakeStorage:
    def __init__(self):
        self.objects, self.fail = {}, False

    def put(self, path, data, mime):
        if self.fail:
            raise RuntimeError("storage unavailable")
        assert path not in self.objects
        self.objects[path] = (data, mime)

    def remove(self, path):
        self.objects.pop(path, None)

    def signed_url(self, path):
        return f"https://storage.example.invalid/sign/{path}?token=t"


@pytest.fixture
def storage(api):  # noqa: F811
    s = FakeStorage()
    no_jobs = jobs.Deps(connect=None, storage=s, store=None, gemini=None, model="none", gemini_cap=0, kick=False)  # Phase 6 tests: queue only, never run
    app.dependency_overrides.update({get_storage: lambda: s, jobs.get_job_deps: lambda: no_jobs})
    return s


def new_app(api, **enquiry):  # noqa: F811
    lo = api.as_("LOAN_OFFICER")
    e = create(lo, **enquiry).json()
    return lo.post(f"/enquiries/{e['id']}/convert", json={"expected_version": 1, "confirm": True}).json()["application_id"]


def upload(c, app_id, requirement, doc_type, data=PDF, name="scan.pdf", mime="application/pdf"):
    return c.put(f"/applications/{app_id}/documents", params={"requirement": requirement, "doc_type": doc_type, "file_name": name}, content=data,
                 headers={"Content-Type": mime})


def ntb_ready(api):  # noqa: F811
    """A new-to-bank application with the registration details completed."""
    aid = new_app(api, applicant=NTB | {"mobile": "9567011111"}, declared_credit_history="NONE")
    r = api.as_("LOAN_OFFICER").patch(f"/applications/{aid}/applicant", json={"pan": "SYNP0457812K", "gender": "M", "address_line": "Flat 12B, Lakeview Residency, MG Road",
                                                                               "city": "Kochi", "state": "Kerala", "pincode": "682016"})
    assert r.status_code == 200, r.text
    return aid


# ---------------------------------------------------------------- intake
def test_existing_customer_intake_uses_bank_identity_and_freezes_a_checklist_with_waivers(api, env, storage):  # noqa: F811
    aid = new_app(api, customer_id="BOB-C0027")
    lo = api.as_("LOAN_OFFICER")
    a = lo.get(f"/applications/{aid}").json()
    assert a["status"] == "INTAKE" and not a["checklist"]["frozen"] and a["applicant"]["city"] == "Pune"
    assert a["intake"]["missing_details"] == [] and a["applicant"]["address_line"] == "14, Paud Road"  # identity and address from the bank's records
    assert lo.patch(f"/applications/{aid}/applicant", json={"pan": "SYNP0000001A"}).status_code == 422  # identity is the bank's, updated via KYC
    assert lo.patch(f"/applications/{aid}/applicant", json={"email": "kavya.s@example.invalid"}).status_code == 200  # contact details may be updated
    assert lo.post(f"/applications/{aid}/intake/complete", json={"expected_version": 0}).status_code == 409
    before = counts(env)
    r = lo.post(f"/applications/{aid}/intake/complete", json={"expected_version": 1})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["status"] == "DOCS_PENDING" and a["version"] == 2 and a["intake"]["completed"]
    ck = a["checklist"]
    assert ck["frozen"] and ck["policy"].startswith("baroda_personal_loan v1") and ck["basis"]["kyc_current"] and ck["basis"]["income_at_bob"]
    assert [i["code"] for i in ck["items"]] == ["APPLICATION_FORM", "PHOTOGRAPH", "SALARY_SLIPS_3M"]
    assert {w["code"] for w in ck["waived"]} == {"PAN_CARD", "ADDRESS_PROOF", "BANK_STATEMENT_6M"}
    assert counts(env) == before  # intake starts no agent run, job, decision or discrepancy
    assert lo.post(f"/applications/{aid}/intake/complete", json={"expected_version": 2}).status_code == 409
    assert lo.patch(f"/applications/{aid}/applicant", json={"address_line": "Somewhere else entirely"}).status_code == 409
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="fixed once intake is complete"):
        env["admin"].execute("UPDATE applications SET documents_checklist = '{}' WHERE id = %s", (aid,))
    ev = env["admin"].execute("SELECT actor_type, actor_role FROM audit_events WHERE event_type = 'STATUS_TRANSITION' AND application_id = %s", (aid,)).fetchone()
    assert ev == {"actor_type": "STAFF", "actor_role": "LOAN_OFFICER"}


def test_new_to_bank_registration_is_completed_at_intake_and_needs_full_kyc_documents(api):  # noqa: F811
    aid = new_app(api, applicant=NTB | {"mobile": "9567022222"}, declared_credit_history="NONE")
    lo = api.as_("LOAN_OFFICER")
    assert set(lo.get(f"/applications/{aid}").json()["intake"]["missing_details"]) == {"pan", "address_line", "city", "state", "pincode"}
    assert lo.patch(f"/applications/{aid}/applicant", json={"pan": "ABCDE1234F"}).status_code == 422  # a real-format PAN is never accepted
    assert lo.patch(f"/applications/{aid}/applicant", json={"pincode": "012345"}).status_code == 422
    r = lo.patch(f"/applications/{aid}/applicant", json={"pan": "SYNP0457813K", "address_line": "22 Marine Drive", "city": "Kochi", "state": "Kerala",
                                                          "pincode": "682031", "residency_status": "RESIDENT"})
    assert r.status_code == 200 and r.json()["intake"]["missing_details"] == []
    a = lo.post(f"/applications/{aid}/intake/complete", json={"expected_version": 1}).json()
    assert a["status"] == "DOCS_PENDING" and a["checklist"]["waived"] == [] and not a["checklist"]["basis"]["kyc_current"]
    assert [i["code"] for i in a["checklist"]["items"]] == ["APPLICATION_FORM", "PHOTOGRAPH", "PAN_CARD", "ADDRESS_PROOF", "SALARY_SLIPS_3M", "BANK_STATEMENT_6M"]
    assert not a["checklist"]["all_uploaded"]


@pytest.mark.parametrize("role", ["ASSISTANT_MANAGER", "BRANCH_MANAGER"])
def test_managers_view_applications_but_do_no_intake(api, role, storage):  # noqa: F811
    aid = ntb_ready(api)
    m = api.as_(role)
    assert m.get(f"/applications/{aid}").status_code == 200 and m.get("/applications", params={"status": "INTAKE"}).status_code == 200
    assert m.patch(f"/applications/{aid}/applicant", json={"city": "Thrissur"}).status_code == 403
    assert m.post(f"/applications/{aid}/intake/complete", json={"expected_version": 1}).status_code == 403
    assert upload(m, aid, "PAN_CARD", "PAN_CARD").status_code == 403


def test_status_cannot_leave_intake_without_a_completed_intake(api, env):  # noqa: F811
    aid = new_app(api, customer_id="BOB-C0027")
    lo = env["LOAN_OFFICER"]
    with pytest.raises(psycopg.errors.CheckViolation, match="complete intake"):
        env["admin"].execute("SELECT app_transition(%s, 'DOCS_PENDING', 1, 'STAFF', %s, NULL)", (aid, lo.user_id))


# ---------------------------------------------------------------- documents
def test_upload_checks_the_file_itself_and_stores_it_privately(api, env, storage):  # noqa: F811
    aid = ntb_ready(api)
    lo = api.as_("LOAN_OFFICER")
    r = upload(lo, aid, "PAN_CARD", "PAN_CARD", name="C:\\Users\\me\\pan card.pdf")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "UPLOADED" and d["original_name"] == "pan card.pdf" and d["sha256"] == hashlib.sha256(PDF).hexdigest()
    path = env["admin"].execute("SELECT storage_path FROM documents WHERE id = %s", (d["id"],)).fetchone()["storage_path"]
    assert path == f"{aid}/{d['id']}.pdf" and storage.objects[path] == (PDF, "application/pdf")  # the path is ours, never the user's
    assert upload(lo, aid, "PAN_CARD", "PAN_CARD").status_code == 409  # the same file twice
    assert upload(lo, aid, "ADDRESS_PROOF", "AADHAAR_MASKED", data=JPG, name="aadhaar.jpeg", mime="image/jpeg").status_code == 201
    assert upload(lo, aid, "PHOTOGRAPH", "PHOTOGRAPH", data=PNG, name="photo.png", mime="image/png").status_code == 201
    a = lo.get(f"/applications/{aid}").json()
    assert {i["code"]: i["uploaded"] for i in a["checklist"]["items"]} == {"APPLICATION_FORM": 0, "PHOTOGRAPH": 1, "PAN_CARD": 1, "ADDRESS_PROOF": 1,
                                                                          "SALARY_SLIPS_3M": 0, "BANK_STATEMENT_6M": 0}


@pytest.mark.parametrize("kw,code", [
    (dict(requirement="PAN_CARD", doc_type="SALARY_SLIP"), 422),                                    # wrong kind of document for the requirement
    (dict(requirement="PAN_CARD", doc_type="PAN_CARD", data=b"MZ\x90\x00binary", name="pan.pdf"), 415),  # not a PDF, PNG or JPEG
    (dict(requirement="PAN_CARD", doc_type="PAN_CARD", data=PDF, name="pan.png"), 415),            # name disagrees with contents
    (dict(requirement="PAN_CARD", doc_type="PAN_CARD", data=PDF, mime="image/png"), 415),          # declared type disagrees with contents
    (dict(requirement="PHOTOGRAPH", doc_type="PHOTOGRAPH", data=PDF), 415),                        # a photograph is an image
    (dict(requirement="PAN_CARD", doc_type="PAN_CARD", data=b""), 422),
    (dict(requirement="PAN_CARD", doc_type="PAN_CARD", data=b"%PDF-" + b"0" * (10 * 1024 * 1024)), 413),
    (dict(requirement="MADE_UP", doc_type="PAN_CARD"), 422),
])
def test_bad_uploads_are_refused_and_nothing_is_stored(api, env, storage, kw, code):  # noqa: F811
    aid = ntb_ready(api)
    assert upload(api.as_("LOAN_OFFICER"), aid, **kw).status_code == code
    assert storage.objects == {} and env["admin"].execute("SELECT count(*) AS n FROM documents WHERE application_id = %s", (aid,)).fetchone()["n"] == 0


def test_a_waived_requirement_cannot_be_uploaded_after_intake(api, storage):  # noqa: F811
    aid = new_app(api, customer_id="BOB-C0027")
    lo = api.as_("LOAN_OFFICER")
    assert lo.post(f"/applications/{aid}/intake/complete", json={"expected_version": 1}).status_code == 200
    assert upload(lo, aid, "PAN_CARD", "PAN_CARD").status_code == 422
    assert upload(lo, aid, "SALARY_SLIPS_3M", "SALARY_SLIP").status_code == 201


def test_if_storage_fails_no_document_record_is_left(api, env, storage):  # noqa: F811
    aid = ntb_ready(api)
    storage.fail = True
    with pytest.raises(RuntimeError):
        upload(api.as_("LOAN_OFFICER"), aid, "PAN_CARD", "PAN_CARD")
    assert env["admin"].execute("SELECT count(*) AS n FROM documents WHERE application_id = %s", (aid,)).fetchone()["n"] == 0


def test_viewing_a_document_gives_a_short_lived_link_and_is_audited(api, env, storage):  # noqa: F811
    aid = ntb_ready(api)
    d = upload(api.as_("LOAN_OFFICER"), aid, "PAN_CARD", "PAN_CARD").json()
    r = api.as_("ASSISTANT_MANAGER").get(f"/applications/{aid}/documents/{d['id']}/url")
    assert r.status_code == 200 and r.json()["expires_in_seconds"] == 60 and "token=" in r.json()["url"]
    ev = env["admin"].execute("SELECT actor_role FROM audit_events WHERE event_type = 'DOCUMENT_VIEWED' AND entity_id = %s", (d["id"],)).fetchall()
    assert ev == [{"actor_role": "ASSISTANT_MANAGER"}]
    other = ntb_ready(api)
    assert api.get(f"/applications/{other}/documents/{d['id']}/url").status_code == 404  # a document is reached only through its own application


def test_a_wrong_file_is_withdrawn_with_a_reason_never_deleted(api, env, storage):  # noqa: F811
    aid = ntb_ready(api)
    lo = api.as_("LOAN_OFFICER")
    d = upload(lo, aid, "PAN_CARD", "PAN_CARD").json()
    assert lo.post(f"/applications/{aid}/documents/{d['id']}/withdraw", json={"reason": "wrong"}).status_code == 422
    assert api.as_("BRANCH_MANAGER").post(f"/applications/{aid}/documents/{d['id']}/withdraw", json={"reason": "Uploaded the spouse's PAN card by mistake"}).status_code == 403
    w = api.as_("LOAN_OFFICER").post(f"/applications/{aid}/documents/{d['id']}/withdraw", json={"reason": "Uploaded the spouse's PAN card by mistake"})
    assert w.status_code == 200 and w.json()["status"] == "WITHDRAWN"
    assert api.post(f"/applications/{aid}/documents/{d['id']}/withdraw", json={"reason": "Withdrawing it a second time"}).status_code == 403
    assert upload(api, aid, "PAN_CARD", "PAN_CARD").status_code == 201  # the same bytes may come back once the earlier copy is withdrawn
    a = api.get(f"/applications/{aid}").json()
    assert len(a["documents"]) == 2 and next(i for i in a["checklist"]["items"] if i["code"] == "PAN_CARD")["uploaded"] == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env["admin"].execute("DELETE FROM documents WHERE id = %s", (d["id"],))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="immutable"):
        env["admin"].execute("UPDATE documents SET storage_path = 'elsewhere.pdf' WHERE id = %s", (d["id"],))


def test_applications_are_searchable_by_reference_mobile_and_name(api, storage):  # noqa: F811
    aid = ntb_ready(api)
    ref = api.get(f"/applications/{aid}").json()["reference"]
    assert [x["id"] for x in api.get("/applications", params={"q": ref}).json()["applications"]] == [aid]
    assert aid in [x["id"] for x in api.get("/applications", params={"q": "9567011111"}).json()["applications"]]
    assert aid in [x["id"] for x in api.get("/applications", params={"q": "rahul"}).json()["applications"]]
