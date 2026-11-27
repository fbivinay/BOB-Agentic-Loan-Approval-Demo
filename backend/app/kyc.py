"""The bank's KYC records (migration 0018).

Existing customers (ETB) have a KYC record from account opening, with its documents (scripts/seed_kyc.py loads the synthetic ones, each a
generated SAMPLE / DEMO document). When their KYC is verified and unchanged, a loan reuses it: PAN and address proof are not asked for again
and the application links to the documents on file. When something changed, or the KYC has expired, staff record the update with a new
document: the record gets a new version, the old document is SUPERSEDED (never deleted), and the history keeps every version.

New-to-bank customers (NTB) complete KYC inside the loan application: their PAN and address-proof documents are uploaded, read and verified
by staff, and attesting the documents creates their KYC record before any assessment, from the applicant's record that every document was
checked against. The same happens for an existing customer whose KYC was not current: the verified loan documents become their KYC documents.
"""

import json
import re
from datetime import date

from app.db import staff_tx

EDITABLE = ("full_name", "date_of_birth", "gender", "aadhaar_last4", "address_line", "city", "state", "pincode")
KYC_DOC_FROM_REQUIREMENT = {"PAN_CARD": "PAN_CARD", "ADDRESS_PROOF": None}  # address proof keeps its own type (masked Aadhaar, passport, voter ID)


def profile(conn, *, profile_id=None, customer_id: str | None = None, pan: str | None = None) -> dict | None:
    if profile_id:
        return conn.execute("SELECT * FROM kyc_profiles WHERE id = %s", (profile_id,)).fetchone()
    if customer_id:
        row = conn.execute("SELECT * FROM kyc_profiles WHERE customer_id = %s", (customer_id.upper(),)).fetchone()
        if row:
            return row
    if pan:
        return conn.execute("SELECT * FROM kyc_profiles WHERE pan = %s", (pan.upper(),)).fetchone()
    return None


def record_for(conn, app: dict) -> dict | None:
    """The KYC record an application belongs to. An existing customer's is found by customer ID only. A new-to-bank applicant's only by
    their PAN, and only a record that is itself new-to-bank (an existing customer's record is never reached by typing their PAN)."""
    if app.get("customer_id"):
        return profile(conn, customer_id=app["customer_id"])
    if app.get("pan"):
        p = profile(conn, pan=app["pan"])
        return p if p is not None and p["customer_id"] is None else None
    return None


def is_current(p: dict | None) -> bool:
    return bool(p) and p["kyc_status"] == "VERIFIED"


def documents(conn, profile_id, current_only: bool = False) -> list[dict]:
    q = "SELECT * FROM kyc_documents WHERE profile_id = %s" + (" AND status = 'CURRENT'" if current_only else "") + " ORDER BY doc_type, version DESC"
    return conn.execute(q, (profile_id,)).fetchall()


def doc_view(d: dict) -> dict:
    return {"id": str(d["id"]), "doc_type": d["doc_type"], "version": d["version"], "status": d["status"], "source": d["source"], "is_sample": d["is_sample"],
            "original_name": d["original_name"], "mime_type": d["mime_type"], "created_at": d["created_at"].isoformat(),
            "superseded_at": d["superseded_at"].isoformat() if d["superseded_at"] else None,
            "label": ("SAMPLE / DEMO document generated for this synthetic customer" if d["is_sample"]
                      else "Submitted and verified during a loan application" if d["source"] == "LOAN_APPLICATION" else "Submitted with a KYC update")}


def view(conn, p: dict, people: dict | None = None) -> dict:
    hist = conn.execute("SELECT version, changed_fields, reason, source, application_id, changed_by, changed_at FROM kyc_profile_history WHERE profile_id = %s "
                        "ORDER BY version DESC", (p["id"],)).fetchall()
    people = people or {r["user_id"]: r["full_name"] for r in conn.execute("SELECT user_id, full_name FROM staff_profiles").fetchall()}
    return {"id": str(p["id"]), "customer_id": p["customer_id"], "pan": p["pan"], "full_name": p["full_name"], "date_of_birth": p["date_of_birth"].isoformat(),
            "gender": p["gender"], "aadhaar_masked": f"XXXX XXXX {p['aadhaar_last4']}" if p["aadhaar_last4"] else None, "address_line": p["address_line"],
            "city": p["city"], "state": p["state"], "pincode": p["pincode"], "kyc_status": p["kyc_status"],
            "verified_on": p["verified_on"].isoformat() if p["verified_on"] else None, "source": p["source"], "version": p["version"],
            "is_current": is_current(p), "updated_at": p["updated_at"].isoformat(),
            "documents": [doc_view(d) for d in documents(conn, p["id"])],
            "history": [{"version": h["version"], "changed_fields": h["changed_fields"], "reason": h["reason"], "source": h["source"],
                         "application_id": str(h["application_id"]) if h["application_id"] else None, "by": people.get(h["changed_by"]),
                         "at": h["changed_at"].isoformat()} for h in hist]}


