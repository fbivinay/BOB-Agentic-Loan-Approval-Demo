"""Phase 10 units: KFS figures against RBI's own worked example, the documents, and the Resend webhook signature."""

import base64
import hashlib
import hmac
import io
from datetime import date
from decimal import Decimal as D

import pytest
from pypdf import PdfReader

from app import outbox, sanction
from app.policy import finance

TODAY = date(2026, 11, 26)  # a Thursday


def test_apr_matches_the_rbi_worked_example():
    """RBI RBC Directions 2025, para 348(3): Rs 20,000, 15% fixed, 24 monthly EMIs, Rs 400 charges -> net 19,600, APR 17.07%."""
    f = finance.kfs_figures(20000, 1500, 24, date(2026, 12, 26), fee_percent_bp=200, fee_min=0, fee_max=100000, gst_percent=0)
    assert f["upfront_charges"] == D("400.00") and f["net_disbursed"] == D("19600.00")
    assert f["apr_percent"] == D("17.07")
    # RBI's 3,274 is 969.73 x 24 - 20,000; our last instalment clears the balance exactly, so the total is within a rupee or two of it
    assert abs(f["total_interest"] - 3274) < 2 and f["emi"] == D("970")  # rounded to the rupee, as in the RBI schedule


def test_indian_grouping_and_working_days():
    assert sanction.inr(D("12345678.9")) == "Rs. 1,23,45,678.90" and sanction.inr(400000) == "Rs. 4,00,000.00" and sanction.inr(999) == "Rs. 999.00"
    assert sanction.working_days_after(TODAY, 3) == date(2026, 12, 1)  # Fri, (Sat, Sun skipped), Mon, Tue


def data(name="Kavya Sharma"):
    first = finance.add_months(TODAY, 1)
    return {"reference": "APP-2026-000003", "sanction_no": "SL-2026-000003", "proposal_no": "KFS-2026-000003", "date": TODAY,
            "kfs_valid_until": sanction.working_days_after(TODAY, 3),
            "borrower": {"name": name, "address": ["14, Paud Road", "Pune, Maharashtra 411038"], "email": "kavya@example.invalid"},
            "approver": {"name": "Anita Rao", "role": "Assistant Manager", "decided_at": TODAY},
            "terms": {"amount": D(400000), "rate_bp": 1150, "tenure_months": 36},
            "figures": finance.kfs_figures(D(400000), 1150, 36, first, fee_percent_bp=100, fee_min=1000, fee_max=10000, gst_percent=18),
            "schedule": finance.schedule(D(400000), 1150, 36, first), "policy": "baroda_personal_loan v1 (test)",
            "charges": {"processing_bp": 100, "processing_min": 1000, "processing_max": 10000, "gst_percent": 18, "penal_percent_pa": 2, "prepayment_percent": 0,
                        "cooling_off_days": 3}}


def text_of(pdf: bytes) -> str:
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(pdf)).pages)


def test_the_pack_is_deterministic_consistent_and_labelled_as_a_demo():
    d = data()
    a, b = sanction.render_pack(d), sanction.render_pack(d)
    assert {k: v[1] for k, v in a.items()} == {k: v[1] for k, v in b.items()}  # identical bytes: a retried job stores the same file
    letter, kfs, sched = (text_of(a[k][0]) for k in sanction.KINDS)
    emi, apr = sanction.inr(d["figures"]["emi"]), str(d["figures"]["apr_percent"])
    for t in (letter, kfs, sched):
        assert "not a loan offer" in t.lower() and "Bank of Baroda" in t and emi in t and apr in t
    assert "Sanction Letter No. SL-2026-000003" in letter and "Anita Rao" in letter and "Rs. 4,00,000.00" in letter
    assert "Proposal number: KFS-2026-000003" in kfs and "Valid until: 01 Dec 2026" in kfs and "Annual Percentage Rate" in kfs
    assert "not as interest" in kfs and "never capitalised" in kfs  # penal charges disclosed as charges, per the RBI directions
    assert kfs.count("Total") >= 1 and f"{d['schedule'][-1].due_date:%d %b %Y}" in sched and len(d["schedule"]) == 36


def test_a_pan_anywhere_in_the_data_stops_rendering():
    with pytest.raises(ValueError, match="PAN"):
        sanction.render_pack(data(name="Kavya SYNP0213813F Sharma"))


def sign(secret, msg_id, ts, body):
    key = base64.b64decode(secret.removeprefix("whsec_"))
    return "v1," + base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()


def test_webhook_signatures():
    secret, body, now = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode(), b'{"type":"email.delivered"}', 1_790_000_000
    h = {"svix-id": "msg_1", "svix-timestamp": str(now), "svix-signature": "v1,bm90LXRoaXM= " + sign(secret, "msg_1", now, body)}
    assert outbox.verify_webhook(secret, h, body, now=now)
    assert not outbox.verify_webhook(secret, h, body + b" ", now=now)  # body changed
    assert not outbox.verify_webhook(secret, h, body, now=now + 301)  # replayed later
    assert not outbox.verify_webhook("whsec_" + base64.b64encode(b"other-key-other-key-other-key-12").decode(), h, body, now=now)
    assert not outbox.verify_webhook(secret, {k: v for k, v in h.items() if k != "svix-signature"}, body, now=now)
    assert not outbox.verify_webhook("", h, body, now=now)


def test_which_resend_errors_are_permanent():
    assert outbox.ResendError(422, "x").permanent and outbox.ResendError(403, "x").permanent
    assert not any(outbox.ResendError(s, "x").permanent for s in (429, 500, 503, 408, 409))
    assert outbox.route("") == "delivered@resend.dev" and outbox.route(" me@mail.test ") == "me@mail.test"


def test_markup_in_names_or_addresses_is_printed_as_text_never_interpreted(tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"not an image")
    d = data(name="Ravi <b>Kumar</b> & Sons")
    d["borrower"]["address"] = [f'<img src="{secret}" width="10" height="10"/> 1, MG Road', '<a href="http://169.254.169.254/latest">Pune</a> 411001']
    d["approver"]["name"] = '<font color="white">Anita</font> Rao'
    pack = sanction.render_pack(d)  # an interpreted <img> with a non-image file would fail to render; escaped, it is just text
    letter = text_of(pack["SANCTION_LETTER"][0])
    assert "Ravi <b>Kumar</b> & Sons" in letter and f'<img src="{secret}"' in letter and "169.254.169.254" in letter
    assert '<font color="white">Anita</font> Rao' in letter
    assert d["borrower"]["name"] == "Ravi <b>Kumar</b> & Sons"  # the caller's data is not modified
