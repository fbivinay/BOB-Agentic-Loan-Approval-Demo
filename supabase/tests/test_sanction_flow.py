"""Phase 10 against real Postgres with the real migrations: human approval -> sanction pack -> SANCTIONED -> approval email through the
outbox, with retries, permanent failure and re-send, the daily cap, signed webhooks and the guards. Storage and Resend are fakes."""

import base64
import hashlib
import hmac
import io
import json
import time
import uuid

import httpx
import pytest
from pypdf import PdfReader

from app import outbox
from app.sanction import inr as sanction_inr
from app.config import settings
from app.main import app
from app.storage import get_generated_storage
from test_applications_api import PNG, new_app, upload  # noqa: F401
from test_assessment_flow import aw, verified_etb  # noqa: F401  (fixtures)
from test_documents_flow import Storage, run_all, world  # noqa: F401  (fixtures)
from test_enquiries_api import api, env  # noqa: F401  (fixtures)
from test_review_flow import approve, claimed, ver

LO, AM, BM = "LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"


class FakeResend:
    def __init__(self):
        self.sent, self.fail, self.events = [], [], {}

    def send(self, payload, key):
        self.sent.append((payload, key))
        if self.fail:
            err = self.fail.pop(0)
            raise err
        return f"re_{hashlib.sha256(key.encode()).hexdigest()[:12]}"

    def last_event(self, message_id):
        return self.events.get(message_id, "sent")


class GenStorage(Storage):
    def put(self, path, data, mime):
        if path in self.objects:
            raise httpx.HTTPStatusError("Duplicate", request=httpx.Request("POST", "http://x"), response=httpx.Response(409))
        super().put(path, data, mime)


@pytest.fixture
def sw(aw):  # noqa: F811
    gen, rs = GenStorage(), FakeResend()
    d = aw["deps"]
    d.generated, d.resend, d.email_redirect_to, d.resend_cap = gen, rs, "", 80
    app.dependency_overrides[get_generated_storage] = lambda: gen
    return aw | {"gen": gen, "resend": rs}


def approved(api, sw, run=True):  # noqa: F811
    aid = claimed(api, sw)
    r = approve(api.as_(AM), aid)
    assert r.status_code == 201, r.text
    if run:
        run_all(sw)
    return aid


def status(w, aid):
    return w["admin"].execute("SELECT status FROM applications WHERE id = %s", (aid,)).fetchone()["status"]


def text(pdf):
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(pdf)).pages)


def test_approval_issues_the_pack_sanctions_and_emails_it_once_with_the_three_pdfs(api, sw):  # noqa: F811
    aid = approved(api, sw, run=False)
    assert sw["admin"].execute("SELECT count(*) AS n FROM jobs WHERE application_id = %s AND kind = 'sanction_pack' AND status = 'QUEUED'", (aid,)).fetchone()["n"] == 1
    run_all(sw)
    assert status(sw, aid) == "SANCTIONED"
    p = api.get(f"/applications/{aid}/sanction-pack").json()
    assert p["state"] == "ISSUED" and [d["kind"] for d in p["documents"]] == ["EMI_SCHEDULE", "KFS", "SANCTION_LETTER"]
    e = p["approval_email"]
    assert e["status"] == "SENT" and e["to_address"] == "delivered@resend.dev" and e["attachments"] == 3
    assert [x["event"] for x in e["events"]] == ["QUEUED", "SENT"]

    payload, key = sw["resend"].sent[0]
    assert len(sw["resend"].sent) == 1 and key == e["id"]  # the outbox id is the idempotency key
    assert payload["to"] == ["delivered@resend.dev"] and payload["from"] == "onboarding@resend.dev"
    ref = api.get(f"/applications/{aid}/review").json()["application"]["reference"]
    assert sorted(a["filename"] for a in payload["attachments"]) == [f"{n}-{ref}.pdf" for n in ("EMI-Schedule", "Key-Facts-Statement", "Sanction-Letter")]
    assert "not from Bank of Baroda" in payload["text"] and "redirected to a test inbox" in payload["text"]
    applicant = sw["admin"].execute("SELECT a.* FROM applicants a JOIN applications ap ON ap.applicant_id = a.id WHERE ap.id = %s", (aid,)).fetchone()
    for att in payload["attachments"]:
        t = text(base64.b64decode(att["content"]))
        assert applicant["full_name"] in t and "not a loan offer" in t.lower()
        assert applicant["pan"] not in t and applicant["mobile"] not in t  # no PAN or mobile number leaves through the email
    assert applicant["pan"] not in payload["html"] + payload["text"] and applicant["mobile"] not in payload["html"] + payload["text"]
    terms = api.get(f"/applications/{aid}/review").json()["terms"]["approved"]
    kfs = text(base64.b64decode(next(a for a in payload["attachments"] if a["filename"].startswith("Key-Facts"))["content"]))
    assert sanction_inr(terms["emi"]) in kfs and sanction_inr(terms["amount"]) in kfs and "Annual Percentage Rate" in kfs

    r = api.get(f"/applications/{aid}/sanction-pack/KFS/url")
    assert r.status_code == 200 and r.json()["expires_in_seconds"] == 60
    assert sw["admin"].execute("SELECT count(*) AS n FROM audit_events WHERE application_id = %s AND event_type = 'SANCTION_DOCUMENT_VIEWED'", (aid,)).fetchone()["n"] == 1
    ev = sw["admin"].execute("SELECT actor_type, reason FROM audit_events WHERE application_id = %s AND event_type = 'STATUS_TRANSITION' ORDER BY id DESC LIMIT 1",
                             (aid,)).fetchone()
    assert ev["actor_type"] == "SYSTEM" and ev["reason"].startswith("Sanction pack generated")
    run_all(sw)
    assert len(sw["resend"].sent) == 1  # nothing is sent twice


