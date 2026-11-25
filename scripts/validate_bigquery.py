"""Validate what is actually in BigQuery against the manifest, as the controlled evaluation identity (can read both datasets),
then confirm what the runtime identities can and cannot see.

    backend/.venv/Scripts/python scripts/validate_bigquery.py
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings  # noqa: E402

P, LOC = settings.gcp_project, settings.bq_location
SA = {n: f"{n}@{P}.iam.gserviceaccount.com" for n in ("sa-app-runtime", "sa-agent-runtime", "sa-truth-eval")}
manifest = json.loads((ROOT / "data" / "out" / "manifest.json").read_text(encoding="utf-8"))
res: list[tuple[str, bool, str]] = []


def token(sa: str) -> str:
    r = subprocess.run([shutil.which("gcloud"), "auth", "print-access-token", f"--impersonate-service-account={sa}"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise SystemExit(r.stderr[:300])
    return r.stdout.strip()


def q(tok: str, sql: str):
    r = httpx.post(f"https://bigquery.googleapis.com/bigquery/v2/projects/{P}/queries", headers={"Authorization": f"Bearer {tok}"},
                   json={"query": sql, "useLegacySql": False, "location": LOC, "maximumBytesBilled": str(200 * 1024 * 1024), "timeoutMs": 60000}, timeout=120)
    if r.status_code != 200:
        return r.status_code, None
    return 200, [[f["v"] for f in row["f"]] for row in r.json().get("rows", [])]


def check(name: str, ok: bool, detail: str = "") -> None:
    res.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))


def one(tok: str, sql: str):
    code, rows = q(tok, sql)
    return rows[0][0] if rows else f"HTTP {code}"


def main() -> int:
    ev = token(SA["sa-truth-eval"])
    app = token(SA["sa-app-runtime"])
    R, T = f"`{P}.lh_raw", f"`{P}.lh_truth"
    # --- counts vs manifest (raw via app identity proves the app can read it; truth via evaluation identity)
    for key, meta in manifest["tables"].items():
        ds, name = key.split("/")
        tok = app if ds == "lh_raw" else ev
        got = one(tok, f"SELECT COUNT(*) FROM `{P}.{ds}.{name}`")
        check(f"row count {key} = {meta['rows']}", str(got) == str(meta["rows"]), f"got {got}")
    # --- segments (raw only: relationship + bureau history; the app can compute this itself without any truth table)
    code, rows = q(app, f"SELECT c.bank_relationship, b.history_status, COUNT(*) FROM {R}.customers` c JOIN {R}.bureau_profiles` b USING (customer_id) GROUP BY 1,2")
    seg = {(a, b): int(n) for a, b, n in rows}
    want = {("ETB", "ESTABLISHED"): 440, ("ETB", "THIN"): 80, ("ETB", "NO_HISTORY"): 80, ("NTB", "ESTABLISHED"): 250, ("NTB", "THIN"): 50, ("NTB", "NO_HISTORY"): 100}
    check("segment counts 440/80/80/250/50/100 from customers + bureau_profiles", seg == want, str(seg))
    # the evaluation identity cannot read lh_raw (by design), so compare the two sides in Python
    _, truth_rows = q(ev, f"SELECT customer_id, segment_code FROM {T}.customer_ground_truth`")
    _, raw_rows = q(app, f"SELECT c.customer_id, CONCAT(c.bank_relationship, '_', CASE b.history_status WHEN 'ESTABLISHED' THEN 'EST' WHEN 'THIN' THEN 'THIN' ELSE 'NTC' END) FROM {R}.customers` c JOIN {R}.bureau_profiles` b USING (customer_id)")
    check("ground-truth segment_code agrees with raw data for all 1,000 customers", truth_rows is not None and raw_rows is not None and len(truth_rows) == 1000 and {tuple(r) for r in truth_rows} == {tuple(r) for r in raw_rows})
    check("thin-file scores: 78 present, 52 null", one(app, f"SELECT CONCAT(CAST(COUNTIF(score IS NOT NULL) AS STRING), '/', CAST(COUNTIF(score IS NULL) AS STRING)) FROM {R}.bureau_profiles` WHERE history_status='THIN'") == "78/52")
    check("NTC: zero tradelines, null score", one(app, f"SELECT COUNT(*) FROM {R}.bureau_profiles` b WHERE history_status='NO_HISTORY' AND (score IS NOT NULL OR tradeline_count != 0)") == "0")
    # --- keys
    pk = {"customers": "customer_id", "accounts": "account_id", "transactions": "txn_id", "existing_loans": "loan_id", "loan_repayments": "repayment_id",
          "bureau_tradelines": "tradeline_id", "employment": "employment_id", "historical_applications": "application_id", "branches": "branch_id",
          "bureau_profiles": "customer_id", "ext_kyc_mock": "customer_id"}
    for t, k in pk.items():
        check(f"primary key unique and non-null: lh_raw.{t}", one(app, f"SELECT COUNT(*) - COUNT(DISTINCT {k}) + COUNTIF({k} IS NULL) FROM {R}.{t}`") == "0")
    fk = [("accounts", "branch_id", "branches", "branch_id"), ("transactions", "account_id", "accounts", "account_id"), ("existing_loans", "customer_id", "customers", "customer_id"),
          ("existing_loans", "repayment_account_id", "accounts", "account_id"), ("loan_repayments", "loan_id", "existing_loans", "loan_id"), ("bureau_tradelines", "customer_id", "customers", "customer_id"),
          ("bureau_tradelines", "existing_loan_id", "existing_loans", "loan_id"), ("transactions", "loan_id", "existing_loans", "loan_id"), ("transactions", "tradeline_id", "bureau_tradelines", "tradeline_id"),
          ("account_holders", "customer_id", "customers", "customer_id"), ("account_holders", "account_id", "accounts", "account_id"), ("employment", "customer_id", "customers", "customer_id"),
          ("customers", "home_branch_id", "branches", "branch_id"), ("historical_applications", "customer_id", "customers", "customer_id"), ("ext_kyc_mock", "customer_id", "customers", "customer_id")]
    for c, col, p, pcol in fk:
        check(f"foreign key resolves: {c}.{col} -> {p}.{pcol}", one(app, f"SELECT COUNT(*) FROM {R}.{c}` x LEFT JOIN {R}.{p}` y ON x.{col} = y.{pcol} WHERE x.{col} IS NOT NULL AND y.{pcol} IS NULL") == "0")
    # --- types, nulls, formats
    check("empty CSV fields loaded as NULL (NTB has no home_branch_id; 400 NTB)", one(app, f"SELECT COUNTIF(home_branch_id IS NULL) FROM {R}.customers`") == "400")
    check("no empty-string values in nullable text columns", one(app, f"SELECT COUNTIF(home_branch_id = '') + COUNTIF(email = '') FROM {R}.customers`") == "0")
    check("customer ids BOB-C#### only, 1,000 distinct", one(app, f"SELECT COUNTIF(NOT REGEXP_CONTAINS(customer_id, r'^BOB-C[0-9]{{4}}$')) FROM {R}.customers`") == "0")
    check("every PAN is the 12-character synthetic form", one(app, f"SELECT COUNTIF(NOT REGEXP_CONTAINS(pan, r'^SYNP[0-9]{{7}}[A-Z]$')) FROM {R}.customers`") == "0")
    check("money columns are NUMERIC with two decimals (sample)", one(app, f"SELECT COUNTIF(amount != ROUND(amount, 2)) FROM {R}.transactions`") == "0")
    check("monthly_dpd_24m parses as a 24-element JSON array", one(app, f"SELECT COUNTIF(ARRAY_LENGTH(JSON_EXTRACT_ARRAY(monthly_dpd_24m)) != 24) FROM {R}.bureau_tradelines`") == "0")
    check("last balance_after equals accounts.current_balance for every account with transactions (and every active Bank of Baroda account has transactions)",
          one(app, f"WITH l AS (SELECT account_id, ARRAY_AGG(balance_after ORDER BY txn_id DESC LIMIT 1)[OFFSET(0)] b FROM {R}.transactions` GROUP BY 1) "
                   f"SELECT CONCAT(CAST(COUNTIF(l.b != a.current_balance) AS STRING), '/', CAST(COUNT(*) AS STRING)) FROM l JOIN {R}.accounts` a USING (account_id)") not in ("", None)
          and one(app, f"WITH l AS (SELECT account_id, ARRAY_AGG(balance_after ORDER BY txn_id DESC LIMIT 1)[OFFSET(0)] b FROM {R}.transactions` GROUP BY 1) "
                       f"SELECT COUNTIF(l.b != a.current_balance) FROM l JOIN {R}.accounts` a USING (account_id)") == "0"
          and one(app, f"SELECT COUNT(DISTINCT account_id) FROM {R}.transactions`") == one(app, f"SELECT COUNT(*) FROM {R}.accounts` WHERE is_bob AND status = 'ACTIVE'"))
    check("repayment account belongs to the loan's primary customer", one(app, f"SELECT COUNT(*) FROM {R}.existing_loans` l JOIN {R}.account_holders` h ON h.account_id = l.repayment_account_id AND h.holder_role='PRIMARY' WHERE h.customer_id != l.customer_id") == "0")
    check("NTB customers have no Bank of Baroda accounts, loans or transactions", one(app, f"SELECT COUNT(*) FROM {R}.customers` c JOIN {R}.account_holders` h USING (customer_id) JOIN {R}.accounts` a USING (account_id) WHERE c.bank_relationship='NTB' AND a.is_bob") == "0")
    # --- isolation of the loaded truth tables
    for name in ("sa-app-runtime", "sa-agent-runtime"):
        tok = token(SA[name])
        for t in ("customer_ground_truth", "documents_truth", "external_statement_lines"):
            code, _ = q(tok, f"SELECT COUNT(*) FROM `{P}.lh_truth.{t}`")
            check(f"{name} cannot read lh_truth.{t}", code in (403, 404), f"HTTP {code}")
        code, rows = q(tok, f"SELECT COUNT(*) FROM `{P}.lh_raw.customers`")
        check(f"{name} can read lh_raw.customers", code == 200 and rows and rows[0][0] == "1000", f"HTTP {code}")
    bad = [r for r in res if not r[1]]
    print(f"\n{len(res) - len(bad)}/{len(res)} BigQuery checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
