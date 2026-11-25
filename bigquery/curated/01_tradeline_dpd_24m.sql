-- One row per tradeline per month: unnests the JSON array loaded as text in lh_raw.bureau_tradelines.monthly_dpd_24m.
-- Index 23 is the most recent complete month (the month before the as-of date), index 0 is 24 months back.
CREATE OR REPLACE TABLE `{P}.lh_curated.tradeline_dpd_24m` AS
SELECT
  t.tradeline_id,
  t.customer_id,
  t.is_bob,
  idx AS month_index,
  DATE_SUB(DATE_TRUNC(DATE '{AS_OF}', MONTH), INTERVAL 24 - idx MONTH) AS month_start,
  CAST(elem AS INT64) AS dpd
FROM `{P}.lh_raw.bureau_tradelines` t,
  UNNEST(JSON_EXTRACT_ARRAY(t.monthly_dpd_24m)) AS elem WITH OFFSET AS idx;
