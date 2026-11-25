"""Indicative figures for a draft enquiry: declared data and the bank's own records only, labelled, conservative."""

import inspect
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from app.policy import indicative as ind
from app.policy import ruleset

RS = ruleset.load()
TODAY = date(2026, 11, 26)


def inp(**over) -> ind.EnquiryInput:
    base = dict(bank_relationship="ETB", declared_credit_history="ESTABLISHED", date_of_birth=date(1990, 5, 14), employment_type="SALARIED_PRIVATE",
                declared_net_income=D(60000), other_lender_emi=D(0), requested_amount=D(400000), requested_tenure_months=36, as_of=TODAY,
                bob_emi_total=D(6000), observed_income=D(60000), observed_income_months=6, kyc_current=True, income_at_bob=True)
    return ind.EnquiryInput(**(base | over))


def codes(docs: list) -> set:
    return {d["code"] for d in docs}


def test_every_estimate_is_labelled_not_an_offer_and_names_its_policy():
    e = ind.estimate(RS, inp())
    assert "not a loan offer" in e["disclaimer"].lower() and "no credit bureau check" in e["disclaimer"].lower()
    assert e["policy"].startswith("baroda_personal_loan v1")


def test_the_estimate_takes_no_bureau_or_external_check_input():
    fields = set(inspect.signature(ind.EnquiryInput).parameters)
    assert not {f for f in fields if any(w in f for w in ("bureau", "score", "tradeline", "dpd", "pan_status", "aadhaar"))}


def test_a_strong_existing_customer_gets_a_range_and_a_conservative_amount():
    e = ind.estimate(RS, inp())
    assert e["likely_eligible"] and e["rate_range_percent"] == ["10.50", "13.00"]
    assert e["indicative_for_request"]["amount"] == "400000" and e["tenure_options_months"] == [12, 24, 36, 48, 60]
    lo, hi = (D(x) for x in e["indicative_for_request"]["emi_range"])
    assert lo < hi  # the EMI is shown as a range too


def test_new_to_bank_customers_carry_the_premium_and_no_history_is_priced_mid_band():
    assert ind.estimate(RS, inp(bank_relationship="NTB", bob_emi_total=None, observed_income=None, observed_income_months=None))["rate_range_percent"] == ["11.50", "14.00"]
    e = ind.estimate(RS, inp(declared_credit_history="NONE"))
    assert e["rate_range_percent"] == ["12.00", "12.00"] and "middle" in e["rate_basis"]
    assert ind.estimate(RS, inp(declared_credit_history="LIMITED"))["rate_range_percent"] == ["10.50", "13.00"]


def test_income_used_is_the_lower_of_declared_and_what_the_bank_sees():
    e = ind.estimate(RS, inp(observed_income=D(42000)))
    assert e["income_used"] == "42000" and any("below the declared" in n for n in e["notes"])
    assert ind.estimate(RS, inp(observed_income=D(42000), observed_income_months=2))["income_used"] == "60000"  # too few months to rely on


def test_existing_emis_reduce_the_amount_and_can_leave_no_room():
    more = ind.estimate(RS, inp(other_lender_emi=D(10000)))
    assert D(more["indicative_max_amount"]) < D(ind.estimate(RS, inp())["indicative_max_amount"])
    none = ind.estimate(RS, inp(other_lender_emi=D(24000)))
    assert not none["likely_eligible"] and any("too little room" in n for n in none["notes"])


def test_a_request_above_the_limit_is_shown_reduced():
    e = ind.estimate(RS, inp(requested_amount=D(2000000)))
    assert D(e["indicative_for_request"]["amount"]) < D(2000000) and any("looks possible" in n for n in e["notes"])


def test_age_rules_shape_or_block_the_estimate():
    young = ind.estimate(RS, inp(date_of_birth=date(2006, 1, 1)))
    assert not young["likely_eligible"] and any("minimum age" in n for n in young["notes"])
    near = ind.estimate(RS, inp(date_of_birth=date(1969, 1, 1), requested_tenure_months=60))
    assert near["max_tenure_months"] < 60 and near["indicative_for_request"]["tenure_months"] == near["max_tenure_months"]


def test_flags_are_notes_for_the_formal_stage_not_rejections():
    for over, word in ((dict(is_staff=True), "staff"), (dict(is_nri=True), "non-resident"), (dict(employment_type="PENSIONER"), "pensioner")):
        e = ind.estimate(RS, inp(**over))
        assert any(word in n.lower() for n in e["notes"]) and e["likely_eligible"]


def test_documents_follow_employment_and_existing_customers_earn_waivers():
    d = ind.estimate(RS, inp())["documents"]
    assert codes(d["required"]) == {"APPLICATION_FORM", "PHOTOGRAPH", "SALARY_SLIPS_3M"}
    assert codes(d["waived"]) == {"PAN_CARD", "ADDRESS_PROOF", "BANK_STATEMENT_6M"}
    ntb = ind.estimate(RS, inp(bank_relationship="NTB", kyc_current=True, income_at_bob=True, employment_type="SELF_EMPLOYED_BUSINESS"))["documents"]
    assert codes(ntb["required"]) == {"APPLICATION_FORM", "PHOTOGRAPH", "PAN_CARD", "ADDRESS_PROOF", "BANK_STATEMENT_6M", "ITR_1Y", "BUSINESS_PROOF"} and ntb["waived"] == []
    expired = ind.estimate(RS, inp(kyc_current=False))["documents"]
    assert {"PAN_CARD", "ADDRESS_PROOF"} <= codes(expired["required"])
    assert "PENSION_PROOF" in codes(ind.estimate(RS, inp(employment_type="PENSIONER"))["documents"]["required"])


def test_missing_documents_are_what_is_required_minus_what_the_customer_has():
    d = ind.estimate(RS, replace(inp(), documents_available=frozenset({"APPLICATION_FORM", "PAN_CARD"})))["documents"]
    assert codes(d["available"]) == {"APPLICATION_FORM"}  # PAN is waived for this customer, so it does not count either way
    assert codes(d["missing"]) == {"PHOTOGRAPH", "SALARY_SLIPS_3M"}


def test_estimates_are_deterministic():
    assert ind.estimate(RS, inp()) == ind.estimate(RS, inp())
