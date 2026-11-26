-- Phase 12: official policy monitoring. GitHub Actions schedules the run; the backend fetches, compares and reports. Nothing here can
-- change an active rule: the monitor writes runs, change reports and proposals; a person drafts and activates a rule set (guarded in 0003).

CREATE TABLE monitor_runs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trigger       text NOT NULL CHECK (trigger IN ('SCHEDULE', 'MANUAL', 'LOCAL')),
  status        text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')),
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  sources       jsonb NOT NULL DEFAULT '[]',   -- per source: checked, unchanged / changed / error, version, method
  reports       integer NOT NULL DEFAULT 0,
  proposals     integer NOT NULL DEFAULT 0,
  errors        jsonb NOT NULL DEFAULT '[]',
  actions_run_url text CHECK (actions_run_url IS NULL OR actions_run_url ~ '^https://github\.com/')
);
CREATE INDEX monitor_runs_started_idx ON monitor_runs (started_at DESC);

CREATE TABLE policy_change_reports (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id           uuid NOT NULL REFERENCES monitor_runs(id),
  source_id        text NOT NULL,
  publisher        text NOT NULL,
  title            text NOT NULL,
  url              text NOT NULL CHECK (url ~ '^https://'),
  publication_date text NOT NULL,                -- an ISO date, or 'unknown' (with the reason in layer_a)
  from_version     integer,                      -- NULL: first sight of the source (a baseline)
  to_version       integer NOT NULL,
  from_sha256      text,
  to_sha256        text NOT NULL,
  classification   text NOT NULL CHECK (classification IN ('DOC_ONLY', 'FACT_CHANGED_NO_RULE_IMPACT', 'RULE_IMPACT', 'COMPLIANCE_ITEM', 'VERIFICATION_CANDIDATE')),
  layer_a          jsonb NOT NULL,               -- document change: sections, added / removed lines, samples
  layer_b          jsonb NOT NULL,               -- fact change: added / removed / changed facts with quotes
  layer_c          jsonb NOT NULL,               -- rule impact: each fact against the rule parameters it bears on (human-maintained map)
  status           text NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW', 'REVIEWED')),
  reviewed_by      uuid REFERENCES staff_profiles(user_id),
  reviewed_at      timestamptz,
  review_note      text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT reviewed_is_complete CHECK (status = 'NEW' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND length(btrim(coalesce(review_note, ''))) >= 10))
);
CREATE INDEX policy_change_reports_created_idx ON policy_change_reports (created_at DESC);

CREATE TABLE policy_change_proposals (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  report_id           uuid NOT NULL REFERENCES policy_change_reports(id),
  kind                text NOT NULL CHECK (kind IN ('RULE_CHANGE', 'VERIFY_VALUE')),
  parameter           text NOT NULL,
  current_value       jsonb NOT NULL,
  current_confidence  text NOT NULL CHECK (current_confidence IN ('DEMO_ASSUMPTION', 'VERIFIED')),
  official_value      jsonb NOT NULL,             -- what the source states, as printed and as a number; a person decides what (if anything) changes
  evidence            jsonb NOT NULL,             -- url, version, quote, condition, fact key
  evidence_sha256     text NOT NULL,
  status              text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'DRAFTED', 'ACTIVATED', 'REJECTED')),
  drafted_rule_set_id uuid REFERENCES policy_rule_sets(id),
  decided_by          uuid REFERENCES staff_profiles(user_id),
  decided_at          timestamptz,
  decision_note       text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT decided_is_complete CHECK (status = 'OPEN' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT rejected_has_reason CHECK (status <> 'REJECTED' OR length(btrim(coalesce(decision_note, ''))) >= 10)
);
-- the same official evidence never opens two proposals for the same parameter
CREATE UNIQUE INDEX policy_change_proposals_once ON policy_change_proposals (parameter, kind, evidence_sha256);

-- reports: written by the system, immutable except the one human review step
CREATE FUNCTION policy_change_reports_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'change reports are kept' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF (to_jsonb(NEW) - ARRAY['status', 'reviewed_by', 'reviewed_at', 'review_note']) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'reviewed_by', 'reviewed_at', 'review_note']) THEN
    RAISE EXCEPTION 'a change report is a record; only its review may be added' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status = 'REVIEWED' THEN RAISE EXCEPTION 'this report has already been reviewed' USING ERRCODE = 'check_violation'; END IF;
  IF NEW.status = 'REVIEWED' AND NOT has_permission(NEW.reviewed_by, 'policy.activate') THEN
    RAISE EXCEPTION 'change reports are reviewed by a human with the policy.activate permission' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER policy_change_reports_guard BEFORE UPDATE OR DELETE ON policy_change_reports FOR EACH ROW EXECUTE FUNCTION policy_change_reports_guard();

-- proposals: the monitor opens them; only a person with policy.activate moves them, and ACTIVATED only follows a real activation
CREATE FUNCTION policy_change_proposals_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'proposals are kept' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'OPEN' THEN RAISE EXCEPTION 'a proposal starts OPEN' USING ERRCODE = 'check_violation'; END IF;
    RETURN NEW;
  END IF;
  IF (to_jsonb(NEW) - ARRAY['status', 'drafted_rule_set_id', 'decided_by', 'decided_at', 'decision_note'])
     IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'drafted_rule_set_id', 'decided_by', 'decided_at', 'decision_note']) THEN
    RAISE EXCEPTION 'a proposal''s evidence is fixed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status IN ('ACTIVATED', 'REJECTED') THEN RAISE EXCEPTION 'this proposal is closed' USING ERRCODE = 'check_violation'; END IF;
  IF NEW.decided_by IS NULL OR NOT has_permission(NEW.decided_by, 'policy.activate') THEN
    RAISE EXCEPTION 'proposals are decided by a human with the policy.activate permission' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.status = 'DRAFTED' AND NOT EXISTS (SELECT 1 FROM policy_rule_sets r WHERE r.id = NEW.drafted_rule_set_id AND r.status = 'DRAFT') THEN
    RAISE EXCEPTION 'a drafted proposal points at a DRAFT rule set' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'ACTIVATED' AND NOT EXISTS (SELECT 1 FROM policy_rule_sets r WHERE r.id = NEW.drafted_rule_set_id AND r.status = 'ACTIVE') THEN
    RAISE EXCEPTION 'a proposal is ACTIVATED only when its rule set has been activated' USING ERRCODE = 'check_violation';
  END IF;
  NEW.decided_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER policy_change_proposals_guard BEFORE INSERT OR UPDATE OR DELETE ON policy_change_proposals FOR EACH ROW EXECUTE FUNCTION policy_change_proposals_guard();

CREATE TRIGGER policy_change_reports_audit AFTER UPDATE ON policy_change_reports FOR EACH ROW EXECUTE FUNCTION audit_row('POLICY_REPORT');
CREATE TRIGGER policy_change_proposals_audit AFTER INSERT OR UPDATE ON policy_change_proposals FOR EACH ROW EXECUTE FUNCTION audit_row('POLICY_PROPOSAL');

-- same deny-by-default as before: the browser roles get nothing
ALTER TABLE monitor_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_change_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_change_proposals ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON monitor_runs, policy_change_reports, policy_change_proposals FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON monitor_runs, policy_change_reports, policy_change_proposals FROM authenticated;
  END IF;
END $$;
REVOKE ALL ON FUNCTION policy_change_reports_guard() FROM PUBLIC;
REVOKE ALL ON FUNCTION policy_change_proposals_guard() FROM PUBLIC;
