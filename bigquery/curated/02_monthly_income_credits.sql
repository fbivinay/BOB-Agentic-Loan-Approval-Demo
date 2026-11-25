-- Posted credits per customer per month, split into salary/pension and business receipts.
-- Bank of Baroda accounts only, PRIMARY holder only, window = the six months before the as-of date.
CREATE OR REPLACE TABLE `{P}.lh_curated.monthly_income_credits` AS
SELECT
  h.customer_id,
  DATE_TRUNC(t.txn_date, MONTH) AS month,
  SUM(IF(t.category IN ('SALARY', 'PENSION'), t.amount, 0)) AS salary_pension_total,
  SUM(IF(t.category = 'BUSINESS_RECEIPT', t.amount, 0)) AS business_receipts_total
FROM `{P}.lh_raw.transactions` t
JOIN `{P}.lh_raw.account_holders` h ON h.account_id = t.account_id AND h.holder_role = 'PRIMARY'
WHERE t.txn_status = 'POSTED'
  AND t.direction = 'CREDIT'
  AND t.txn_date >= DATE '{INCOME_FROM}'
  AND t.txn_date < DATE '{AS_OF}'
GROUP BY 1, 2;
