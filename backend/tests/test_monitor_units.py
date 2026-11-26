"""Phase 12 units: the three comparison layers, one fixture per classification (plan section 15), figure parsing, quote checks, and the
golden cases that gate activation."""

import copy
from decimal import Decimal as D

import pytest

from app import monitor as m
from app.policy import golden, ruleset

RS = ruleset.load()
FMAP = m.fact_map()
SRC = {"id": "bob_personal_loan", "url": "https://bankofbaroda.bank.in/loans/personal-loan/baroda-personal-loan", "publisher": "Bank of Baroda", "title": "PL"}


def fact(key, value, condition=None, quote=None):
    return {"key": key, "value": value, "condition": condition, "quote": quote or f"... {value} ...", "numbers": [str(n) for n in m.numbers(value)]}


@pytest.mark.parametrize("printed,expected", [("Rs.20.00 lakhs", ["2000000.00"]), ("48 to 60 Months", ["48", "60"]), ("2% + GST", ["2"]),
                                              ("Rs. 1,000", ["1000"]), ("Rs 1.5 crore", ["15000000.0"]), ("Nil", [])])
def test_figures_are_read_as_printed_including_lakhs_and_ranges(printed, expected):
    assert [str(x) for x in m.numbers(printed)] == expected


def test_layer_a_reports_changed_sections_with_counts_and_samples():
    old = [{"section": "Eligibility", "text": "Age 21 to 60\nIncome Rs 15,000"}, {"section": "Charges", "text": "Processing 2%"}]
    new = [{"section": "Eligibility", "text": "Age 21 to 60\nIncome Rs 20,000"}, {"section": "Charges", "text": "Processing 2%"}]
    a = m.diff_text(old, new)
    assert a["changed"] and a["added_lines"] == 1 and a["removed_lines"] == 1 and [s["section"] for s in a["sections"]] == ["Eligibility"]
    assert not m.diff_text(old, old)["changed"]


def test_layer_b_keeps_only_values_quoted_from_the_source():
    text = "Repayment Period: 84 Months for Govt employees. Processing charges: 2% of loan amount."
    got = m.PolicyFacts(facts=[m.PolicyFact(key="tenure_max_months", value="84 Months", condition="Govt employees", quote="Repayment Period: 84 Months"),
                               m.PolicyFact(key="processing_fee_percent", value="1%", quote="Processing charges: 1% of loan amount."),   # quote not in text
                               m.PolicyFact(key="loan_amount_max", value="Rs. 50 lakhs", quote="Repayment Period: 84 Months")])          # value not in quote
    facts, dropped = m.check_facts(got, text)
    assert [f["key"] for f in facts] == ["tenure_max_months"] and dropped == 2


def run_layers(old_facts, new_facts, text_changed=True, feed_item=False, rs=RS):
    a = {"changed": text_changed}
    b = m.diff_facts(old_facts, new_facts)
    changed = b["current"] if b["baseline"] else b["added"] + [f for f in new_facts if (f["key"], f["value"]) in {(c["key"], c["new"]) for c in b["changed"]}]
    items = m.impact(changed, rs, FMAP, SRC, 2)
    return m.classify(a, b, items, feed_item), items, b


def test_doc_only_when_the_text_changes_but_no_fact_does():
    f = [fact("tenure_max_months", "60 Months")]
    cls, items, _ = run_layers(f, f)
    assert cls == "DOC_ONLY" and items == []


def test_fact_changed_without_rule_impact():
    cls, items, b = run_layers([fact("benchmark_rate", "MCLR 1 year 8.70%")], [fact("benchmark_rate", "MCLR 1 year 8.85%")])
    assert cls == "FACT_CHANGED_NO_RULE_IMPACT" and b["changed"][0]["new"] == "MCLR 1 year 8.85%" and items[0]["kind"] == "NO_RULE"


def test_rule_impact_when_an_official_figure_differs_from_a_parameter():
    cls, items, _ = run_layers(None, [fact("tenure_max_months", "84 Months", "Govt employees with salary account")])
    imp = [i for i in items if i["kind"] == "RULE_IMPACT"]
    assert cls == "RULE_IMPACT" and imp[0]["parameter"] == "tenure.max_months" and imp[0]["current_value"] == 60 and imp[0]["official_value"] == "84"
    assert set(imp[0]["segments"]) == set(ruleset.SEGMENTS) and "employer category" in imp[0]["note"]


def test_verification_candidate_when_an_official_figure_confirms_a_demo_assumption():
    cls, items, _ = run_layers(None, [fact("loan_amount_max", "Rs.20.00 lakhs")])
    assert cls == "VERIFICATION_CANDIDATE" and items[0]["parameter"] == "loan.max_amount" and items[0]["current_confidence"] == "DEMO_ASSUMPTION"


