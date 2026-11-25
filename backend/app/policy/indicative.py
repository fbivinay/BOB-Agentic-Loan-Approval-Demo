"""Indicative figures for a draft enquiry. Deterministic, from the active rule set, using only what the customer declares and what
the bank itself holds about an existing customer. No bureau pull, no external KYC, no AI, no workflow.

Because the credit history is only declared at this stage, the rate is a range and the amount is computed at the top of that range,
so the estimate errs on the low side. Everything is labelled as an estimate, not a loan offer.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from app.policy import finance
from app.policy.engine import SALARIED, age_on
from app.policy.ruleset import RuleSet

D = Decimal
DISCLAIMER = ("Indicative estimate, not a loan offer. Based on declared details and the bank's own records only; no credit bureau check has been made. "
              "Demo parameters, not Bank of Baroda policy.")


@dataclass(frozen=True)
class EnquiryInput:
    bank_relationship: str                  # ETB | NTB
    declared_credit_history: str            # ESTABLISHED | LIMITED | NONE | UNKNOWN
    date_of_birth: date
    employment_type: str
    declared_net_income: Decimal
    other_lender_emi: Decimal               # EMIs the customer declares to other lenders
    requested_amount: Decimal
    requested_tenure_months: int
    as_of: date
    # existing-customer facts from the bank's own records (None for NTB)
    bob_emi_total: Decimal | None = None
    observed_income: Decimal | None = None
    observed_income_months: int | None = None
    kyc_current: bool = False
    income_at_bob: bool = False
    is_staff: bool = False
    is_nri: bool = False
    documents_available: frozenset[str] = field(default_factory=frozenset)


def segment(i: EnquiryInput) -> str:
    h = {"ESTABLISHED": "EST", "LIMITED": "THIN", "NONE": "NTC"}.get(i.declared_credit_history, "EST")
    return f"{i.bank_relationship}_{h}"


def rate_range(rs: RuleSet, i: EnquiryInput) -> tuple[int, int, str]:
    seg = segment(i)
    bands = rs.param("pricing.score_bands", seg).value
    eligible_bands = [b["rate_bp"] for b in bands if b["min_score"] >= rs.param("bureau.min_score", seg).value]
    if i.declared_credit_history == "NONE":
        lo = hi = int(rs.param("pricing.no_score_rate_bp", seg).value)
        basis = "no credit history declared: priced in the middle band"
    elif i.declared_credit_history == "LIMITED":
        lo, hi = min(eligible_bands), max(max(eligible_bands), int(rs.param("pricing.no_score_rate_bp", seg).value))
        basis = "limited credit history declared: final rate depends on the credit assessment"
    else:
        lo, hi = min(eligible_bands), max(eligible_bands)
        basis = "depends on the credit score found at the formal assessment"
    if i.bank_relationship == "NTB":
        prem = int(rs.param("pricing.ntb_premium_bp", seg).value)
        lo, hi = lo + prem, hi + prem
    floor, ceil = int(rs.param("pricing.min_rate_bp", seg).value), int(rs.param("pricing.max_rate_bp", seg).value)
    return min(max(lo, floor), ceil), min(max(hi, floor), ceil), basis


def documents(rs: RuleSet, i: EnquiryInput) -> dict:
    kind = "SALARIED" if i.employment_type in SALARIED else "PENSIONER" if i.employment_type == "PENSIONER" else "SELF_EMPLOYED"
    waivers = set()
    if i.bank_relationship == "ETB" and i.kyc_current:
        waivers.add("ETB_KYC_CURRENT")
    if i.bank_relationship == "ETB" and i.income_at_bob:
        waivers.add("ETB_INCOME_AT_BOB")
    required, waived = [], []
    for d in rs.param("documents.required", segment(i)).value:
        if "ALL" in d["applies"] or kind in d["applies"]:
            (waived if set(d["waived_if"]) & waivers else required).append({"code": d["code"], "label": d["label"]})
    have = set(i.documents_available)
    return {"required": required, "waived": waived, "available": [d for d in required if d["code"] in have],
            "missing": [d for d in required if d["code"] not in have]}


def estimate(rs: RuleSet, i: EnquiryInput) -> dict:
    seg = segment(i)
    V = lambda n: rs.param(n, seg).value  # noqa: E731
    notes: list[str] = []
    age = age_on(i.date_of_birth, i.as_of)
    salaried = i.employment_type in SALARIED
    max_age = V("age.max_at_maturity.salaried" if salaried else "age.max_at_maturity.non_salaried")
    allowed = [t for t in range(V("tenure.min_months"), V("tenure.max_months") + 1) if age_on(i.date_of_birth, finance.add_months(i.as_of, t)) <= max_age]
    options = [t for t in range(V("tenure.min_months"), V("tenure.max_months") + 1, V("tenure.step_months")) if t in allowed]
    if age < V("age.min_years"):
        notes.append(f"Below the minimum age of {V('age.min_years')}: not eligible at present.")
    if not allowed:
        notes.append(f"Even the shortest tenure would end after age {max_age}: not eligible under this scheme.")
    if i.employment_type == "PENSIONER":
        notes.append("Pensioners are usually served by a separate pensioner scheme; the formal assessment will refer this case.")
    if i.is_staff:
        notes.append("Bank staff: eligibility under this scheme will be reviewed at the formal stage.")
    if i.is_nri:
        notes.append("Non-resident: eligibility under this scheme will be reviewed at the formal stage.")

    income = i.declared_net_income
    if i.observed_income is not None and (i.observed_income_months or 0) >= 3:
        income = min(income, i.observed_income)
        if i.observed_income < i.declared_net_income * D("0.85"):
            notes.append(f"Salary credits seen at the bank average {i.observed_income:,.0f}, below the declared {i.declared_net_income:,.0f}; the estimate uses the lower figure.")
    if income < V("income.min_net_monthly"):
        notes.append(f"Net income below {V('income.min_net_monthly'):,} a month: not eligible at present.")
    obligations = i.other_lender_emi + (i.bob_emi_total or D(0))
    lo, hi, basis = rate_range(rs, i)
    room = max(D(0), income * D(V("foir.max_percent")) / 100 - obligations)
    ceiling = min(D(V("loan.max_amount")), income * V("loan.max_income_multiple"))
    step = V("loan.amount_step")

    def amount(t: int) -> Decimal:
        return (min(ceiling, room * finance.annuity_factor(hi, t)) / step).to_integral_value(ROUND_FLOOR) * step

    by_tenure = {t: amount(t) for t in options}
    best = max(by_tenure.values(), default=D(0))
    tenure = min(max(i.requested_tenure_months, V("tenure.min_months")), max(allowed)) if allowed else None
    for_request = (min(i.requested_amount, amount(tenure)) / step).to_integral_value(ROUND_FLOOR) * step if tenure else D(0)
    if best < V("loan.min_amount") and allowed and income >= V("income.min_net_monthly"):
        notes.append(f"Existing EMIs of {obligations:,.0f} leave too little room under the {V('foir.max_percent')}% limit for the minimum loan of {V('loan.min_amount'):,}.")
    if tenure and for_request < i.requested_amount and for_request >= V("loan.min_amount"):
        notes.append(f"The requested {i.requested_amount:,.0f} is above the indicative limit for {tenure} months; about {for_request:,.0f} looks possible.")
    emi_lo = finance.emi(for_request, lo, tenure) if for_request >= V("loan.min_amount") and tenure else None
    emi_hi = finance.emi(for_request, hi, tenure) if for_request >= V("loan.min_amount") and tenure else None
    eligible_now = not any("not eligible" in n for n in notes) and best >= V("loan.min_amount")
    return {
        "disclaimer": DISCLAIMER, "policy": rs.identity, "segment_hint": seg, "as_of": i.as_of.isoformat(),
        "likely_eligible": eligible_now, "notes": notes,
        "income_used": str(income.quantize(D("1"))), "existing_emis": str(obligations.quantize(D("1"))),
        "rate_range_percent": [f"{lo / 100:.2f}", f"{hi / 100:.2f}"], "rate_basis": basis,
        "tenure_options_months": options, "max_tenure_months": max(allowed) if allowed else None,
        "indicative_max_amount": str(best), "indicative_by_tenure": {str(t): str(a) for t, a in by_tenure.items()},
        "requested": {"amount": str(i.requested_amount), "tenure_months": i.requested_tenure_months},
        "indicative_for_request": {"amount": str(for_request), "tenure_months": tenure,
                                   "emi_range": [str(emi_lo), str(emi_hi)] if emi_lo else None},
        "documents": documents(rs, i),
    }
