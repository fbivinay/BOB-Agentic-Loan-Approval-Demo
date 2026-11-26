-- Phase 8: the assessment graph records a recommendation and the engine's eligible terms.
--
-- ELIGIBLE terms cap what a human may approve (terms_within_limits), so they must come from the deterministic engine, never from a person
-- or a hand-written row: an ELIGIBLE row is accepted only when it matches, figure for figure and rule set for rule set, a finished
-- ASSESSMENT run for the same application that is waiting to be recorded (status DOCS_VERIFIED). REQUESTED and APPROVED terms are unchanged.

CREATE OR REPLACE FUNCTION application_terms_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_problem text;
BEGIN
  IF NEW.kind = 'ELIGIBLE' THEN
    IF NEW.policy_rule_set_id IS NULL THEN RAISE EXCEPTION 'eligible terms must cite the policy rule set they were computed under' USING ERRCODE = 'check_violation'; END IF;
    IF NEW.created_by IS NOT NULL OR current_setting('app.actor_type', true) IS DISTINCT FROM 'SYSTEM' THEN
      RAISE EXCEPTION 'eligible terms are computed by the policy engine, not entered by a person' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM applications a WHERE a.id = NEW.application_id AND a.status = 'DOCS_VERIFIED') THEN
      RAISE EXCEPTION 'eligible terms are recorded only with an assessment of a documents-verified application' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent_runs r
                   WHERE r.application_id = NEW.application_id AND r.graph = 'ASSESSMENT' AND r.status = 'DONE' AND r.policy_rule_set_id = NEW.policy_rule_set_id
                     AND (r.output #>> '{assessment,figures,recommended_amount}')::numeric = NEW.amount
                     AND (r.output #>> '{assessment,figures,indicative_rate_bp}')::integer = NEW.rate_bp
                     AND (r.output #>> '{assessment,figures,recommended_tenure_months}')::integer = NEW.tenure_months) THEN
      RAISE EXCEPTION 'eligible terms must match a finished assessment run for this application' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.kind = 'REQUESTED' THEN
    IF NEW.created_by IS NULL OR NOT has_permission(NEW.created_by, 'application.intake') THEN
      RAISE EXCEPTION 'requested terms are entered by a Loan Officer' USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSE  -- APPROVED: human only, inside demo limits, tied to a human decision or a recorded term change
    IF NEW.created_by IS NULL OR NOT has_permission(NEW.created_by, 'decision.make') THEN
      RAISE EXCEPTION 'approved terms can only be set by a human reviewer' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT (EXISTS (SELECT 1 FROM decisions d WHERE d.id = NEW.decision_id AND d.application_id = NEW.application_id AND d.outcome = 'APPROVED' AND d.decided_by = NEW.created_by)
         OR EXISTS (SELECT 1 FROM term_changes c WHERE c.id = NEW.term_change_id AND c.application_id = NEW.application_id AND c.changed_by = NEW.created_by)) THEN
      RAISE EXCEPTION 'approved terms must reference the approving decision or a recorded term change' USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_problem := terms_within_limits(NEW.application_id, NEW.amount, NEW.rate_bp, NEW.tenure_months);
    IF v_problem IS NOT NULL THEN RAISE EXCEPTION 'approved terms rejected: %', v_problem USING ERRCODE = 'check_violation'; END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE INDEX IF NOT EXISTS agent_runs_app_graph_idx ON agent_runs (application_id, graph, started_at DESC);

REVOKE ALL ON FUNCTION application_terms_guard() FROM PUBLIC, anon, authenticated;