def snapshot(p: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items() if k in EDITABLE + ("pan", "kyc_status", "verified_on", "customer_id")}


def create(conn, values: dict, source: str, reason: str, staff_id=None, application_id=None) -> dict:
    p = conn.execute("INSERT INTO kyc_profiles (customer_id, pan, full_name, date_of_birth, gender, aadhaar_last4, address_line, city, state, pincode, kyc_status, "
                     "verified_on, source, updated_by) VALUES (%(customer_id)s, %(pan)s, %(full_name)s, %(date_of_birth)s, %(gender)s, %(aadhaar_last4)s, "
                     "%(address_line)s, %(city)s, %(state)s, %(pincode)s, %(kyc_status)s, %(verified_on)s, %(source)s, %(updated_by)s) RETURNING *",
                     {**{k: None for k in ("customer_id", "gender", "aadhaar_last4", "address_line", "city", "state", "pincode", "verified_on")}, **values,
                      "source": source, "updated_by": staff_id}).fetchone()
    conn.execute("INSERT INTO kyc_profile_history (profile_id, version, snapshot, changed_fields, reason, source, application_id, changed_by) "
                 "VALUES (%s, 1, %s, %s, %s, %s, %s, %s)", (p["id"], json.dumps(snapshot(p)), list(values), reason, source, application_id, staff_id))
    return p


