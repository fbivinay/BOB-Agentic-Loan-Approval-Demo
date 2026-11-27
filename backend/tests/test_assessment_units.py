"""Phase 8 units: policy text extraction and retrieval, evidence selection, and the checks on the AI's explanation."""

from decimal import Decimal as D

from app import policy_kb as kb
from app.assessment import Summary, check_summary, income_evidence, obligations

PAGE = """<html><head><script>var x = "Penal interest 9%";</script></head><body>
<nav><a>Home</a><a>Loans</a></nav>
<main>
  <h2>Eligibility</h2>
  <p>Applicants aged 21 years and above.</p>
  <!-- <p>Processing charges 2% of loan amount (old, commented out)</p> -->
  <table><tr><td>Repayment Period</td><td>84 Months</td></tr></table>
  <h2>Banking Mantra</h2><p>Articles you may like</p>
</main>
<footer>Copyright</footer></body></html>"""


def test_html_text_keeps_the_container_and_drops_comments_scripts_and_furniture():
    blocks = kb.html_blocks(PAGE, {"tag": "main"}, stop_at=["Banking Mantra"])
    text = " ".join(t for t, _ in blocks)
    assert "Applicants aged 21" in text and "Repayment Period | 84 Months" in text
    assert "commented out" not in text and "Penal interest 9%" not in text and "Home" not in text and "Copyright" not in text
    assert "Articles you may like" not in text  # everything after stop_at is page furniture
    assert ("Eligibility", True) in blocks


def test_chunks_respect_the_size_limit_and_carry_their_section():
    blocks = [("Key Facts Statement", True)] + [(f"({i}) " + "The lender shall disclose the annual percentage rate. " * 6, False) for i in range(8)]
    chunks = kb.chunk_blocks(blocks, limit=600)
    assert len(chunks) > 1 and all(len(c.text) <= 600 for c in chunks)
    assert all(c.section == "Key Facts Statement" for c in chunks) and [c.chunk_no for c in chunks] == list(range(1, len(chunks) + 1))
    long = kb.chunk_blocks([("Sentence one is here. " * 200, False)], limit=500)
    assert all(len(c.text) <= 500 for c in long) and len(long) > 5


def row(cid, source, text, section=None):
    return {"chunk_id": cid, "source_id": source, "version": 1, "publisher": "P", "title": "T", "url": "https://example.invalid", "fetched_at": "2026-11-26",
            "section": section, "page_no": None, "text": text, "extraction_method": "HTML_TEXT"}


ROWS = [row("a", "rbi_cb_responsible_business_conduct", "Penal charges in loan accounts shall not be capitalised; no further interest on penal charges.", "D. Penal Charges"),
        row("b", "rbi_cb_responsible_business_conduct", "The Key Facts Statement shall include the annual percentage rate and the amortisation schedule.", "B. KFS"),
        row("c", "bob_personal_loan", "Repayment Capacity (FOIR): 75% of GMI for salaried employees with salary account.", "Repayment Capacity (FOIR)"),
        row("d", "rbi_cb_kyc", "Customer due diligence for individuals: PAN and an officially valid document.", "CDD"),
        row("e", "bob_personal_loan", "Branch locator and customer care numbers.", None)]


def test_bm25_finds_the_relevant_passage_filters_by_source_and_cites_nothing_weak():
    idx = kb.Index(ROWS)
    assert idx.search("penal charges loan accounts", 1, min_score=0.5)[0]["chunk_id"] == "a"
    assert idx.search("repayment capacity FOIR", 1, ("bob_personal_loan",), min_score=0.5)[0]["chunk_id"] == "c"
    assert idx.search("penal charges", 3, ("rbi_cb_kyc",), min_score=0.5) == []  # outside the allowed sources
    assert idx.search("zebra giraffe", 3, min_score=0.1) == []
    assert idx.search("penal charges loan accounts", 3, min_score=99) == []  # below the relevance floor nothing is cited


def test_citations_only_for_failed_or_referred_rules_plus_the_conduct_topics():
    idx = kb.Index(ROWS)
    rules = [{"id": "R10", "status": "FAIL"}, {"id": "R11", "status": "NEEDS_REVIEW"}, {"id": "R01", "status": "PASS"}, {"id": "R07", "status": "SKIPPED"}]
    c = kb.retrieve_for(idx, rules)
    assert set(c["by_rule"]) == {"R10", "R11"} and set(c["terms"]) == set(kb.TERMS_QUERIES)
    for hits in list(c["by_rule"].values()) + list(c["terms"].values()):
        for h in hits:
            assert {"url", "version", "fetched_at", "quote", "source_id", "extraction_method"} <= set(h)


APP = {"bank_relationship": "NTB", "employment_type": "SALARIED_PRIVATE", "existing_monthly_emi": D("3000")}


def doc(t, **fields):
    return {"doc_type": t, "fields": {k: str(v) for k, v in fields.items()}}


