"""Validate the curated and gold layers.

    backend/.venv/Scripts/python scripts/validate_layers.py

1. Independently recomputes every credit feature in plain Python from the local canonical CSVs and compares all 1,000 customers
   with what BigQuery's customer_360 holds (read as the application identity).
2. Structural and business checks: ETB/NTB rules, ranges, bureau consistency, no ground truth in the SQL, runtime access.
"""

import calendar
import csv
import json
import shutil
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "data"))
from app.config import settings  # noqa: E402
from generator.build import AS_OF  # noqa: E402

P, LOC = settings.gcp_project, settings.bq_location
OUT = ROOT / "data" / "out"
INCOME_FROM = date(2026, 5, 1)
res: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    res.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def full_months(d: date, on: date = AS_OF) -> int:
    n = (on.year - d.year) * 12 + on.month - d.month
    return n - (1 if add_months(d, n) > on else 0)


def full_years(d: date, on: date = AS_OF) -> int:
    n = on.year - d.year
    try:
        shifted = date(d.year + n, d.month, d.day)
    except ValueError:
        shifted = date(d.year + n, 2, 28)
    return n - (1 if shifted > on else 0)


def rows(ds: str, name: str) -> list[dict]:
    with open(OUT / ds / f"{name}.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def num(x: str) -> float:
    return float(x)


def token(sa: str) -> str:
    r = subprocess.run([shutil.which("gcloud"), "auth", "print-access-token", f"--impersonate-service-account={sa}"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise SystemExit(r.stderr[:300])
    return r.stdout.strip()


def query(tok: str, sql: str):
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{P}/queries"
    body = {"query": sql, "useLegacySql": False, "location": LOC, "maximumBytesBilled": str(200 * 1024 * 1024), "timeoutMs": 60000, "maxResults": 5000}
    r = httpx.post(url, headers={"Authorization": f"Bearer {tok}"}, json=body, timeout=120)
    if r.status_code != 200:
        return r.status_code, None, None
    j = r.json()
    cols = [f["name"] for f in j["schema"]["fields"]]
    data = [dict(zip(cols, [f["v"] for f in row["f"]])) for row in j.get("rows", [])]
    while j.get("pageToken"):
        j = httpx.get(f"https://bigquery.googleapis.com/bigquery/v2/projects/{P}/queries/{j['jobReference']['jobId']}", headers={"Authorization": f"Bearer {tok}"},
                      params={"location": LOC, "pageToken": j["pageToken"], "maxResults": 5000}, timeout=120).json()
        data += [dict(zip(cols, [f["v"] for f in row["f"]])) for row in j.get("rows", [])]
    return 200, data, cols


def expected() -> dict[str, dict]:
    cust = {r["customer_id"]: r for r in rows("lh_raw", "customers")}
    emp = {r["customer_id"]: r for r in rows("lh_raw", "employment") if r["is_current"] == "true"}
    bur = {r["customer_id"]: r for r in rows("lh_raw", "bureau_profiles")}
    acc = {r["account_id"]: r for r in rows("lh_raw", "accounts")}
    primary = {r["account_id"]: r["customer_id"] for r in rows("lh_raw", "account_holders") if r["holder_role"] == "PRIMARY"}
    months = [add_months(INCOME_FROM, i) for i in range(6)]
    sp: dict = defaultdict(lambda: defaultdict(float))
    br: dict = defaultdict(lambda: defaultdict(float))
    returned: dict = defaultdict(int)
    for t in rows("lh_raw", "transactions"):
        d = date.fromisoformat(t["txn_date"])
        c = primary.get(t["account_id"])
        if c is None or not (INCOME_FROM <= d < AS_OF):
            continue
        key = d.replace(day=1)
        if t["txn_status"] == "RETURNED":
            returned[c] += 1
        elif t["direction"] == "CREDIT":
            if t["category"] in ("SALARY", "PENSION"):
                sp[c][key] += num(t["amount"])
            elif t["category"] == "BUSINESS_RECEIPT":
                br[c][key] += num(t["amount"])
    bank: dict = defaultdict(lambda: {"open": None, "bal": 0.0, "sal": False})
    for a, c in primary.items():
        x = acc[a]
        if x["is_bob"] == "true" and x["status"] == "ACTIVE":
            b = bank[c]
            o = date.fromisoformat(x["opened_on"])
            b["open"] = o if b["open"] is None else min(b["open"], o)
            b["bal"] += num(x["avg_balance_6m"])
            b["sal"] = b["sal"] or x["is_salary_account"] == "true"
    bob_emi: dict = defaultdict(float)
    bob_n: dict = defaultdict(int)
    for l in rows("lh_raw", "existing_loans"):
        if l["status"] in ("ACTIVE", "OVERDUE"):
            bob_emi[l["customer_id"]] += num(l["emi_amount"])
            bob_n[l["customer_id"]] += 1
    late: dict = defaultdict(int)
    for t in rows("lh_raw", "bureau_tradelines"):
        late[t["customer_id"]] += sum(1 for v in json.loads(t["monthly_dpd_24m"]) if v > 0)
    out = {}
    for cid, c in cust.items():
        etb = c["bank_relationship"] == "ETB"
        net = num(emp[cid]["declared_monthly_net"])
        svals = [sp[cid][m] for m in months]
        bvals = [br[cid][m] for m in months]
        avg_s, avg_b = sum(svals) / 6, sum(bvals) / 6
        b = bur[cid]
        e = {"age_years": full_years(date.fromisoformat(c["date_of_birth"])),
             "current_job_tenure_months": full_months(date.fromisoformat(emp[cid]["start_date"])),
             "declared_net_income": net, "bureau_score": int(b["score"]) if b["score"] else None, "tradeline_count": int(b["tradeline_count"]),
             "oldest_tradeline_age_months": full_months(date.fromisoformat(b["oldest_tradeline_date"])) if b["oldest_tradeline_date"] else None,
             "max_dpd_12m": int(b["max_dpd_last_12m"]) if b["max_dpd_last_12m"] else None, "late_payment_months_24m": late[cid],
             "total_monthly_obligations": num(b["total_monthly_emi"]), "existing_obligation_pct": round(num(b["total_monthly_emi"]) * 100 / net, 1)}
        if etb:
            e.update(has_bob_salary_account=bank[cid]["sal"], bob_account_age_months=full_months(bank[cid]["open"]), avg_balance_6m=bank[cid]["bal"],
                     avg_salary_credit_6m=round(avg_s, 2), salary_credit_months_6m=sum(1 for v in svals if v > 0),
                     salary_regularity=round(statistics.pstdev(svals) / avg_s, 4) if avg_s > 0 else None,
                     avg_business_receipts_6m=round(avg_b, 2), business_receipt_months_6m=sum(1 for v in bvals if v > 0),
                     income_credit_vs_declared_pct=round((avg_s + avg_b) * 100 / net, 1) if avg_s + avg_b > 0 else None,
                     bob_emi_total=bob_emi[cid], bob_active_loans=bob_n[cid], returned_debits_6m=returned[cid])
        else:
            for k in ("has_bob_salary_account", "bob_account_age_months", "avg_balance_6m", "avg_salary_credit_6m", "salary_credit_months_6m", "salary_regularity", "avg_business_receipts_6m",
                      "business_receipt_months_6m", "income_credit_vs_declared_pct", "bob_emi_total", "bob_active_loans", "returned_debits_6m"):
                e[k] = None
        out[cid] = e
    return out


ONE_DECIMAL = {"existing_obligation_pct", "income_credit_vs_declared_pct"}  # BigQuery rounds half up, Python half to even: one rounding step of tolerance


def same(exp, got, key: str = "") -> bool:
    if exp is None or got is None:
        return exp is None and got is None
    if isinstance(exp, bool):
        return (got == "true") == exp
    if key in ONE_DECIMAL:
        return abs(float(got) - float(exp)) <= 0.1001
    return abs(float(got) - float(exp)) <= (0.011 if abs(float(exp)) > 5 else 0.0006)


def main() -> int:
    app = token(f"sa-app-runtime@{P}.iam.gserviceaccount.com")
    agent = token(f"sa-agent-runtime@{P}.iam.gserviceaccount.com")
    code, gold, cols = query(app, f"SELECT * FROM `{P}.lh_gold.customer_360`")
    check("runtime identity can read lh_gold.customer_360", code == 200 and gold is not None, f"HTTP {code}")
    if gold is None:
        return 1
    check("customer_360 has exactly 1,000 distinct customers", len(gold) == 1000 and len({g["customer_id"] for g in gold}) == 1000)
    exp = expected()
    mism: dict[str, list[str]] = defaultdict(list)
    for g in gold:
        for k, v in exp[g["customer_id"]].items():
            if not same(v, g.get(k), k):
                mism[k].append(f"{g['customer_id']}: expected {v}, got {g.get(k)}")
    for k in next(iter(exp.values())):
        check(f"feature {k}: all 1,000 customers match an independent recomputation", not mism[k], "; ".join(mism[k][:2]) + f" ({len(mism[k])} differ)")
    etb = [g for g in gold if g["bank_relationship"] == "ETB"]
    ntb = [g for g in gold if g["bank_relationship"] == "NTB"]
    bank_cols = ["has_bob_salary_account", "bob_account_age_months", "avg_balance_6m", "avg_salary_credit_6m", "salary_credit_months_6m", "salary_regularity", "avg_business_receipts_6m",
                 "business_receipt_months_6m", "income_credit_vs_declared_pct", "bob_emi_total", "bob_active_loans", "returned_debits_6m", "home_branch_id", "customer_since"]
    check("600 ETB and 400 NTB rows", (len(etb), len(ntb)) == (600, 400))
    check("NTB rows hold no bank-held data (all bank columns NULL)", all(g[c] is None for g in ntb for c in bank_cols))
    check("every ETB row has bank-held basics (salary-account flag, account age, balance, loans count, returned debits)", all(g[c] is not None for g in etb for c in ("has_bob_salary_account", "bob_account_age_months", "avg_balance_6m", "bob_active_loans", "returned_debits_6m", "home_branch_id")))
    check("bureau status split on gold is 690 / 130 / 180", sorted([sum(1 for g in gold if g["bureau_history_status"] == s) for s in ("ESTABLISHED", "THIN", "NO_HISTORY")]) == [130, 180, 690])
    check("no-history customers: zero tradelines, no score, no oldest age", all(g["tradeline_count"] == "0" and g["bureau_score"] is None and g["oldest_tradeline_age_months"] is None for g in gold if g["bureau_history_status"] == "NO_HISTORY"))
    check("thin: 78 with a score, 52 without", (sum(1 for g in gold if g["bureau_history_status"] == "THIN" and g["bureau_score"] is not None), sum(1 for g in gold if g["bureau_history_status"] == "THIN" and g["bureau_score"] is None)) == (78, 52))
    check("ranges: age 18-62, tenure >= 0, regularity >= 0, obligation pct >= 0", all(18 <= int(g["age_years"]) <= 62 and int(g["current_job_tenure_months"]) >= 0 and float(g["existing_obligation_pct"]) >= 0 and (g["salary_regularity"] is None or float(g["salary_regularity"]) >= 0) for g in gold))
    check("Bank of Baroda EMI never exceeds total bureau obligations", all(float(g["bob_emi_total"]) <= float(g["total_monthly_obligations"]) + 0.01 for g in etb))
    check("customer ids are BOB-C####; no PAN in real format", all(g["customer_id"].startswith("BOB-C") and len(g["pan"]) == 12 for g in gold))
    check("as-of date is the fixed 2026-11-01 on every row", all(g["as_of_date"] == "2026-11-01" for g in gold))
    # the income signal is meaningful: salaried ETB customers' salary credit tracks declared income, so mismatches stand out
    ratios = [float(g["income_credit_vs_declared_pct"]) for g in etb if g["income_credit_vs_declared_pct"] and g["employment_type"].startswith("SALARIED")]
    check("salaried ETB: median observed/declared income is 100%, with a minority far from it", abs(statistics.median(ratios) - 100) < 1 and 0.03 < sum(1 for r in ratios if abs(r - 100) > 15) / len(ratios) < 0.2, f"median {statistics.median(ratios):.1f}")
    for sa_name, tok in (("sa-app-runtime", app), ("sa-agent-runtime", agent)):
        for t in ("lh_curated.credit_features", "lh_curated.tradeline_dpd_24m", "lh_curated.monthly_income_credits", "lh_gold.customer_360"):
            c, _, _ = query(tok, f"SELECT COUNT(*) FROM `{P}.{t}`")
            check(f"{sa_name} can read {t}", c == 200, f"HTTP {c}")
        c, _, _ = query(tok, f"SELECT COUNT(*) FROM `{P}.lh_truth.customer_ground_truth`")
        check(f"{sa_name} still cannot read lh_truth", c in (403, 404), f"HTTP {c}")
    sqls = list((ROOT / "bigquery").rglob("*.sql"))
    check("no layer SQL references ground truth", not any("lh_truth" in f.read_text(encoding="utf-8") for f in sqls) and len(sqls) == 4)
    code, rows_, _ = query(app, f"SELECT COUNT(*) AS n, COUNTIF(dpd > 0) AS late FROM `{P}.lh_curated.tradeline_dpd_24m`")
    check("tradeline_dpd_24m has 24 rows per tradeline (3,888 x 24)", rows_ and rows_[0]["n"] == str(3888 * 24))
    bad = [r for r in res if not r[1]]
    print(f"\n{len(res) - len(bad)}/{len(res)} layer checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