def test_transient_errors_retry_with_the_same_key_and_sanction_never_waits_for_email(api, sw):  # noqa: F811
    sw["resend"].fail = [outbox.ResendError(503, "service unavailable"), httpx.ConnectTimeout("timed out")]
    aid = approved(api, sw)
    e = api.get(f"/applications/{aid}/emails").json()["emails"][0]
    assert status(sw, aid) == "SANCTIONED" and e["status"] == "SENT" and e["attempts"] == 3
    assert [x["event"] for x in e["events"]] == ["QUEUED", "RETRY", "RETRY", "SENT"]
    assert len({k for _, k in sw["resend"].sent}) == 1  # every attempt reused one idempotency key


def test_a_permanent_failure_stops_and_the_loan_officer_resends_with_a_reason(api, sw):  # noqa: F811
    sw["resend"].fail = [outbox.ResendError(422, "validation_error: invalid attachment")]
    aid = approved(api, sw)
    e = api.get(f"/applications/{aid}/emails").json()["emails"][0]
    assert e["status"] == "FAILED" and e["can_resend"] and len(sw["resend"].sent) == 1 and "422" in e["last_error"]
    assert status(sw, aid) == "SANCTIONED"
    url = f"/applications/{aid}/emails/{e['id']}/resend"
    assert api.as_(AM).post(url, json={"reason": "Customer gave a corrected address"}).status_code == 403
    lo = api.as_(LO)
    assert lo.post(url, json={"reason": "short"}).status_code == 422
    r = lo.post(url, json={"reason": "Customer gave a corrected address at the branch", "email": "kavya.sharma.new@example.invalid"})
    assert r.status_code == 202
    run_all(sw)
    e = lo.get(f"/applications/{aid}/emails").json()["emails"][0]
    assert e["status"] == "SENT" and sw["resend"].sent[-1][1] == f"{e['id']}:resend:1"  # a new send gets a new key
    assert sw["resend"].sent[-1][0]["to"] == ["delivered@resend.dev"]  # still the test inbox, never the customer's address
    assert "kavya.sharma.new@example.invalid" in sw["resend"].sent[-1][0]["text"]  # the corrected intended address is named
    assert lo.post(url, json={"reason": "Customer gave a corrected address at the branch"}).status_code == 409  # only after a failure
    a = sw["admin"].execute("SELECT reason FROM audit_events WHERE entity_id = %s AND event_type = 'EMAIL_RESENT'", (e["id"],)).fetchone()
    assert a["reason"].startswith("Customer gave")


def test_the_daily_cap_holds_the_email_and_nothing_else(api, sw):  # noqa: F811
    sw["deps"].resend_cap = 0
    aid = approved(api, sw, run=False)
    run_all(sw, rounds=2)
    e = api.get(f"/applications/{aid}/emails").json()["emails"][0]
    assert status(sw, aid) == "SANCTIONED" and e["status"] == "QUEUED" and "cap" in e["last_error"] and sw["resend"].sent == []


def signed(secret, body: bytes, msg_id=None):
    msg_id, ts = msg_id or f"msg_{uuid.uuid4().hex[:10]}", str(int(time.time()))
    key = base64.b64decode(secret.removeprefix("whsec_"))
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return {"svix-id": msg_id, "svix-timestamp": ts, "svix-signature": f"v1,{sig}", "content-type": "application/json"}


