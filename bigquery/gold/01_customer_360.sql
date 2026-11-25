-- The table the Loan Officer lookup and the assessment agent read: one row per customer.
-- Built only from lh_raw and lh_curated. Bank-held columns are NULL for NTB customers, who are not searchable by the ETB lookup.
CREATE OR REPLACE TABLE `{P}.lh_gold.customer_360` AS
SELECT
  c.customer_id, c.full_name, c.date_of_birth, f.age_years, c.gender, c.mobile, c.email, c.pan,
  c.address_line, c.city, c.state, c.pincode, c.residency_status, c.is_staff,
  c.bank_relationship, c.customer_since, c.home_branch_id,
  br.branch_name AS home_branch_name, br.city_tier AS home_branch_tier,
  c.kyc_status, c.kyc_last_verified_on,
  k.pan_status, k.name_match_score, k.aadhaar_check, k.address_match,
  e.employment_type, e.employer_name, e.employer_category, e.designation, e.start_date AS employment_start_date,
  f.current_job_tenure_months, e.declared_monthly_gross AS declared_gross_income, f.declared_net_income,
  f.bureau_history_status, f.bureau_score, f.tradeline_count, b.active_tradeline_count, f.oldest_tradeline_age_months,
  f.max_dpd_12m, f.late_payment_months_24m, b.has_written_off, b.has_settled, b.enquiries_last_6m,
  f.total_monthly_obligations, f.existing_obligation_pct,
  f.has_bob_salary_account, f.bob_account_age_months, f.avg_balance_6m,
  f.avg_salary_credit_6m, f.salary_credit_months_6m, f.salary_regularity,
  f.avg_business_receipts_6m, f.business_receipt_months_6m, f.income_credit_vs_declared_pct,
  f.bob_emi_total, f.bob_active_loans, f.returned_debits_6m,
  f.as_of_date
FROM `{P}.lh_raw.customers` c
JOIN `{P}.lh_curated.credit_features` f USING (customer_id)
JOIN `{P}.lh_raw.employment` e ON e.customer_id = c.customer_id AND e.is_current
JOIN `{P}.lh_raw.bureau_profiles` b ON b.customer_id = c.customer_id
JOIN `{P}.lh_raw.ext_kyc_mock` k ON k.customer_id = c.customer_id
LEFT JOIN `{P}.lh_raw.branches` br ON br.branch_id = c.home_branch_id;
