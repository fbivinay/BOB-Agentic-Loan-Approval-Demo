"""Rule set invariants, per-segment golden cases, the thin-file and no-history principles, exclusions, and invariants over random cases."""

import copy
import json
import random
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest
import yaml

from app.policy import engine as e
from app.policy import ruleset as r
from app.policy.engine import FAIL, PASS, REVIEW, SKIPPED, Case

AS_OF = date(2026, 11, 1)
RS = r.load()
SEGMENTS = r.SEGMENTS
HIST = {"EST": "ESTABLISHED", "THIN": "THIN", "NTC": "NO_HISTORY"}


def good(segment="ETB_EST", **over) -> Case:
    """A strong, ordinary applicant in the given segment. Tests change one thing at a time."""
    rel, h = segment.split("_")
    base = dict(bank_relationship=rel, history_status=HIST[h], date_of_birth=date(1988, 3, 5), as_of=AS_OF, employment_type="SALARIED_PRIVATE",
                declared_net_income=D(60000), requested_amount=D(400000), requested_tenure_months=36, existing_monthly_obligations=D(6000),
                current_job_tenure_months=48, income_evidence_monthly=D(60000), income_evidence_months=6, bureau_score=745 if h == "EST" else None,
                tradeline_count={"EST": 5, "THIN": 2, "NTC": 0}[h], max_dpd_12m=0 if h != "NTC" else None, late_payment_months_24m=0,
                returned_debits_6m=0 if rel == "ETB" else None, kyc_status="VERIFIED" if rel == "ETB" else "NOT_APPLICABLE", pan_status="VALID", name_match_percent=98, aadhaar_check="VERIFIED")
    if h == "NTC":
        base["existing_monthly_obligations"] = D(0)
    return Case(**(base | over))


def statuses(a) -> dict:
    return {x.id: x.status for x in a.rules}


# ---------------------------------------------------------------- rule set invariants
def test_the_shipped_rule_set_loads_and_every_value_is_a_labelled_demo_assumption():
    assert RS.scheme == "baroda_personal_loan" and RS.version == 1
    labels = {p["confidence"] for p in RS.content["parameters"].values()} | {p["confidence"] for o in RS.content["segment_overrides"].values() for p in o.values()}
    assert labels == {"DEMO_ASSUMPTION"}  # nothing has been verified against an official document
    assert all(p.get("note") for p in RS.content["parameters"].values())


def test_every_segment_resolves_every_required_parameter_with_a_label():
    for seg in SEGMENTS:
        for name in r.REQUIRED:
            p = RS.param(name, seg)
            assert p.confidence in r.LABELS and p.value is not None


def test_segments_differ_only_where_the_rule_set_says_so():
    assert RS.param("income.min_evidence_months", "ETB_EST").value == 3 and RS.param("income.min_evidence_months", "NTB_EST").value == 3
    for s in ("ETB_THIN", "ETB_NTC", "NTB_THIN", "NTB_NTC"):
        assert RS.param("income.min_evidence_months", s).value == 6
    assert RS.param("foir.max_percent", "NTB_NTC").value == RS.param("foir.max_percent", "ETB_EST").value  # same scheme, same limit


def mutate(fn) -> dict:
    c = copy.deepcopy(RS.content)
    fn(c)
    return c