def test_a_verified_parameter_that_matches_is_only_confirmed():
    c = copy.deepcopy(RS.content)
    c["parameters"]["loan.max_amount"].update(confidence="VERIFIED", source={"url": SRC["url"], "quote": "Maximum Rs.20.00 lakhs"})
    cls, items, _ = run_layers(None, [fact("loan_amount_max", "Rs.20.00 lakhs")], rs=ruleset.validate(c))
    assert items[0]["kind"] == "CONFIRMED" and cls == "FACT_CHANGED_NO_RULE_IMPACT"


def test_compliance_item_for_rbi_requirements_and_new_notifications():
    cls, items, _ = run_layers(None, [fact("kfs_validity_working_days", "three working days")])
    assert cls == "COMPLIANCE_ITEM" and items[0]["kind"] == "COMPLIANCE_ITEM"
    assert run_layers(None, [], feed_item=True)[0] == "COMPLIANCE_ITEM"


def test_percent_figures_are_scaled_to_basis_points_before_comparing():
    _, items, _ = run_layers(None, [fact("processing_fee_percent", "1.00%")])
    assert items[0]["parameter"] == "fees.processing_bp" and items[0]["kind"] == "VERIFICATION_CANDIDATE" and items[0]["official_value"] == "100"


def test_rule_impact_outranks_everything_else_in_one_report():
    cls, _, _ = run_layers(None, [fact("loan_amount_max", "Rs.20.00 lakhs"), fact("tenure_max_months", "84 Months"), fact("kfs_validity_working_days", "three working days")])
    assert cls == "RULE_IMPACT"


def test_golden_cases_pass_for_the_shipped_rules_and_catch_a_broken_candidate():
    ok = golden.run(RS.content)
    assert ok["passed"] and ok["counts"]["failed"] == 0 and ok["counts"]["passed"] >= 15
    broken = copy.deepcopy(RS.content)
    broken["parameters"]["pricing.min_rate_bp"]["value"] = 2000  # floor above the ceiling
    r = golden.run(broken)
    assert not r["passed"] and any("rate band" in c["case"] for c in r["cases"] if not c["passed"])
    bad = copy.deepcopy(RS.content)
    bad["parameters"]["tenure.max_months"]["confidence"] = "UNVERIFIED_CANDIDATE"
    assert golden.run(bad)["schema"].startswith("invalid")
    unsourced = copy.deepcopy(RS.content)
    unsourced["parameters"]["tenure.max_months"]["confidence"] = "VERIFIED"  # verified must cite the official source
    assert "official source" in golden.run(unsourced)["schema"]


def test_a_legitimate_change_still_passes_the_golden_cases():
    c = copy.deepcopy(RS.content)
    c["parameters"]["tenure.max_months"]["value"] = 84
    assert golden.run(c)["passed"]


def test_summary_markdown_carries_urls_hashes_and_short_quotes_only():
    result = {"run_id": "abcd1234-0000", "status": "SUCCEEDED", "reports": 1, "proposals": 1,
              "sources": [{"source": "bob_personal_loan", "status": "baseline", "version": 1, "note": None}]}
    report = {"classification": "RULE_IMPACT", "title": "PL", "url": SRC["url"], "to_version": 1, "to_sha256": "f" * 64, "publication_date": "unknown",
              "layer_c": [{"kind": "RULE_IMPACT", "key": "tenure_max_months", "value": "84 Months", "parameter": "tenure.max_months", "current_value": 60,
                           "current_confidence": "DEMO_ASSUMPTION", "quote": "Q" * 400}]}
    md = m.summary_markdown(result, [report])
    assert SRC["url"] in md and "ffffffffffffffff" in md and "Q" * 141 not in md and "Nothing is activated automatically" in md


def test_a_conditioned_fact_is_compared_only_with_the_parameter_for_that_condition():
    _, items, _ = run_layers(None, [fact("age_max_at_maturity_years", "60 years", "salaried"), fact("age_max_at_maturity_years", "65", "non-salaried persons")])
    pairs = {(i["parameter"], i["official_value"], i["kind"]) for i in items}
    assert pairs == {("age.max_at_maturity.salaried", "60", "VERIFICATION_CANDIDATE"), ("age.max_at_maturity.non_salaried", "65", "VERIFICATION_CANDIDATE")}
    _, items, _ = run_layers(None, [fact("age_max_at_maturity_years", "60 years")])  # no condition: compared with both
    assert {i["parameter"] for i in items} == {"age.max_at_maturity.salaried", "age.max_at_maturity.non_salaried"}


def test_quotes_of_table_rows_match_without_our_cell_separators():
    text = "| 1 | Overnight MCLR | 7.85\n| 5 | 1 Year MCLR | 8.85"
    got = m.PolicyFacts(facts=[m.PolicyFact(key="benchmark_rate", value="8.85", condition="1 Year MCLR", quote="5 1 Year MCLR 8.85")])
    facts, dropped = m.check_facts(got, text)
    assert len(facts) == 1 and dropped == 0
