"""Validation suite for the canonical dataset files. Reads only the CSV files and manifest (what would be loaded).

    backend/.venv/Scripts/python data/validate.py [--dir data/out]

Every check is a hard requirement; nothing here is weakened to pass.
"""

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from .build import AS_OF, SEGMENTS, TAGS, THIN_SCORE_PRESENT
from .schema import TABLES

REAL_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
SYN_PAN = re.compile(r"^SYNP[0-9]{7}[A-Z]$")
CUST_ID = re.compile(r"^BOB-C[0-9]{4}$")
D = date.fromisoformat


def load(out: Path) -> dict:
    t = {}
    for ds, tbls in TABLES.items():
        for name in tbls:
            with open(out / ds / f"{name}.csv", encoding="utf-8", newline="") as f:
                t[name] = [{k: (v if v != "" else None) for k, v in row.items()} for row in csv.DictReader(f)]
    return t


def age(dob: str, on: date = AS_OF) -> int:
    d = D(dob)
    return on.year - d.year - ((on.month, on.day) < (d.month, d.day))


def run(out: Path) -> list[tuple[str, bool, str]]:
    res: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        res.append((name, bool(ok), detail))

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    t = load(out)
    cust = {r["customer_id"]: r for r in t["customers"]}
    bur = {r["customer_id"]: r for r in t["bureau_profiles"]}
    truth = {r["customer_id"]: r for r in t["customer_ground_truth"]}
    acc = {r["account_id"]: r for r in t["accounts"]}
    loans = {r["loan_id"]: r for r in t["existing_loans"]}
    tl = {r["tradeline_id"]: r for r in t["bureau_tradelines"]}
    tags = {c: set(json.loads(r["edge_case_tags"])) for c, r in truth.items()}
    primary = {r["account_id"]: r["customer_id"] for r in t["account_holders"] if r["holder_role"] == "PRIMARY"}

    # --- D. canonical ID format (no second customer ID format anywhere)
    ids = [r["customer_id"] for tn in ("customers", "employment", "account_holders", "existing_loans", "bureau_profiles", "bureau_tradelines", "ext_kyc_mock", "customer_ground_truth",
                                       "documents_truth", "external_statement_lines", "historical_applications") for r in t[tn] if r["customer_id"]]
    check("D1 every customer_id is BOB-C0001 format", all(CUST_ID.match(i) for i in ids))
    check("D2 no competing CUST- identifier in any file", not any(b"CUST-" in p.read_bytes() for p in out.rglob("*") if p.is_file()))
    check("D3 customer ids are BOB-C0001..BOB-C1000", sorted(cust) == [f"BOB-C{i:04d}" for i in range(1, 1001)])


    try:
        _rest(res, check, manifest, t, cust, bur, truth, acc, loans, tl, tags, primary, out)
    except Exception as e:  # a crash is a failure, never a silent pass
        check(f"Z validation aborted: {type(e).__name__}", False, str(e)[:200])
    return res


