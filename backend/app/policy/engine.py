"""Deterministic eligibility engine for the standard Baroda Personal Loan (demo parameters).

No AI is involved. Given a rule set and a case, it returns rule-by-rule results, the eligible amount and a recommendation:

    any rule FAIL            -> POTENTIALLY_INELIGIBLE
    else any NEEDS_REVIEW    -> REFER_FOR_FURTHER_REVIEW
    else                     -> RECOMMENDED_FOR_APPROVAL

It is a recommendation only: a human Assistant Manager or Branch Manager decides.

Principles built into the rules:
  * Thin credit history and missing credit history are never failures by themselves. A rule that needs a score is SKIPPED when there is no score.
    The engine looks at the evidence that exists (repayment behaviour, income credits, obligations) and refers only when evidence is
    insufficient, inconsistent, or needs human judgement.
  * Applicants with a weak bureau file are asked for more months of income evidence (a parameter), not rejected.
  * A numeric limit may fail a case, and the message says it is a demo assumption. An exclusion (staff, NRI, pensioner scheme) can only
    refer a case unless it has been VERIFIED against the official scheme text.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from app.policy import finance
from app.policy.ruleset import RuleSet

D = Decimal
PASS, FAIL, SKIPPED, REVIEW = "PASS", "FAIL", "SKIPPED", "NEEDS_REVIEW"
REC_OK, REC_REFER, REC_NO = "RECOMMENDED_FOR_APPROVAL", "REFER_FOR_FURTHER_REVIEW", "POTENTIALLY_INELIGIBLE"
SALARIED = ("SALARIED_PRIVATE", "SALARIED_GOVT", "SALARIED_PSU")
DEMO_NOTE = "per demo assumption, not Bank of Baroda policy"


@dataclass(frozen=True)
class Case:
    bank_relationship: str                 # ETB | NTB
    history_status: str                    # ESTABLISHED | THIN | NO_HISTORY
    date_of_birth: date
    as_of: date
    employment_type: str
    declared_net_income: Decimal
    requested_amount: Decimal
    requested_tenure_months: int
    existing_monthly_obligations: Decimal = D(0)
    current_job_tenure_months: int | None = None
    # income evidence: what bank credits (or verified documents) actually show
    income_evidence_monthly: Decimal | None = None
    income_evidence_months: int | None = None
    # bureau
    bureau_score: int | None = None
    tradeline_count: int = 0
    max_dpd_12m: int | None = None
    late_payment_months_24m: int = 0
    has_written_off: bool = False
    has_settled: bool = False
    returned_debits_6m: int | None = None  # Bank of Baroda account conduct; None when the bank holds no account data
    # identity and documents
    kyc_status: str | None = None
    pan_status: str | None = None
    name_match_percent: int | None = None
    aadhaar_check: str | None = None
    is_staff: bool = False
    is_nri: bool = False
    documents_verified: bool = True
    open_discrepancies: int = 0

    @property
    def segment(self) -> str:
        return f"{self.bank_relationship}_{ {'ESTABLISHED': 'EST', 'THIN': 'THIN', 'NO_HISTORY': 'NTC'}[self.history_status] }"


@dataclass
class Rule:
    id: str
    title: str
    status: str
    confidence: str
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class Assessment:
    recommendation: str
    segment: str
    policy: str
    rules: list[Rule]
    figures: dict
    reasons: list[str]
    disclaimer: str

    def to_dict(self) -> dict:
        def conv(x):
            if isinstance(x, Decimal):
                return str(x)
            if isinstance(x, date):
                return x.isoformat()
            if isinstance(x, dict):
                return {k: conv(v) for k, v in x.items()}
            if isinstance(x, (list, tuple)):
                return [conv(v) for v in x]
            return x

        return conv({"recommendation": self.recommendation, "segment": self.segment, "policy": self.policy, "disclaimer": self.disclaimer,
                     "figures": self.figures, "reasons": self.reasons, "rules": [r.__dict__ for r in self.rules]})


def age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def _floor_step(x: Decimal, step: int) -> Decimal:
    return (x / step).to_integral_value(ROUND_FLOOR) * step


def _weakest(*confidences: str) -> str:
    return "DEMO_ASSUMPTION" if "DEMO_ASSUMPTION" in confidences else "VERIFIED"


def rate_for(rs: RuleSet, case: Case) -> tuple[int, str]:
    """Indicative annual rate in basis points, and which price band it came from."""
    seg = case.segment
    if case.bureau_score is None:
        base, band = int(rs.param("pricing.no_score_rate_bp", seg).value), "no usable score (priced mid-band)"
    else:
        for b in rs.param("pricing.score_bands", seg).value:
            if case.bureau_score >= b["min_score"]:
                base, band = int(b["rate_bp"]), f"score {b['min_score']}+"
                break
    if case.bank_relationship == "NTB":
        base += int(rs.param("pricing.ntb_premium_bp", seg).value)
    lo, hi = int(rs.param("pricing.min_rate_bp", seg).value), int(rs.param("pricing.max_rate_bp", seg).value)
    return min(max(base, lo), hi), band


def evaluate(rs: RuleSet, case: Case) -> Assessment:
    seg = case.segment
    P = lambda name: rs.param(name, seg)  # noqa: E731
    V = lambda name: P(name).value  # noqa: E731
    rules: list[Rule] = []

    def add(rid, title, status, params, message, **details):
        rules.append(Rule(rid, title, status, _weakest(*(P(p).confidence for p in params)) if params else "DEMO_ASSUMPTION", message, details))

    age = age_on(case.date_of_birth, case.as_of)
    salaried = case.employment_type in SALARIED

    # ---- R01 minimum age
    if age < V("age.min_years"):
        add("R01", "Minimum age", FAIL, ["age.min_years"], f"Applicant is {age}; the minimum is {V('age.min_years')} ({DEMO_NOTE}).", age=age)
    else:
        add("R01", "Minimum age", PASS, ["age.min_years"], f"Applicant is {age}, at or above the minimum of {V('age.min_years')}.", age=age)

    # ---- R02 age at maturity limits the tenure
    max_age_param = "age.max_at_maturity.salaried" if salaried else "age.max_at_maturity.non_salaried"
    options = [t for t in range(V("tenure.min_months"), V("tenure.max_months") + 1, V("tenure.step_months"))]
    allowed = [t for t in range(V("tenure.min_months"), V("tenure.max_months") + 1)
               if age_on(case.date_of_birth, finance.add_months(case.as_of, t)) <= V(max_age_param)]
    options = [t for t in options if t in allowed]
    max_tenure = max(allowed) if allowed else None
    if max_tenure is None:
        add("R02", "Age at maturity", FAIL, [max_age_param, "tenure.min_months"],
            f"Even the shortest tenure ends after age {V(max_age_param)} ({DEMO_NOTE}).", age=age)
    else:
        add("R02", "Age at maturity", PASS, [max_age_param, "tenure.max_months"],
            f"Loan must end by age {V(max_age_param)}; tenure up to {max_tenure} months is allowed.", max_tenure_months=max_tenure)

    # ---- R03 income evidence: enough months of income credits
    need_months = V("income.min_evidence_months")
    have_months = case.income_evidence_months or 0
    if case.income_evidence_monthly is None or have_months == 0:
        add("R03", "Income evidence", REVIEW, ["income.min_evidence_months"], "No income evidence on file yet (bank statement or verified income documents). Needs review.")
    elif have_months < need_months:
        add("R03", "Income evidence", REVIEW, ["income.min_evidence_months"],
            f"Only {have_months} month(s) of income evidence; {need_months} are needed for this applicant's profile. Insufficient evidence, so referred.",
            months_found=have_months, months_needed=need_months)
    else:
        add("R03", "Income evidence", PASS, ["income.min_evidence_months"], f"{have_months} month(s) of income evidence, {need_months} needed.",
            months_found=have_months, months_needed=need_months)

    # ---- R04 income consistency
    declared, observed = case.declared_net_income, case.income_evidence_monthly
    if observed is None or have_months < need_months:
        add("R04", "Income consistency", SKIPPED, ["income.evidence_tolerance_percent"], "Skipped: there is not enough income evidence to compare with the declared income.")
        income_used = declared
    else:
        gap = abs(observed - declared) * 100 / declared if declared > 0 else D(100)
        income_used = min(declared, observed)  # lower of declared and observed: the conservative choice
        if gap > V("income.evidence_tolerance_percent"):
            add("R04", "Income consistency", REVIEW, ["income.evidence_tolerance_percent"],
                f"Observed income {observed:,.0f} differs from declared {declared:,.0f} by {gap:.0f}% (tolerance {V('income.evidence_tolerance_percent')}%). Inconsistent, so referred.",
                declared=declared, observed=observed, gap_percent=gap.quantize(D('0.1')))
        else:
            add("R04", "Income consistency", PASS, ["income.evidence_tolerance_percent"],
                f"Observed income {observed:,.0f} is within {V('income.evidence_tolerance_percent')}% of declared {declared:,.0f}.", declared=declared, observed=observed)

    # ---- R05 minimum income
    if income_used < V("income.min_net_monthly"):
        add("R05", "Minimum income", FAIL, ["income.min_net_monthly"], f"Verified net income {income_used:,.0f} is below the minimum of {V('income.min_net_monthly'):,} ({DEMO_NOTE}).", income_used=income_used)
    else:
        add("R05", "Minimum income", PASS, ["income.min_net_monthly"], f"Net income used {income_used:,.0f} meets the minimum of {V('income.min_net_monthly'):,}.", income_used=income_used)

    # ---- R06 employment tenure (salaried only)
    if not salaried or case.current_job_tenure_months is None:
        add("R06", "Time in current job", SKIPPED, ["employment.min_current_tenure_months"], "Skipped: applies to salaried applicants with a known employment start date.")
    elif case.current_job_tenure_months < V("employment.min_current_tenure_months"):
        add("R06", "Time in current job", REVIEW, ["employment.min_current_tenure_months"],
            f"{case.current_job_tenure_months} months in the current job; {V('employment.min_current_tenure_months')} preferred. Referred for judgement.", months=case.current_job_tenure_months)
    else:
        add("R06", "Time in current job", PASS, ["employment.min_current_tenure_months"], f"{case.current_job_tenure_months} months in the current job.", months=case.current_job_tenure_months)

    # ---- R07 bureau score: only when a score exists, never failed for lacking one
    if case.bureau_score is None:
        why = {"NO_HISTORY": "no credit history (new to credit)", "THIN": "a thin credit file without a score"}.get(case.history_status, "no score available")
        add("R07", "Bureau score", SKIPPED, ["bureau.min_score"], f"Skipped: {why}. A missing score is not a failure; other evidence is assessed instead.")
    elif case.bureau_score < V("bureau.min_score"):
        add("R07", "Bureau score", FAIL, ["bureau.min_score"], f"Score {case.bureau_score} is below the minimum of {V('bureau.min_score')} ({DEMO_NOTE}).", score=case.bureau_score)
    else:
        add("R07", "Bureau score", PASS, ["bureau.min_score"], f"Score {case.bureau_score} meets the minimum of {V('bureau.min_score')}.", score=case.bureau_score)

    # ---- R08 repayment behaviour on the tradelines that exist
    if case.tradeline_count == 0:
        add("R08", "Repayment behaviour", SKIPPED, ["repayment.fail_dpd_days"], "Skipped: no credit accounts to assess, so there is no repayment history either way.")
    else:
        worst = case.max_dpd_12m or 0
        if case.has_written_off or case.has_settled:
            add("R08", "Repayment behaviour", FAIL, ["repayment.fail_dpd_days"], f"A written-off or settled account is on file ({DEMO_NOTE}).", written_off=case.has_written_off, settled=case.has_settled)
        elif worst >= V("repayment.fail_dpd_days"):
            add("R08", "Repayment behaviour", FAIL, ["repayment.fail_dpd_days"], f"Worst delay in 12 months was {worst} days, at or above {V('repayment.fail_dpd_days')} ({DEMO_NOTE}).", max_dpd=worst)
        elif worst >= V("repayment.review_dpd_days") or case.late_payment_months_24m >= V("repayment.review_late_months_24m"):
            add("R08", "Repayment behaviour", REVIEW, ["repayment.review_dpd_days", "repayment.review_late_months_24m"],
                f"Worst delay {worst} days and {case.late_payment_months_24m} late month(s) in 24 months. Referred for judgement.", max_dpd=worst, late_months=case.late_payment_months_24m)
        else:
            add("R08", "Repayment behaviour", PASS, ["repayment.review_dpd_days"], f"Clean recent repayment: worst delay {worst} days, {case.late_payment_months_24m} late month(s) in 24 months.", max_dpd=worst, late_months=case.late_payment_months_24m)

    # ---- R09 conduct of the Bank of Baroda account (existing customers only)
    if case.returned_debits_6m is None:
        add("R09", "Account conduct", SKIPPED, ["conduct.review_returned_debits_6m"], "Skipped: the bank holds no account history for this applicant.")
    elif case.returned_debits_6m >= V("conduct.review_returned_debits_6m"):
        add("R09", "Account conduct", REVIEW, ["conduct.review_returned_debits_6m"], f"{case.returned_debits_6m} returned debits in six months. Referred for judgement.", returned=case.returned_debits_6m)
    else:
        add("R09", "Account conduct", PASS, ["conduct.review_returned_debits_6m"], f"{case.returned_debits_6m} returned debit(s) in six months.", returned=case.returned_debits_6m)

    # ---- pricing and capacity (figures; rule R10 judges them)
    rate_bp, band = rate_for(rs, case)
    foir_cap = D(V("foir.max_percent")) / 100
    emi_room = max(D(0), income_used * foir_cap - case.existing_monthly_obligations)
    step, lo_amt, hi_amt = V("loan.amount_step"), D(V("loan.min_amount")), D(V("loan.max_amount"))
    ceiling = min(hi_amt, income_used * V("loan.max_income_multiple"))

    def eligible_for(tenure: int) -> Decimal:
        return _floor_step(min(ceiling, emi_room * finance.annuity_factor(rate_bp, tenure)), step)

    by_tenure = {t: eligible_for(t) for t in (allowed if allowed else [])}
    best_eligible = max(by_tenure.values()) if by_tenure else D(0)
    rec_tenure = min(max(case.requested_tenure_months, V("tenure.min_months")), max_tenure) if max_tenure else None
    rec_amount = _floor_step(min(case.requested_amount, by_tenure[rec_tenure]), step) if rec_tenure else D(0)
    rec_emi = finance.emi(rec_amount, rate_bp, rec_tenure) if rec_amount > 0 and rec_tenure else D(0)
    foir_after = ((case.existing_monthly_obligations + rec_emi) * 100 / income_used).quantize(D("0.1")) if income_used > 0 else None

    # ---- R10 repayment capacity (FOIR) and loan size
    if not allowed:
        add("R10", "Repayment capacity (FOIR)", SKIPPED, ["foir.max_percent"], "Skipped: no allowed tenure (see age at maturity).")
    elif best_eligible < lo_amt:
        add("R10", "Repayment capacity (FOIR)", FAIL, ["foir.max_percent", "loan.min_amount"],
            f"After existing obligations of {case.existing_monthly_obligations:,.0f}, the {V('foir.max_percent')}% limit leaves room for at most {best_eligible:,.0f}, below the minimum loan of {lo_amt:,.0f} ({DEMO_NOTE}).",
            emi_room=emi_room.quantize(D('0.01')), max_eligible=best_eligible)
    elif rec_amount < lo_amt:
        add("R10", "Repayment capacity (FOIR)", FAIL, ["foir.max_percent", "loan.min_amount"], f"The requested loan cannot be sized within the limits ({DEMO_NOTE}).")
    elif rec_amount < case.requested_amount:
        add("R10", "Repayment capacity (FOIR)", PASS, ["foir.max_percent"],
            f"Requested {case.requested_amount:,.0f} is above what the {V('foir.max_percent')}% limit allows; {rec_amount:,.0f} is recommended instead. FOIR after the loan: {foir_after}%.",
            requested=case.requested_amount, recommended=rec_amount, foir_after_percent=foir_after)
    else:
        add("R10", "Repayment capacity (FOIR)", PASS, ["foir.max_percent"], f"Requested {rec_amount:,.0f} fits; FOIR after the loan would be {foir_after}% against a limit of {V('foir.max_percent')}%.",
            requested=case.requested_amount, foir_after_percent=foir_after)

    # ---- R11 identity and KYC
    problems = []
    if case.kyc_status in ("EXPIRED", "PENDING"):
        problems.append(f"KYC is {case.kyc_status.lower()}")
    if case.pan_status is not None and case.pan_status != "VALID":
        problems.append(f"PAN check returned {case.pan_status}")
    if case.name_match_percent is not None and case.name_match_percent < V("kyc.min_name_match_percent"):
        problems.append(f"name match {case.name_match_percent}% is below {V('kyc.min_name_match_percent')}%")
    if case.aadhaar_check == "MISMATCH":
        problems.append("identity check mismatch")
    if problems:
        add("R11", "Identity and KYC", REVIEW, ["kyc.min_name_match_percent"], (lambda t: t[:1].upper() + t[1:])("; ".join(problems)) + ". Referred for verification.", issues=problems)
    else:
        add("R11", "Identity and KYC", PASS, ["kyc.min_name_match_percent"], "Identity checks are clear.")

    # ---- R12 document completeness
    if not case.documents_verified or case.open_discrepancies > 0:
        add("R12", "Documents", REVIEW, [], f"Documents are not fully verified ({case.open_discrepancies} open discrepancy/discrepancies). Referred until resolved.", open_discrepancies=case.open_discrepancies)
    else:
        add("R12", "Documents", PASS, [], "Documents are verified and no discrepancies are open.")

    # ---- R13-R15 exclusions: flags that refer, and can fail only when VERIFIED
    def exclusion(rid, title, name, hit, what):
        p, raw = P(name), rs.raw(name)
        if not hit:
            add(rid, title, PASS, [name], f"Not {what}.")
        elif raw.get("on_fail") == "FAIL" and p.confidence == "VERIFIED":
            add(rid, title, FAIL, [name], f"Applicant is {what}, which this scheme excludes (verified).")
        else:
            add(rid, title, REVIEW, [name], f"Applicant is {what}. Whether this scheme excludes them is not verified, so the case is flagged for review, not rejected.")

    exclusion("R13", "Bank staff", "exclusion.staff", case.is_staff, "a member of bank staff")
    exclusion("R14", "Residency", "exclusion.nri", case.is_nri, "a non-resident")
    exclusion("R15", "Scheme coverage", "scope.pensioner_other_scheme", case.employment_type == "PENSIONER", "a pensioner (a separate scheme normally applies)")

    statuses = {r.status for r in rules}
    rec = REC_NO if FAIL in statuses else REC_REFER if REVIEW in statuses else REC_OK
    order = {FAIL: 0, REVIEW: 1, PASS: 2, SKIPPED: 3}
    reasons = [f"{r.title}: {r.message}" for r in sorted(rules, key=lambda r: order[r.status]) if r.status in (FAIL, REVIEW)]
    if not reasons:
        reasons = ["All checks passed on the evidence available."]
    figures = {
        "income_used": income_used.quantize(D("0.01")), "existing_obligations": case.existing_monthly_obligations, "emi_room": emi_room.quantize(D("0.01")),
        "max_eligible_amount": best_eligible, "eligible_by_tenure": {str(t): a for t, a in by_tenure.items()}, "tenure_options": options,
        "max_tenure_months": max_tenure, "indicative_rate_bp": rate_bp, "rate_basis": band,
        "recommended_amount": rec_amount, "recommended_tenure_months": rec_tenure, "recommended_emi": rec_emi, "foir_after_percent": foir_after,
        "requested_amount": case.requested_amount, "requested_tenure_months": case.requested_tenure_months,
    }
    return Assessment(rec, seg, rs.identity, rules, figures, reasons,
                      "Recommendation only. A human Assistant Manager or Branch Manager decides. All parameters are demo assumptions, not Bank of Baroda policy.")