@pytest.mark.parametrize("fn,why", [
    (lambda c: c["parameters"]["age.min_years"].update(confidence="UNVERIFIED_CANDIDATE"), "UNVERIFIED_CANDIDATE"),
    (lambda c: c["parameters"]["age.min_years"].update(note="UNVERIFIED_CANDIDATE from a search excerpt"), "UNVERIFIED_CANDIDATE"),
    (lambda c: c["parameters"]["age.min_years"].update(confidence="GUESS"), "confidence"),
    (lambda c: c["parameters"]["age.min_years"].pop("confidence"), "confidence"),
    (lambda c: c["parameters"].pop("foir.max_percent"), "missing"),
    (lambda c: c["segments"].pop(), "segments"),
    (lambda c: c["segment_overrides"].update(MADE_UP={}), "unknown segment"),
    (lambda c: c["segment_overrides"]["ETB_THIN"].update({"nonsense.param": {"value": 1, "confidence": "DEMO_ASSUMPTION"}}), "unknown parameter"),
    (lambda c: c["parameters"]["exclusion.staff"].update(on_fail="FAIL"), "VERIFIED"),
    (lambda c: c["parameters"]["exclusion.nri"].update(on_fail="MAYBE"), "on_fail"),
    (lambda c: c["parameters"]["tenure.min_months"].update(value=99), "minimum exceeds"),
    (lambda c: c["parameters"]["pricing.score_bands"]["value"].reverse(), "score_bands"),
])
def test_invalid_rule_sets_cannot_be_loaded(fn, why):
    with pytest.raises(r.RuleSetError, match=why):
        r.validate(mutate(fn))


def test_the_shipped_yaml_round_trips_and_its_hash_is_stable():
    again = r.validate(yaml.safe_load(yaml.safe_dump(RS.content)))
    assert again.sha256 == RS.sha256
    changed = r.validate(mutate(lambda c: c["parameters"]["foir.max_percent"].update(value=45)))
    assert changed.sha256 != RS.sha256


# ---------------------------------------------------------------- golden cases: one strong applicant per segment
@pytest.mark.parametrize("segment", SEGMENTS)
def test_a_strong_applicant_in_every_segment_is_recommended(segment):
    a = e.evaluate(RS, good(segment))
    assert a.recommendation == e.REC_OK, a.reasons
    assert a.segment == segment and not [x for x in a.rules if x.status in (FAIL, REVIEW)]


def test_established_applicants_are_scored_and_everyone_else_skips_the_score_rule():
    assert statuses(e.evaluate(RS, good("ETB_EST")))["R07"] == PASS
    for seg in ("ETB_NTC", "NTB_NTC"):
        assert statuses(e.evaluate(RS, good(seg)))["R07"] == SKIPPED


# ---------------------------------------------------------------- thin files: evidence decides, thinness alone does not
def test_a_thin_file_without_a_score_but_with_clean_repayment_and_steady_income_is_recommended():
    a = e.evaluate(RS, good("ETB_THIN", bureau_score=None))
    s = statuses(a)
    assert a.recommendation == e.REC_OK and s["R07"] == SKIPPED and s["R08"] == PASS and s["R03"] == PASS


def test_a_thin_file_with_a_good_score_is_scored_like_anyone_else():
    a = e.evaluate(RS, good("NTB_THIN", bureau_score=720))
    assert statuses(a)["R07"] == PASS and a.recommendation == e.REC_OK


def test_a_thin_file_with_insufficient_income_evidence_is_referred_not_rejected():
    a = e.evaluate(RS, good("ETB_THIN", income_evidence_months=4))  # six are needed for thin files
    assert a.recommendation == e.REC_REFER and statuses(a)["R03"] == REVIEW and FAIL not in statuses(a).values()
    assert "Insufficient evidence" in a.reasons[0]


def test_the_same_four_months_is_enough_for_an_established_file():
    assert e.evaluate(RS, good("ETB_EST", income_evidence_months=4)).recommendation == e.REC_OK


def test_a_thin_file_with_inconsistent_income_is_referred():
    a = e.evaluate(RS, good("NTB_THIN", income_evidence_monthly=D(38000)))  # declared 60,000, statements show 38,000
    assert a.recommendation == e.REC_REFER and statuses(a)["R04"] == REVIEW
    assert a.figures["income_used"] == D("38000.00")  # the lower figure is what capacity is sized on


def test_a_thin_file_with_adverse_behaviour_is_potentially_ineligible():
    for over in (dict(has_written_off=True), dict(has_settled=True), dict(max_dpd_12m=90), dict(max_dpd_12m=120)):
        a = e.evaluate(RS, good("ETB_THIN", **over))
        assert a.recommendation == e.REC_NO and statuses(a)["R08"] == FAIL, over


