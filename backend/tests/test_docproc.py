"""Field normalisation, the quote checks on AI output, PAN redaction, and discrepancy rules (no services needed)."""

from decimal import Decimal

import pytest

from app import discrepancy, docproc
from app.docproc import Extraction, ExtractedField


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e999", "1E5", "-500", "12.345", "sNaN", "0x10"])
def test_amounts_accept_plain_digits_only(raw):
    assert docproc.normalise("amount", raw)[1] == "not a number"


@pytest.mark.parametrize("raw,want", [("Rs. 1,20,000", "120000"), ("₹ 62,000.50", "62000.50"), ("INR 5900", "5900"), ("38500.00", "38500")])
def test_amounts_normalise(raw, want):
    assert docproc.normalise("amount", raw) == (want, None)


def shape(value, snippet, page_text="Net Pay: Rs. 62,000\nEmployee: Kavya Sharma", field="net"):
    ex = Extraction(fields=[ExtractedField(field=field, value=value, page=1, snippet=snippet)])
    return {r["field"]: r for r in docproc.check_and_shape("SALARY_SLIP", ex, [page_text.replace("\\n", "\n")], {})}[field]


def test_a_real_quote_with_a_different_value_is_flagged():
    assert shape("Rs. 99,000", "Net Pay: Rs. 62,000")["check_note"] == "value does not appear in the quoted text"
    assert shape("Rs. 62,000", "Net Pay: Rs. 62,000")["check_note"] is None
    assert shape("62000", "Net Pay: Rs. 62,000")["check_note"] is None  # same digits, different formatting
    assert "not found on page" in shape("Rs. 62,000", "Net Pay: Rs. 62,000 only")["check_note"]
    assert shape("Kavya Sharma", "Employee: Kavya Sharma", field="employee_name")["check_note"] is None
    assert shape("Ravi Kumar", "Employee: Kavya Sharma", field="employee_name")["check_note"] == "value does not appear in the quoted text"
    # partial matches do not count: a number inside a bigger number, a name inside a longer word
    assert shape("62", "Net Pay: Rs. 62,000")["check_note"] == "value does not appear in the quoted text"
    assert shape("2,000", "Net Pay: Rs. 62,000")["check_note"] == "value does not appear in the quoted text"
    assert shape("Kavya Sharm", "Employee: Kavya Sharma", field="employee_name")["check_note"] == "value does not appear in the quoted text"
    assert shape("2026-1", "Month: 2026-10", page_text="Month: 2026-10", field="month")["check_note"] == "value does not appear in the quoted text"
    assert shape("2026-10", "Month: 2026-10", page_text="Month: 2026-10", field="month")["check_note"] is None


@pytest.mark.parametrize("period,months", [("2026-05..2026-10", 6), ("01/05/2026 to 31/10/2026", 6), ("May 2026 - Oct 2026", 6),
                                           ("01-May-2026 to 31-Oct-2026", 6), ("1 November 2025 to 30 April 2026", 6), ("05/2026 - 10/2026", 6),
                                           ("2026-10..2026-10", 1), ("Statement period", None), ("2026-13..2026-14", None), (None, None), ("2026-10..2026-05", None)])
def test_statement_months_from_the_printed_period(period, months):
    assert docproc.statement_months(period) == months


@pytest.mark.parametrize("raw,want", [("Female", ("F", None)), ("MALE", ("M", None)), ("F", ("F", None)), (" m ", ("M", None)), ("Other", ("O", None)),
                                      ("Transgender", ("O", None)), ("unknown", ("unknown", "gender not recognised")), ("", (None, None))])
def test_gender_is_stored_as_the_records_code(raw, want):
    assert docproc.normalise("gender", raw) == want


def test_an_aadhaar_gender_printed_in_words_is_stored_as_a_code_and_a_null_value_is_flagged():
    page = "Name: Nayana Shenoy\nGender: Female"
    ex = Extraction(fields=[ExtractedField(field="gender", value="Female", page=1, snippet="Gender: Female"),
                            ExtractedField(field="name", value=None, page=1, snippet="")])
    rows = {r["field"]: r for r in docproc.check_and_shape("AADHAAR_MASKED", ex, [page], {})}
    assert (rows["gender"]["value"], rows["gender"]["check_note"]) == ("F", None)
    assert rows["name"]["value"] is None and rows["name"]["check_note"] == "not found on the document"


def test_pans_are_redacted_and_restored():
    pages, m = docproc.redact(["PAN SYNP0007919P twice SYNP0007919P and SYNP0000001A"])
    assert "SYNP" not in pages[0] and pages[0].count("[PAN-1]") == 2 and docproc.restore("[PAN-2]", m) == "SYNP0000001A"


APPLICANT = {"full_name": "Kavya Sharma", "date_of_birth": "1991-02-09", "pan": "SYNP0213813F"}


def test_income_outside_tolerance_and_unreadable_income_both_open_discrepancies():
    ok = discrepancy.find(APPLICANT, Decimal(62000), [{"id": "d1", "doc_type": "SALARY_SLIP", "fields": {"employee_name": "Kavya Sharma", "net": "65000"}}])
    assert ok == []
    far = discrepancy.find(APPLICANT, Decimal(62000), [{"id": "d1", "doc_type": "SALARY_SLIP", "fields": {"net": "48000"}}])
    assert [x["rule"] for x in far] == ["INCOME_SALARY_SLIP"]
    bad = discrepancy.find(APPLICANT, Decimal(62000), [{"id": "d1", "doc_type": "SALARY_SLIP", "fields": {"net": "sixty thousand"}}])
    assert [x["rule"] for x in bad] == ["INCOME_SALARY_SLIP"] and "could not be compared" in bad[0]["detail"]


def test_statement_income_is_compared_per_month_not_per_credit():
    stmt = {"id": "s", "doc_type": "BANK_STATEMENT", "fields": {"income_credit_total": "252200", "income_credit_count": "58", "period": "2026-05..2026-10"}}
    assert discrepancy.find(APPLICANT, Decimal(43500), [stmt]) == []  # 42,033 a month: within 15% of 43,500
    far = discrepancy.find(APPLICANT, Decimal(60000), [stmt])
    assert [x["rule"] for x in far] == ["INCOME_BANK_CREDITS"] and far[0]["document_value"] == "42033"


def test_name_dob_and_pan_mismatches_are_found():
    found = discrepancy.find(APPLICANT, None, [{"id": "d", "doc_type": "PAN_CARD", "fields": {"name": "K. SHARMA", "dob": "1991-03-09", "pan": "SYNP0213813G"}}])
    assert {x["rule"] for x in found} == {"NAME", "DOB", "PAN"}
    assert discrepancy.find(APPLICANT, None, [{"id": "d", "doc_type": "PAN_CARD", "fields": {"name": "KAVYA  SHARMA", "dob": "1991-02-09", "pan": "SYNP0213813F"}}]) == []