def test_signed_webhooks_move_the_status_forward_only_and_a_bounce_can_be_resent(api, sw, monkeypatch):  # noqa: F811
    aid = approved(api, sw)
    e = api.get(f"/applications/{aid}/emails").json()["emails"][0]
    body = lambda t: json.dumps({"type": t, "created_at": "2026-11-26T10:00:00Z", "data": {"email_id": e["provider_message_id"]}}).encode()  # noqa: E731
    assert api.post("/webhooks/resend", content=body("email.delivered")).status_code == 503  # off until configured
    secret = "whsec_" + base64.b64encode(b"test-secret-test-secret-test-sec").decode()
    monkeypatch.setattr(settings, "resend_webhook_secret", secret)
    assert api.post("/webhooks/resend", content=body("email.delivered"), headers={"svix-id": "x", "svix-timestamp": "1", "svix-signature": "v1,bad"}).status_code == 401
    h = signed(secret, body("email.delivered"))
    assert api.post("/webhooks/resend", content=body("email.delivered"), headers=h).json()["result"] == "updated"
    assert api.post("/webhooks/resend", content=body("email.delivered"), headers=h).json()["result"] == "duplicate"
    assert api.post("/webhooks/resend", content=body("email.sent"), headers=signed(secret, body("email.sent"))).json()["result"] == "recorded"  # no downgrade
    assert api.get(f"/applications/{aid}/emails").json()["emails"][0]["status"] == "DELIVERED"
    assert api.post("/webhooks/resend", content=body("email.bounced"), headers=signed(secret, body("email.bounced"))).json()["result"] == "updated"
    e = api.get(f"/applications/{aid}/emails").json()["emails"][0]
    assert e["status"] == "BOUNCED" and e["can_resend"]


def test_local_polling_updates_delivery(api, sw):  # noqa: F811
    aid = approved(api, sw)
    e = api.get(f"/applications/{aid}/emails").json()["emails"][0]
    sw["resend"].events[e["provider_message_id"]] = "delivered"
    assert api.get(f"/applications/{aid}/emails", params={"refresh": True}).json()["emails"][0]["status"] == "DELIVERED"


def test_guards_no_pack_or_email_without_approval_and_never_to_a_synthetic_address(api, sw):  # noqa: F811
    aid = claimed(api, sw)
    r = api.as_(AM).post(f"/applications/{aid}/decision", json={"expected_version": ver(api, aid), "outcome": "DECLINED",
                                                                 "reason": "Customer withdrew the application at the branch"})
    assert r.status_code == 201
    run_all(sw)
    assert api.get(f"/applications/{aid}/sanction-pack").json()["state"] == "NOT_APPLICABLE" and sw["gen"].objects == {}
    adm = sw["admin"]
    with pytest.raises(Exception, match="APPROVED decision"):
        adm.execute("INSERT INTO email_outbox (application_id, kind, to_address, subject, idempotency_key) VALUES (%s, 'APPROVAL', 'delivered@resend.dev', 's', %s)",
                    (aid, str(uuid.uuid4())))
    ok = approved(api, sw)
    with pytest.raises(Exception, match="email_outbox_never_synthetic"):
        adm.execute("UPDATE email_outbox SET to_address = 'kavya@example.invalid' WHERE application_id = %s", (ok,))
    with pytest.raises(Exception, match="own generated documents"):  # another application's documents cannot be attached
        adm.execute(
            "INSERT INTO email_outbox (application_id, kind, to_address, subject, idempotency_key, attachment_ids) "
            "SELECT %s, 'DISBURSEMENT_CONFIRMATION', 'delivered@resend.dev', 's', %s, array_agg(id) FROM generated_documents WHERE application_id = %s",
            (aid, str(uuid.uuid4()), ok))
    with pytest.raises(Exception, match="fixed once queued"):
        adm.execute("UPDATE email_outbox SET subject = 'changed' WHERE application_id = %s", (ok,))
    with pytest.raises(Exception):
        adm.execute("DELETE FROM generated_documents WHERE application_id = %s", (ok,))
    with pytest.raises(Exception):
        adm.execute("UPDATE generated_documents SET sha256 = repeat('0', 64) WHERE application_id = %s", (ok,))
    assert api.as_(LO).post(f"/applications/{ok}/sanction-pack/retry", json={"expected_version": ver(api, ok)}).status_code == 409  # already sanctioned


def test_a_send_only_key_explains_why_status_cannot_be_polled(api, sw):  # noqa: F811
    aid = approved(api, sw)

    def restricted(message_id):
        raise outbox.ResendError(401, "restricted_api_key: This API key is restricted to only send emails")

    sw["resend"].last_event = restricted
    r = api.get(f"/applications/{aid}/emails", params={"refresh": True}).json()
    assert r["emails"][0]["status"] == "SENT" and "send-only" in r["note"]
