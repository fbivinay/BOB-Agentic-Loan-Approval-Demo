-- Analytics KPIs (views: always current, nothing to rebuild). Built from lh_raw only (synthetic history, the loan book, and the demo's own
-- events loaded from the Supabase analytics outbox). Events are de-duplicated on event_id, so a repeated load is never counted twice.

CREATE OR REPLACE VIEW `{P}.lh_curated.events` OPTIONS (description = "Demo workflow events from the analytics outbox, one row per event_id. Synthetic.") AS
SELECT * EXCEPT (rn) FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY loaded_at) AS rn FROM `{P}.lh_raw.events`) WHERE rn = 1;

CREATE OR REPLACE VIEW `{P}.lh_gold.kpi_decisions_monthly`
OPTIONS (description = "Decisions by month, channel, role and outcome. HISTORICAL = synthetic generator history; LIVE = decisions made in the demo.") AS
WITH live_apps AS (
  SELECT application_id, ANY_VALUE(JSON_VALUE(payload, '$.channel')) AS channel,
         MIN(TIMESTAMP(JSON_VALUE(payload, '$.application_created_at'))) AS created_at
  FROM `{P}.lh_curated.events` WHERE event_type = 'STATUS_CHANGED' GROUP BY application_id
), live AS (
  SELECT 'LIVE' AS source, DATE_TRUNC(DATE(e.occurred_at, 'Asia/Kolkata'), MONTH) AS month, COALESCE(a.channel, 'BRANCH') AS channel,
         JSON_VALUE(e.payload, '$.decided_by_role') AS role, JSON_VALUE(e.payload, '$.outcome') AS outcome,
         TIMESTAMP_DIFF(e.occurred_at, a.created_at, SECOND) / 86400 AS tat_days, CAST(NULL AS NUMERIC) AS approved_amount
  FROM `{P}.lh_curated.events` e LEFT JOIN live_apps a USING (application_id) WHERE e.event_type = 'DECISION'
), hist AS (
  SELECT 'HISTORICAL' AS source, DATE_TRUNC(decision_date, MONTH) AS month, channel, decided_by_role AS role, outcome,
         CAST(processing_days AS FLOAT64) AS tat_days, approved_amount
  FROM `{P}.lh_raw.historical_applications` WHERE outcome IN ('APPROVED', 'DECLINED')
)
SELECT source, month, channel, role, outcome, COUNT(*) AS decisions, COUNTIF(outcome = 'APPROVED') AS approved, SUM(approved_amount) AS approved_amount,
       SUM(tat_days) AS tat_days_total
FROM (SELECT * FROM hist UNION ALL SELECT * FROM live)
GROUP BY source, month, channel, role, outcome;

CREATE OR REPLACE VIEW `{P}.lh_gold.kpi_ai_vs_human` OPTIONS (description = "Demo decisions: the AI recommendation against the human outcome. The AI only recommends.") AS
SELECT JSON_VALUE(payload, '$.ai_recommendation') AS ai_recommendation, JSON_VALUE(payload, '$.outcome') AS outcome,
       JSON_VALUE(payload, '$.is_override') = 'true' AS is_override, COUNT(*) AS decisions
FROM `{P}.lh_curated.events` WHERE event_type = 'DECISION' GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW `{P}.lh_gold.kpi_funnel_live` OPTIONS (description = "Demo applications that reached each workflow status.") AS
WITH steps AS (
  SELECT status, step FROM UNNEST([STRUCT('DOCS_PENDING' AS status, 1 AS step), ('DOCS_VERIFIED', 2), ('AI_ASSESSED', 3), ('FORWARDED', 4), ('UNDER_REVIEW', 5),
                                   ('APPROVED', 6), ('DECLINED', 6), ('SANCTIONED', 7), ('DISBURSED', 8)])
)
SELECT s.step, s.status, COUNT(DISTINCT e.application_id) AS applications
FROM steps s LEFT JOIN `{P}.lh_curated.events` e ON e.event_type = 'STATUS_CHANGED' AND JSON_VALUE(e.payload, '$.to') = s.status
GROUP BY s.step, s.status;

CREATE OR REPLACE VIEW `{P}.lh_gold.kpi_disbursements_live` OPTIONS (description = "Simulated disbursements released in the demo (no money moved).") AS
SELECT DATE_TRUNC(DATE(occurred_at, 'Asia/Kolkata'), MONTH) AS month, COUNT(*) AS disbursements,
       SUM(CAST(JSON_VALUE(payload, '$.net_amount') AS NUMERIC)) AS net_amount, SUM(CAST(JSON_VALUE(payload, '$.approved_amount') AS NUMERIC)) AS approved_amount
FROM `{P}.lh_curated.events` WHERE event_type = 'DISBURSEMENT_RELEASED' GROUP BY month;

CREATE OR REPLACE VIEW `{P}.lh_gold.kpi_portfolio` OPTIONS (description = "Existing synthetic loan book by product and status.") AS
SELECT product_type, status, COUNT(*) AS loans, SUM(sanctioned_amount) AS sanctioned, SUM(outstanding_amount) AS outstanding,
       SAFE_DIVIDE(SUM(interest_rate * outstanding_amount), SUM(outstanding_amount)) AS weighted_rate
FROM `{P}.lh_raw.existing_loans` GROUP BY product_type, status;

CREATE OR REPLACE VIEW `{P}.lh_gold.kpi_portfolio_dpd` OPTIONS (description = "Open synthetic loans by days past due on their latest instalment.") AS
WITH latest AS (
  SELECT loan_id, days_past_due FROM `{P}.lh_raw.loan_repayments`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY loan_id ORDER BY due_date DESC) = 1
)
SELECT CASE WHEN COALESCE(l.days_past_due, 0) = 0 THEN 'Current' WHEN l.days_past_due <= 30 THEN '1-30' WHEN l.days_past_due <= 60 THEN '31-60'
            WHEN l.days_past_due <= 90 THEN '61-90' ELSE '90+' END AS bucket,
       CASE WHEN COALESCE(l.days_past_due, 0) = 0 THEN 0 WHEN l.days_past_due <= 30 THEN 1 WHEN l.days_past_due <= 60 THEN 2 WHEN l.days_past_due <= 90 THEN 3 ELSE 4 END AS bucket_order,
       COUNT(*) AS loans, SUM(x.outstanding_amount) AS outstanding
FROM `{P}.lh_raw.existing_loans` x LEFT JOIN latest l USING (loan_id) WHERE x.status <> 'CLOSED'
GROUP BY bucket, bucket_order;
