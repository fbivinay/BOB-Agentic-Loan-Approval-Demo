-- Facts extracted (Layer B of the policy monitor) from the latest version of each source that has been read for facts.
CREATE OR REPLACE VIEW `{P}.lh_curated.policy_facts_current`
OPTIONS (description = "Policy facts (with quotes) from the latest fact-read version of each official source. Evidence only; rules change through a human-approved rule set.")
AS
SELECT f.source_id, f.version, f.fact_key, f.value, f.`condition`, f.quote, f.numbers, f.model, f.prompt_version, f.extracted_at
FROM `{P}.lh_raw.policy_facts` f
JOIN (SELECT source_id, MAX(version) AS version FROM `{P}.lh_raw.policy_facts` GROUP BY source_id) m USING (source_id, version)
WHERE f.fact_key <> '_extracted';