def test_a_thin_file_with_a_score_below_the_minimum_fails_the_score_rule():
    a = e.evaluate(RS, good("ETB_THIN", bureau_score=600))
    assert a.recommendation == e.REC_NO and statuses(a)["R07"] == FAIL and "demo assumption" in a.reasons[0]


def test_one_light_late_payment_pattern_is_referred_for_judgement_not_failed():
    a = e.evaluate(RS, good("ETB_THIN", max_dpd_12m=30))
    assert a.recommendation == e.REC_REFER and statuses(a)["R08"] == REVIEW
    assert e.evaluate(RS, good("ETB_EST", late_payment_months_24m=4)).recommendation == e.REC_REFER


# ---------------------------------------------------------------- no history: missing history is never a failed score rule
@pytest.mark.parametrize("seg", ["ETB_NTC", "NTB_NTC"])
def test_a_strong_new_to_credit_applicant_is_recommended_with_the_score_skipped_not_failed(seg):
    a = e.evaluate(RS, good(seg))
    s = statuses(a)
    assert a.recommendation == e.REC_OK and s["R07"] == SKIPPED and s["R08"] == SKIPPED
    assert "not a failure" in next(x for x in a.rules if x.id == "R07").message.lower()
    assert FAIL not in s.values()


@pytest.mark.parametrize("seg", ["ETB_NTC", "NTB_NTC"])
def test_new_to_credit_applicants_are_never_failed_just_for_having_no_history(seg):
    for over in (dict(bureau_score=None), dict(tradeline_count=0, max_dpd_12m=None), dict(requested_amount=D(100000)), dict(requested_tenure_months=60)):
        a = e.evaluate(RS, good(seg, **over))
        assert a.recommendation != e.REC_NO and FAIL not in statuses(a).values(), over


@pytest.mark.parametrize("seg", ["ETB_NTC", "NTB_NTC"])
def test_a_new_to_credit_applicant_with_thin_evidence_is_referred(seg):
    a = e.evaluate(RS, good(seg, income_evidence_months=3))
    assert a.recommendation == e.REC_REFER and statuses(a)["R03"] == REVIEW


def test_missing_income_evidence_is_a_referral_for_everyone():
    for seg in SEGMENTS:
        a = e.evaluate(RS, good(seg, income_evidence_monthly=None, income_evidence_months=None))
        assert a.recommendation == e.REC_REFER and statuses(a)["R03"] == REVIEW and statuses(a)["R04"] == SKIPPED


def test_a_missing_score_is_priced_in_the_middle_band_not_penalised():
    scored = e.evaluate(RS, good("ETB_EST", bureau_score=745)).figures
    none = e.evaluate(RS, good("ETB_NTC")).figures
    assert (scored["indicative_rate_bp"], none["indicative_rate_bp"]) == (1150, 1200)
    assert "middle" in none["rate_basis"] or "mid" in none["rate_basis"]


# ---------------------------------------------------------------- hard limits and capacity
def test_age_limits():
    assert statuses(e.evaluate(RS, good(date_of_birth=date(2006, 6, 1))))["R01"] == FAIL           # 20 years old
    assert statuses(e.evaluate(RS, good(date_of_birth=date(2005, 11, 1))))["R01"] == PASS          # exactly 21
    old = e.evaluate(RS, good(date_of_birth=date(1966, 1, 1)))                                      # 60: even 12 months passes 60
    assert statuses(old)["R02"] == FAIL and old.recommendation == e.REC_NO


def test_age_at_maturity_shortens_the_tenure_that_is_offered():
    a = e.evaluate(RS, good(date_of_birth=date(1969, 6, 1), requested_tenure_months=60))  # 57: loan must end by 60
    # born June 1969: turns 61 in June 2030, so the last month at age 60 is May 2030, which is 42 months from 1 Nov 2026
    assert a.figures["max_tenure_months"] == 42 and a.figures["recommended_tenure_months"] == 42
    assert a.figures["tenure_options"] == [12, 24, 36]


