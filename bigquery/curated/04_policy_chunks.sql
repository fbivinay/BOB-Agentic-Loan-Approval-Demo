-- The policy knowledge base the assessment reads: every chunk of the latest version of each official source.
-- A view over lh_raw.policy_sources and lh_raw.policy_chunks (appended by the policy monitor and the seed ingest), so a new version is
-- searchable as soon as it is stored and the app identity needs no table-creation rights. Public documents only.
CREATE OR REPLACE VIEW `{P}.lh_curated.policy_chunks`
OPTIONS (description = "Policy knowledge base: chunks of the latest version of each official public source (RBI, Bank of Baroda). Evidence for reviewers, never an automatic decision input.")
AS
SELECT
  CONCAT(c.source_id, '-v', CAST(c.version AS STRING), '-', FORMAT('%04d', c.chunk_no)) AS chunk_id,
  c.source_id, c.version, s.publisher, s.title, s.url, s.fetched_at, s.text_sha256, s.storage_path,
  c.chunk_no, c.page_no, c.section, c.text, c.extraction_method
FROM `{P}.lh_raw.policy_chunks` c
JOIN (
  SELECT * FROM `{P}.lh_raw.policy_sources`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY version DESC) = 1
) s USING (source_id, version);