def test_income_evidence_prefers_bank_held_credits_then_verified_documents():
    etb = APP | {"bank_relationship": "ETB"}
    credit = {"avg_salary_credit_6m": D("62000"), "salary_credit_months_6m": 6}
    assert income_evidence(etb, credit, [doc("SALARY_SLIP", net=99999)])["monthly"] == D("62000")
    self_emp = etb | {"employment_type": "SELF_EMPLOYED_BUSINESS"}
    e = income_evidence(self_emp, {"avg_salary_credit_6m": None, "avg_business_receipts_6m": D("71000"), "business_receipt_months_6m": 6}, [])
    assert (e["monthly"], e["months"]) == (D("71000"), 6)
    stmt = income_evidence(APP, None, [doc("BANK_STATEMENT", income_credit_total=330000, income_credit_count=6, period="2026-05..2026-10"),
                                       doc("SALARY_SLIP", net=60000, month="2026-10")])
    assert (stmt["monthly"], stmt["months"]) == (D("55000.00"), 6) and stmt["source"] == "Bank statement income credits verified by the Loan Officer"
    slips = income_evidence(APP, None, [doc("SALARY_SLIP", net=60000, month="2026-09"), doc("SALARY_SLIP", net=62000, month="2026-10")])
    assert (slips["monthly"], slips["months"]) == (D("61000.00"), 2)
    assert income_evidence(APP, None, [doc("ITR", gross_total_income=1200000)])["monthly"] == D("100000.00")
    assert income_evidence(APP, None, [doc("PAN_CARD", name="X")])["monthly"] is None


def test_statement_income_is_per_month_of_the_period_never_per_credit():
    # a business with 58 receipts in six months earns its six-month total over 6 (the bug divided by 58: a tenth of the real income)
    biz = APP | {"employment_type": "SELF_EMPLOYED_BUSINESS"}
    e = income_evidence(biz, None, [doc("BANK_STATEMENT", income_credit_total=252200, income_credit_count=58, period="2026-05..2026-10", account_id="ACC-1")])
    assert (e["monthly"], e["months"]) == (D("42033.33"), 6)
    # one statement is used, never several added together: a second copy, even with the account written differently, cannot double it
    first = doc("BANK_STATEMENT", income_credit_total=252200, period="2026-05..2026-10", account_id="ACC-1")
    copy = doc("BANK_STATEMENT", income_credit_total=252200, period="May 2026 - Oct 2026", account_id="ACC 1")
    assert income_evidence(biz, None, [first, copy])["monthly"] == D("42033.33")
    # the statement covering the most months; the lower income when two cover as many
    longer = income_evidence(biz, None, [doc("BANK_STATEMENT", income_credit_total=90000, period="2026-08..2026-10"),
                                         doc("BANK_STATEMENT", income_credit_total=150000, period="2026-05..2026-10")])
    assert (longer["monthly"], longer["months"]) == (D("25000.00"), 6)
    tie = income_evidence(biz, None, [doc("BANK_STATEMENT", income_credit_total=120000, period="2026-05..2026-10"),
                                      doc("BANK_STATEMENT", income_credit_total=60000, period="2026-05..2026-10")])
    assert tie["monthly"] == D("10000.00")
    # a period that is not recognised is never guessed: that statement is not used
    assert income_evidence(APP, None, [doc("BANK_STATEMENT", income_credit_total=180000, period="see page 2"), doc("SALARY_SLIP", net=50000)])["monthly"] == D("50000.00")
    assert income_evidence(biz, None, [doc("BANK_STATEMENT", income_credit_total=180000, income_credit_count=40)])["monthly"] is None
    # a statement with no income credits is not evidence of income: the slips are used
    assert income_evidence(APP, None, [doc("BANK_STATEMENT", income_credit_total=0, period="2026-05..2026-10"), doc("SALARY_SLIP", net=50000)])["monthly"] == D("50000.00")


def test_obligations_take_the_higher_of_bureau_and_declared():
    assert obligations(APP, {"total_monthly_obligations": D("8000")})["monthly"] == D("8000")
    o = obligations(APP, {"total_monthly_obligations": D("1000")})
    assert o["monthly"] == D("3000") and "declared" in o["source"]
    assert obligations(APP, None)["monthly"] == D("3000")


FACTS = '{"recommendation": "REFER_FOR_FURTHER_REVIEW", "rules": [{"id": "R10", "message": "FOIR after the loan would be 41.3% against a limit of 50%."}], ' \
        '"figures": {"recommended_amount": "400000", "recommended_emi": "13286.57"}}'


def test_the_explanation_may_only_use_the_engines_numbers_and_never_speak_as_the_decider():
    ok = Summary(summary="Referred for review. FOIR after the loan would be 41.3% against a limit of 50%, for a loan of 400,000.", points=["EMI 13286.57 per month (R10)."])
    assert check_summary(ok, FACTS) is None
    assert "numbers" in check_summary(Summary(summary="FOIR would be 42% after the loan.", points=[]), FACTS)
    assert "numbers" in check_summary(Summary(summary="A loan of 450,000 fits.", points=[]), FACTS)
    assert "decision" in check_summary(Summary(summary="The loan is approved.", points=[]), FACTS)
    assert "decision" in check_summary(Summary(summary="Referred.", points=["We reject the income figure."]), FACTS)