def test_self_employed_applicants_get_the_higher_age_limit():
    dob = date(1965, 6, 1)  # 61
    assert statuses(e.evaluate(RS, good(date_of_birth=dob)))["R02"] == FAIL
    assert statuses(e.evaluate(RS, good(date_of_birth=dob, employment_type="SELF_EMPLOYED_BUSINESS")))["R02"] == PASS


def test_income_below_the_minimum_fails():
    a = e.evaluate(RS, good(declared_net_income=D(14000), income_evidence_monthly=D(14000), existing_monthly_obligations=D(0)))
    assert statuses(a)["R05"] == FAIL and a.recommendation == e.REC_NO


def test_obligations_that_leave_no_room_make_the_case_potentially_ineligible():
    a = e.evaluate(RS, good(existing_monthly_obligations=D(29000)))  # 50% of 60,000 is 30,000
    assert statuses(a)["R10"] == FAIL and a.recommendation == e.REC_NO and a.figures["max_eligible_amount"] < 50000
    assert statuses(e.evaluate(RS, good(existing_monthly_obligations=D(40000))))["R10"] == FAIL


def test_a_request_above_capacity_is_reduced_not_rejected():
    a = e.evaluate(RS, good(requested_amount=D(2000000)))
    assert a.recommendation == e.REC_OK and a.figures["recommended_amount"] < D(2000000)
    assert a.figures["foir_after_percent"] <= 50 and "recommended instead" in next(x for x in a.rules if x.id == "R10").message


def test_eligible_amounts_respect_the_step_the_cap_and_the_income_multiple():
    a = e.evaluate(RS, good(declared_net_income=D(500000), income_evidence_monthly=D(500000), existing_monthly_obligations=D(0), requested_amount=D(2000000)))
    assert a.figures["max_eligible_amount"] == D(2000000)  # the loan cap, not the income
    b = e.evaluate(RS, good(declared_net_income=D(30000), income_evidence_monthly=D(30000), existing_monthly_obligations=D(0)))
    assert b.figures["max_eligible_amount"] <= D(30000) * 20 and b.figures["max_eligible_amount"] % 5000 == 0


def test_capacity_is_sized_on_the_lower_of_declared_and_observed_income():
    higher = e.evaluate(RS, good(income_evidence_monthly=D(70000))).figures["max_eligible_amount"]
    lower = e.evaluate(RS, good(income_evidence_monthly=D(55000))).figures["max_eligible_amount"]
    assert e.evaluate(RS, good()).figures["income_used"] == D("60000.00") and higher == e.evaluate(RS, good()).figures["max_eligible_amount"] and lower < higher


def test_longer_tenures_allow_larger_loans_and_lower_rates_need_less_income():
    f = e.evaluate(RS, good(requested_amount=D(2000000))).figures["eligible_by_tenure"]
    amounts = [D(f[k]) for k in sorted(f, key=int)]
    assert amounts == sorted(amounts)
    cheap, dear = e.rate_for(RS, good(bureau_score=780))[0], e.rate_for(RS, good(bureau_score=660))[0]
    assert cheap < dear


def test_pricing_follows_score_band_and_relationship_and_stays_inside_the_limits():
    assert [e.rate_for(RS, good("ETB_EST", bureau_score=s))[0] for s in (800, 750, 749, 700, 699, 650, 649, 400)] == [1050, 1050, 1150, 1150, 1300, 1300, 1500, 1500]
    assert e.rate_for(RS, good("NTB_EST", bureau_score=800))[0] == 1150  # new-to-bank premium
    assert all(1050 <= e.rate_for(RS, good(s, bureau_score=sc))[0] <= 1900 for s in SEGMENTS for sc in (None, 300, 650, 900))


# ---------------------------------------------------------------- identity, documents, exclusions
@pytest.mark.parametrize("over", [dict(kyc_status="EXPIRED"), dict(kyc_status="PENDING"), dict(pan_status="NOT_FOUND"), dict(pan_status="DEACTIVATED"), dict(name_match_percent=80), dict(aadhaar_check="MISMATCH")])
def test_identity_problems_are_referred(over):
    a = e.evaluate(RS, good(**over))
    assert a.recommendation == e.REC_REFER and statuses(a)["R11"] == REVIEW


