"""Deterministic discrepancy checks between human-verified document values and what the application says.

Each finding opens a discrepancy for the Loan Officer, who accepts it with a note, corrects the record, or asks for a new document.
Nothing here decides anything about the loan. Tolerances are DEMO_ASSUMPTION values, not Bank of Baroda policy.
"""

import re
from decimal import Decimal

from app import docproc

NAME_FIELDS = ("name", "employee_name", "account_holder", "pensioner_name", "applicant_name")
TOLERANCE = {  # relative difference from the declared monthly net income that opens a discrepancy (DEMO_ASSUMPTION)
    "INCOME_SALARY_SLIP": Decimal("0.10"),
    "INCOME_BANK_CREDITS": Decimal("0.15"),
    "INCOME_ITR": Decimal("0.20"),
}


def norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z ]", " ", s.upper())).strip()


def _income(rule: str, doc: dict, field: str, monthly: Decimal | None, declared: Decimal) -> dict | None:
    if monthly is None or declared <= 0:
        return None
    diff = (monthly - declared) / declared
    if abs(diff) <= TOLERANCE[rule]:
        return None
    return {"rule": rule, "document_id": doc["id"], "field": field, "form_value": str(declared), "document_value": str(monthly.quantize(Decimal("1"))),
            "detail": f"monthly income from the document differs from the declared figure by {diff * 100:+.0f}% (tolerance {TOLERANCE[rule] * 100:.0f}%)"}


def find(applicant: dict, declared_net: Decimal | None, docs: list[dict]) -> list[dict]:
    """docs: [{id, doc_type, fields: {name: value}}] of VERIFIED documents only."""
    out = []
    for d in docs:
        f = d["fields"]
        for n in NAME_FIELDS:
            if f.get(n) and norm_name(f[n]) != norm_name(applicant["full_name"]):
                out.append({"rule": "NAME", "document_id": d["id"], "field": n, "form_value": applicant["full_name"], "document_value": f[n],
                            "detail": "name on the document differs from the applicant's name"})
                break
        if f.get("dob") and applicant.get("date_of_birth") and f["dob"] != str(applicant["date_of_birth"]):
            out.append({"rule": "DOB", "document_id": d["id"], "field": "dob", "form_value": str(applicant["date_of_birth"]), "document_value": f["dob"],
                        "detail": "date of birth on the document differs from the applicant record"})
        if f.get("pan") and applicant.get("pan") and f["pan"] != applicant["pan"]:
            out.append({"rule": "PAN", "document_id": d["id"], "field": "pan", "form_value": applicant["pan"], "document_value": f["pan"],
                        "detail": "PAN on the document differs from the applicant record"})
        if declared_net is None:
            continue
        num = lambda k: Decimal(f[k]) if f.get(k) not in (None, "") else None  # noqa: E731
        try:
            if d["doc_type"] == "SALARY_SLIP":
                out.append(_income("INCOME_SALARY_SLIP", d, "net", num("net"), declared_net))
            elif d["doc_type"] == "ITR":
                g = num("gross_total_income")
                out.append(_income("INCOME_ITR", d, "gross_total_income", g / 12 if g is not None else None, declared_net))
            elif d["doc_type"] == "BANK_STATEMENT":  # per calendar month of the statement's period, never per credit
                total, months = num("income_credit_total"), docproc.statement_months(f.get("period"))
                if total and not months:
                    out.append({"rule": "INCOME_BANK_CREDITS", "document_id": d["id"], "field": "period", "form_value": str(declared_net),
                                "document_value": f.get("period"), "detail": "the statement period could not be read, so its monthly income could not be compared"})
                else:
                    out.append(_income("INCOME_BANK_CREDITS", d, "income_credit_total", total / months if total else None, declared_net))
        except ArithmeticError:  # a verified value that cannot be compared is a finding, never a silent pass
            rule, fld = {"SALARY_SLIP": ("INCOME_SALARY_SLIP", "net"), "ITR": ("INCOME_ITR", "gross_total_income"),
                         "BANK_STATEMENT": ("INCOME_BANK_CREDITS", "income_credit_total")}[d["doc_type"]]
            out.append({"rule": rule, "document_id": d["id"], "field": fld, "form_value": str(declared_net), "document_value": f.get(fld),
                        "detail": "the income on the document could not be read as a number, so it could not be compared"})
    return [x for x in out if x]
