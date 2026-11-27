"""DEMO / SIMULATED credit profile, one per PAN per calendar month. There is no credit bureau in this demo.

* The PAN is the key. The first request in a month creates the profile; every later request that month (another application, a recheck,
  a page refresh) reads the same row. The primary key (pan, period_start) and INSERT ... ON CONFLICT DO NOTHING make simultaneous first
  requests safe: one row wins and all of them read it back.
* A synthetic bank customer's profile is a snapshot of their synthetic bureau record (lh_gold.customer_360), so it agrees with the rest of
  the dataset. Any other PAN (for example a real one used to test the app) gets a simulated profile generated deterministically from the PAN
  and the month: never random per call.
* A new profile is generated only when the month changes. Every value carries the DEMO / SIMULATED label; none of it is a bureau score.
"""

import calendar
import hashlib
import json
import random
from datetime import date

LABEL = "DEMO / SIMULATED credit profile, not a credit bureau score"
FIELDS = ("bureau_history_status", "bureau_score", "tradeline_count", "max_dpd_12m", "late_payment_months_24m", "has_written_off", "has_settled",
          "total_monthly_obligations")


def period(today: date) -> tuple[date, date]:
    return today.replace(day=1), today.replace(day=calendar.monthrange(today.year, today.month)[1])


def simulate(pan: str, start: date) -> dict:
    """A plausible profile from the PAN and the month alone: the same inputs always give the same profile."""
    r = random.Random(int(hashlib.sha256(f"{pan.upper()}|{start:%Y-%m}".encode()).hexdigest(), 16))
    status = r.choices(["ESTABLISHED", "THIN", "NO_HISTORY"], weights=[65, 20, 15])[0]
    if status == "NO_HISTORY":
        return {"bureau_history_status": "NO_HISTORY", "bureau_score": None, "tradeline_count": 0, "max_dpd_12m": None, "late_payment_months_24m": 0,
                "has_written_off": False, "has_settled": False, "total_monthly_obligations": 0}
    score = int(r.triangular(560, 880, 760 if status == "ESTABLISHED" else 700))
    lines = r.randint(2, 7) if status == "ESTABLISHED" else r.randint(1, 2)
    late = 0 if score >= 720 else r.choice([0, 1, 1, 2, 3])
    return {"bureau_history_status": status, "bureau_score": score, "tradeline_count": lines, "max_dpd_12m": 0 if late == 0 else r.choice([15, 30, 30, 60]),
            "late_payment_months_24m": late, "has_written_off": score < 600 and r.random() < 0.3, "has_settled": False,
            "total_monthly_obligations": r.choice([0, 0, 2500, 4200, 6800, 9500, 12500]) if lines > 1 else 0}


def from_bureau(credit: dict) -> dict:
    return {k: credit.get(k) for k in FIELDS}


def get_or_create(conn, pan: str, today: date, bureau: dict | None = None) -> dict:
    """The PAN's profile for this month, created on first use. `bureau` is the synthetic bureau record when the PAN belongs to a synthetic customer."""
    pan = pan.upper()
    start, end = period(today)
    found = bureau is not None and bureau.get("bureau_history_status") is not None
    profile = from_bureau(bureau) if found else simulate(pan, start)
    conn.execute("INSERT INTO simulated_credit_profiles (pan, period_start, valid_until, source, score, history_status, profile, label) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (pan, period_start) DO NOTHING",
                 (pan, start, end, "SYNTHETIC_BUREAU" if found else "SIMULATED", profile["bureau_score"], profile["bureau_history_status"],
                  json.dumps(profile, default=str), LABEL))
    return view(conn.execute("SELECT * FROM simulated_credit_profiles WHERE pan = %s AND period_start = %s", (pan, start)).fetchone())


def current(conn, pan: str, today: date) -> dict | None:
    """This month's profile if one exists (reading never creates one)."""
    start, _ = period(today)
    row = conn.execute("SELECT * FROM simulated_credit_profiles WHERE pan = %s AND period_start = %s", ((pan or "").upper(), start)).fetchone()
    return view(row) if row else None


def view(row: dict) -> dict:
    return {"pan": row["pan"], "period_start": row["period_start"].isoformat(), "valid_until": row["valid_until"].isoformat(), "source": row["source"],
            "score": row["score"], "history_status": row["history_status"], "profile": row["profile"], "label": row["label"],
            "generated_at": row["generated_at"].isoformat()}


def risk_category(score: int | None, history: str, foir_after_percent, recommendation: str) -> dict:
    """Deterministic: the simulated score band and the FOIR after this loan. Explained, never AI-generated."""
    foir = float(foir_after_percent) if foir_after_percent not in (None, "") else None
    reasons = []
    if history == "NO_HISTORY" or score is None:
        level = "MEDIUM"
        reasons.append("no credit history on the simulated profile")
    elif score >= 750:
        level = "LOW"
        reasons.append(f"simulated score {score} (750 or above)")
    elif score >= 680:
        level = "MEDIUM"
        reasons.append(f"simulated score {score} (680 to 749)")
    else:
        level = "HIGH"
        reasons.append(f"simulated score {score} (below 680)")
    if foir is not None and foir > 50 and level != "HIGH":
        level = "HIGH" if foir > 60 else "MEDIUM" if level == "LOW" else level
        reasons.append(f"FOIR after this loan {foir:.1f}%")
    if recommendation == "POTENTIALLY_INELIGIBLE":
        level = "HIGH"
        reasons.append("the policy engine found the applicant potentially ineligible")
    return {"level": level, "reasons": reasons, "basis": "simulated credit score band and FOIR after this loan (deterministic)"}
