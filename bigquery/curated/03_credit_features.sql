-- One row per customer. Bank-held columns are populated for ETB customers only: an NTB customer has no Bank of Baroda
-- data, and their external statements only exist as documents at application time, never in this layer.
-- Months are completed months, years are completed years. Everything is relative to the fixed as-of date, never CURRENT_DATE.
CREATE TEMP FUNCTION full_months(d DATE, as_of DATE) AS (
  DATE_DIFF(as_of, d, MONTH) - IF(DATE_ADD(d, INTERVAL DATE_DIFF(as_of, d, MONTH) MONTH) > as_of, 1, 0));
CREATE TEMP FUNCTION full_years(d DATE, as_of DATE) AS (
  DATE_DIFF(as_of, d, YEAR) - IF(DATE_ADD(d, INTERVAL DATE_DIFF(as_of, d, YEAR) YEAR) > as_of, 1, 0));

CREATE OR REPLACE TABLE `{P}.lh_curated.credit_features` AS
WITH months AS (
  SELECT m FROM UNNEST(GENERATE_DATE_ARRAY(DATE '{INCOME_FROM}', DATE_SUB(DATE '{AS_OF}', INTERVAL 1 MONTH), INTERVAL 1 MONTH)) AS m
),
etb AS (SELECT customer_id FROM `{P}.lh_raw.customers` WHERE bank_relationship = 'ETB'),
grid AS (
  SELECT e.customer_id, mo.m AS month,
         COALESCE(i.salary_pension_total, 0) AS sp, COALESCE(i.business_receipts_total, 0) AS br
  FROM etb e CROSS JOIN months mo
  LEFT JOIN `{P}.lh_curated.monthly_income_credits` i ON i.customer_id = e.customer_id AND i.month = mo.m
),
inc AS (
  SELECT customer_id,
         AVG(sp) AS avg_sp, COUNTIF(sp > 0) AS sp_months, STDDEV_POP(CAST(sp AS FLOAT64)) AS sd_sp,
         AVG(br) AS avg_br, COUNTIF(br > 0) AS br_months, STDDEV_POP(CAST(br AS FLOAT64)) AS sd_br
  FROM grid GROUP BY customer_id
),
bank AS (
  SELECT h.customer_id, MIN(a.opened_on) AS first_open, SUM(a.avg_balance_6m) AS avg_bal, LOGICAL_OR(a.is_salary_account) AS has_salary_acct
  FROM `{P}.lh_raw.account_holders` h JOIN `{P}.lh_raw.accounts` a USING (account_id)
  WHERE h.holder_role = 'PRIMARY' AND a.is_bob AND a.status = 'ACTIVE'
  GROUP BY h.customer_id
),
bobl AS (
  SELECT customer_id, SUM(emi_amount) AS bob_emi, COUNT(*) AS bob_loans
  FROM `{P}.lh_raw.existing_loans` WHERE status IN ('ACTIVE', 'OVERDUE') GROUP BY customer_id
),
late AS (SELECT customer_id, COUNTIF(dpd > 0) AS late_months FROM `{P}.lh_curated.tradeline_dpd_24m` GROUP BY customer_id),
ret AS (
  SELECT h.customer_id, COUNT(*) AS returned
  FROM `{P}.lh_raw.transactions` t
  JOIN `{P}.lh_raw.account_holders` h ON h.account_id = t.account_id AND h.holder_role = 'PRIMARY'
  WHERE t.txn_status = 'RETURNED' AND t.txn_date >= DATE '{INCOME_FROM}' AND t.txn_date < DATE '{AS_OF}'
  GROUP BY h.customer_id
),
emp AS (SELECT * FROM `{P}.lh_raw.employment` WHERE is_current)
SELECT
  c.customer_id,
  DATE '{AS_OF}' AS as_of_date,
  full_years(c.date_of_birth, DATE '{AS_OF}') AS age_years,
  full_months(e.start_date, DATE '{AS_OF}') AS current_job_tenure_months,
  e.declared_monthly_net AS declared_net_income,
  -- bank-held (ETB only)
  IF(c.bank_relationship = 'ETB', bank.has_salary_acct, NULL) AS has_bob_salary_account,
  IF(c.bank_relationship = 'ETB', full_months(bank.first_open, DATE '{AS_OF}'), NULL) AS bob_account_age_months,
  IF(c.bank_relationship = 'ETB', bank.avg_bal, NULL) AS avg_balance_6m,
  IF(c.bank_relationship = 'ETB', ROUND(inc.avg_sp, 2), NULL) AS avg_salary_credit_6m,
  IF(c.bank_relationship = 'ETB', inc.sp_months, NULL) AS salary_credit_months_6m,
  IF(c.bank_relationship = 'ETB' AND inc.avg_sp > 0, ROUND(inc.sd_sp / CAST(inc.avg_sp AS FLOAT64), 4), NULL) AS salary_regularity,
  IF(c.bank_relationship = 'ETB', ROUND(inc.avg_br, 2), NULL) AS avg_business_receipts_6m,
  IF(c.bank_relationship = 'ETB', inc.br_months, NULL) AS business_receipt_months_6m,
  IF(c.bank_relationship = 'ETB' AND (inc.avg_sp + inc.avg_br) > 0,
     ROUND(CAST(inc.avg_sp + inc.avg_br AS FLOAT64) * 100 / CAST(e.declared_monthly_net AS FLOAT64), 1), NULL) AS income_credit_vs_declared_pct,
  IF(c.bank_relationship = 'ETB', COALESCE(bobl.bob_emi, 0), NULL) AS bob_emi_total,
  IF(c.bank_relationship = 'ETB', COALESCE(bobl.bob_loans, 0), NULL) AS bob_active_loans,
  IF(c.bank_relationship = 'ETB', COALESCE(ret.returned, 0), NULL) AS returned_debits_6m,
  -- bureau-derived (all customers); the bureau total already includes Bank of Baroda loans, so nothing is double counted
  b.history_status AS bureau_history_status,
  b.score AS bureau_score,
  b.tradeline_count,
  IF(b.oldest_tradeline_date IS NULL, NULL, full_months(b.oldest_tradeline_date, DATE '{AS_OF}')) AS oldest_tradeline_age_months,
  b.max_dpd_last_12m AS max_dpd_12m,
  COALESCE(late.late_months, 0) AS late_payment_months_24m,
  b.total_monthly_emi AS bureau_emi_total,
  b.total_monthly_emi AS total_monthly_obligations,
  ROUND(CAST(b.total_monthly_emi AS FLOAT64) * 100 / CAST(e.declared_monthly_net AS FLOAT64), 1) AS existing_obligation_pct
FROM `{P}.lh_raw.customers` c
JOIN emp e USING (customer_id)
JOIN `{P}.lh_raw.bureau_profiles` b USING (customer_id)
LEFT JOIN inc USING (customer_id)
LEFT JOIN bank USING (customer_id)
LEFT JOIN bobl USING (customer_id)
LEFT JOIN late USING (customer_id)
LEFT JOIN ret USING (customer_id);