def _rest(res, check, manifest, t, cust, bur, truth, acc, loans, tl, tags, primary, out) -> None:
    # --- A. files, counts, checksums
    bad = []
    for key, meta in manifest["tables"].items():
        data = (out / f"{key}.csv").read_bytes()
        if hashlib.sha256(data).hexdigest() != meta["sha256"] or len(t[key.split("/")[1]]) != meta["rows"]:
            bad.append(key)
    check("A1 row counts and checksums match the manifest", not bad, str(bad))
    check("A2 manifest holds no runtime metadata", not re.search(r"generated_at|hostname|platform|python", json.dumps(manifest)), "")
    check("A3 volatile metadata lives only in run_info.json", (out / "run_info.json").exists())

    # --- B. segments
    derived = Counter(f"{cust[c]['bank_relationship']}_{ {'ESTABLISHED': 'EST', 'THIN': 'THIN', 'NO_HISTORY': 'NTC'}[bur[c]['history_status']] }" for c in cust)
    check("B1 approved segment counts exactly 440/80/80/250/50/100 (1,000)", dict(derived) == SEGMENTS and len(cust) == 1000, str(dict(derived)))
    check("B2 ground-truth segment agrees with relationship and bureau status for every customer",
          all(truth[c]["segment_code"] == f"{cust[c]['bank_relationship']}_{ {'ESTABLISHED': 'EST', 'THIN': 'THIN', 'NO_HISTORY': 'NTC'}[bur[c]['history_status']] }" for c in cust))
    check("B3 credit-history status is not a column of customers", "history_status" not in t["customers"][0] and "credit_history_status" not in t["customers"][0])

    # --- C. keys
    for ds, tbls in TABLES.items():
        for name, (pk, _) in tbls.items():
            keys = [tuple(r[k] for k in pk) for r in t[name]]
            check(f"C1 primary key unique and non-null: {name}", len(set(keys)) == len(keys) and all(None not in k for k in keys))
    fks = [("employment", "customer_id", cust), ("account_holders", "customer_id", cust), ("account_holders", "account_id", acc), ("transactions", "account_id", acc),
           ("existing_loans", "customer_id", cust), ("existing_loans", "repayment_account_id", acc), ("loan_repayments", "loan_id", loans), ("bureau_profiles", "customer_id", cust),
           ("bureau_tradelines", "customer_id", cust), ("ext_kyc_mock", "customer_id", cust), ("customer_ground_truth", "customer_id", cust), ("documents_truth", "customer_id", cust),
           ("external_statement_lines", "customer_id", cust), ("external_statement_lines", "account_id", acc), ("accounts", "branch_id", {r["branch_id"] for r in t["branches"]}),
           ("customers", "home_branch_id", {r["branch_id"] for r in t["branches"]}), ("transactions", "loan_id", loans), ("transactions", "tradeline_id", tl),
           ("external_statement_lines", "tradeline_id", tl), ("bureau_tradelines", "existing_loan_id", loans), ("historical_applications", "customer_id", cust)]
    for tbl, col, parent in fks:
        miss = [r[col] for r in t[tbl] if r[col] is not None and r[col] not in parent]
        check(f"C2 foreign key resolves: {tbl}.{col}", not miss, str(miss[:3]))

    # --- E. relationship rules
    holders = defaultdict(list)
    for h in t["account_holders"]:
        holders[h["customer_id"]].append(h)
    etb = [c for c, r in cust.items() if r["bank_relationship"] == "ETB"]
    ntb = [c for c, r in cust.items() if r["bank_relationship"] == "NTB"]
    check("E1 ETB: Bank of Baroda primary account, branch and customer_since",
          all(any(h["holder_role"] == "PRIMARY" and acc[h["account_id"]]["is_bob"] == "true" for h in holders[c]) and cust[c]["home_branch_id"] and cust[c]["customer_since"] for c in etb))
    check("E2 NTB: no Bank of Baroda account, branch or customer_since", all(not any(acc[h["account_id"]]["is_bob"] == "true" for h in holders[c]) and not cust[c]["home_branch_id"] and not cust[c]["customer_since"] for c in ntb))
    check("E3 one current employment per customer", Counter(r["customer_id"] for r in t["employment"] if r["is_current"] == "true") == Counter(list(cust)))
    check("E4 NTB has no Bank of Baroda loans or tradelines", not any(loans[l]["customer_id"] in ntb for l in loans) and not any(r["is_bob"] == "true" and r["customer_id"] in ntb for r in t["bureau_tradelines"]))
    check("E5 transactions only on Bank of Baroda accounts", all(acc[r["account_id"]]["is_bob"] == "true" for r in t["transactions"]))
    check("E6 external statement lines only on non-Bank-of-Baroda accounts held by the customer",
          all(acc[r["account_id"]]["is_bob"] == "false" and primary[r["account_id"]] == r["customer_id"] for r in t["external_statement_lines"]))

    # --- F. credit-history rules
    tcount = Counter(r["customer_id"] for r in t["bureau_tradelines"])
    ntc = [c for c in cust if bur[c]["history_status"] == "NO_HISTORY"]
    thin = [c for c in cust if bur[c]["history_status"] == "THIN"]
    est = [c for c in cust if bur[c]["history_status"] == "ESTABLISHED"]
    check("F1 NTC: zero tradelines, score, max_dpd and oldest date all null", all(tcount[c] == 0 and bur[c]["score"] is None and bur[c]["max_dpd_last_12m"] is None and bur[c]["oldest_tradeline_date"] is None for c in ntc))
    check("F2 thin: 1-2 tradelines, oldest under 12 months", all(1 <= tcount[c] <= 2 and D(bur[c]["oldest_tradeline_date"]) > AS_OF - timedelta(days=335 + 30) for c in thin))
    present = {rel: sum(1 for c in thin if cust[c]["bank_relationship"] == rel and bur[c]["score"] is not None) for rel in ("ETB", "NTB")}
    nulls = {rel: sum(1 for c in thin if cust[c]["bank_relationship"] == rel and bur[c]["score"] is None) for rel in ("ETB", "NTB")}
    check("F3 thin-file scores exact: 78 present (ETB 48, NTB 30), 52 null (ETB 32, NTB 20), of 130", present == THIN_SCORE_PRESENT and nulls == {"ETB": 32, "NTB": 20}
          and sum(present.values()) == 78 and sum(nulls.values()) == 52, f"present={present} null={nulls}")
    check("F4 established: 3-8 tradelines and a score", all(3 <= tcount[c] <= 8 and bur[c]["score"] is not None for c in est))
    check("F5 no thin customer has zero tradelines and no NTC has any", all(tcount[c] > 0 for c in thin) and all(tcount[c] == 0 for c in ntc))
    check("F6 scores within 300-900", all(300 <= int(r["score"]) <= 900 for r in t["bureau_profiles"] if r["score"]))
    check("F7 all three history states appear under both ETB and NTB", all(any(cust[c]["bank_relationship"] == rel and bur[c]["history_status"] == h for c in cust) for rel in ("ETB", "NTB") for h in ("ESTABLISHED", "THIN", "NO_HISTORY")))
    agg = defaultdict(lambda: [0, 0, 0])
    for r in t["bureau_tradelines"]:
        a = agg[r["customer_id"]]
        a[0] += 1
        if r["status"] in ("ACTIVE", "OVERDUE"):
            a[1] += 1
            a[2] += int(r["emi_amount"].split(".")[0]) if r["emi_amount"] else 0
    check("F8 bureau profile aggregates equal the tradelines", all(int(bur[c]["tradeline_count"]) == agg[c][0] and int(bur[c]["active_tradeline_count"]) == agg[c][1] and int(bur[c]["total_monthly_emi"].split(".")[0]) == agg[c][2] for c in cust))

    # --- G. Bank of Baroda loans, repayment accounts, tradelines
    bob_tl = {r["existing_loan_id"]: r for r in t["bureau_tradelines"] if r["is_bob"] == "true"}
    check("G1 every Bank of Baroda tradeline maps to one loan and every loan to one tradeline", set(bob_tl) == set(loans) and len([r for r in t["bureau_tradelines"] if r["is_bob"] == "true"]) == len(loans))
    check("G2 loan and tradeline amounts, EMI, balance and customer agree", all(
        bob_tl[l]["customer_id"] == r["customer_id"] and bob_tl[l]["sanctioned_amount"] == r["sanctioned_amount"] and bob_tl[l]["current_balance"] == r["outstanding_amount"] and bob_tl[l]["emi_amount"] == r["emi_amount"]
        for l, r in loans.items()))
    check("G3 repayment account is an active Bank of Baroda account whose PRIMARY holder is the loan customer",
          all(acc[r["repayment_account_id"]]["is_bob"] == "true" and acc[r["repayment_account_id"]]["status"] == "ACTIVE" and primary[r["repayment_account_id"]] == r["customer_id"] for r in loans.values()))
    check("G4 the repayment account existed before the loan started", all(D(acc[r["repayment_account_id"]]["opened_on"]) <= D(r["start_date"]) for r in loans.values()))
    check("G5 loan customers are ETB with an established or thin history", all(cust[r["customer_id"]]["bank_relationship"] == "ETB" and bur[r["customer_id"]]["history_status"] != "NO_HISTORY" for r in loans.values()))
    check("G6 transaction EMI debits with a loan_id are on that loan's repayment account", all(loans[r["loan_id"]]["repayment_account_id"] == r["account_id"] for r in t["transactions"] if r["loan_id"]))
    check("G7 Bank of Baroda accounts never carry another lender's tradeline as a loan_id, and tradeline_id points only to non-Bank-of-Baroda tradelines of the same customer",
          all(tl[r["tradeline_id"]]["is_bob"] == "false" and tl[r["tradeline_id"]]["customer_id"] == primary[r["account_id"]] and not r["loan_id"] for r in t["transactions"] if r["tradeline_id"]))
    check("G8 external-bank EMI evidence references non-Bank-of-Baroda tradelines of the same NTB customer only",
          all(tl[r["tradeline_id"]]["is_bob"] == "false" and tl[r["tradeline_id"]]["customer_id"] == r["customer_id"] and cust[r["customer_id"]]["bank_relationship"] == "NTB" for r in t["external_statement_lines"] if r["tradeline_id"]))
    check("G9 every external EMI line names its tradeline", all(r["tradeline_id"] for r in t["external_statement_lines"] if r["category"] == "EMI"))
    check("G10 every Bank of Baroda EMI transaction names a loan or another lender's tradeline", all((r["loan_id"] is None) != (r["tradeline_id"] is None) for r in t["transactions"] if r["category"] == "EMI"))

    # repayments reconcile with EMI debits
    emi_post = {(r["loan_id"], r["txn_date"], r["amount"]) for r in t["transactions"] if r["category"] == "EMI" and r["loan_id"] and r["txn_status"] == "POSTED"}
    emi_ret = {(r["loan_id"], r["txn_date"]) for r in t["transactions"] if r["txn_status"] == "RETURNED"}
    win = date(2025, 11, 1)
    paid_in_win = {(r["loan_id"], r["paid_date"], r["paid_amount"]) for r in t["loan_repayments"] if r["paid_date"] and D(r["paid_date"]) >= win}
    check("G11 paid repayments in the window equal the posted EMI debits exactly", paid_in_win == emi_post, f"{len(paid_in_win)} vs {len(emi_post)}")
    late_rep = {(r["loan_id"], r["due_date"]) for r in t["loan_repayments"] if r["status"] == "PAID_LATE" and r["paid_date"] and D(r["paid_date"]) >= win}
    check("G12 every returned EMI is followed by a late repayment", emi_ret <= late_rep)

    # --- H. accounts, balances, income
    chain_bad = []
    for name, key in (("transactions", "txn_id"), ("external_statement_lines", "line_id")):
        by_acc = defaultdict(list)
        for r in sorted(t[name], key=lambda r: r[key]):
            by_acc[r["account_id"]].append(r)
        for a, rows in by_acc.items():
            prev = None
            for r in rows:
                bal = int(r["balance_after"].split(".")[0])
                amt = int(r["amount"].split(".")[0])
                posted = r.get("txn_status", "POSTED") == "POSTED"
                if bal < 0 or (prev is not None and bal != prev + (0 if not posted else amt if r["direction"] == "CREDIT" else -amt)):
                    chain_bad.append(a)
                    break
                prev = bal
            if name == "transactions" and int(acc[a]["current_balance"].split(".")[0]) != prev:
                chain_bad.append(a)
    check("H1 balance_after chains are consistent, never negative, and match account current_balance", not chain_bad, str(chain_bad[:3]))
    sal = defaultdict(list)
    for r in t["transactions"]:
        if r["category"] == "SALARY" and r["account_id"] in primary:
            sal[primary[r["account_id"]]].append(int(r["amount"].split(".")[0]))
    emp_cur = {r["customer_id"]: r for r in t["employment"] if r["is_current"] == "true"}
    clean = [c for c in etb if emp_cur[c]["employment_type"].startswith("SALARIED") and not tags[c] & {"SALARY_IRREGULAR", "INCOME_MISMATCH_15", "JOB_UNDER_1Y"} and not tags[c] & {"BORDERLINE_FOIR"}]
    check("H2 salary credits equal declared net income for ordinary salaried ETB customers", all(sal[c] and all(a == int(emp_cur[c]["declared_monthly_net"].split(".")[0]) for a in sal[c]) for c in clean), f"{len(clean)} customers")
    mism = [c for c in etb if "INCOME_MISMATCH_15" in tags[c] and emp_cur[c]["employment_type"].startswith("SALARIED") and "SALARY_IRREGULAR" not in tags[c] and sal[c]]
    check("H3 income-mismatch customers' salary credits differ from declared net by more than 15%", all(any(abs(a - int(emp_cur[c]["declared_monthly_net"].split(".")[0])) * 100 > 15 * int(emp_cur[c]["declared_monthly_net"].split(".")[0]) for a in sal[c]) for c in mism), f"{len(mism)} customers")

    # --- I. realism, PAN safety, synthetic markers
    ages = {c: age(r["date_of_birth"]) for c, r in cust.items()}
    check("I1 ages 18-62 and exactly 8 customers under 21", all(18 <= a <= 62 for a in ages.values()) and sum(1 for a in ages.values() if a < 21) == 8, f"under21={sum(1 for a in ages.values() if a < 21)}")
    check("I2 declared net income at least 15,000 and gross >= net", all(int(r["declared_monthly_net"].split(".")[0]) >= 15000 and int(r["declared_monthly_gross"].split(".")[0]) >= int(r["declared_monthly_net"].split(".")[0]) for r in t["employment"]))
    pans = [r["pan"] for r in t["customers"]]
    check("I3 every PAN uses the synthetic SYNP format and none can match a real PAN", all(SYN_PAN.match(p) and not REAL_PAN.match(p) and len(p) == 12 for p in pans))
    check("I4 PANs unique and consistent with the mock KYC table", len(set(pans)) == 1000 and all(r["pan"] == cust[r["customer_id"]]["pan"] for r in t["ext_kyc_mock"]))
    doc_pans = [json.loads(r["expected_fields"]).get("pan") for r in t["documents_truth"] if json.loads(r["expected_fields"]).get("pan")]
    check("I5 document PAN values (including typos) never match the real PAN format", all(not REAL_PAN.match(p) for p in doc_pans) and len(doc_pans) > 0)
    check("I6 only four Aadhaar digits stored", all(re.fullmatch(r"[0-9]{4}", r["aadhaar_last4"]) for r in t["customers"]))
    check("I7 emails use the reserved example.invalid domain", all(r["email"].endswith("@example.invalid") for r in t["customers"]))
    check("I8 IFSC codes are synthetic (BARB0SYN...)", all(r["ifsc_code"].startswith("BARB0SYN") for r in t["branches"]))
    check("I9 manifest carries the synthetic notice", "synthetic" in manifest.get("synthetic_notice", "").lower())
    check("I10 mock KYC timestamps are deterministic (derived from the as-of date)", all(r["checked_at"].startswith("2026-11-01T") and r["checked_at"].endswith("Z") for r in t["ext_kyc_mock"]))
    check("I11 no date is after the as-of date", all(D(v) <= AS_OF for tn in ("transactions", "loan_repayments", "customers", "accounts") for r in t[tn] for k, v in r.items() if v and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) and k not in ("maturity_date",)))

    # --- J. edge-case counts
    want = Counter()
    for name, count, _ in TAGS:
        want[name] += count
    got = Counter(x for s in tags.values() for x in s)
    off = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
    check("J1 every edge-case tag has exactly its specified count", not off, str(off))
    fo = [(int(bur[c]["total_monthly_emi"].split(".")[0]), int(emp_cur[c]["declared_monthly_net"].split(".")[0])) for c in cust if "BORDERLINE_FOIR" in tags[c]]
    check("J2 borderline-FOIR customers sit between 44% and 56% FOIR", all(44 <= e * 100 / n <= 56 for e, n in fo), f"{len(fo)} customers")
    check("J3 staff are ETB only; NRI split is 12 ETB and 18 NTB",
          all(cust[c]["is_staff"] == "true" for c in cust if "STAFF" in tags[c]) and sum(1 for r in cust.values() if r["is_staff"] == "true") == 20 and all(r["bank_relationship"] == "ETB" for r in cust.values() if r["is_staff"] == "true")
          and Counter(r["bank_relationship"] for r in cust.values() if r["residency_status"] == "NRI") == Counter({"ETB": 12, "NTB": 18}))
    dm = Counter(r["mobile"] for r in cust.values())
    check("J4 exactly six duplicate-mobile pairs", sorted(dm.values(), reverse=True)[:7] == [2] * 6 + [1] and sum(1 for v in dm.values() if v > 1) == 6)
    check("J5 thin customers tagged THIN_DPD all show a 30+ DPD", all(int(bur[c]["max_dpd_last_12m"] or 0) >= 30 or any(int(r["max_dpd"]) >= 30 for r in t["bureau_tradelines"] if r["customer_id"] == c) for c in cust if "THIN_DPD" in tags[c]))
    ret_c = {primary[r["account_id"]] for r in t["transactions"] if r["txn_status"] == "RETURNED"}
    check("J6 exactly the 25 RETURNED_EMI customers have returned EMI debits", ret_c == {c for c in cust if "RETURNED_EMI" in tags[c]}, f"{len(ret_c)}")
    check("J7 KYC expired or pending count is 30 (18 expired)", Counter(r["kyc_status"] for r in cust.values() if r["kyc_status"] in ("EXPIRED", "PENDING")) == Counter({"EXPIRED": 18, "PENDING": 12}))
    dorm = {primary[a] for a, r in acc.items() if r["status"] == "DORMANT" and a in primary}
    check("J8 30 customers hold a dormant account and none of them is transacting on it", len(dorm) == 30 and not any(acc[r["account_id"]]["status"] == "DORMANT" for r in t["transactions"]))
    gov = [c for c in etb if "GOVT_SALARY_BOB" in tags[c]]
    check("J9 govt-salary customers are SALARIED_GOVT with a Bank of Baroda salary account", all(emp_cur[c]["employment_type"] == "SALARIED_GOVT" and any(acc[h["account_id"]]["account_type"] == "SALARY" and acc[h["account_id"]]["is_bob"] == "true" for h in holders[c]) for c in gov) and len(gov) == 50)
    short = [c for c in cust if "JOB_UNDER_1Y" in tags[c]]
    check("J10 JOB_UNDER_1Y customers started their current job within 12 months", all(D(emp_cur[c]["start_date"]) > AS_OF - timedelta(days=365) for c in short))

    # --- K. documents
    docs = t["documents_truth"]
    per = Counter(d["customer_id"] for d in docs)
    segs = Counter(truth[c]["segment_code"] for c in per)
    check("K1 480 documents for 120 customers, 4 each, 20 per segment", len(docs) == 480 and len(per) == 120 and set(per.values()) == {4} and dict(segs) == {s: 20 for s in SEGMENTS})
    check("K2 document quality counts 240/96/72/72", Counter(d["quality"] for d in docs) == Counter({"CLEAN": 240, "SKEWED": 96, "LOW_RES": 72, "SCANNED_NOISE": 72}))
    check("K3 injected discrepancies 24/24/12/12 and 408 clean", Counter(d["injected_discrepancy"] for d in docs) == Counter({"NONE": 408, "NAME_VARIANT": 24, "INCOME_MISMATCH": 24, "DOB_MISMATCH": 12, "PAN_TYPO": 12}))
    check("K4 every document is marked synthetic", all("SYNTHETIC" in json.loads(d["expected_fields"])["synthetic_notice"] for d in docs))
    stm_ok = True
    for d in docs:
        if d["doc_type"] == "BANK_STATEMENT":
            f = json.loads(d["expected_fields"])
            a = f["account_id"]
            if acc[a]["institution_name"] != f["institution"] or primary[a] != d["customer_id"]:
                stm_ok = False
    check("K5 bank-statement documents describe an account the customer holds", stm_ok)

    # --- L. coverage: is the volume enough for the demo scenarios and analytics?
    primary_acc = {}
    for h in t["account_holders"]:
        if h["holder_role"] == "PRIMARY" and acc[h["account_id"]]["is_bob"] == "true":
            primary_acc.setdefault(h["customer_id"], h["account_id"])
    tx_months = defaultdict(set)
    tx_count = Counter()
    for r in t["transactions"]:
        if r["account_id"] in primary_acc.values():
            tx_months[primary[r["account_id"]]].add(r["txn_date"][:7])
            tx_count[primary[r["account_id"]]] += 1
    check("L1 every ETB primary account has transactions in all 12 months", all(len(tx_months[c]) == 12 for c in etb), f"min txns per ETB customer {min(tx_count[c] for c in etb)}")
    check("L2 every ETB customer has at least 150 transactions over the year", all(tx_count[c] >= 150 for c in etb))
    ln_months = defaultdict(set)
    for r in t["external_statement_lines"]:
        ln_months[r["customer_id"]].add(r["txn_date"][:7])
    check("L3 every NTB customer has external statement lines in all 6 months", all(len(ln_months[c]) == 6 for c in ntb))
    full = [c for c in clean if "AGE_UNDER_21" not in tags[c]]  # under-21 customers legitimately started their first job during the window
    check("L4 ordinary salaried ETB customers (not under 21) have a salary credit in every one of the 12 months", all(len(sal[c]) == 12 for c in full), f"{len(full)} customers")
    hm = Counter(r["application_date"][:7] for r in t["historical_applications"])
    hs = Counter((r["relationship_at_application"], r["history_status_at_application"]) for r in t["historical_applications"])
    check("L5 historical applications cover 34 months with at least 30 per month and at least 50 in each of the six segments",
          len(hm) == 34 and min(hm.values()) >= 30 and len(hs) == 6 and min(hs.values()) >= 50, f"min per month {min(hm.values())}, min per segment {min(hs.values())}")


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parents[1] / "out"))
    a = ap.parse_args(argv)
    results = run(Path(a.dir))
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0