def update(conn, p: dict, changes: dict, source: str, reason: str, staff_id, application_id=None, today: date | None = None) -> dict:
    """A new version: changed values, KYC verified as of today, the previous version kept in the history."""
    changed = [k for k, v in changes.items() if k in EDITABLE and v is not None and str(v) != str(p.get(k) or "")]
    sets = {k: changes[k] for k in changed} | {"kyc_status": "VERIFIED", "verified_on": today or date.today()}
    cols = ", ".join(f"{k} = %({k})s" for k in sets)
    p2 = conn.execute(f"UPDATE kyc_profiles SET {cols}, source = %(source)s, version = version + 1, updated_by = %(by)s, updated_at = now() "  # noqa: S608
                      "WHERE id = %(id)s AND version = %(v)s RETURNING *", sets | {"source": source, "by": staff_id, "id": p["id"], "v": p["version"]}).fetchone()
    if p2 is None:
        raise ValueError("the KYC record changed since it was read")
    conn.execute("INSERT INTO kyc_profile_history (profile_id, version, snapshot, changed_fields, reason, source, application_id, changed_by) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                 (p2["id"], p2["version"], json.dumps(snapshot(p2)), changed + ["kyc_status", "verified_on"], reason, source, application_id, staff_id))
    return p2


def add_document(conn, profile_id, doc_type: str, *, bucket: str, path: str, name: str, mime: str, sha256: str, source: str, staff_id=None,
                 is_sample: bool = False, application_document_id=None) -> dict:
    """The new version of a KYC document; the previous one (if any) is superseded, never removed."""
    cur = conn.execute("SELECT id, version FROM kyc_documents WHERE profile_id = %s AND doc_type = %s AND status = 'CURRENT'", (profile_id, doc_type)).fetchone()
    last = conn.execute("SELECT coalesce(max(version), 0) AS v FROM kyc_documents WHERE profile_id = %s AND doc_type = %s", (profile_id, doc_type)).fetchone()["v"]
    if cur:
        conn.execute("UPDATE kyc_documents SET status = 'SUPERSEDED' WHERE id = %s", (cur["id"],))
    return conn.execute("INSERT INTO kyc_documents (profile_id, doc_type, version, bucket, storage_path, original_name, mime_type, sha256, source, is_sample, "
                        "application_document_id, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                        (profile_id, doc_type, last + 1, bucket, path, name, mime, sha256, source, is_sample, application_document_id, staff_id)).fetchone()


def _fields(conn, doc_id) -> dict:
    return {r["field"]: r["value"] for r in conn.execute("SELECT field, value FROM document_fields WHERE document_id = %s", (doc_id,)).fetchall()}


def from_application(conn, app: dict, staff_id, today: date) -> dict | None:
    """After attestation: a new-to-bank applicant's KYC record is created, and an existing customer whose KYC was not current gets a new
    version. Inside the attestation transaction. Returns what was done.

    The values are the applicant's record. Every verified document was checked against it, each difference was raised as a discrepancy, and
    attestation cannot pass while one is open, so the record is what a person confirmed. A document's own printing (a misspelt name, a
    mistyped PAN, an address proof that shows only the city) never becomes the KYC record. The documents add only the masked Aadhaar's last
    four digits, and are kept as the record's KYC documents."""
    existing = record_for(conn, app)
    if app["bank_relationship"] == "ETB" and is_current(existing):
        return None  # verified and unchanged: reused as it is
    docs = conn.execute("SELECT * FROM documents WHERE application_id = %s AND status = 'VERIFIED' AND requirement IN ('PAN_CARD', 'ADDRESS_PROOF') "
                        "ORDER BY uploaded_at", (app["id"],)).fetchall()
    pan_doc = next((d for d in docs if d["requirement"] == "PAN_CARD"), None)
    addr_doc = next((d for d in docs if d["requirement"] == "ADDRESS_PROOF"), None)
    if pan_doc is None and existing is None:
        return {"done": "skipped", "note": "no verified PAN document to build a KYC record from"}
    pan = (app.get("pan") or "").upper()
    if not pan:
        return {"done": "skipped", "note": "the applicant has no PAN on record"}
    if app["bank_relationship"] == "NTB" and existing is None and profile(conn, pan=pan) is not None:
        # the PAN already belongs to someone on record: never merge two people; a person reviews it
        return {"done": "skipped", "note": "the PAN is already on another KYC record; refer for manual review"}
    if existing is not None and (pan != existing["pan"] or str(app["date_of_birth"]) != existing["date_of_birth"].isoformat()):
        # an update must be the same person
        return {"done": "skipped", "note": "the applicant's PAN or date of birth does not match the KYC record; not updated"}
    af = _fields(conn, addr_doc["id"]) if addr_doc else {}
    last4 = (re.findall(r"(\d{4})\s*$", af.get("aadhaar_masked") or "") or [None])[0]
    values = {"full_name": app["full_name"], "date_of_birth": app["date_of_birth"], "gender": app.get("gender"),
              "aadhaar_last4": last4 or (existing or {}).get("aadhaar_last4"), "address_line": app.get("address_line"), "city": app.get("city"),
              "state": app.get("state"), "pincode": app.get("pincode")}
    reason = f"KYC completed from the documents verified on {app['reference']}"
    if existing is None:
        p = create(conn, {**values, "pan": pan, "customer_id": app.get("customer_id"), "kyc_status": "VERIFIED", "verified_on": today},
                   "LOAN_ONBOARDING", reason, staff_id, app["id"])
        done = "created"
    else:
        p = update(conn, existing, values, "KYC_UPDATE", reason, staff_id, app["id"], today)
        done = "updated"
    for d in (pan_doc, addr_doc):
        if d:
            add_document(conn, p["id"], d["doc_type"] if d["requirement"] == "ADDRESS_PROOF" else "PAN_CARD", bucket="loan-documents", path=d["storage_path"],
                         name=d["original_name"], mime=d["mime_type"], sha256=d["sha256"], source="LOAN_APPLICATION", staff_id=staff_id,
                         application_document_id=d["id"])
    conn.execute("SELECT audit_write('STAFF', %s, staff_role(%s), %s, 'kyc_profiles', %s, %s, NULL, %s, %s)",
                 (staff_id, staff_id, f"KYC_{done.upper()}", str(p["id"]), app["id"], json.dumps({"version": p["version"], "kyc_status": p["kyc_status"]}), reason))
    return {"done": done, "profile_id": str(p["id"]), "version": p["version"]}


def record_update(conn, p: dict, changes: dict, reason: str, staff, today: date) -> dict:
    """Staff record a KYC change outside a loan (e.g. a new address with its proof already added as a document)."""
    with staff_tx(conn, staff.user_id, staff.role):
        p2 = update(conn, p, changes, "KYC_UPDATE", reason, staff.user_id, None, today)
        conn.execute("SELECT audit_write('STAFF', %s, %s, 'KYC_UPDATED', 'kyc_profiles', %s, NULL, %s, %s, %s)",
                     (staff.user_id, staff.role, str(p["id"]), json.dumps({"version": p["version"]}), json.dumps({"version": p2["version"]}), reason))
    return p2
