"""Generate the synthetic dataset as canonical CSV files plus manifest.json.

    backend/.venv/Scripts/python data/generate.py [--seed N] [--out data/out]

Output is gitignored. Nothing is loaded anywhere: loading into BigQuery is a later phase.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generator.build import AS_OF, SEED, build  # noqa: E402
from generator.canonical import write_canonical  # noqa: E402


def generate(out: Path, seed: int = SEED) -> dict:
    tables, summary = build(seed)
    m = write_canonical(tables, out, seed, AS_OF.isoformat(), summary)
    write_customer_list(tables, Path(__file__).resolve().parent / "customers_1000.csv")
    return m


def write_customer_list(tables: dict, path: Path) -> None:
    """data/customers_1000.csv (committed): one row per synthetic customer, for people browsing the repository. Aadhaar is the last 4 digits
    only, PANs use the synthetic SYNP form, emails are @example.invalid: none of it identifies a real person."""
    import csv

    raw = tables["lh_raw"]
    emp = {r["customer_id"]: r for r in raw["employment"] if r["is_current"]}
    bur = {r["customer_id"]: r for r in raw["bureau_profiles"]}
    branch = {r["branch_id"]: r for r in raw["branches"]}
    cols = ["customer_id", "bank_relationship", "full_name", "date_of_birth", "gender", "mobile", "email", "pan", "aadhaar_last4", "address_line", "city", "state",
            "pincode", "residency_status", "employment_type", "employer_name", "declared_monthly_net", "home_branch", "customer_since", "kyc_status",
            "kyc_last_verified_on", "credit_history", "simulated_bureau_score"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in raw["customers"]:
            e, b = emp.get(c["customer_id"], {}), bur.get(c["customer_id"], {})
            br = branch.get(c["home_branch_id"])
            w.writerow([c["customer_id"], c["bank_relationship"], c["full_name"], c["date_of_birth"], c["gender"], c["mobile"], c["email"], c["pan"],
                        c["aadhaar_last4"], c["address_line"], c["city"], c["state"], c["pincode"], c["residency_status"], e.get("employment_type"),
                        e.get("employer_name"), e.get("declared_monthly_net"), br["branch_name"] if br else "", c["customer_since"] or "", c["kyc_status"],
                        c["kyc_last_verified_on"] or "", b.get("history_status"), b.get("score") if b.get("score") is not None else ""])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
    a = ap.parse_args()
    t0 = time.time()
    m = generate(Path(a.out), a.seed)
    print(f"{m['dataset_version']}  canonical_sha256={m['canonical_sha256']}  ({time.time() - t0:.1f}s)")
    for k, v in m["tables"].items():
        print(f"  {k:<40} {v['rows']:>8} rows")
