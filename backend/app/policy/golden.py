"""Golden cases: the principles any rule set must keep before it may be activated (plan section 15). They run in-process against a candidate
rule set, so a proposal drafted from the policy monitor is tested exactly as it would be used.

The cases are built from the candidate's own parameters (a strong applicant earns well above its minimum income, borrows inside its loan
limits, and so on), so a legitimate change of a figure does not break them; a change that breaks a principle does. Results are stored on
the rule set (test_result) and the database refuses activation unless they passed.
"""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal as D

from app.policy import engine as e
from app.policy.engine import FAIL, PASS, REVIEW, SKIPPED, Case
from app.policy.ruleset import SEGMENTS, RuleSet, RuleSetError, validate

SUITE = "golden-v1"
AS_OF = date(2026, 11, 1)
HIST = {"EST": "ESTABLISHED", "THIN": "THIN", "NTC": "NO_HISTORY"}


def strong(rs: RuleSet, segment: str, **over) -> Case:
    rel, h = segment.split("_")
    v = lambda n: rs.param(n, segment).value  # noqa: E731
    income = D(max(60000, 4 * int(v("income.min_net_monthly"))))
    amount = D(max(int(v("loan.min_amount")), min(400000, int(v("loan.max_amount")))))
    tenure = min(max(36, int(v("tenure.min_months"))), int(v("tenure.max_months")))
    base = dict(bank_relationship=rel, history_status=HIST[h], date_of_birth=date(AS_OF.year - max(32, int(v("age.min_years")) + 6), 3, 5), as_of=AS_OF,
                employment_type="SALARIED_PRIVATE", declared_net_income=income, requested_amount=amount, requested_tenure_months=tenure,
                existing_monthly_obligations=D(0) if h == "NTC" else income / 10, current_job_tenure_months=max(48, int(v("employment.min_current_tenure_months")) + 12),
                income_evidence_monthly=income, income_evidence_months=max(6, int(v("income.min_evidence_months"))),
                bureau_score=max(760, int(v("bureau.min_score")) + 60) if h == "EST" else None, tradeline_count={"EST": 5, "THIN": 2, "NTC": 0}[h],
                max_dpd_12m=0 if h != "NTC" else None, late_payment_months_24m=0, returned_debits_6m=0 if rel == "ETB" else None,
                kyc_status="VERIFIED" if rel == "ETB" else "NOT_APPLICABLE", pan_status="VALID", name_match_percent=99, aadhaar_check="VERIFIED")
    return Case(**(base | over))


def _st(a) -> dict:
    return {r.id: r.status for r in a.rules}


