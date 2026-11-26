"""Email outbox through Resend (free plan): the approval email with the sanction pack, and later the disbursement confirmation.

  * Outbox first: the email row is written in the same transaction as the business change, then a job sends it. Email failure never
    blocks or undoes the sanction; its status is shown on the application.
  * Idempotent: the outbox id is sent as Resend's Idempotency-Key, so a retry after a timeout never sends twice (keys last 24 hours).
  * Retry with backoff (the job runner) on 429, 5xx and network errors; a permanent 4xx goes straight to FAILED. Every attempt and status
    is an email_events row. The API key is never logged or stored.
  * Demo routing: synthetic customers' addresses never receive mail. Every email goes to EMAIL_REDIRECT_TO (your own inbox) or, if that is
    unset, to Resend's test address delivered@resend.dev; the intended address is named in the email body.
  * Daily cap (default 80 of the free 100) through the same take_budget counter as Gemini.
  * Delivery status: a signed webhook (/webhooks/resend, Svix signatures) or, locally, polling Resend for the message's last event.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from html import escape

import httpx

from app.db import staff_tx

TEST_INBOX = "delivered@resend.dev"
RANK = {"QUEUED": 0, "SENDING": 1, "SENT": 2, "DELAYED": 3, "DELIVERED": 4, "BOUNCED": 5, "COMPLAINED": 5, "FAILED": 5}
EVENT_STATUS = {"email.sent": "SENT", "email.delivered": "DELIVERED", "email.delivery_delayed": "DELAYED", "email.bounced": "BOUNCED",
                "email.complained": "COMPLAINED", "email.failed": "FAILED"}
LAST_EVENT_STATUS = {k.split(".", 1)[1]: v for k, v in EVENT_STATUS.items()}
WEBHOOK_TOLERANCE_SECONDS = 300


class ResendError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Resend {status}: {message}")
        self.status, self.message = status, message

    @property
    def permanent(self) -> bool:
        return 400 <= self.status < 500 and self.status not in (408, 409, 429)


class Resend:
    """Minimal client for the two calls needed. The key goes only in the Authorization header."""

    def __init__(self, api_key: str):
        self.http = httpx.Client(base_url="https://api.resend.com", headers={"Authorization": f"Bearer {api_key}"}, timeout=30)

    def send(self, payload: dict, idempotency_key: str) -> str:
        r = self.http.post("/emails", json=payload, headers={"Idempotency-Key": idempotency_key})
        if r.status_code >= 300:
            raise ResendError(r.status_code, _error_text(r))
        return r.json()["id"]

    def last_event(self, message_id: str) -> str | None:
        r = self.http.get(f"/emails/{message_id}")
        if r.status_code >= 300:
            raise ResendError(r.status_code, _error_text(r))
        return r.json().get("last_event")


def _error_text(r: httpx.Response) -> str:
    try:
        j = r.json()
        return f"{j.get('name', '')}: {j.get('message', '')}"[:300]
    except ValueError:
        return r.text[:300]


def route(redirect_to: str) -> str:
    return redirect_to.strip() or TEST_INBOX


def event(conn, email_id, kind: str, raw: dict | None = None) -> None:
    conn.execute("INSERT INTO email_events (email_id, event_type, raw) VALUES (%s, %s, %s)", (email_id, kind, json.dumps(raw or {})))


def set_status(conn, email_id, status: str, **cols) -> bool:
    """Move forward only (a late 'sent' never overwrites 'delivered')."""
    row = conn.execute("SELECT status FROM email_outbox WHERE id = %s FOR UPDATE", (email_id,)).fetchone()
    if row is None or RANK[status] < RANK[row["status"]] or (RANK[status] == RANK[row["status"]] == 5):
        return False
    extra = "".join(f", {k} = %s" for k in cols)
    conn.execute(f"UPDATE email_outbox SET status = %s, updated_at = now(){extra} WHERE id = %s", (status, *cols.values(), email_id))  # noqa: S608
    return True


# ---------------------------------------------------------------- queue
def queue(conn, app_id, kind: str, subject: str, attachment_ids: list, redirect_to: str) -> uuid.UUID:
    """Inside the caller's transaction: the outbox row and the job that sends it."""
    from app import jobs

    email_id = uuid.uuid4()
    conn.execute("INSERT INTO email_outbox (id, application_id, kind, to_address, subject, attachment_ids, idempotency_key) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                 (email_id, app_id, kind, route(redirect_to), subject, attachment_ids, str(email_id)))
    event(conn, email_id, "QUEUED")
    jobs.enqueue(conn, "send_email", {"email_id": str(email_id)}, app_id, f"email:{email_id}:{email_id}")
    return email_id


# ---------------------------------------------------------------- content
def render_email(name: str, headline: str, reference: str, lines: list[tuple[str, str]], closing: str, demo: str, banner: str | None = None) -> tuple[str, str]:
    """(html, text) in the demo's email layout. Every value is escaped in the html. `headline` contains {ref} where the reference goes."""
    text = (f"Dear {name},\n\n{headline.format(ref=reference)}\n\n" + "\n".join(f"{k}: {v}" for k, v in lines)
            + f"\n\n{closing}\n\nRegards,\nBaroda Lending Desk (Demo)\n\n{demo}")
    rows = "".join(f"<tr><td style='padding:4px 12px 4px 0;color:#555'>{escape(k)}</td><td style='padding:4px 0'><b>{escape(v)}</b></td></tr>" for k, v in lines)
    html = (f"<div style='font-family:Arial,sans-serif;font-size:14px;color:#222;max-width:560px'>"
            f"<div style='background:#C2410C;color:#fff;padding:12px 16px;font-weight:bold'>Baroda Lending Desk (Demo)</div>"
            + (f"<div style='background:#FEF3C7;border:1px solid #F59E0B;padding:8px 12px;margin:10px 0;font-size:12px'>{banner}</div>" if banner else "")
            + f"<p>Dear {escape(name)},</p><p>{escape(headline).format(ref=f'<b>{escape(reference)}</b>')}</p><table>{rows}</table>"
            f"<p>{escape(closing)}</p><p>Regards,<br>Baroda Lending Desk (Demo)</p>"
            f"<p style='font-size:11px;color:#777;border-top:1px solid #ddd;padding-top:8px'>{escape(demo)}</p></div>")
    return html, text


def approval_email(name: str, reference: str, amount, rate_bp: int, tenure_months: int, valid_until, intended: str | None) -> tuple[str, str]:
    from app.policy import finance
    from app.sanction import inr

    lines = [("Sanctioned amount", inr(amount)), ("Rate of interest", f"{rate_bp / 100:.2f}% per annum, fixed"), ("Tenure", f"{tenure_months} months"),
             ("EMI", inr(finance.emi(amount, rate_bp, tenure_months)))]
    demo = ("This is a demonstration email from Baroda Lending Desk (Demo), using synthetic data and demo parameters. It is not from Bank of Baroda and "
            f"is not a loan offer. Demo routing: it was addressed to {intended or 'the applicant'} and redirected to a test inbox.")
    closing = (f"Attached: the sanction letter, the Key Facts Statement (KFS) and the EMI schedule. Please read the KFS before you accept; it is valid until "
               f"{valid_until:%d %b %Y}. To accept, visit your branch with your original KYC documents.")
    return render_email(name, "Your personal loan application {ref} has been sanctioned.", reference, lines, closing, demo)


def disbursement_email(name: str, reference: str, x: dict, d: dict, released, intended: str | None) -> tuple[str, str]:
    """x: the released disbursement (loan account, reference, net amount, first EMI date); d: sanction.pack_data for the release date."""
    from datetime import timedelta

    from app.policy import finance
    from app.sanction import inr

    t, f = d["terms"], d["figures"]
    sched = finance.schedule(t["amount"], t["rate_bp"], t["tenure_months"], x["first_emi_date"])
    cooling_until = released + timedelta(days=int(d["charges"]["cooling_off_days"]))
    lines = [("Loan account number", x["loan_account_number"]), ("Reference", x["simulated_reference"]), ("Sanctioned amount", inr(t["amount"])),
             ("Processing fee and GST deducted", inr(f["upfront_charges"])), ("Net amount credited (simulated)", inr(x["net_amount"])),
             ("EMI", f"{inr(f['emi'])} x {len(sched)} (last {inr(sched[-1].payment)})"), ("First EMI due", f"{sched[0].due_date:%d %b %Y}"),
             ("Last EMI due", f"{sched[-1].due_date:%d %b %Y}"), ("Rate of interest", f"{t['rate_bp'] / 100:.2f}% per annum, fixed"),
             ("APR", f"{f['apr_percent']}%")]
    cooling = (f"Cooling-off: until {cooling_until:%d %b %Y} you may exit the loan by repaying the principal and the proportionate APR, "
               "without any penalty.")
    demo = ("SIMULATED DISBURSEMENT: no money has moved and no real account was credited. This is a demonstration email from Baroda Lending Desk (Demo), "
            "using synthetic data and demo parameters. It is not from Bank of Baroda. "
            f"Demo routing: it was addressed to {intended or 'the applicant'} and redirected to a test inbox.")
    return render_email(name, "Your personal loan {ref} has been disbursed.", reference, lines,
                        f"Your EMI will be collected through the repayment mandate you registered. {cooling}", demo,
                        banner="<b>Simulated disbursement</b>: no money has moved.")


def approval_content(conn, email_id) -> tuple[str, str, list[tuple[str, str]]]:
    """(html, text, [(file name, storage path)]) for the approval email, from the application's approved terms and generated documents."""
    from app.sanction import FILE_NAMES, KFS_VALIDITY_WORKING_DAYS, working_days_after

    e = conn.execute("SELECT o.*, ap.reference, a.full_name, a.email AS intended FROM email_outbox o JOIN applications ap ON ap.id = o.application_id "
                     "JOIN applicants a ON a.id = ap.applicant_id WHERE o.id = %s", (email_id,)).fetchone()
    t = conn.execute("SELECT amount, rate_bp, tenure_months FROM application_terms WHERE application_id = %s AND kind = 'APPROVED' ORDER BY created_at DESC LIMIT 1",
                     (e["application_id"],)).fetchone()
    docs = conn.execute("SELECT id, kind, storage_path, created_at FROM generated_documents WHERE id = ANY(%s) ORDER BY kind", (e["attachment_ids"],)).fetchall()
    files = [(f"{FILE_NAMES[d['kind']]}-{e['reference']}.pdf", d["storage_path"]) for d in docs]
    valid = working_days_after(min(d["created_at"] for d in docs).date(), KFS_VALIDITY_WORKING_DAYS)
    html, text = approval_email(e["full_name"], e["reference"], t["amount"], t["rate_bp"], t["tenure_months"], valid, e["intended"])
    return html, text, files


def disbursement_content(conn, email_id) -> tuple[str, str, list[tuple[str, str]]]:
    """The separate confirmation after the (simulated) release: account, amounts, repayment dates and the cooling-off window. No attachments."""
    from app.sanction import pack_data

    e = conn.execute("SELECT o.*, ap.reference, a.full_name, a.email AS intended FROM email_outbox o JOIN applications ap ON ap.id = o.application_id "
                     "JOIN applicants a ON a.id = ap.applicant_id WHERE o.id = %s", (email_id,)).fetchone()
    x = conn.execute("SELECT * FROM disbursements WHERE application_id = %s AND status = 'RELEASED'", (e["application_id"],)).fetchone()
    released = x["released_at"].date()
    html, text = disbursement_email(e["full_name"], e["reference"], x, pack_data(conn, e["application_id"], released), released, e["intended"])
    return html, text, []


CONTENT = {"APPROVAL": approval_content, "DISBURSEMENT_CONFIRMATION": disbursement_content}


# ---------------------------------------------------------------- send (job handler)
def send_job(conn, job: dict, deps) -> None:
    from app.docproc import PAN_RE
    from app.jobs import Retry

    email_id = job["payload"]["email_id"]
    e = conn.execute("SELECT * FROM email_outbox WHERE id = %s", (email_id,)).fetchone()
    if e is None or e["status"] not in ("QUEUED", "SENDING"):
        return  # already sent, or failed and waiting for a person
    if deps.resend is None:
        with staff_tx(conn, actor_type="SYSTEM"):
            conn.execute("UPDATE email_outbox SET last_error = %s, updated_at = now() WHERE id = %s", ("RESEND_API_KEY is not configured", email_id))
        raise Retry("RESEND_API_KEY is not configured", delay=3600)
    if not conn.execute("SELECT take_budget('resend', %s) AS ok", (deps.resend_cap,)).fetchone()["ok"]:
        with staff_tx(conn, actor_type="SYSTEM"):
            conn.execute("UPDATE email_outbox SET last_error = %s, updated_at = now() WHERE id = %s", (f"daily email cap of {deps.resend_cap} reached", email_id))
        raise Retry(f"daily email cap of {deps.resend_cap} reached; retrying later", delay=3600)
    html, text, files = CONTENT[e["kind"]](conn, email_id)
    if PAN_RE.search(html + text):
        raise ValueError("refusing to send: a PAN-like value is in the email body")
    attachments = [{"filename": name, "content": base64.b64encode(deps.generated.get(path)).decode()} for name, path in files]
    with staff_tx(conn, actor_type="SYSTEM"):
        set_status(conn, email_id, "SENDING")
        conn.execute("UPDATE email_outbox SET attempts = attempts + 1 WHERE id = %s", (email_id,))
    payload = {"from": deps.email_from, "to": [e["to_address"]], "subject": e["subject"], "html": html, "text": text, "attachments": attachments}
    try:
        message_id = deps.resend.send(payload, e["idempotency_key"])
    except (ResendError, httpx.HTTPError) as err:
        permanent = isinstance(err, ResendError) and err.permanent
        note = str(err)[:300]
        with staff_tx(conn, actor_type="SYSTEM"):
            conn.execute("UPDATE email_outbox SET status = %s, last_error = %s, updated_at = now() WHERE id = %s", ("FAILED" if permanent else "QUEUED", note, email_id))
            event(conn, email_id, "FAILED" if permanent else "RETRY", {"error": note})
        if permanent:
            return  # a person corrects the address or the content and resends
        raise Retry(f"email not sent ({note}); retrying later") from err
    with staff_tx(conn, actor_type="SYSTEM"):
        set_status(conn, email_id, "SENT", provider_message_id=message_id, last_error=None)
        event(conn, email_id, "SENT", {"provider_message_id": message_id, "attachments": [n for n, _ in files]})


def on_final_failure(conn, job: dict, note: str) -> None:
    with staff_tx(conn, actor_type="SYSTEM"):
        conn.execute("UPDATE email_outbox SET status = 'FAILED', last_error = %s, updated_at = now() WHERE id = %s AND status IN ('QUEUED', 'SENDING')",
                     (f"stopped after retries: {note}"[:300], job["payload"]["email_id"]))


# ---------------------------------------------------------------- delivery status
def verify_webhook(secret: str, headers: dict, body: bytes, now: float | None = None) -> bool:
    """Svix signature (Resend webhooks): HMAC-SHA256 over 'id.timestamp.body' with the base64 secret after 'whsec_'."""
    msg_id, ts, sigs = headers.get("svix-id"), headers.get("svix-timestamp"), headers.get("svix-signature")
    if not (secret and msg_id and ts and sigs):
        return False
    try:
        if abs((now or time.time()) - int(ts)) > WEBHOOK_TOLERANCE_SECONDS:
            return False
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError:
        return False
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, s.split(",", 1)[1]) for s in sigs.split() if s.startswith("v1,"))