def test_unresolved_documents_are_referred():
    assert e.evaluate(RS, good(documents_verified=False)).recommendation == e.REC_REFER
    assert e.evaluate(RS, good(open_discrepancies=2)).recommendation == e.REC_REFER


@pytest.mark.parametrize("over,rule", [(dict(is_staff=True), "R13"), (dict(is_nri=True), "R14"), (dict(employment_type="PENSIONER"), "R15")])
def test_unverified_exclusions_only_refer_and_never_fail(over, rule):
    a = e.evaluate(RS, good(**over))
    assert a.recommendation == e.REC_REFER and statuses(a)[rule] == REVIEW and FAIL not in statuses(a).values()
    assert "not verified" in next(x for x in a.rules if x.id == rule).message


def test_an_exclusion_can_fail_a_case_only_after_it_is_verified():
    verified = mutate(lambda c: c["parameters"]["exclusion.staff"].update(confidence="VERIFIED", on_fail="FAIL",
                                                                          source={"url": "https://example.org/scheme", "quote": "Staff members are not eligible."}))
    a = e.evaluate(r.validate(verified), good(is_staff=True))
    assert statuses(a)["R13"] == FAIL and a.recommendation == e.REC_NO
    with pytest.raises(r.RuleSetError):  # the same switch without verification cannot even be loaded
        r.validate(mutate(lambda c: c["parameters"]["exclusion.staff"].update(on_fail="FAIL")))


def test_returned_debits_on_the_bank_account_are_referred_for_existing_customers_only():
    assert e.evaluate(RS, good("ETB_EST", returned_debits_6m=2)).recommendation == e.REC_REFER
    assert e.evaluate(RS, good("ETB_EST", returned_debits_6m=1)).recommendation == e.REC_OK
    assert statuses(e.evaluate(RS, good("NTB_EST")))["R09"] == SKIPPED


def test_a_short_time_in_the_current_job_is_referred_for_salaried_applicants_only():
    assert e.evaluate(RS, good(current_job_tenure_months=6)).recommendation == e.REC_REFER
    assert statuses(e.evaluate(RS, good(current_job_tenure_months=6, employment_type="SELF_EMPLOYED_BUSINESS")))["R06"] == SKIPPED


# ---------------------------------------------------------------- explainability and determinism
def test_every_rule_names_its_confidence_and_every_failure_says_it_is_a_demo_assumption():
    for seg in SEGMENTS:
        a = e.evaluate(RS, good(seg, bureau_score=600 if seg.endswith("EST") else None, has_written_off=True))
        assert {x.confidence for x in a.rules} <= {"DEMO_ASSUMPTION", "VERIFIED"}
        for x in a.rules:
            if x.status == FAIL:
                assert "demo assumption" in x.message
    assert "human" in e.evaluate(RS, good()).disclaimer.lower()


def test_results_are_deterministic_and_serialisable():
    a, b = e.evaluate(RS, good("NTB_THIN")), e.evaluate(RS, good("NTB_THIN"))
    assert json.dumps(a.to_dict(), sort_keys=True) == json.dumps(b.to_dict(), sort_keys=True)
    d = a.to_dict()
    assert d["recommendation"] in (e.REC_OK, e.REC_REFER, e.REC_NO) and d["policy"].startswith("baroda_personal_loan v1")