def cases(rs: RuleSet):
    """(name, check) pairs; each check returns None when the principle holds, otherwise what went wrong."""
    out = []
    for seg in SEGMENTS:
        def strong_ok(seg=seg):
            a = e.evaluate(rs, strong(rs, seg))
            return None if a.recommendation == e.REC_OK else f"{a.recommendation}: {a.reasons[:2]}"
        out.append((f"a strong applicant in {seg} is recommended", strong_ok))

    def thin_clean():
        a = e.evaluate(rs, strong(rs, "ETB_THIN", bureau_score=None))
        s = _st(a)
        return None if a.recommendation == e.REC_OK and s["R07"] == SKIPPED else f"{a.recommendation}, R07 {s['R07']}"

    def thin_short_evidence():
        need = int(rs.param("income.min_evidence_months", "ETB_THIN").value)
        a = e.evaluate(rs, strong(rs, "ETB_THIN", income_evidence_months=max(1, need - 1)))
        s = _st(a)
        return None if a.recommendation == e.REC_REFER and s["R03"] == REVIEW and FAIL not in s.values() else f"{a.recommendation}, R03 {s['R03']}"

    def thin_adverse():
        a = e.evaluate(rs, strong(rs, "ETB_THIN", has_written_off=True))
        return None if a.recommendation == e.REC_NO and _st(a)["R08"] == FAIL else a.recommendation

    def ntc_never_failed_for_no_history():
        for seg in ("ETB_NTC", "NTB_NTC"):
            a = e.evaluate(rs, strong(rs, seg))
            s = _st(a)
            if a.recommendation != e.REC_OK or s["R07"] != SKIPPED or s["R08"] != SKIPPED or FAIL in s.values():
                return f"{seg}: {a.recommendation}, R07 {s['R07']}, R08 {s['R08']}"
        return None

    def missing_evidence_refers():
        for seg in SEGMENTS:
            a = e.evaluate(rs, strong(rs, seg, income_evidence_monthly=None, income_evidence_months=None))
            if a.recommendation != e.REC_REFER or _st(a)["R03"] != REVIEW:
                return f"{seg}: {a.recommendation}"
        return None

    def no_room_is_ineligible():
        c = strong(rs, "ETB_EST")
        a = e.evaluate(rs, replace(c, existing_monthly_obligations=c.declared_net_income))
        return None if a.recommendation == e.REC_NO and _st(a)["R10"] == FAIL else a.recommendation

    def exclusions_refer_unless_verified():
        for name, field, rid in (("exclusion.staff", "is_staff", "R13"), ("exclusion.nri", "is_nri", "R14")):
            p, raw = rs.param(name, "ETB_EST"), rs.raw(name)
            got = _st(e.evaluate(rs, strong(rs, "ETB_EST", **{field: True})))[rid]
            want = FAIL if (raw.get("on_fail") == "FAIL" and p.confidence == "VERIFIED") else REVIEW
            if got != want:
                return f"{rid} is {got}, expected {want}"
        return None

    def pricing_inside_band():
        lo, hi = int(rs.param("pricing.min_rate_bp", "ETB_EST").value), int(rs.param("pricing.max_rate_bp", "ETB_EST").value)
        for seg in SEGMENTS:
            for score in (None, 600, 700, 800):
                bp, _ = e.rate_for(rs, strong(rs, seg, bureau_score=score))
                if not lo <= bp <= hi:
                    return f"{seg} score {score}: {bp} bp outside {lo}-{hi}"
        return None

    def labels_only_demo_or_verified():
        a = e.evaluate(rs, strong(rs, "ETB_EST"))
        bad = {r.confidence for r in a.rules} - {"DEMO_ASSUMPTION", "VERIFIED"}
        return f"unexpected labels {bad}" if bad else None

    out += [("a thin file with clean repayment and no score is recommended, the score rule skipped", thin_clean),
            ("a thin file with insufficient income evidence is referred, not rejected", thin_short_evidence),
            ("a thin file with a written-off account is potentially ineligible", thin_adverse),
            ("new-to-credit applicants are recommended on evidence and never failed for having no history", ntc_never_failed_for_no_history),
            ("missing income evidence refers every segment", missing_evidence_refers),
            ("obligations that leave no room for an EMI are potentially ineligible", no_room_is_ineligible),
            ("staff and NRI exclusions refer unless VERIFIED to fail", exclusions_refer_unless_verified),
            ("indicative pricing stays inside the rate band", pricing_inside_band),
            ("every rule result is labelled DEMO_ASSUMPTION or VERIFIED", labels_only_demo_or_verified)]
    return out


def run(content: dict) -> dict:
    """Validate the candidate (schema and invariants), then run every golden case. Never raises: the result says what failed."""
    ran_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        rs = validate(content)
    except (RuleSetError, KeyError, TypeError, ValueError) as err:
        return {"passed": False, "suite": SUITE, "ran_at": ran_at, "schema": f"invalid: {err}", "cases": [], "counts": {"passed": 0, "failed": 1}}
    results = []
    for name, check in cases(rs):
        try:
            problem = check()
        except Exception as err:  # noqa: BLE001  a case that crashes is a failed case, with the reason
            problem = f"{type(err).__name__}: {err}"
        results.append({"case": name, "passed": problem is None, "detail": problem})
    failed = sum(1 for r in results if not r["passed"])
    return {"passed": failed == 0, "suite": SUITE, "ran_at": ran_at, "schema": "valid", "rule_set_sha256": rs.sha256, "cases": results,
            "counts": {"passed": len(results) - failed, "failed": failed}}
