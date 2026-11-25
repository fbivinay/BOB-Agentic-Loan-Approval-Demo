"""Canonical schema for the synthetic dataset (Gate A, with the review corrections applied).

Column types here are *logical* types used by the canonical writer and, later, the BigQuery loader:
  STRING, INT64, BOOL, DATE, TIMESTAMP, JSON (compact sorted JSON text), ARRAY_INT64 (JSON array text)
  MONEY  whole rupees held as int, written "123456.00"      (BigQuery NUMERIC)
  BP     rate in basis points held as int, written "9.45"   (BigQuery NUMERIC(5,2))
  PCT2   0..100 held as int, written "0.97"                  (BigQuery NUMERIC(3,2))
"""

# dataset -> table -> (primary key columns, [(column, type), ...])
S = "STRING"
TABLES: dict[str, dict[str, tuple[list[str], list[tuple[str, str]]]]] = {
    "lh_raw": {
        "branches": (["branch_id"], [("branch_id", S), ("branch_name", S), ("city", S), ("state", S), ("pincode", S), ("city_tier", S), ("ifsc_code", S)]),
        "customers": (["customer_id"], [
            ("customer_id", S), ("bank_relationship", S), ("full_name", S), ("date_of_birth", "DATE"), ("gender", S),
            ("mobile", S), ("email", S), ("pan", S), ("aadhaar_last4", S), ("address_line", S), ("city", S), ("state", S),
            ("pincode", S), ("residency_status", S), ("is_staff", "BOOL"), ("home_branch_id", S), ("customer_since", "DATE"),
            ("kyc_status", S), ("kyc_last_verified_on", "DATE")]),
        "employment": (["employment_id"], [
            ("employment_id", S), ("customer_id", S), ("employment_type", S), ("employer_name", S), ("employer_category", S),
            ("designation", S), ("start_date", "DATE"), ("end_date", "DATE"), ("is_current", "BOOL"),
            ("declared_monthly_gross", "MONEY"), ("declared_monthly_net", "MONEY")]),
        "accounts": (["account_id"], [
            ("account_id", S), ("institution_name", S), ("is_bob", "BOOL"), ("branch_id", S), ("account_type", S), ("opened_on", "DATE"),
            ("status", S), ("is_salary_account", "BOOL"), ("current_balance", "MONEY"), ("avg_balance_6m", "MONEY"), ("currency", S)]),
        "account_holders": (["account_id", "customer_id"], [
            ("account_id", S), ("customer_id", S), ("holder_role", S), ("relationship_since", "DATE")]),
        "transactions": (["txn_id"], [
            ("txn_id", S), ("account_id", S), ("txn_date", "DATE"), ("amount", "MONEY"), ("direction", S), ("category", S), ("channel", S),
            ("narration", S), ("counterparty", S), ("txn_status", S), ("balance_after", "MONEY"), ("loan_id", S), ("tradeline_id", S)]),
        "existing_loans": (["loan_id"], [
            ("loan_id", S), ("customer_id", S), ("product_type", S), ("sanctioned_amount", "MONEY"), ("outstanding_amount", "MONEY"),
            ("emi_amount", "MONEY"), ("interest_rate", "BP"), ("tenure_months", "INT64"), ("start_date", "DATE"), ("maturity_date", "DATE"),
            ("status", S), ("repayment_account_id", S)]),
        "loan_repayments": (["repayment_id"], [
            ("repayment_id", S), ("loan_id", S), ("due_date", "DATE"), ("due_amount", "MONEY"), ("paid_date", "DATE"),
            ("paid_amount", "MONEY"), ("days_past_due", "INT64"), ("status", S)]),
        "bureau_profiles": (["customer_id"], [
            ("customer_id", S), ("bureau_name", S), ("history_status", S), ("score", "INT64"), ("score_date", "DATE"),
            ("tradeline_count", "INT64"), ("active_tradeline_count", "INT64"), ("oldest_tradeline_date", "DATE"),
            ("total_outstanding", "MONEY"), ("total_monthly_emi", "MONEY"), ("enquiries_last_6m", "INT64"),
            ("max_dpd_last_12m", "INT64"), ("has_written_off", "BOOL"), ("has_settled", "BOOL"), ("report_date", "DATE")]),
        "bureau_tradelines": (["tradeline_id"], [
            ("tradeline_id", S), ("customer_id", S), ("lender_name", S), ("lender_type", S), ("is_bob", "BOOL"), ("existing_loan_id", S),
            ("account_type", S), ("ownership", S), ("opened_date", "DATE"), ("closed_date", "DATE"), ("sanctioned_amount", "MONEY"),
            ("credit_limit", "MONEY"), ("current_balance", "MONEY"), ("emi_amount", "MONEY"), ("status", S), ("max_dpd", "INT64"),
            ("monthly_dpd_24m", "ARRAY_INT64")]),
        "historical_applications": (["application_id"], [
            ("application_id", S), ("customer_id", S), ("applicant_ref", S), ("scheme", S), ("application_date", "DATE"), ("channel", S),
            ("relationship_at_application", S), ("history_status_at_application", S), ("requested_amount", "MONEY"),
            ("requested_tenure_months", "INT64"), ("approved_amount", "MONEY"), ("approved_rate", "BP"),
            ("approved_tenure_months", "INT64"), ("outcome", S), ("decline_reason_code", S), ("decision_date", "DATE"),
            ("processing_days", "INT64"), ("decided_by_role", S)]),
        "ext_kyc_mock": (["customer_id"], [
            ("customer_id", S), ("pan", S), ("pan_status", S), ("pan_name_on_record", S), ("name_match_score", "PCT2"),
            ("aadhaar_check", S), ("address_match", "BOOL"), ("checked_at", "TIMESTAMP")]),
    },
    "lh_truth": {
        "customer_ground_truth": (["customer_id"], [
            ("customer_id", S), ("segment_code", S), ("edge_case_tags", "JSON"), ("persona_notes", S)]),
        "external_statement_lines": (["line_id"], [
            ("line_id", S), ("customer_id", S), ("account_id", S), ("tradeline_id", S), ("statement_month", "DATE"), ("txn_date", "DATE"),
            ("amount", "MONEY"), ("direction", S), ("category", S), ("narration", S), ("balance_after", "MONEY")]),
        "documents_truth": (["doc_id"], [
            ("doc_id", S), ("customer_id", S), ("doc_type", S), ("period", S), ("page_count", "INT64"), ("quality", S),
            ("expected_fields", "JSON"), ("injected_discrepancy", S), ("file_name", S)]),
    },
}
