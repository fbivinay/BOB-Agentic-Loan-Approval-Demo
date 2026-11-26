-- Current official facts beside their source, for the analytics and policy pages.
CREATE OR REPLACE VIEW `{P}.lh_gold.policy_current`
OPTIONS (description = "Current facts from official sources with the source title, URL and fetch time. Evidence only.")
AS
SELECT f.source_id, s.publisher, s.title, s.url, s.fetched_at, f.version, f.fact_key, f.value, f.`condition`, f.quote
FROM `{P}.lh_curated.policy_facts_current` f
JOIN `{P}.lh_raw.policy_sources` s USING (source_id, version);
