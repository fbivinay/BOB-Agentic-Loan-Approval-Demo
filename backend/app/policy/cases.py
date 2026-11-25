"""Build an engine Case from a lh_gold.customer_360 row.

For existing customers the income evidence comes from their own Bank of Baroda account credits. New-to-bank customers have no
bank-held data, so their evidence (months and average of income credits from the external statement, as confirmed by the Loan Officer)
is supplied by the caller through `income_evidence`. Nothing here reads ground-truth tables.
"""

from datetime import date
from decimal import Decimal

from app.policy.engine import SALARIED, Case

D = Decimal


def _num(x, cast=D):
    return None if x is None or x == "" else cast(str(x))


def _bool(x) -> bool:
    return x is True or str(x).lower() == "true"


def case_from_customer_360(row: dict, *, requested_amount, requested_tenure_months: int, as_of: date, income_evidence: tuple[Decimal, int] | None = None,
                           documents_verified: bool = True, open_discrepancies: int = 0) -> Case:
    etype = row["employment_type"]
    if income_evidence is not None:
        evidence, months = D(str(income_evidence[0])), int(income_evidence[1])
    elif _num(row.get("avg_salary_credit_6m")) is None:  # new to bank: nothing held by the bank
        evidence, months = None, None
    elif etype.startswith("SELF_EMPLOYED"):
        evidence, months = _num(row.get("avg_business_receipts_6m")), _num(row.get("business_receipt_months_6m"), int)
    else:
        evidence, months = _num(row.get("avg_salary_credit_6m")), _num(row.get("salary_credit_months_6m"), int)
    name_match = _num(row.get("name_match_score"))
    dob = row["date_of_birth"]
    return Case(
        bank_relationship=row["bank_relationship"], history_status=row["bureau_history_status"],
        date_of_birth=dob if isinstance(dob, date) else date.fromisoformat(str(dob)), as_of=as_of, employment_type=etype,
        declared_net_income=D(str(row["declared_net_income"])), requested_amount=D(str(requested_amount)), requested_tenure_months=int(requested_tenure_months),
        existing_monthly_obligations=_num(row.get("total_monthly_obligations")) or D(0), current_job_tenure_months=_num(row.get("current_job_tenure_months"), int),
        income_evidence_monthly=evidence, income_evidence_months=months, bureau_score=_num(row.get("bureau_score"), int),
        tradeline_count=_num(row.get("tradeline_count"), int) or 0, max_dpd_12m=_num(row.get("max_dpd_12m"), int),
        late_payment_months_24m=_num(row.get("late_payment_months_24m"), int) or 0, has_written_off=_bool(row.get("has_written_off")), has_settled=_bool(row.get("has_settled")),
        returned_debits_6m=_num(row.get("returned_debits_6m"), int), kyc_status=row.get("kyc_status"), pan_status=row.get("pan_status"),
        name_match_percent=None if name_match is None else int((name_match * 100).to_integral_value()), aadhaar_check=row.get("aadhaar_check"),
        is_staff=_bool(row.get("is_staff")), is_nri=row.get("residency_status") == "NRI", documents_verified=documents_verified, open_discrepancies=open_discrepancies,
    )