# ---------------------------------------------------------------- invariants over many random cases
def random_case(rnd: random.Random) -> Case:
    """Start from a strong applicant and perturb about one field in six, so all three outcomes are reachable."""
    seg = rnd.choice(SEGMENTS)
    h = seg.split("_")[1]
    c = good(seg)
    pick = lambda p: rnd.random() < p  # noqa: E731
    over: dict = {}
    if pick(.12): over["date_of_birth"] = date(rnd.randint(1958, 2008), rnd.randint(1, 12), rnd.randint(1, 28))
    if pick(.15): over["employment_type"] = rnd.choice(["SALARIED_GOVT", "SELF_EMPLOYED_BUSINESS", "SELF_EMPLOYED_PROFESSIONAL", "PENSIONER"])
    if pick(.25):
        net = D(rnd.randint(8000, 400000))
        over.update(declared_net_income=net, income_evidence_monthly=net if pick(.8) else D(rnd.randint(5000, 400000)))
    if pick(.12): over.update(income_evidence_monthly=None, income_evidence_months=None)
    if pick(.15): over["income_evidence_months"] = rnd.choice([0, 2, 3, 4, 6])
    if pick(.5): over["requested_amount"] = D(rnd.randint(20000, 3000000))
    if pick(.3): over["requested_tenure_months"] = rnd.randint(1, 120)
    if pick(.2): over["existing_monthly_obligations"] = D(rnd.randint(0, 150000))
    if h != "NTC":
        if pick(.2): over["bureau_score"] = rnd.choice([None, rnd.randint(300, 900)])
        if pick(.2): over["max_dpd_12m"] = rnd.choice([0, 30, 60, 90, 150])
        if pick(.15): over["late_payment_months_24m"] = rnd.randint(0, 8)
        if pick(.05): over["has_written_off"] = True
        if pick(.05): over["has_settled"] = True
    if pick(.1): over["returned_debits_6m"] = rnd.choice([None, 0, 1, 3])
    if pick(.05): over["is_staff"] = True
    if pick(.05): over["is_nri"] = True
    if pick(.1): over["kyc_status"] = rnd.choice(["EXPIRED", "PENDING", None])
    if pick(.08): over["pan_status"] = "NOT_FOUND"
    if pick(.08): over["name_match_percent"] = rnd.choice([None, 70, 90])
    if pick(.08): over["documents_verified"] = False
    if pick(.08): over["open_discrepancies"] = 1
    if pick(.1): over["current_job_tenure_months"] = rnd.choice([None, 3, 30])
    return replace(c, **over)


def test_invariants_hold_over_20000_random_cases():
    rnd = random.Random(7)
    seen = {e.REC_OK: 0, e.REC_REFER: 0, e.REC_NO: 0}
    for _ in range(20000):
        c = random_case(rnd)
        a = e.evaluate(RS, c)
        st = {x.status for x in a.rules}
        seen[a.recommendation] += 1
        assert a.recommendation in seen
        assert (FAIL in st) == (a.recommendation == e.REC_NO)
        assert (FAIL not in st and REVIEW in st) == (a.recommendation == e.REC_REFER)
        assert (st <= {PASS, SKIPPED}) == (a.recommendation == e.REC_OK)
        assert len(a.rules) == 15 and [x.id for x in a.rules] == [f"R{i:02d}" for i in range(1, 16)]
        # a missing score or missing history is never what fails a case
        r07 = next(x for x in a.rules if x.id == "R07")
        assert (r07.status == FAIL) == (c.bureau_score is not None and c.bureau_score < 650)
        assert (r07.status == SKIPPED) == (c.bureau_score is None)
        if c.history_status == "NO_HISTORY":
            assert next(x for x in a.rules if x.id == "R08").status == SKIPPED
        # exclusions never fail while unverified
        assert all(x.status != FAIL for x in a.rules if x.id in ("R13", "R14", "R15"))
        f = a.figures
        if a.recommendation == e.REC_OK:
            assert f["recommended_amount"] >= 50000 and f["recommended_amount"] <= min(c.requested_amount, f["max_eligible_amount"]) and f["foir_after_percent"] <= 50
        assert 1050 <= f["indicative_rate_bp"] <= 1900
        json.dumps(a.to_dict())
    assert all(v > 500 for v in seen.values()), seen  # the random mix reaches all three outcomes


def test_the_gate_b_parameter_sheet_matches_the_rule_set():
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    import generate_parameter_sheet as g

    assert (root / "docs" / "GATE_B_PARAMETER_SHEET.md").read_text(encoding="utf-8") == g.build(), "regenerate with scripts/generate_parameter_sheet.py"
