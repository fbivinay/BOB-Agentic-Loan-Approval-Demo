"""Phase 7 against real Postgres with the real migrations: text from one labelled method, Gemini extraction with PAN redaction, review and
correction, verification, discrepancies, attestation, and the AI boundaries. Storage, BigQuery and Gemini are fakes; the database is real."""

import hashlib

import psycopg
import pytest
from psycopg.rows import dict_row

from app import jobs
from app.config import settings
from app.docproc import Extraction, ExtractedField
from app.main import app
from app.ocr_store import get_ocr_store
from app.storage import get_storage
from test_applications_api import JPG, PNG, ntb_ready, new_app, upload  # noqa: F401
from test_enquiries_api import api, counts, env  # noqa: F401  (fixtures)

NTB_PAN = "SYNP0457812K"


def make_pdf(lines: list[str]) -> bytes:
    """A small born-digital PDF with a real text layer."""
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    content = "BT /F1 11 Tf 50 760 Td 16 TL " + " ".join(f"({esc(line)}) '" for line in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


PAN_PDF = make_pdf(["INCOME TAX DEPARTMENT  GOVT. OF INDIA  (synthetic sample)", "Name: RAHUL MENON", "Father's Name: SURESH MENON",
                    "Date of Birth: 21/08/1994", f"Permanent Account Number: {NTB_PAN}"])
SLIP_PDF = make_pdf(["Maple IT Services Pvt Ltd - Payslip for October 2026 (synthetic)", "Employee: Kavya Sharma", "Month: 2026-10",
                     "Gross Pay: Rs. 73,200", "Total Deductions: Rs. 11,200", "Net Pay: Rs. 62,000"])
FORM_PDF = make_pdf(["PERSONAL LOAN APPLICATION FORM (synthetic sample)", "Applicant Name: Kavya Sharma", "Loan Amount Applied: Rs. 4,00,000",
                     "Signature of Applicant: signed"])


def ef(field, value, snippet, page=1):
    return ExtractedField(field=field, value=value, page=page, snippet=snippet)


ANSWERS = {
    "PAN_CARD": Extraction(fields=[ef("name", "RAHUL MENON", "Name: RAHUL MENON"), ef("father_name", "SURESH MENON", "Father's Name: SURESH MENON"),
                                   ef("dob", "21/08/1994", "Date of Birth: 21/08/1994"), ef("pan", "[PAN-1]", "Permanent Account Number: [PAN-1]")]),
    "SALARY_SLIP": Extraction(fields=[ef("employee_name", "Kavya Sharma", "Employee: Kavya Sharma"), ef("employer", "Maple IT Services Pvt Ltd", "Maple IT Services Pvt Ltd"),
                                      ef("month", "2026-10", "Month: 2026-10"), ef("gross", "Rs. 73,200", "Gross Pay: Rs. 73,200"),
                                      ef("deductions", "Rs. 11,200", "Total Deductions: Rs. 11,200"), ef("net", "Rs. 62,000", "Net Pay: Rs. 62,000")]),
    "APPLICATION_FORM": Extraction(fields=[ef("applicant_name", "Kavya Sharma", "Applicant Name: Kavya Sharma"),
                                           ef("requested_amount", "4,00,000", "Loan Amount Applied: Rs. 4,00,000"), ef("signed", "yes", "Signature of Applicant: signed")]),
}


class FakeGemini:
    def __init__(self):
        self.calls, self.answers, self.fail = [], dict(ANSWERS), None

    def extract(self, doc_type, pages):
        self.calls.append((doc_type, pages))
        if self.fail:
            raise self.fail
        return self.answers[doc_type]


class FakeStore:
    def __init__(self):
        self.rows = {"ocr_pages": [], "extraction_outputs": []}
        self.precomputed, self.prepared = {}, {}

    def append(self, table, rows):
        self.rows[table] += rows

    def precomputed_pages(self, sha):
        return [{"page_no": i, "text": t, "extraction_method": "PRECOMPUTED_UNLIMITED_OCR", "engine": "Unlimited-OCR-2", "engine_version": None}
                for i, t in enumerate(self.precomputed.get(sha, []), 1)]

    def document_pages(self, doc_id):
        return [{"page_no": r["page_no"], "text": r["text"], "extraction_method": r["extraction_method"], "engine": r["engine"]}
                for r in self.rows["ocr_pages"] if r["document_id"] == doc_id]

    def prepared_values(self, sha):
        return self.prepared.get(sha)

    def cached_extraction(self, sha, model, prompt_version):
        hit = [r for r in self.rows["extraction_outputs"] if (r["source_sha256"], r["model"], r["prompt_version"]) == (sha, model, prompt_version)]
        return Extraction.model_validate_json(hit[-1]["output_json"]).model_dump() if hit else None


class Storage:
    def __init__(self):
        self.objects = {}

    def put(self, path, data, mime):
        self.objects[path] = data

    def get(self, path):
        return self.objects[path]

    def remove(self, path):
        self.objects.pop(path, None)

    def signed_url(self, path):
        return "https://storage.example.invalid/x?token=t"


@pytest.fixture
def world(api, env):  # noqa: F811
    env["admin"].execute("UPDATE jobs SET status = 'DONE' WHERE status IN ('QUEUED', 'RUNNING')")  # the queue is shared: start each test with it empty
    env["admin"].execute("DELETE FROM usage_counters")  # daily budgets are shared too: each test starts with a fresh day's allowance
    st, store, gem = Storage(), FakeStore(), FakeGemini()
    deps = jobs.Deps(connect=lambda: psycopg.connect(env["url"], row_factory=dict_row, autocommit=True), storage=st, store=store, gemini=gem,
                     model="gemini-test", gemini_cap=100, kick=False)
    app.dependency_overrides.update({get_storage: lambda: st, get_ocr_store: lambda: store, jobs.get_job_deps: lambda: deps})
    return {"deps": deps, "store": store, "gemini": gem, "storage": st, "admin": env["admin"]}


def run_all(w, rounds=8):
    """Drain until nothing is ready, making retries due immediately (time travel for the backoff)."""
    out = []
    for _ in range(rounds):
        w["admin"].execute("UPDATE jobs SET run_after = now() WHERE status = 'QUEUED'")
        r = jobs.drain(w["deps"], limit=20)
        out += r
        if not r:
            break
    return out


def doc_row(w, doc_id):
    return w["admin"].execute("SELECT * FROM documents WHERE id = %s", (doc_id,)).fetchone()


def fields(w, doc_id):
    return {r["field"]: r for r in w["admin"].execute("SELECT * FROM document_fields WHERE document_id = %s", (doc_id,)).fetchall()}


def ntb_docs_pending(api):  # noqa: F811
    aid = ntb_ready(api)
    r = api.as_("LOAN_OFFICER").post(f"/applications/{aid}/intake/complete", json={"expected_version": 1})
    assert r.status_code == 200, r.text
    return aid


# ---------------------------------------------------------------- text and extraction
def test_born_digital_pdf_uses_its_text_layer_and_the_pan_never_leaves(api, world):  # noqa: F811
    aid = ntb_docs_pending(api)
    d = upload(api.as_("LOAN_OFFICER"), aid, "PAN_CARD", "PAN_CARD", data=PAN_PDF).json()
    assert run_all(world)[0]["result"] == "DONE"
    row = doc_row(world, d["id"])
    assert row["status"] == "EXTRACTED" and row["text_method"] == "EMBEDDED_TEXT" and row["page_count"] == 1
    sent = world["gemini"].calls[0][1]
    assert NTB_PAN not in " ".join(sent) and "[PAN-1]" in " ".join(sent)  # the synthetic PAN is replaced before anything is sent
    f = fields(world, d["id"])
    assert f["pan"]["value"] == NTB_PAN and f["dob"]["value"] == "1994-08-21" and f["name"]["source"] == "AI_EXTRACTED"
    assert all(x["check_note"] is None for x in f.values()) and all(x["text_method"] == "EMBEDDED_TEXT" for x in f.values())
    assert [p["extraction_method"] for p in world["store"].rows["ocr_pages"]] == ["EMBEDDED_TEXT"]
    assert NTB_PAN not in world["store"].rows["extraction_outputs"][0]["output_json"]
    run = world["admin"].execute("SELECT graph, status, model, steps FROM agent_runs WHERE application_id = %s", (aid,)).fetchone()
    assert (run["graph"], run["status"], run["model"]) == ("DOCUMENT", "DONE", "gemini-test") and [s["step"] for s in run["steps"]] == ["text", "extract", "check"]
    actors = {r["actor_type"] for r in world["admin"].execute("SELECT actor_type FROM audit_events WHERE event_type = 'DOCUMENT_FIELD_INSERT' AND application_id IS NULL "
                                                              "AND entity_id IN (SELECT id::text FROM document_fields WHERE document_id = %s)", (d["id"],)).fetchall()}
    assert actors == {"AGENT"}
    rv = api.as_("ASSISTANT_MANAGER").get(f"/applications/{aid}/documents/{d['id']}/review").json()
    assert rv["document"]["text_method_label"] == "Embedded PDF text (not OCR)" and rv["pages"][0]["method"] == "EMBEDDED_TEXT"
    assert all(x["source_label"] == "AI suggestion, not yet verified" for x in rv["fields"])


def test_a_value_whose_quote_is_not_on_the_page_is_flagged_and_blocks_verification_until_a_person_confirms_it(api, world):  # noqa: F811
    world["gemini"].answers["PAN_CARD"] = Extraction(fields=[*ANSWERS["PAN_CARD"].fields[:3], ef("pan", "SYNP9999999Z", "PAN: SYNP9999999Z")])
    aid = ntb_docs_pending(api)
    lo = api.as_("LOAN_OFFICER")
    d = upload(lo, aid, "PAN_CARD", "PAN_CARD", data=PAN_PDF).json()
    run_all(world)
    assert "quoted text not found" in fields(world, d["id"])["pan"]["check_note"]
    r = lo.post(f"/applications/{aid}/documents/{d['id']}/verify")
    assert r.status_code == 422 and "pan" in r.json()["detail"]
    assert lo.patch(f"/applications/{aid}/documents/{d['id']}/fields", json={"values": {"pan": "ABCDE1234F"}}).status_code == 422  # real-format PAN refused
    fx = lo.patch(f"/applications/{aid}/documents/{d['id']}/fields", json={"values": {"pan": NTB_PAN}})
    assert fx.status_code == 200
    c = world["admin"].execute("SELECT value_before, value_after FROM extraction_corrections WHERE document_id = %s", (d["id"],)).fetchone()
    assert c == {"value_before": "SYNP9999999Z", "value_after": NTB_PAN} and fields(world, d["id"])["pan"]["source"] == "MANUAL"
    v = lo.post(f"/applications/{aid}/documents/{d['id']}/verify")
    assert v.status_code == 200 and v.json()["document"]["status"] == "VERIFIED" and v.json()["discrepancies_opened"] == 0
    assert lo.patch(f"/applications/{aid}/documents/{d['id']}/fields", json={"values": {"name": "X Y"}}).status_code == 403  # verified is final


def test_a_name_variant_opens_a_discrepancy_that_the_officer_resolves(api, world):  # noqa: F811
    world["gemini"].answers["PAN_CARD"] = Extraction(fields=[ef("name", "R. MENON", "Name: RAHUL MENON"), *ANSWERS["PAN_CARD"].fields[1:]])
    aid = ntb_docs_pending(api)
    lo = api.as_("LOAN_OFFICER")
    d = upload(lo, aid, "PAN_CARD", "PAN_CARD", data=PAN_PDF).json()
    run_all(world)
    lo.patch(f"/applications/{aid}/documents/{d['id']}/fields", json={"values": {"name": "R. MENON"}})  # the flagged value, confirmed by a person
    v = lo.post(f"/applications/{aid}/documents/{d['id']}/verify").json()
    assert v["discrepancies_opened"] == 1 and v["application_status"] == "DISCREPANCY"
    disc = api.get(f"/applications/{aid}/discrepancies").json()["discrepancies"]
    assert [(x["rule"], x["form_value"], x["document_value"], x["status"]) for x in disc] == [("NAME", "Rahul Menon", "R. MENON", "OPEN")]
    assert lo.post(f"/applications/{aid}/discrepancies/{disc[0]['id']}/resolve", json={"resolution": "ACCEPT", "note": "short"}).status_code == 422
    r = api.as_("LOAN_OFFICER").post(f"/applications/{aid}/discrepancies/{disc[0]['id']}/resolve",
                                     json={"resolution": "ACCEPT", "note": "Initial used on the card; full name matches the KYC record"})
    assert r.status_code == 200 and r.json()["application_status"] == "DOCS_PENDING" and r.json()["discrepancies"][0]["status"] == "RESOLVED"


def test_a_scan_with_no_ocr_waits_retries_then_goes_to_manual_review_and_is_keyed_by_hand(api, world):  # noqa: F811
    aid = ntb_docs_pending(api)
    lo = api.as_("LOAN_OFFICER")
    d = upload(lo, aid, "PAN_CARD", "PAN_CARD", data=PNG, name="pan.png", mime="image/png").json()
    jobs.drain(world["deps"])
    assert doc_row(world, d["id"])["status"] == "AWAITING_OCR"
    results = run_all(world)
    assert [r["result"] for r in results][-1] == "FAILED" and world["gemini"].calls == []  # nothing substituted, no AI on an empty page
    row = doc_row(world, d["id"])
    assert row["status"] == "NEEDS_MANUAL_REVIEW" and "Key the values" in row["processing_note"] and row["text_method"] is None
    r = lo.patch(f"/applications/{aid}/documents/{d['id']}/fields", json={"values": {"name": "RAHUL MENON", "father_name": "SURESH MENON", "dob": "21-08-1994",
                                                                                      "pan": NTB_PAN}})
    assert r.status_code == 200 and {f["source"] for f in r.json()["fields"]} == {"MANUAL"} and r.json()["pages"] == []
    assert lo.post(f"/applications/{aid}/documents/{d['id']}/verify").json()["document"]["status"] == "VERIFIED"


def test_imported_unlimited_ocr_output_is_used_for_a_scan_and_labelled_as_such(api, world):  # noqa: F811
    world["store"].precomputed[hashlib.sha256(JPG).hexdigest()] = ["Name: RAHUL MENON\nFather's Name: SURESH MENON\nDate of Birth: 21/08/1994\n"
                                                                  f"Permanent Account Number: {NTB_PAN}"]
    aid = ntb_docs_pending(api)
    d = upload(api.as_("LOAN_OFFICER"), aid, "PAN_CARD", "PAN_CARD", data=JPG, name="pan.jpg", mime="image/jpeg").json()
    run_all(world)
    row = doc_row(world, d["id"])
    assert row["status"] == "EXTRACTED" and row["text_method"] == "PRECOMPUTED_UNLIMITED_OCR"
    assert {f["text_method"] for f in fields(world, d["id"]).values()} == {"PRECOMPUTED_UNLIMITED_OCR"}


def test_prepared_demo_data_is_labelled_and_is_never_evidence_until_a_person_confirms_it(api, world):  # noqa: F811
    world["store"].prepared[hashlib.sha256(PNG).hexdigest()] = {"name": "Rahul Menon", "father_name": "Suresh Menon", "dob": "1994-08-21", "pan": NTB_PAN}
    aid = ntb_docs_pending(api)
    lo = api.as_("LOAN_OFFICER")
    d = upload(lo, aid, "PAN_CARD", "PAN_CARD", data=PNG, name="pan.png", mime="image/png").json()
    run_all(world)
    row, f = doc_row(world, d["id"]), fields(world, d["id"])
    assert row["status"] == "NEEDS_MANUAL_REVIEW" and row["text_method"] == "PREPARED_DEMO_TEXT" and world["gemini"].calls == []
    assert {x["source"] for x in f.values()} == {"PREPARED_DEMO_TEXT"} and f["pan"]["check_note"] == "Prepared demo data — not OCR"
    rv = lo.get(f"/applications/{aid}/documents/{d['id']}/review").json()
    assert rv["document"]["text_method_label"] == "Prepared demo data — not OCR" and {x["source_label"] for x in rv["fields"]} == {"Prepared demo data — not OCR"}
    r = lo.post(f"/applications/{aid}/documents/{d['id']}/verify")
    assert r.status_code == 422 and "not evidence" in r.json()["detail"]
    lo.patch(f"/applications/{aid}/documents/{d['id']}/fields", json={"values": {k: v["value"] for k, v in f.items()}})  # each value checked against the image
    assert lo.post(f"/applications/{aid}/documents/{d['id']}/verify").status_code == 200


def test_rate_limits_and_the_daily_budget_retry_and_never_overspend(api, world):  # noqa: F811
    aid = ntb_docs_pending(api)
    world["deps"].gemini_cap = 0
    d = upload(api.as_("LOAN_OFFICER"), aid, "PAN_CARD", "PAN_CARD", data=PAN_PDF).json()
    r = jobs.drain(world["deps"])
    assert r[0]["result"] == "RETRY" and "budget" in r[0]["note"] and world["gemini"].calls == []
    world["deps"].gemini_cap = 100
    world["gemini"].fail = RuntimeError("429 RESOURCE_EXHAUSTED: quota")
    results = run_all(world)
    assert all("rate limit" in x["note"] for x in results) and results[-1]["result"] == "FAILED"
    assert doc_row(world, d["id"])["status"] == "NEEDS_MANUAL_REVIEW"


def test_the_same_file_is_never_sent_to_gemini_twice(api, world):  # noqa: F811
    for _ in range(2):
        aid = ntb_docs_pending(api)
        upload(api.as_("LOAN_OFFICER"), aid, "PAN_CARD", "PAN_CARD", data=PAN_PDF)
        run_all(world)
    assert len(world["gemini"].calls) == 1


# ---------------------------------------------------------------- AI boundaries and roles
def test_the_system_and_the_ai_cannot_verify_documents_or_overwrite_people(api, world, env):  # noqa: F811
    aid = ntb_docs_pending(api)
    lo = api.as_("LOAN_OFFICER")
    d = upload(lo, aid, "PAN_CARD", "PAN_CARD", data=PAN_PDF).json()
    run_all(world)
    lo.patch(f"/applications/{aid}/documents/{d['id']}/fields", json={"values": {"name": "RAHUL MENON"}})
    a = env["admin"]
    for actor in ("AGENT", "SYSTEM"):
        with a.transaction():
            a.execute("SELECT set_config('app.actor_type', %s, true)", (actor,))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with a.transaction():
                    a.execute("UPDATE documents SET status = 'VERIFIED', reviewed_by = %s, reviewed_at = now() WHERE id = %s", (env["LOAN_OFFICER"].user_id, d["id"]))
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="never overwrites"):
                with a.transaction():
                    a.execute("UPDATE document_fields SET value = 'SOMEONE ELSE', source = 'AI_EXTRACTED' WHERE document_id = %s AND field = 'name'", (d["id"],))
    with a.transaction():
        a.execute("SELECT set_config('app.actor_type', 'STAFF', true), set_config('app.actor_id', %s, true)", (str(env["LOAN_OFFICER"].user_id),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="MANUAL"):
            with a.transaction():
                a.execute("UPDATE document_fields SET value = 'X', source = 'AI_EXTRACTED' WHERE document_id = %s AND field = 'dob'", (d["id"],))
        with pytest.raises(psycopg.errors.CheckViolation, match="extraction_corrections"):  # a person's change without its correction record
            with a.transaction():
                a.execute("UPDATE document_fields SET value = '1990-01-01', source = 'MANUAL' WHERE document_id = %s AND field = 'dob'", (d["id"],))


@pytest.mark.parametrize("role", ["ASSISTANT_MANAGER", "BRANCH_MANAGER"])
def test_managers_can_also_review_and_verify_documents(api, world, role):  # noqa: F811
    """Migration 0015: managers can process applications too (four-eyes then keeps them off the decision)."""
    aid = ntb_docs_pending(api)
    d = upload(api.as_("LOAN_OFFICER"), aid, "PAN_CARD", "PAN_CARD", data=PAN_PDF).json()
    run_all(world)
    m = api.as_(role)
    assert m.get(f"/applications/{aid}/documents/{d['id']}/review").status_code == 200
    assert m.patch(f"/applications/{aid}/documents/{d['id']}/fields", json={"values": {"name": "RAHUL MENON"}}).status_code == 200


# ---------------------------------------------------------------- attestation
def test_documents_attestation_moves_the_application_only_when_it_is_true(api, world, env):  # noqa: F811
    aid = new_app(api, customer_id="BOB-C0027")
    lo = api.as_("LOAN_OFFICER")
    assert lo.post(f"/applications/{aid}/intake/complete", json={"expected_version": 1}).status_code == 200  # needs form, photograph, salary slips
    form = upload(lo, aid, "APPLICATION_FORM", "APPLICATION_FORM", data=FORM_PDF).json()
    photo = upload(lo, aid, "PHOTOGRAPH", "PHOTOGRAPH", data=PNG, name="photo.png", mime="image/png").json()
    run_all(world)
    attest = {"expected_version": 2, "attestation": "I have checked every document against the originals shown by the customer."}
    assert lo.post(f"/applications/{aid}/documents/attest", json=attest | {"attestation": "ok"}).status_code == 422
    r = lo.post(f"/applications/{aid}/documents/attest", json=attest)
    assert r.status_code == 422 and "SALARY_SLIPS_3M" in r.json()["detail"]  # the database checks the attestation is true
    slip = upload(lo, aid, "SALARY_SLIPS_3M", "SALARY_SLIP", data=SLIP_PDF).json()
    run_all(world)
    assert doc_row(world, photo["id"])["status"] == "NEEDS_MANUAL_REVIEW"  # a photograph is checked by eye
    assert lo.post(f"/applications/{aid}/documents/{form['id']}/verify").status_code == 422  # "yes" is inferred, not printed: a person confirms it
    assert lo.patch(f"/applications/{aid}/documents/{form['id']}/fields", json={"values": {"signed": "yes"}}).status_code == 200
    for d in (form, photo, slip):
        assert lo.post(f"/applications/{aid}/documents/{d['id']}/verify").status_code == 200, d
    assert lo.get(f"/applications/{aid}/discrepancies").json()["discrepancies"] == []  # slip net pay 62,000 = declared
    before = counts(env)
    r = lo.post(f"/applications/{aid}/documents/attest", json=attest)
    assert r.status_code == 200 and r.json()["status"] == "DOCS_VERIFIED"
    assert counts(env)["decisions"] == before["decisions"]  # verification decides nothing about the loan
    ev = env["admin"].execute("SELECT actor_role, reason FROM audit_events WHERE event_type = 'STATUS_TRANSITION' AND application_id = %s ORDER BY id DESC LIMIT 1",
                              (aid,)).fetchone()
    assert ev["actor_role"] == "LOAN_OFFICER" and ev["reason"].startswith("I have checked")
    assert upload(lo, aid, "SALARY_SLIPS_3M", "SALARY_SLIP", data=make_pdf(["another slip " * 4])).status_code == 409


def test_a_rejected_document_must_be_replaced(api, world):  # noqa: F811
    aid = ntb_docs_pending(api)
    lo = api.as_("LOAN_OFFICER")
    d = upload(lo, aid, "PAN_CARD", "PAN_CARD", data=PAN_PDF).json()
    run_all(world)
    assert lo.post(f"/applications/{aid}/documents/{d['id']}/reject", json={"reason": "bad"}).status_code == 422
    r = lo.post(f"/applications/{aid}/documents/{d['id']}/reject", json={"reason": "Card image cut off; customer to bring the original"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    item = next(i for i in lo.get(f"/applications/{aid}").json()["checklist"]["items"] if i["code"] == "PAN_CARD")
    assert item["uploaded"] == 1  # still counted as uploaded, but not verified: the attestation guard requires a VERIFIED copy


def test_the_scheduled_drain_needs_the_internal_token(api, world, monkeypatch):  # noqa: F811
    assert api.post("/internal/jobs/drain").status_code == 503
    monkeypatch.setattr(settings, "internal_api_token", "t0ken-for-tests")
    assert api.post("/internal/jobs/drain", headers={"X-Internal-Token": "wrong"}).status_code == 401
    assert api.post("/internal/jobs/drain", headers={"X-Internal-Token": "t0ken-for-tests"}).json() == {"results": []}
