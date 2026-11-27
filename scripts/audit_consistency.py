"""Consistency audit (owner tool, read-only): every synthetic person's data says the same thing in every store. Changes nothing.

Reference: the generator's output in data/out/lh_raw (what BigQuery was loaded from) and what each document actually prints
(data/out/lh_truth/documents_truth.csv). Compared against: data/customers_1000.csv, BigQuery lh_gold.customer_360, and Supabase
(applicants, kyc_profiles, kyc_documents, documents + document_fields, discrepancies, simulated_credit_profiles, assessments, applications,
terms, decisions, sanction documents, disbursements, emails, analytics events). Documents that differ from the record on purpose (the
dataset's planted name, date-of-birth, PAN and income differences) are expected to raise a discrepancy, never to reach the record.

    backend/.venv/Scripts/python scripts/audit_consistency.py [--no-bq]      # exit code 1 when anything does not match
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NO_BQ = "--no-bq" in sys.argv
sys.path.insert(0, str(ROOT / "backend"))
from app.db import connect  # noqa: E402

OUT = ROOT / "data" / "out"
FINDINGS: list[tuple[str, int, list]] = []


def report(check: str, bad: list, show: int = 6):
    FINDINGS.append((check, len(bad), bad[:show]))


def rows(path):
    return list(csv.DictReader(open(path, encoding="utf-8")))


def norm_name(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z ]", " ", str(s or "").upper())).strip()


def num(v):
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def same(a, b) -> bool:
    """Equal after normalising: None and '' alike, numbers by value, text case- and space-insensitive."""
    a, b = ("" if a is None else str(a).strip()), ("" if b is None else str(b).strip())
    if a == b:
        return True
    na, nb = num(a), num(b)
    if na is not None and nb is not None:
        return na == nb
    return re.sub(r"\s+", " ", a).casefold() == re.sub(r"\s+", " ", b).casefold()


def fmt(v):
    return v.isoformat() if isinstance(v, date) else v


# ------------------------------------------------------------------ reference data (the generator's output)
customers = {r["customer_id"]: r for r in rows(OUT / "lh_raw" / "customers.csv")}
by_pan = {r["pan"]: cid for cid, r in customers.items()}
emp = {r["customer_id"]: r for r in rows(OUT / "lh_raw" / "employment.csv") if r["is_current"] == "true"}
bureau = {r["customer_id"]: r for r in rows(OUT / "lh_raw" / "bureau_profiles.csv")}
accounts = {r["account_id"]: r for r in rows(OUT / "lh_raw" / "accounts.csv")}
holders = defaultdict(set)
for r in rows(OUT / "lh_raw" / "account_holders.csv"):
    holders[r["account_id"]].add(r["customer_id"])
truth = {r["file_name"]: r for r in rows(OUT / "lh_truth" / "documents_truth.csv")}
hist = rows(OUT / "lh_raw" / "historical_applications.csv")

# 1. the committed customer list says the same as the generator's tables
bad = []
listed = {r["customer_id"]: r for r in rows(ROOT / "data" / "customers_1000.csv")}
for cid, c in customers.items():
    l = listed.get(cid)
    if l is None:
        bad.append((cid, "missing from customers_1000.csv"))
        continue
    for k in ("bank_relationship", "full_name", "date_of_birth", "gender", "mobile", "email", "pan", "aadhaar_last4", "address_line", "city", "state", "pincode",
              "residency_status", "customer_since", "kyc_status", "kyc_last_verified_on"):
        if not same(l[k], c[k]):
            bad.append((cid, k, l[k], c[k]))
    e, b = emp.get(cid, {}), bureau.get(cid, {})
    for k, ref in (("employment_type", e.get("employment_type")), ("employer_name", e.get("employer_name")), ("declared_monthly_net", e.get("declared_monthly_net")),
                   ("credit_history", b.get("history_status")), ("simulated_bureau_score", b.get("score"))):
        if not same(l[k], ref):
            bad.append((cid, k, l[k], ref))
report("1. data/customers_1000.csv vs generator tables (1,000 people)", bad)

# 2. BigQuery customer_360 (what the app reads) says the same as the generator's tables
if not NO_BQ:
    from app.config import settings
    from app.lakehouse import _client
    from google.cloud import bigquery

    cols = ["customer_id", "bank_relationship", "full_name", "date_of_birth", "gender", "mobile", "email", "pan", "address_line", "city", "state", "pincode",
            "residency_status", "is_staff", "kyc_status", "kyc_last_verified_on", "customer_since", "employment_type", "employer_name", "declared_net_income",
            "bureau_history_status", "bureau_score"]
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=200 * 1024 * 1024)
    bq = {r["customer_id"]: dict(r.items()) for r in _client().query(f"SELECT {', '.join(cols)} FROM `{settings.gcp_project}.lh_gold.customer_360`", job_config=cfg).result()}
    bad = []
    for cid, c in customers.items():
        g = bq.get(cid)
        if g is None:
            bad.append((cid, "missing from customer_360"))
            continue
        for k in ("bank_relationship", "full_name", "date_of_birth", "gender", "mobile", "email", "pan", "address_line", "city", "state", "pincode",
                  "residency_status", "kyc_status", "kyc_last_verified_on", "customer_since"):
            if not same(fmt(g[k]), c[k]):
                bad.append((cid, k, fmt(g[k]), c[k]))
        if str(g["is_staff"]).lower() != c["is_staff"]:
            bad.append((cid, "is_staff", g["is_staff"], c["is_staff"]))
        e, b = emp.get(cid, {}), bureau.get(cid, {})
        for k, ref in (("employment_type", e.get("employment_type")), ("employer_name", e.get("employer_name")), ("declared_net_income", e.get("declared_monthly_net")),
                       ("bureau_history_status", b.get("history_status")), ("bureau_score", b.get("score"))):
            if not same(fmt(g[k]), ref):
                bad.append((cid, k, fmt(g[k]), ref))
    report(f"2. BigQuery lh_gold.customer_360 vs generator tables ({len(bq)} rows)", bad)
    ev = {r["event_type"]: r["n"] for r in _client().query(f"SELECT event_type, COUNT(DISTINCT event_id) n FROM `{settings.gcp_project}.lh_raw.events` GROUP BY 1",
                                                          job_config=cfg).result()}

conn = connect()
q = lambda s, *a: conn.execute(s, a).fetchall()  # noqa: E731

# ------------------------------------------------------------------ 3. applicants vs the person's record
applicants = q("SELECT * FROM applicants")
person_of = {}
bad, unknown = [], []
for a in applicants:
    cid = a["customer_id"] or by_pan.get(a["pan"])
    if cid is None:
        unknown.append((a["full_name"], a["pan"], a["bank_relationship"], str(a["created_at"])[:16]))
        continue
    person_of[a["id"]] = cid
    c, e = customers[cid], emp.get(cid, {})
    if a["bank_relationship"] != c["bank_relationship"]:
        bad.append((cid, "bank_relationship", a["bank_relationship"], c["bank_relationship"]))
    for k in ("full_name", "date_of_birth", "gender", "mobile", "email", "pan", "residency_status", "address_line", "city", "state", "pincode"):
        if not same(fmt(a[k]), c[k]):
            bad.append((cid, a["bank_relationship"], k, fmt(a[k]), c[k]))
    if str(a["is_staff"]).lower() != c["is_staff"]:
        bad.append((cid, a["bank_relationship"], "is_staff", a["is_staff"], c["is_staff"]))
    if not same(a["employment_type"], e.get("employment_type")):
        bad.append((cid, a["bank_relationship"], "employment_type", a["employment_type"], e.get("employment_type")))
    if not same(a["declared_monthly_net"], e.get("declared_monthly_net")):
        bad.append((cid, a["bank_relationship"], "declared_monthly_net", a["declared_monthly_net"], e.get("declared_monthly_net")))
report(f"3. Supabase applicants vs the person's record ({len(applicants)} applicants)", bad, show=40)
report("3b. applicants who are not one of the 1,000 synthetic people", unknown)

# ------------------------------------------------------------------ 4. KYC records vs the person's record and the applicant
kyc = q("SELECT * FROM kyc_profiles")
bad, orphan = [], []
for p in kyc:
    cid = p["customer_id"] or by_pan.get(p["pan"])
    if cid is None:
        orphan.append((p["full_name"], p["pan"], p["source"], p["version"]))
        continue
    c = customers[cid]
    if p["customer_id"] and p["pan"] != c["pan"]:
        bad.append((cid, p["source"], "pan", p["pan"], c["pan"]))
    for k in ("full_name", "date_of_birth", "gender", "aadhaar_last4", "address_line", "city", "state", "pincode"):
        if not same(fmt(p[k]), c[k]):
            bad.append((cid, p["source"], k, fmt(p[k]), c[k]))
    if p["source"] == "ACCOUNT_OPENING":
        if p["kyc_status"] != c["kyc_status"]:
            bad.append((cid, p["source"], "kyc_status", p["kyc_status"], c["kyc_status"]))
        if not same(fmt(p["verified_on"]), c["kyc_last_verified_on"]):
            bad.append((cid, p["source"], "verified_on", fmt(p["verified_on"]), c["kyc_last_verified_on"]))
report(f"4. KYC records vs the person's record ({len(kyc)} records)", bad, show=40)
report("4b. KYC records whose PAN is not any synthetic person's PAN", orphan)
etb = [c for c in customers.values() if c["bank_relationship"] == "ETB"]
have = {p["customer_id"] for p in kyc if p["customer_id"]}
report("4c. existing customers without a KYC record", [c["customer_id"] for c in etb if c["customer_id"] not in have])
ntb_kyc = {p["pan"] for p in kyc if p["customer_id"] is None}
attested = q("SELECT DISTINCT a.id, a.full_name, a.pan, ap.reference, ap.status FROM applications ap JOIN applicants a ON a.id = ap.applicant_id "
             "WHERE a.bank_relationship = 'NTB' AND ap.status NOT IN ('INTAKE', 'DOCS_PENDING', 'DISCREPANCY')")
report("4d. new-to-bank applicants past document attestation without a KYC record", [(r["reference"], r["full_name"], r["pan"], r["status"]) for r in attested
                                                                                       if r["pan"] not in ntb_kyc])
kdocs = q("SELECT k.profile_id, k.doc_type, k.status, k.source, p.customer_id FROM kyc_documents k JOIN kyc_profiles p ON p.id = k.profile_id WHERE k.status = 'CURRENT'")
per = Counter((r["profile_id"]) for r in kdocs if r["source"] == "ACCOUNT_OPENING")
report("4e. account-opening KYC records without exactly 3 current documents",
       [(p["customer_id"], per.get(p["id"], 0)) for p in kyc if p["customer_id"] and per.get(p["id"], 0) != 3 and p["source"] == "ACCOUNT_OPENING"])

# ------------------------------------------------------------------ 5. verified document values vs the document and the person
docs = q("SELECT d.*, ap.reference, ap.applicant_id FROM documents d JOIN applications ap ON ap.id = d.application_id")
fields = defaultdict(dict)
for f in q("SELECT document_id, field, value, source FROM document_fields"):
    fields[f["document_id"]][f["field"]] = f
discs = defaultdict(list)
for x in q("SELECT * FROM discrepancies"):
    discs[x["document_id"]].append(x)
RULE = {"NAME_VARIANT": {"NAME"}, "DOB_MISMATCH": {"DOB"}, "PAN_TYPO": {"PAN"}, "INCOME_MISMATCH": {"INCOME_SALARY_SLIP", "INCOME_ITR", "INCOME_BANK_CREDITS"}}
misread, vs_person, no_disc, injected = [], [], [], Counter()
for d in docs:
    if d["status"] != "VERIFIED":
        continue
    t = truth.get(d["original_name"])
    cid = person_of.get(d["applicant_id"])
    vals = {k: v["value"] for k, v in fields[d["id"]].items()}
    if t:
        printed = {k: v for k, v in json.loads(t["expected_fields"]).items() if k != "synthetic_notice"}
        inj = t["injected_discrepancy"]
        injected[inj] += 1
        for k, v in printed.items():
            if k in vals and not same(vals[k], v) and not (k in ("name", "employee_name", "account_holder") and norm_name(vals[k]) == norm_name(v)):
                misread.append((d["reference"], d["original_name"], d["doc_type"], k, "verified=" + str(vals[k]), "printed=" + str(v), fields[d["id"]][k]["source"]))
        if inj in RULE and not RULE[inj] & {x["rule"] for x in discs[d["id"]]}:
            no_disc.append((d["reference"], d["original_name"], inj, "verified fields: " + json.dumps({k: vals.get(k) for k in ("name", "dob", "pan", "net")
                                                                                                         if k in vals})))
    else:
        inj = "NONE"
    if cid is None:
        continue
    c, e = customers[cid], emp.get(cid, {})
    checks = []
    for k in ("name", "employee_name", "account_holder", "pensioner_name", "applicant_name", "proprietor_name"):
        if vals.get(k):
            checks.append((k, norm_name(vals[k]) == norm_name(c["full_name"]), c["full_name"], "NAME_VARIANT"))
    if vals.get("dob"):
        checks.append(("dob", vals["dob"] == c["date_of_birth"], c["date_of_birth"], "DOB_MISMATCH"))
    if vals.get("pan"):
        checks.append(("pan", vals["pan"] == c["pan"], c["pan"], "PAN_TYPO"))
    if vals.get("gender"):
        checks.append(("gender", same(vals["gender"], c["gender"]), c["gender"], None))
    if vals.get("address") and d["doc_type"] == "AADHAAR_MASKED":
        checks.append(("address", same(vals["address"], f"{c['city']}, {c['state']}"), f"{c['city']}, {c['state']}", None))
    if vals.get("aadhaar_masked"):
        checks.append(("aadhaar_masked", vals["aadhaar_masked"].strip().endswith(c["aadhaar_last4"]), c["aadhaar_last4"], None))
    if vals.get("employer") and d["doc_type"] == "SALARY_SLIP":
        checks.append(("employer", same(vals["employer"], e.get("employer_name")), e.get("employer_name"), None))
    if vals.get("net") and d["doc_type"] == "SALARY_SLIP":
        checks.append(("net", same(vals["net"], e.get("declared_monthly_net")), e.get("declared_monthly_net"), "INCOME_MISMATCH"))
    if vals.get("account_id") and d["doc_type"] == "BANK_STATEMENT":
        checks.append(("account_id", cid in holders.get(vals["account_id"], set()), "an account the person holds", None))
        acc = accounts.get(vals["account_id"])
        if acc and vals.get("institution"):
            checks.append(("institution", same(vals["institution"], acc["institution_name"]), acc["institution_name"], None))
    for k, ok, ref, expected_when in checks:
        if not ok and inj != expected_when:
            vs_person.append((d["reference"], cid, d["original_name"], d["doc_type"], k, str(vals[k]), "record=" + str(ref), "injected=" + inj))
report("5a. verified document values that differ from what the document prints", misread, show=40)
report("5b. verified document values that differ from the person's record (not a planted discrepancy)", vs_person, show=40)
report("5c. planted discrepancies (name/DOB/PAN/income) with no discrepancy raised", no_disc, show=40)
FINDINGS.append(("   (info) planted discrepancies among verified documents", 0, [dict(injected)]))
acc_notes = [(x["rule"], x["form_value"], x["document_value"], x["resolution_note"]) for v in discs.values() for x in v
             if x["rule"] in ("DOB", "PAN", "INCOME_SALARY_SLIP", "INCOME_ITR", "INCOME_BANK_CREDITS") and "formatting" in (x["resolution_note"] or "")]
report("5d. discrepancies closed as 'formatting only' that are not formatting (DOB, PAN, income)", acc_notes, show=40)

# ------------------------------------------------------------------ 6. credit profiles vs the synthetic bureau
bad = []
for r in q("SELECT * FROM simulated_credit_profiles"):
    cid = by_pan.get(r["pan"])
    if cid is None:
        if r["source"] != "SIMULATED":
            bad.append((r["pan"], "not a synthetic PAN but source", r["source"]))
        continue
    b = bureau[cid]
    if r["source"] != "SYNTHETIC_BUREAU":
        bad.append((cid, r["pan"], "source", r["source"]))
    if r["history_status"] != b["history_status"] or not same(r["score"], b["score"]):
        bad.append((cid, r["pan"], f"profile {r['history_status']} {r['score']}", f"bureau {b['history_status']} {b['score'] or None}"))
report("6. simulated credit profiles vs the synthetic bureau record", bad)

# ------------------------------------------------------------------ 7. assessment outputs vs the applicant and the bureau
bad = []
runs = q("SELECT r.application_id, r.output, r.recommendation, ap.ai_recommendation, ap.ai_recommendation_run_id = r.id AS latest, a.pan, a.customer_id "
         "FROM agent_runs r JOIN applications ap ON ap.id = r.application_id JOIN applicants a ON a.id = ap.applicant_id "
         "WHERE r.graph = 'ASSESSMENT' AND r.status = 'DONE'")
for r in runs:
    o = r["output"]
    cp = o.get("credit_profile") or {}
    if cp.get("pan") and cp["pan"] != r["pan"]:
        bad.append((r["application_id"], "credit profile PAN", cp["pan"], r["pan"]))
    cid = r["customer_id"] or by_pan.get(r["pan"])
    if cid and cp:
        b = bureau[cid]
        if cp.get("history_status") != b["history_status"] or not same(cp.get("score"), b["score"]):
            bad.append((cid, "assessment credit profile", cp.get("history_status"), cp.get("score"), "bureau", b["history_status"], b["score"]))
    if r["latest"] and r["recommendation"] != r["ai_recommendation"]:
        bad.append((r["application_id"], "application shows", r["ai_recommendation"], "run says", r["recommendation"]))
report(f"7. assessment outputs vs applicant, bureau and the application ({len(runs)} runs)", bad)

# ------------------------------------------------------------------ 8. application chain: enquiry, terms, decision, sanction, disbursement, email
bad = []
apps = q("SELECT ap.*, e.applicant_id AS e_applicant, e.requested_amount AS e_amount, e.requested_tenure_months AS e_tenure, e.status AS e_status "
         "FROM applications ap LEFT JOIN enquiries e ON e.id = ap.enquiry_id")
terms = defaultdict(list)
for t in q("SELECT * FROM application_terms ORDER BY created_at"):
    terms[t["application_id"]].append(t)
decisions = defaultdict(list)
for x in q("SELECT * FROM decisions ORDER BY decided_at"):
    decisions[x["application_id"]].append(x)
gen = defaultdict(dict)
for g in q("SELECT * FROM generated_documents"):
    gen[g["application_id"]][g["kind"]] = g
disb = {x["application_id"]: x for x in q("SELECT * FROM disbursements")}
emails = defaultdict(dict)
for m in q("SELECT * FROM email_outbox"):
    emails[m["application_id"]][m["kind"]] = m
APPROVED_STATES = ("APPROVED", "SANCTIONED", "DISBURSED")
for a in apps:
    ref = a["reference"]
    if a["enquiry_id"]:
        if a["e_applicant"] != a["applicant_id"]:
            bad.append((ref, "enquiry is for a different applicant"))
        if a["e_status"] != "CONVERTED":
            bad.append((ref, "enquiry status", a["e_status"]))
        if not same(a["e_amount"], a["requested_amount"]) or a["e_tenure"] != a["requested_tenure_months"]:
            bad.append((ref, "requested on enquiry", a["e_amount"], a["e_tenure"], "on application", a["requested_amount"], a["requested_tenure_months"]))
    req = [t for t in terms[a["id"]] if t["kind"] == "REQUESTED"]
    if len(req) != 1 or not same(req[0]["amount"], a["requested_amount"]) or req[0]["tenure_months"] != a["requested_tenure_months"]:
        bad.append((ref, "REQUESTED terms", [(t["amount"], t["tenure_months"]) for t in req], "application", a["requested_amount"], a["requested_tenure_months"]))
    outs = [x["outcome"] for x in decisions[a["id"]]]
    appr = [t for t in terms[a["id"]] if t["kind"] == "APPROVED"]
    if a["status"] in APPROVED_STATES:
        if "APPROVED" not in outs or not appr:
            bad.append((ref, a["status"], "but decisions", outs, "approved terms", len(appr)))
            continue
        latest = appr[-1]
        for kind in ("SANCTION_LETTER", "KFS", "EMI_SCHEDULE"):
            g = gen[a["id"]].get(kind)
            if a["status"] != "APPROVED" and g is None:
                bad.append((ref, a["status"], "no", kind))
            elif g and g["terms_id"] != latest["id"]:
                bad.append((ref, kind, "built from older terms than the approved ones"))
        if "APPROVAL" not in emails[a["id"]] and a["status"] != "APPROVED":
            bad.append((ref, a["status"], "no approval email"))
    elif a["status"] == "DECLINED":
        if not outs or outs[-1] != "DECLINED":
            bad.append((ref, "DECLINED but decisions", outs))
        if appr or gen[a["id"]] or a["id"] in disb:
            bad.append((ref, "DECLINED but has approved terms / sanction documents / disbursement"))
    elif outs and a["status"] not in ("UNDER_REVIEW", "FORWARDED", "RECONSIDERATION"):
        bad.append((ref, a["status"], "has decisions", outs))
    dd = disb.get(a["id"])
    if a["status"] == "DISBURSED":
        if dd is None or dd["status"] != "RELEASED":
            bad.append((ref, "DISBURSED but disbursement", dd and dd["status"]))
        else:
            if dd["terms_id"] and appr and dd["terms_id"] != appr[-1]["id"]:
                bad.append((ref, "disbursement uses older terms"))
            if "DISBURSEMENT_CONFIRMATION" not in emails[a["id"]]:
                bad.append((ref, "no disbursement confirmation email"))
            try:
                from app import sanction
                pd = sanction.pack_data(conn, a["id"], date.today())
                if not same(dd["net_amount"], pd["figures"]["net_disbursed"]):
                    bad.append((ref, "net credited", dd["net_amount"], "KFS net", pd["figures"]["net_disbursed"]))
            except Exception as err:  # noqa: BLE001
                bad.append((ref, "could not recompute KFS figures", str(err)[:120]))
    elif dd is not None and dd["status"] == "RELEASED":
        bad.append((ref, a["status"], "but a released disbursement exists"))
report(f"8. application chain: enquiry, terms, decision, sanction pack, disbursement, emails ({len(apps)} applications)", bad, show=40)

# sanction documents and emails name the applicant as recorded
bad = []
for a in q("SELECT ap.reference, a.full_name, a.email, a.address_line, a.city, m.kind, m.to_address, m.subject FROM email_outbox m "
           "JOIN applications ap ON ap.id = m.application_id JOIN applicants a ON a.id = ap.applicant_id"):
    if a["reference"] not in a["subject"]:
        bad.append((a["reference"], a["kind"], "subject does not carry the reference", a["subject"]))
report("8b. emails carry the application they belong to", bad)

# ------------------------------------------------------------------ 9. analytics: events in BigQuery vs the operational record
if not NO_BQ:
    sent = {r["event_type"]: r["n"] for r in q("SELECT event_type, count(*) n FROM analytics_outbox WHERE status = 'SENT' GROUP BY 1")}
    allv = {r["event_type"]: r["n"] for r in q("SELECT event_type, count(*) n FROM analytics_outbox GROUP BY 1")}
    bad = [(k, "outbox sent", sent.get(k, 0), "BigQuery", ev.get(k, 0), "outbox total", allv.get(k, 0)) for k in sorted(set(sent) | set(ev)) if sent.get(k, 0) != ev.get(k, 0)]
    report("9. analytics events: Supabase outbox (SENT) vs BigQuery lh_raw.events", bad)
dec = Counter(x["outcome"] for v in decisions.values() for x in v)
ev_dec = q("SELECT payload->>'outcome' AS outcome, count(*) n FROM analytics_outbox WHERE event_type = 'DECISION' GROUP BY 1")
evd = {r["outcome"]: r["n"] for r in ev_dec}
if evd:
    report("9b. decisions in the database vs decision events", [(k, dec.get(k, 0), evd.get(k, 0)) for k in set(dec) | set(evd) if dec.get(k, 0) != evd.get(k, 0)])

# ------------------------------------------------------------------ 10. figures the public pages state vs the synthetic history
decided = [h for h in hist if h["outcome"] in ("APPROVED", "DECLINED")]
appr_rate = sum(h["outcome"] == "APPROVED" for h in decided) / len(decided)
tat = sum(int(h["processing_days"]) for h in decided if h["processing_days"]) / len([h for h in decided if h["processing_days"]])
FINDINGS.append(("   (info) synthetic history: decided / approval rate / average turnaround", 0,
                 [f"{len(decided):,} decided, {appr_rate:.1%} approved, {tat:.1f} days (all {len(hist):,} incl. withdrawn)"]))

conn.close()
print()
for check, n, ex in FINDINGS:
    print(("OK   " if n == 0 else f"{n:>4} ") + check)
    if n or check.startswith("   (info)"):
        for e in ex:
            print("        ", e)
sys.exit(1 if any(n for _, n, _ in FINDINGS) else 0)