def apply_webhook(conn, svix_id: str, payload: dict) -> str:
    status = EVENT_STATUS.get(payload.get("type", ""))
    message_id = (payload.get("data") or {}).get("email_id")
    if status is None or not message_id:
        return "ignored"
    with staff_tx(conn, actor_type="SYSTEM"):
        e = conn.execute("SELECT id FROM email_outbox WHERE provider_message_id = %s", (message_id,)).fetchone()
        if e is None:
            return "unknown message"
        if conn.execute("SELECT 1 FROM email_events WHERE email_id = %s AND raw ->> 'svix_id' = %s", (e["id"], svix_id)).fetchone():
            return "duplicate"
        event(conn, e["id"], payload["type"], {"svix_id": svix_id, "created_at": payload.get("created_at")})
        return "updated" if set_status(conn, e["id"], status) else "recorded"


def refresh(conn, resend, email_id) -> str | None:
    """Poll Resend for the last event of a sent message (local development has no public webhook URL)."""
    e = conn.execute("SELECT provider_message_id, status FROM email_outbox WHERE id = %s", (email_id,)).fetchone()
    if not e or not e["provider_message_id"] or e["status"] not in ("SENT", "DELAYED", "SENDING"):
        return None
    last = resend.last_event(e["provider_message_id"])
    status = LAST_EVENT_STATUS.get(last or "")
    if status:
        with staff_tx(conn, actor_type="SYSTEM"):
            if set_status(conn, email_id, status):
                event(conn, email_id, f"poll.{last}")
    return status
